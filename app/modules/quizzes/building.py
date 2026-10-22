"""Quiz assembly and publishing (S05.2, FR-05).

A quiz is built from bank questions matching a subject or topic, a difficulty and a
count, with a duration for its timer. It starts as a draft only its author can see;
publishing puts it in a channel's Quizzes tab (or the general list) for everyone who
can open that channel. No HTTP here: ``router.py`` adds access rules and wording.
"""

import random
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.questions import service as questions
from app.modules.questions.service import Difficulty, QuestionStatus
from app.modules.quizzes import service
from app.modules.quizzes.models import Quiz, QuizQuestion, QuizStatus


class NotEnoughQuestionsError(Exception):
    """The bank holds fewer matching questions than the quiz asks for."""

    def __init__(self, available: int, requested: int) -> None:
        self.available, self.requested = available, requested
        noun = "question is" if available == 1 else "questions are"
        super().__init__(
            f"Only {available} matching {noun} available ({requested} requested). "
            "Broaden the criteria or ask for fewer questions."
        )


class UnusableQuestionError(Exception):
    """A chosen question is missing, unpublished, or from a bank the quiz cannot use."""


class AlreadyPublishedError(Exception):
    pass


@dataclass(frozen=True)
class QuizDetails:
    id: int
    title: str
    subject: str
    topic: str
    difficulty: str
    duration_minutes: int
    channel_id: int | None
    creator_id: int
    status: QuizStatus
    created_at: datetime | None
    questions: tuple[questions.BankQuestion, ...]

    @property
    def question_count(self) -> int:
        return len(self.questions)


def select_questions(
    db: Session,
    *,
    subject: str | None,
    topic: str | None,
    difficulty: Difficulty,
    count: int,
    channel_id: int | None = None,
    rng: random.Random | None = None,
) -> list[questions.BankQuestion]:
    """Pick ``count`` matching questions at random from the general bank and, with
    ``channel_id``, that channel's bank. Raises ``NotEnoughQuestionsError``."""
    pool = questions.quiz_candidates(
        db, subject=subject, topic=topic, difficulty=difficulty, channel_id=channel_id
    )
    if len(pool) < count:
        raise NotEnoughQuestionsError(len(pool), count)
    return (rng or random.Random()).sample(pool, count)


def _usable(
    db: Session, question_ids: list[int], channel_id: int | None
) -> list[questions.BankQuestion]:
    """The chosen questions in order, checked to be published and in a usable bank."""
    chosen = []
    for qid in question_ids:
        item = questions.get_bank_question(db, qid)
        if item is None or item.status is not QuestionStatus.PUBLISHED:
            raise UnusableQuestionError(f"Question {qid} is not in the question bank")
        if item.channel_id is not None and item.channel_id != channel_id:
            raise UnusableQuestionError(
                f"Question {qid} belongs to another channel's bank and cannot be used here"
            )
        chosen.append(item)
    return chosen


def create_draft(
    db: Session,
    *,
    creator_id: int,
    title: str,
    subject: str | None,
    topic: str | None,
    difficulty: Difficulty,
    duration_minutes: int,
    question_ids: list[int],
    channel_id: int | None = None,
) -> QuizDetails:
    """Save an unpublished quiz with the questions in the given order.

    ``channel_id`` is the channel whose bank the questions may come from; the quiz
    stays private to its author until ``publish``.
    """
    chosen = _usable(db, question_ids, channel_id)
    quiz_id = service.create_quiz(
        db,
        creator_id=creator_id,
        title=title,
        subject=(subject or chosen[0].question.subject).strip(),
        topic=(topic or chosen[0].question.topic).strip(),
        difficulty=difficulty.value,
        duration_minutes=duration_minutes,
        question_ids=question_ids,
        channel_id=channel_id,
        publish=False,
    )
    return get_quiz(db, quiz_id)


def get_quiz(db: Session, quiz_id: int) -> QuizDetails | None:
    quiz = db.get(Quiz, quiz_id)
    if quiz is None:
        return None
    links = db.scalars(
        select(QuizQuestion).where(QuizQuestion.quiz_id == quiz.id).order_by(QuizQuestion.position)
    ).all()
    items = [questions.get_bank_question(db, link.question_id) for link in links]
    return QuizDetails(
        id=quiz.id,
        title=quiz.title,
        subject=quiz.subject,
        topic=quiz.topic,
        difficulty=quiz.difficulty,
        duration_minutes=quiz.duration_minutes,
        channel_id=quiz.channel_id,
        creator_id=quiz.creator_id,
        status=quiz.status,
        created_at=quiz.created_at,
        questions=tuple(item for item in items if item is not None),
    )


def list_published(db: Session, channel_id: int | None) -> list[QuizDetails]:
    """Published quizzes in one channel's Quizzes tab (``None``: the general list),
    newest first."""
    stmt = select(Quiz.id).where(Quiz.status == QuizStatus.PUBLISHED)
    if channel_id is None:
        stmt = stmt.where(Quiz.channel_id.is_(None))
    else:
        stmt = stmt.where(Quiz.channel_id == channel_id)
    ids = db.scalars(stmt.order_by(Quiz.created_at.desc(), Quiz.id.desc())).all()
    return [get_quiz(db, qid) for qid in ids]


def list_by_creator(db: Session, creator_id: int) -> list[QuizDetails]:
    """The author's own drafts and published quizzes, newest first."""
    ids = db.scalars(
        select(Quiz.id)
        .where(
            Quiz.creator_id == creator_id,
            Quiz.status != QuizStatus.REMOVED,
            Quiz.is_adaptive.is_(False),  # adaptive runs (S07.2) are not authored quizzes
        )
        .order_by(Quiz.created_at.desc(), Quiz.id.desc())
    ).all()
    return [get_quiz(db, qid) for qid in ids]


def publish(db: Session, quiz_id: int, channel_id: int | None) -> QuizDetails:
    """Publish a draft to ``channel_id`` (or the general list).

    Every question must still be published and come from the general bank or the
    target channel's bank, so publishing never leaks one channel's questions into
    another channel.
    """
    quiz = db.get(Quiz, quiz_id)
    if quiz is None:
        raise LookupError(f"quiz {quiz_id} not found")
    if quiz.is_adaptive:
        raise UnusableQuestionError("An adaptive quiz is personal and cannot be published")
    if quiz.status is not QuizStatus.DRAFT:
        raise AlreadyPublishedError("This quiz is already published")
    question_ids = db.scalars(
        select(QuizQuestion.question_id)
        .where(QuizQuestion.quiz_id == quiz.id)
        .order_by(QuizQuestion.position)
    ).all()
    _usable(db, list(question_ids), channel_id)
    quiz.channel_id = channel_id
    quiz.status = QuizStatus.PUBLISHED
    db.flush()
    return get_quiz(db, quiz.id)
