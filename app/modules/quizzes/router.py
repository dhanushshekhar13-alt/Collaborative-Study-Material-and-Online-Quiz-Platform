"""HTTP surface for building and publishing quizzes (S05.2, FR-05), under ``/api/v1``.

    POST /quizzes/preview          pick matching bank questions for review (no save)
    POST /quizzes                  save a draft quiz (private to its author)
    GET  /quizzes                  a channel's Quizzes tab, the general list, or mine
    GET  /quizzes/{quiz_id}        one quiz with its questions
    POST /quizzes/{quiz_id}/publish publish the draft to a channel or the general list

Taking a quiz lives in ``attempts_router`` (S06.1). A draft answers 404 to everyone
but its author; a channel's quizzes are for its members (and admins).
"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.modules.accounts import service as accounts
from app.modules.accounts.service import (
    Capability,
    UserSummary,
    capabilities_for,
    current_user,
    require_capability,
)
from app.modules.channels import service as channels
from app.modules.channels.service import ChannelType
from app.modules.questions import service as questions
from app.modules.questions.service import Difficulty, QuestionType
from app.modules.quizzes import building
from app.modules.quizzes.models import QuizStatus

router = APIRouter(prefix="/quizzes", tags=["quizzes"])

QUIZ_NOT_FOUND = "Quiz not found"
CHANNEL_NOT_FOUND = "Channel not found"
TOPIC_REQUIRED = "Select a topic"
BANK_MEMBERS_ONLY = "Join the channel to use its question bank"
PUBLISH_MEMBERS_ONLY = "Join the channel to publish quizzes to it"
QUIZZES_MEMBERS_ONLY = "Join the channel to see its quizzes"
AUTHORIZED_ONLY = "Only authorized users can add content to Authorized Channels"


def _count_message(count: int) -> str:
    return f"Choose exactly {count} different questions"


class QuizCriteria(BaseModel):
    subject: str | None = Field(default=None, max_length=100)
    topic: str | None = Field(default=None, max_length=100)
    difficulty: Difficulty
    question_count: int = Field(ge=1, le=100, description="Number of questions")
    duration_minutes: int = Field(ge=1, le=300, description="Timer length in minutes")
    channel_id: int | None = Field(
        default=None,
        description="Also draw from this channel's bank (needs membership); "
        "the general bank is always included",
    )

    @model_validator(mode="after")
    def subject_or_topic(self) -> "QuizCriteria":
        self.subject = (self.subject or "").strip() or None
        self.topic = (self.topic or "").strip() or None
        if self.subject is None and self.topic is None:
            raise ValueError(TOPIC_REQUIRED)
        return self


class QuizCreate(QuizCriteria):
    title: str | None = Field(default=None, max_length=200)
    question_ids: list[int] | None = Field(
        default=None,
        description="The reviewed question set, in order. Omit to let the system pick "
        "question_count matching questions.",
    )

    @model_validator(mode="after")
    def chosen_set_matches_count(self) -> "QuizCreate":
        self.title = (self.title or "").strip() or None
        if self.question_ids is not None and (
            len(self.question_ids) != self.question_count
            or len(set(self.question_ids)) != len(self.question_ids)
        ):
            raise ValueError(_count_message(self.question_count))
        return self


class PublishRequest(BaseModel):
    channel_id: int | None = Field(
        default=None, description="Channel whose Quizzes tab shows it; omit for general"
    )


class QuizQuestionResponse(BaseModel):
    id: int
    position: int
    text: str
    subject: str
    topic: str
    difficulty: Difficulty
    question_type: QuestionType
    options: list[str]
    channel_id: int | None
    correct_answer: str | None = Field(description="Shown to the quiz author only")
    explanation: str | None = Field(description="Shown to the quiz author only")


class QuizResponse(BaseModel):
    id: int
    title: str
    subject: str
    topic: str
    difficulty: str
    duration_minutes: int
    question_count: int
    channel_id: int | None
    creator_id: int
    # Who built it, so channel contributions are attributed (S03.2).
    creator_name: str | None = None
    status: QuizStatus
    created_at: datetime | None
    questions: list[QuizQuestionResponse]


class QuizSummaryResponse(BaseModel):
    id: int
    title: str
    subject: str
    topic: str
    difficulty: str
    duration_minutes: int
    question_count: int
    channel_id: int | None
    creator_id: int
    # Who built it, so channel contributions are attributed (S03.2).
    creator_name: str | None = None
    status: QuizStatus
    created_at: datetime | None


class PreviewResponse(BaseModel):
    subject: str | None
    topic: str | None
    difficulty: Difficulty
    question_count: int
    duration_minutes: int
    channel_id: int | None
    questions: list[QuizQuestionResponse]


class NotEnoughQuestionsResponse(BaseModel):
    detail: str
    available: int
    requested: int


def _question(
    item: questions.BankQuestion, position: int, with_answers: bool
) -> QuizQuestionResponse:
    q = item.question
    return QuizQuestionResponse(
        id=q.id,
        position=position,
        text=q.text,
        subject=q.subject,
        topic=q.topic,
        difficulty=q.difficulty,
        question_type=q.question_type,
        options=list(q.options),
        channel_id=item.channel_id,
        correct_answer=q.correct_answer if with_answers else None,
        explanation=q.explanation if with_answers else None,
    )


def _creator_name(db: Session, user_id: int, names: dict[int, str | None] | None) -> str | None:
    if names is not None and user_id in names:
        return names[user_id]
    creator = accounts.get_user(db, user_id)
    name = creator.display_name if creator else None
    if names is not None:
        names[user_id] = name
    return name


def _summary(
    quiz: building.QuizDetails, db: Session, names: dict[int, str | None] | None = None
) -> QuizSummaryResponse:
    return QuizSummaryResponse(
        id=quiz.id,
        title=quiz.title,
        subject=quiz.subject,
        topic=quiz.topic,
        difficulty=quiz.difficulty,
        duration_minutes=quiz.duration_minutes,
        question_count=quiz.question_count,
        channel_id=quiz.channel_id,
        creator_id=quiz.creator_id,
        creator_name=_creator_name(db, quiz.creator_id, names),
        status=quiz.status,
        created_at=quiz.created_at,
    )


def _response(quiz: building.QuizDetails, viewer: UserSummary, db: Session) -> QuizResponse:
    with_answers = quiz.creator_id == viewer.id
    return QuizResponse(
        **_summary(quiz, db).model_dump(),
        questions=[
            _question(item, position, with_answers)
            for position, item in enumerate(quiz.questions, start=1)
        ],
    )


def _not_enough(exc: building.NotEnoughQuestionsError) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_409_CONFLICT,
        content={"detail": str(exc), "available": exc.available, "requested": exc.requested},
    )


def _can(user: UserSummary, capability: Capability) -> bool:
    return capability in capabilities_for(user.role)


def _is_admin(user: UserSummary) -> bool:
    return _can(user, Capability.CHANNEL_APPROVE)


def _member_channel(
    db: Session, channel_id: int, user: UserSummary, refusal: str
) -> channels.ChannelSummary:
    """The channel if the caller is a member (or admin): 404 if they cannot see it,
    403 with ``refusal`` if they can see it but have not joined."""
    summary = channels.describe_channel(db, channel_id)
    if summary is None or not channels.is_visible_to(summary, user.id, _is_admin(user)):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=CHANNEL_NOT_FOUND)
    if not _is_admin(user) and not channels.is_member(db, channel_id, user.id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=refusal)
    return summary


def _readable(db: Session, quiz_id: int, user: UserSummary) -> building.QuizDetails:
    """The quiz if the caller may open it, else 404 (403 for a non-member of a channel
    they can see). Drafts are private to their author."""
    quiz = building.get_quiz(db, quiz_id)
    if quiz is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=QUIZ_NOT_FOUND)
    if quiz.creator_id == user.id and quiz.status is not QuizStatus.REMOVED:
        return quiz
    if quiz.status is not QuizStatus.PUBLISHED:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=QUIZ_NOT_FOUND)
    if quiz.channel_id is not None:
        try:
            _member_channel(db, quiz.channel_id, user, QUIZZES_MEMBERS_ONLY)
        except HTTPException as exc:
            if exc.status_code == status.HTTP_404_NOT_FOUND:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND, detail=QUIZ_NOT_FOUND
                ) from None
            raise
    return quiz


@router.post(
    "/preview",
    response_model=PreviewResponse,
    responses={409: {"model": NotEnoughQuestionsResponse}},
)
def preview_quiz(
    criteria: QuizCriteria,
    user: UserSummary = Depends(require_capability(Capability.QUIZ_PUBLISH)),
    db: Session = Depends(get_db),
):
    """Pick ``question_count`` matching bank questions for the author to review
    before saving (use case Create Quiz, step 3). Nothing is saved.

    Too few matching questions answers 409 with how many are available, so the client
    can offer to broaden the criteria.
    """
    if criteria.channel_id is not None:
        _member_channel(db, criteria.channel_id, user, BANK_MEMBERS_ONLY)
    try:
        chosen = building.select_questions(
            db,
            subject=criteria.subject,
            topic=criteria.topic,
            difficulty=criteria.difficulty,
            count=criteria.question_count,
            channel_id=criteria.channel_id,
        )
    except building.NotEnoughQuestionsError as exc:
        return _not_enough(exc)
    return PreviewResponse(
        **criteria.model_dump(),
        questions=[_question(item, i, True) for i, item in enumerate(chosen, start=1)],
    )


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=QuizResponse,
    responses={409: {"model": NotEnoughQuestionsResponse}},
)
def create_quiz(
    payload: QuizCreate,
    user: UserSummary = Depends(require_capability(Capability.QUIZ_PUBLISH)),
    db: Session = Depends(get_db),
):
    """Save a quiz with the requested count, difficulty and timer (IT_10) as a draft
    only its author can see.

    With ``question_ids`` the reviewed set is saved as given (any published question
    from the general bank or the ``channel_id`` bank, including one contributed a
    moment ago, ST_10); without, the system picks matching questions. Publish it
    with ``POST /quizzes/{id}/publish``.
    """
    if payload.channel_id is not None:
        _member_channel(db, payload.channel_id, user, BANK_MEMBERS_ONLY)
    question_ids = payload.question_ids
    if question_ids is None:
        try:
            chosen = building.select_questions(
                db,
                subject=payload.subject,
                topic=payload.topic,
                difficulty=payload.difficulty,
                count=payload.question_count,
                channel_id=payload.channel_id,
            )
        except building.NotEnoughQuestionsError as exc:
            return _not_enough(exc)
        question_ids = [item.question.id for item in chosen]
    label = payload.topic or payload.subject
    title = payload.title or f"{label} quiz ({payload.difficulty.value.title()})"
    try:
        quiz = building.create_draft(
            db,
            creator_id=user.id,
            title=title,
            subject=payload.subject,
            topic=payload.topic,
            difficulty=payload.difficulty,
            duration_minutes=payload.duration_minutes,
            question_ids=question_ids,
            channel_id=payload.channel_id,
        )
    except building.UnusableQuestionError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from None
    db.commit()
    return _response(quiz, user, db)


@router.get("", response_model=list[QuizSummaryResponse])
def list_quizzes(
    channel_id: int | None = Query(default=None, description="The channel's Quizzes tab"),
    mine: bool = Query(default=False, description="The caller's own quizzes, drafts too"),
    user: UserSummary = Depends(current_user),
    db: Session = Depends(get_db),
) -> list[QuizSummaryResponse]:
    """Published quizzes in a channel's Quizzes tab (members only), the general list
    (no ``channel_id``), or with ``mine=true`` the caller's own drafts and quizzes."""
    names: dict[int, str | None] = {}
    if mine:
        found = building.list_by_creator(db, user.id)
        if channel_id is not None:
            found = [q for q in found if q.channel_id == channel_id]
        return [_summary(q, db, names) for q in found]
    if channel_id is not None:
        _member_channel(db, channel_id, user, QUIZZES_MEMBERS_ONLY)
    return [_summary(q, db, names) for q in building.list_published(db, channel_id)]


@router.get("/{quiz_id}", response_model=QuizResponse)
def read_quiz(
    quiz_id: int, user: UserSummary = Depends(current_user), db: Session = Depends(get_db)
) -> QuizResponse:
    """One quiz with its questions. The answer key is shown to its author only."""
    return _response(_readable(db, quiz_id, user), user, db)


@router.post("/{quiz_id}/publish", response_model=QuizResponse)
def publish_quiz(
    quiz_id: int,
    payload: PublishRequest,
    user: UserSummary = Depends(require_capability(Capability.QUIZ_PUBLISH)),
    db: Session = Depends(get_db),
) -> QuizResponse:
    """Publish the caller's draft to a channel's Quizzes tab, visible to all its
    members (ST_09), or to the general list. Only the author can publish; to anyone
    else the draft does not exist."""
    quiz = building.get_quiz(db, quiz_id)
    if quiz is None or quiz.creator_id != user.id or quiz.status is QuizStatus.REMOVED:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=QUIZ_NOT_FOUND)
    if payload.channel_id is not None:
        channel = _member_channel(db, payload.channel_id, user, PUBLISH_MEMBERS_ONLY)
        if channel.channel_type is ChannelType.AUTHORIZED and not _can(
            user, Capability.OFFICIAL_CONTENT_MANAGE
        ):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=AUTHORIZED_ONLY)
    try:
        published = building.publish(db, quiz_id, payload.channel_id)
    except building.AlreadyPublishedError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from None
    except building.UnusableQuestionError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from None
    db.commit()
    return _response(published, user, db)
