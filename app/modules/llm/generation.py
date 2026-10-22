"""LLM question generation with mandatory review (S07.1, FR-07, NFR-06).

The model is asked for questions as JSON. Its answer is never trusted: every item is
checked for a question, at least two distinct options, a correct answer that is one
of them, an explanation, and subject/topic/difficulty tags equal to the ones asked
for. Items that fail, including any outside the selected topic, are dropped and the
shortfall is requested again, a bounded number of times.

Valid items are stored as *draft* questions (``source=llm``). A draft is invisible to
the bank, search and quiz assembly until the requester approves it; a rejected draft
is removed for good. A batch can only be turned into a quiz once every one of its
questions has been approved or rejected.

Every provider call is recorded in ``llm_requests`` with its outcome.
"""

import json
import re
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.llm.models import LlmRequest, LlmRequestStatus, LlmTask
from app.modules.llm.provider import LlmError, LlmProvider, LlmTimeoutError
from app.modules.llm.service import complete_request, record_request
from app.modules.questions import service as questions
from app.modules.questions.service import (
    Difficulty,
    QuestionSource,
    QuestionStatus,
    QuestionType,
)

MIN_QUESTIONS, MAX_QUESTIONS = 5, 10
# Provider calls per batch: the first plus retries for dropped (invalid/off-topic) items.
MAX_ROUNDS = 3


class GenerationFailedError(Exception):
    """The provider failed, timed out, or never produced enough valid questions."""

    def __init__(self, message: str, *, timeout: bool = False) -> None:
        super().__init__(message)
        self.timeout = timeout


class NotReviewedError(Exception):
    """Add to Quiz was asked for while some questions still await review."""


class NothingApprovedError(Exception):
    pass


@dataclass(frozen=True)
class GeneratedItem:
    """One validated question from the model, not yet stored."""

    subject: str
    topic: str
    difficulty: Difficulty
    text: str
    options: tuple[str, ...]
    correct_answer: str
    explanation: str


@dataclass(frozen=True)
class GeneratedQuestion:
    question: questions.QuestionView
    review_status: str  # pending | approved | rejected
    edited: bool


@dataclass(frozen=True)
class Generation:
    id: int
    user_id: int
    subject: str
    topic: str
    difficulty: Difficulty
    channel_id: int | None
    questions: tuple[GeneratedQuestion, ...]

    @property
    def pending_count(self) -> int:
        return sum(1 for q in self.questions if q.review_status == "pending")

    @property
    def approved_ids(self) -> list[int]:
        return [q.question.id for q in self.questions if q.review_status == "approved"]

    @property
    def can_add_to_quiz(self) -> bool:
        """Add to Quiz is offered only once nothing is pending and something is approved."""
        return self.pending_count == 0 and bool(self.approved_ids)


def _same(a: str, b: str) -> bool:
    return " ".join(a.split()).lower() == " ".join(b.split()).lower()


def build_prompt(subject: str, topic: str, difficulty: Difficulty, count: int) -> str:
    return (
        f"Write {count} multiple-choice questions for the subject {subject!r}, strictly "
        f"limited to the topic {topic!r}, at {difficulty.value} difficulty.\n"
        'Return JSON of the form {"questions": [{"subject": str, "topic": str, '
        '"difficulty": "easy"|"medium"|"hard", "question": str, '
        '"options": [4 distinct strings], "correct_answer": one of the options, '
        '"explanation": str}]}.\n'
        f"Every item must use subject {subject!r}, topic {topic!r} and difficulty "
        f"{difficulty.value!r} exactly. Do not include questions about any other topic."
    )


def _json_payload(text: str) -> Any:
    """Parse the model's JSON, tolerating a Markdown code fence around it."""
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()
    try:
        return json.loads(text)
    except ValueError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise
        return json.loads(text[start : end + 1])


def validate_item(
    raw: Any, *, subject: str, topic: str, difficulty: Difficulty
) -> GeneratedItem | None:
    """The item as a ``GeneratedItem`` if it is complete and on target, else ``None``.

    An item tagged with another subject, topic or difficulty is rejected, so content
    outside the selected topic never reaches the review screen.
    """
    if not isinstance(raw, dict):
        return None
    fields = {k: raw.get(k) for k in ("subject", "topic", "difficulty", "question")}
    fields |= {k: raw.get(k) for k in ("correct_answer", "explanation")}
    if not all(isinstance(v, str) and v.strip() for v in fields.values()):
        return None
    if not (
        _same(fields["subject"], subject)
        and _same(fields["topic"], topic)
        and _same(fields["difficulty"], difficulty.value)
    ):
        return None
    options = raw.get("options")
    if not isinstance(options, list) or not all(isinstance(o, str) for o in options):
        return None
    options = [" ".join(o.split()) for o in options if o.strip()]
    if len(options) < 2 or len({o.lower() for o in options}) != len(options):
        return None
    match = [o for o in options if _same(o, fields["correct_answer"])]
    if not match:
        return None
    return GeneratedItem(
        subject=subject,
        topic=topic,
        difficulty=difficulty,
        text=fields["question"].strip(),
        options=tuple(options),
        correct_answer=match[0],
        explanation=fields["explanation"].strip(),
    )


def request_items(
    db: Session,
    provider: LlmProvider,
    *,
    user_id: int,
    task: LlmTask,
    subject: str,
    topic: str,
    difficulty: Difficulty,
    count: int,
    channel_id: int | None = None,
    offset: int = 0,
    avoid: set[str] | None = None,
) -> tuple[int, list[GeneratedItem]]:
    """Ask the provider for ``count`` valid on-topic items; returns (request id, items).

    Invalid or off-topic items are dropped and the shortfall re-requested, up to
    ``MAX_ROUNDS`` calls. Returns fewer than ``count`` only when the rounds run out;
    raises ``GenerationFailedError`` when the provider errors or times out. The
    request and its outcome are recorded and flushed either way.
    """
    prompt = build_prompt(subject, topic, difficulty, count)
    request_id = record_request(
        db,
        user_id=user_id,
        task=task,
        prompt=prompt,
        channel_id=channel_id,
        subject=subject,
        topic=topic,
    )
    seen = {t.lower() for t in avoid or set()}
    items: list[GeneratedItem] = []
    rejected = 0
    for round_no in range(MAX_ROUNDS):
        wanted = count - len(items)
        if wanted <= 0:
            break
        params = {
            "subject": subject,
            "topic": topic,
            "difficulty": difficulty.value,
            "count": wanted,
            "round": round_no,
            "offset": offset,
        }
        try:
            text = provider.complete(task, build_prompt(subject, topic, difficulty, wanted), params)
        except LlmError as exc:
            complete_request(db, request_id, error=str(exc) or type(exc).__name__)
            raise GenerationFailedError(str(exc), timeout=isinstance(exc, LlmTimeoutError)) from exc
        try:
            payload = _json_payload(text)
        except ValueError:
            rejected += 1
            continue
        raw_items = payload.get("questions") if isinstance(payload, dict) else payload
        for raw in raw_items if isinstance(raw_items, list) else []:
            item = validate_item(raw, subject=subject, topic=topic, difficulty=difficulty)
            if item is None or item.text.lower() in seen:
                rejected += 1
                continue
            seen.add(item.text.lower())
            items.append(item)
            if len(items) == count:
                break
    complete_request(
        db,
        request_id,
        output={"accepted": len(items), "rejected": rejected, "question_ids": []},
        error=None if items else "no valid questions in the AI response",
    )
    return request_id, items


# --------------------------------------------------------------------------- #
# Batches for review
# --------------------------------------------------------------------------- #

_REVIEW = {
    QuestionStatus.DRAFT: "pending",
    QuestionStatus.PUBLISHED: "approved",
    QuestionStatus.REMOVED: "rejected",
}


def _output(req: LlmRequest) -> dict[str, Any]:
    return dict(req.output or {})


def generate(
    db: Session,
    provider: LlmProvider,
    *,
    user_id: int,
    subject: str,
    topic: str,
    difficulty: Difficulty,
    count: int,
    channel_id: int | None = None,
) -> Generation:
    """Generate ``count`` (5-10) questions and store them as drafts for review."""
    if not MIN_QUESTIONS <= count <= MAX_QUESTIONS:
        raise ValueError(f"count must be between {MIN_QUESTIONS} and {MAX_QUESTIONS}")
    request_id, items = request_items(
        db,
        provider,
        user_id=user_id,
        task=LlmTask.GENERATE_QUESTIONS,
        subject=subject,
        topic=topic,
        difficulty=difficulty,
        count=count,
        channel_id=channel_id,
    )
    if len(items) < MIN_QUESTIONS:
        req = db.get(LlmRequest, request_id)
        req.status = LlmRequestStatus.FAILED
        req.error = f"only {len(items)} valid questions after {MAX_ROUNDS} attempts"
        db.flush()
        raise GenerationFailedError(req.error)
    ids = [
        questions.create_question(
            db,
            author_id=user_id,
            subject=item.subject,
            topic=item.topic,
            difficulty=item.difficulty,
            question_type=QuestionType.MULTIPLE_CHOICE,
            text=item.text,
            options=list(item.options),
            correct_answer=item.correct_answer,
            explanation=item.explanation,
            channel_id=channel_id,
            source=QuestionSource.LLM,
        ).id
        for item in items
    ]
    req = db.get(LlmRequest, request_id)
    req.output = _output(req) | {"question_ids": ids, "edited": [], "difficulty": difficulty.value}
    db.flush()
    return get_generation(db, request_id)


def get_generation(db: Session, generation_id: int) -> Generation | None:
    req = db.get(LlmRequest, generation_id)
    if req is None or req.task is not LlmTask.GENERATE_QUESTIONS:
        return None
    out = _output(req)
    ids = list(out.get("question_ids") or [])
    if not ids:
        return None
    edited = set(out.get("edited") or [])
    found = {qid: questions.get_bank_question(db, qid) for qid in ids}
    items = tuple(
        GeneratedQuestion(
            question=item.question,
            review_status=_REVIEW[item.status],
            edited=qid in edited,
        )
        for qid, item in found.items()
        if item is not None
    )
    return Generation(
        id=req.id,
        user_id=req.user_id,
        subject=req.subject or "",
        topic=req.topic or "",
        difficulty=Difficulty(out.get("difficulty", items[0].question.difficulty.value)),
        channel_id=req.channel_id,
        questions=items,
    )


def list_generations(db: Session, user_id: int, limit: int = 20) -> list[Generation]:
    ids = db.scalars(
        select(LlmRequest.id)
        .where(
            LlmRequest.user_id == user_id,
            LlmRequest.task == LlmTask.GENERATE_QUESTIONS,
            LlmRequest.status == LlmRequestStatus.SUCCEEDED,
        )
        .order_by(LlmRequest.id.desc())
        .limit(limit)
    ).all()
    return [g for g in (get_generation(db, gid) for gid in ids) if g is not None]


def _member(generation: Generation, question_id: int) -> None:
    if question_id not in {q.question.id for q in generation.questions}:
        raise LookupError(f"question {question_id} is not in generation {generation.id}")


def edit(
    db: Session,
    generation: Generation,
    question_id: int,
    *,
    text: str,
    options: list[str],
    correct_answer: str,
    explanation: str,
) -> Generation:
    """Save a correction to a pending question (it still needs approving)."""
    _member(generation, question_id)
    questions.update_draft(
        db,
        question_id,
        text=text,
        options=options,
        correct_answer=correct_answer,
        explanation=explanation,
    )
    req = db.get(LlmRequest, generation.id)
    out = _output(req)
    out["edited"] = sorted(set(out.get("edited") or []) | {question_id})
    req.output = out
    db.flush()
    return get_generation(db, generation.id)


def approve(db: Session, generation: Generation, question_id: int) -> Generation:
    _member(generation, question_id)
    questions.publish_draft(db, question_id)
    return get_generation(db, generation.id)


def reject(db: Session, generation: Generation, question_id: int) -> Generation:
    _member(generation, question_id)
    questions.discard_draft(db, question_id)
    return get_generation(db, generation.id)


def approved_for_quiz(generation: Generation) -> list[int]:
    """The approved question ids, once the whole batch is reviewed (UT_14)."""
    if generation.pending_count:
        raise NotReviewedError(
            "Approve or reject every generated question before adding them to a quiz"
        )
    if not generation.approved_ids:
        raise NothingApprovedError("Approve at least one question to add to a quiz")
    return generation.approved_ids
