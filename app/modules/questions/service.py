"""Public interface of the question bank module.

``QuestionView`` is the read model other modules (quizzes, LLM, analytics) receive;
it carries everything needed to present and score a question.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.modules.questions.models import (
    Difficulty,
    Question,
    QuestionRating,
    QuestionSource,
    QuestionStatus,
    QuestionType,
)
from app.modules.reputation import service as reputation


@dataclass(frozen=True)
class QuestionView:
    id: int
    subject: str
    topic: str
    difficulty: Difficulty
    question_type: QuestionType
    text: str
    options: tuple[str, ...]
    correct_answer: str | None
    explanation: str | None
    expected_concepts: tuple[str, ...]

    @property
    def is_objective(self) -> bool:
        return self.question_type is not QuestionType.DESCRIPTIVE


def _view(q: Question) -> QuestionView:
    return QuestionView(
        id=q.id,
        subject=q.subject,
        topic=q.topic,
        difficulty=q.difficulty,
        question_type=q.question_type,
        text=q.text,
        options=tuple(q.options or ()),
        correct_answer=q.correct_answer,
        explanation=q.explanation,
        expected_concepts=tuple(q.expected_concepts or ()),
    )


def create_question(
    db: Session,
    *,
    author_id: int,
    subject: str,
    topic: str,
    difficulty: Difficulty,
    question_type: QuestionType,
    text: str,
    options: list[str] | None = None,
    correct_answer: str | None = None,
    explanation: str | None = None,
    expected_concepts: list[str] | None = None,
    channel_id: int | None = None,
    source: QuestionSource = QuestionSource.PEER,
) -> QuestionView:
    """Add a question. LLM-generated questions start as drafts for review (FR-07)."""
    status = QuestionStatus.DRAFT if source is QuestionSource.LLM else QuestionStatus.PUBLISHED
    q = Question(
        author_id=author_id,
        subject=subject,
        topic=topic,
        difficulty=difficulty,
        question_type=question_type,
        text=text,
        options=options,
        correct_answer=correct_answer,
        explanation=explanation,
        expected_concepts=expected_concepts,
        channel_id=channel_id,
        source=source,
        status=status,
    )
    db.add(q)
    db.flush()
    return _view(q)


def get_questions(db: Session, ids: Iterable[int]) -> dict[int, QuestionView]:
    """Fetch questions by id, keyed by id (missing ids are simply absent)."""
    ids = list(ids)
    if not ids:
        return {}
    rows = db.scalars(select(Question).where(Question.id.in_(ids)))
    return {q.id: _view(q) for q in rows}


def find_questions(
    db: Session,
    *,
    subject: str | None = None,
    topic: str | None = None,
    difficulty: Difficulty | None = None,
    channel_id: int | None = None,
    limit: int = 50,
) -> list[QuestionView]:
    """Published questions matching every filter given."""
    stmt = select(Question).where(Question.status == QuestionStatus.PUBLISHED)
    if subject:
        stmt = stmt.where(Question.subject == subject)
    if topic:
        stmt = stmt.where(Question.topic == topic)
    if difficulty:
        stmt = stmt.where(Question.difficulty == difficulty)
    if channel_id is not None:
        stmt = stmt.where(Question.channel_id == channel_id)
    return [_view(q) for q in db.scalars(stmt.order_by(Question.id).limit(limit))]


@dataclass(frozen=True)
class QuestionContribution:
    """One question the user contributed, for their profile's history (S01.2)."""

    id: int
    title: str
    created_at: datetime


def count_authored_by(db: Session, user_id: int) -> int:
    """How many published questions the user wrote; drafts and removed ones don't count."""
    return (
        db.scalar(
            select(func.count(Question.id)).where(
                Question.author_id == user_id, Question.status == QuestionStatus.PUBLISHED
            )
        )
        or 0
    )


def recent_authored_by(db: Session, user_id: int, limit: int = 10) -> list[QuestionContribution]:
    rows = db.scalars(
        select(Question)
        .where(Question.author_id == user_id, Question.status == QuestionStatus.PUBLISHED)
        .order_by(Question.created_at.desc(), Question.id.desc())
        .limit(limit)
    )
    return [QuestionContribution(r.id, _headline(r.text), r.created_at) for r in rows]


def _headline(text: str, limit: int = 80) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "\u2026"


# --------------------------------------------------------------------------- #
# S05.1 - question bank contribution and discovery
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class BankQuestion:
    """A question as the bank presents it: content, scope, provenance and ratings."""

    question: QuestionView
    channel_id: int | None
    author_id: int
    source: QuestionSource
    status: QuestionStatus
    created_at: datetime | None
    rating_average: float | None
    rating_count: int
    my_rating: int | None = None


def _ratings(db: Session, ids: list[int]) -> dict[int, tuple[float, int]]:
    if not ids:
        return {}
    rows = db.execute(
        select(
            QuestionRating.question_id,
            func.avg(QuestionRating.rating),
            func.count(QuestionRating.id),
        )
        .where(QuestionRating.question_id.in_(ids))
        .group_by(QuestionRating.question_id)
    )
    return {qid: (round(float(avg), 2), int(count)) for qid, avg, count in rows}


def _my_ratings(db: Session, ids: list[int], user_id: int | None) -> dict[int, int]:
    if not ids or user_id is None:
        return {}
    rows = db.execute(
        select(QuestionRating.question_id, QuestionRating.rating).where(
            QuestionRating.question_id.in_(ids), QuestionRating.user_id == user_id
        )
    )
    return dict(rows.tuples().all())


def _bank(db: Session, rows: list[Question], viewer_id: int | None) -> list[BankQuestion]:
    ids = [q.id for q in rows]
    ratings, mine = _ratings(db, ids), _my_ratings(db, ids, viewer_id)
    return [
        BankQuestion(
            question=_view(q),
            channel_id=q.channel_id,
            author_id=q.author_id,
            source=q.source,
            status=q.status,
            created_at=q.created_at,
            rating_average=ratings.get(q.id, (None, 0))[0],
            rating_count=ratings.get(q.id, (None, 0))[1],
            my_rating=mine.get(q.id),
        )
        for q in rows
    ]


def get_bank_question(
    db: Session, question_id: int, viewer_id: int | None = None
) -> BankQuestion | None:
    """One question with its rating summary, whatever its status (callers check access)."""
    q = db.get(Question, question_id)
    return _bank(db, [q], viewer_id)[0] if q else None


def search_bank(
    db: Session,
    *,
    keyword: str | None = None,
    subject: str | None = None,
    topic: str | None = None,
    difficulty: Difficulty | None = None,
    question_type: QuestionType | None = None,
    channel_id: int | None = None,
    viewer_id: int | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[BankQuestion]:
    """Published questions in one bank, newest first.

    ``channel_id=None`` searches the general bank; otherwise that channel's bank. The
    keyword matches the question text, subject, topic or explanation, ignoring case;
    subject and topic filters match whole values, ignoring case.
    """
    stmt = select(Question).where(Question.status == QuestionStatus.PUBLISHED)
    if channel_id is None:
        stmt = stmt.where(Question.channel_id.is_(None))
    else:
        stmt = stmt.where(Question.channel_id == channel_id)
    if keyword and keyword.strip():
        needle = f"%{keyword.strip().lower()}%"
        stmt = stmt.where(
            or_(
                func.lower(Question.text).like(needle),
                func.lower(Question.subject).like(needle),
                func.lower(Question.topic).like(needle),
                func.lower(Question.explanation).like(needle),
            )
        )
    if subject and subject.strip():
        stmt = stmt.where(func.lower(Question.subject) == subject.strip().lower())
    if topic and topic.strip():
        stmt = stmt.where(func.lower(Question.topic) == topic.strip().lower())
    if difficulty is not None:
        stmt = stmt.where(Question.difficulty == difficulty)
    if question_type is not None:
        stmt = stmt.where(Question.question_type == question_type)
    stmt = stmt.order_by(Question.created_at.desc(), Question.id.desc())
    rows = list(db.scalars(stmt.offset(offset).limit(limit)))
    return _bank(db, rows, viewer_id)


QUESTION_RATING_SOURCE = "question_rating"


def rate_question(db: Session, question_id: int, user_id: int, rating: int) -> BankQuestion:
    """Save the user's 1-5 rating; rating again replaces their earlier rating."""
    if not 1 <= rating <= 5:
        raise ValueError("rating must be between 1 and 5")
    question = db.get(Question, question_id)
    if question is None:
        raise LookupError(f"question {question_id} not found")
    existing = db.scalar(
        select(QuestionRating).where(
            QuestionRating.question_id == question_id, QuestionRating.user_id == user_id
        )
    )
    if existing is None:
        existing = QuestionRating(question_id=question_id, user_id=user_id, rating=rating)
        db.add(existing)
    else:
        existing.rating = rating
    db.flush()
    reputation.record_peer_rating(
        db,
        contributor_id=question.author_id,
        rater_id=user_id,
        rating=rating,
        source_type=QUESTION_RATING_SOURCE,
        source_id=existing.id,
    )
    return get_bank_question(db, question_id, user_id)


# --------------------------------------------------------------------------- #
# S05.2 - choosing questions for a quiz
# --------------------------------------------------------------------------- #


def quiz_candidates(
    db: Session,
    *,
    subject: str | None = None,
    topic: str | None = None,
    difficulty: Difficulty | None = None,
    channel_id: int | None = None,
) -> list[BankQuestion]:
    """Published questions a quiz may use, oldest first.

    The general bank is always included; with ``channel_id`` that channel's bank is
    too. Subject and topic match whole values ignoring case. A question is usable the
    moment it is contributed: there is no cache or index to refresh (ST_10).
    """
    stmt = select(Question).where(Question.status == QuestionStatus.PUBLISHED)
    if channel_id is None:
        stmt = stmt.where(Question.channel_id.is_(None))
    else:
        stmt = stmt.where(or_(Question.channel_id.is_(None), Question.channel_id == channel_id))
    if subject and subject.strip():
        stmt = stmt.where(func.lower(Question.subject) == subject.strip().lower())
    if topic and topic.strip():
        stmt = stmt.where(func.lower(Question.topic) == topic.strip().lower())
    if difficulty is not None:
        stmt = stmt.where(Question.difficulty == difficulty)
    rows = list(db.scalars(stmt.order_by(Question.id)))
    return _bank(db, rows, None)


# --------------------------------------------------------------------------- #
# S07.1 - reviewing LLM-generated drafts
# --------------------------------------------------------------------------- #


class NotADraftError(Exception):
    """The question has already been reviewed (approved or rejected)."""


def _draft(db: Session, question_id: int) -> Question:
    q = db.get(Question, question_id)
    if q is None:
        raise LookupError(f"question {question_id} not found")
    if q.status is not QuestionStatus.DRAFT:
        raise NotADraftError("This question has already been reviewed")
    return q


def update_draft(
    db: Session,
    question_id: int,
    *,
    text: str,
    options: list[str],
    correct_answer: str,
    explanation: str,
) -> QuestionView:
    """Save a reviewer's correction to a draft; it stays a draft until approved."""
    q = _draft(db, question_id)
    q.text, q.options, q.correct_answer, q.explanation = text, options, correct_answer, explanation
    db.flush()
    return _view(q)


def publish_draft(db: Session, question_id: int) -> None:
    """Approve a draft: it joins its bank and becomes usable in quizzes."""
    _draft(db, question_id).status = QuestionStatus.PUBLISHED
    db.flush()


def discard_draft(db: Session, question_id: int) -> None:
    """Reject a draft. It is kept only as a removed record and never reaches a bank,
    a search result or a quiz, all of which use published questions only."""
    _draft(db, question_id).status = QuestionStatus.REMOVED
    db.flush()
