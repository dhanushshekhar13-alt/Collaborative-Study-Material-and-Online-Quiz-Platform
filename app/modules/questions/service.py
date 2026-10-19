"""Public interface of the question bank module.

``QuestionView`` is the read model other modules (quizzes, LLM, analytics) receive;
it carries everything needed to present and score a question.
"""

from collections.abc import Iterable
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.questions.models import (
    Difficulty,
    Question,
    QuestionSource,
    QuestionStatus,
    QuestionType,
)


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
