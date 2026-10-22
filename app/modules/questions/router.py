"""HTTP surface of the question bank, mounted at ``/api/v1/questions`` (S05.1, FR-05).

Questions live in the general bank (``channel_id`` null) or in one channel's bank.
Anyone signed in can browse the general bank; a channel's bank is open to whoever can
see the channel, and only its members (or admins) can contribute to it.
"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.modules.accounts.service import (
    Capability,
    UserSummary,
    capabilities_for,
    current_user,
    require_capability,
)
from app.modules.channels import service as channels
from app.modules.moderation.service import (
    ReportReason,
    ReportTarget,
    file_report,
    find_open_report,
)
from app.modules.questions import service
from app.modules.questions.models import (
    Difficulty,
    QuestionSource,
    QuestionStatus,
    QuestionType,
)

router = APIRouter(prefix="/questions", tags=["questions"])

# Wording fixed by the Test Plan (UT_10).
CORRECT_ANSWER_MESSAGE = "Please select the correct answer"
ANSWER_NOT_AN_OPTION_MESSAGE = "The correct answer must be one of the options"
OPTIONS_MESSAGE = "Enter at least two different options"
TRUE_FALSE_MESSAGE = "The correct answer must be True or False"
QUESTION_NOT_FOUND = "Question not found"
CHANNEL_NOT_FOUND = "Channel not found"
NOT_A_MEMBER = "Join the channel to add questions to its bank"
# S03.1: an Authorized Channel's bank is official content, the same rule as materials
# and quizzes.
AUTHORIZED_ONLY = "Only authorized users can add content to Authorized Channels"
REPORT_LOGGED = "Report submitted for moderation"


def _required(value: str | None, message: str) -> str:
    value = (value or "").strip()
    if not value:
        raise ValueError(message)
    return value


class QuestionCreate(BaseModel):
    text: str = Field(max_length=5000)
    subject: str = Field(max_length=100)
    topic: str = Field(max_length=100)
    difficulty: Difficulty
    question_type: QuestionType
    options: list[str] | None = Field(default=None, max_length=10)
    correct_answer: str | None = Field(default=None, max_length=5000)
    explanation: str | None = Field(default=None, max_length=5000)
    expected_concepts: list[str] | None = Field(default=None, max_length=20)
    channel_id: int | None = Field(
        default=None, description="Channel bank to add to; omit for the general bank"
    )

    @model_validator(mode="after")
    def complete_and_answerable(self) -> "QuestionCreate":
        """Every tag is required and the correct answer must be chosen (UT_09, UT_10)."""
        self.text = _required(self.text, "Enter the question text")
        self.subject = _required(self.subject, "Select a subject")
        self.topic = _required(self.topic, "Select a topic")
        self.explanation = (self.explanation or "").strip() or None
        answer = (self.correct_answer or "").strip() or None

        if self.question_type is QuestionType.MULTIPLE_CHOICE:
            options = [o.strip() for o in self.options or [] if o and o.strip()]
            if len(options) < 2 or len({o.lower() for o in options}) != len(options):
                raise ValueError(OPTIONS_MESSAGE)
            self.options = options
            if answer is None:
                raise ValueError(CORRECT_ANSWER_MESSAGE)
            match = [o for o in options if o.lower() == answer.lower()]
            if not match:
                raise ValueError(ANSWER_NOT_AN_OPTION_MESSAGE)
            answer = match[0]
        elif self.question_type is QuestionType.TRUE_FALSE:
            self.options = ["True", "False"]
            if answer is None:
                raise ValueError(CORRECT_ANSWER_MESSAGE)
            if answer.lower() not in {"true", "false"}:
                raise ValueError(TRUE_FALSE_MESSAGE)
            answer = answer.title()
        else:
            self.options = None
            concepts = [c.strip() for c in self.expected_concepts or [] if c and c.strip()]
            self.expected_concepts = concepts or None
            # A descriptive question is answerable with a model answer or the concepts
            # an answer must cover (FR-08); a short answer needs the answer itself.
            if answer is None and not (self.question_type is QuestionType.DESCRIPTIVE and concepts):
                raise ValueError(CORRECT_ANSWER_MESSAGE)
        self.correct_answer = answer
        return self


class RatingRequest(BaseModel):
    rating: int = Field(ge=1, le=5, description="1 to 5 stars")


class ReportRequest(BaseModel):
    reason: ReportReason = Field(description='"incorrect" is the "Incorrect answer" report')
    details: str | None = Field(default=None, max_length=2000)


class QuestionResponse(BaseModel):
    id: int
    text: str
    subject: str
    topic: str
    difficulty: Difficulty
    question_type: QuestionType
    options: list[str]
    correct_answer: str | None
    explanation: str | None
    expected_concepts: list[str]
    channel_id: int | None
    bank: str
    author_id: int
    source: QuestionSource
    status: QuestionStatus
    created_at: datetime | None
    rating_average: float | None
    rating_count: int
    my_rating: int | None


class ReportResponse(BaseModel):
    report_id: int
    question_id: int
    reason: ReportReason
    status: str
    message: str


def _response(item: service.BankQuestion) -> QuestionResponse:
    q = item.question
    return QuestionResponse(
        id=q.id,
        text=q.text,
        subject=q.subject,
        topic=q.topic,
        difficulty=q.difficulty,
        question_type=q.question_type,
        options=list(q.options),
        correct_answer=q.correct_answer,
        explanation=q.explanation,
        expected_concepts=list(q.expected_concepts),
        channel_id=item.channel_id,
        bank="channel" if item.channel_id is not None else "general",
        author_id=item.author_id,
        source=item.source,
        status=item.status,
        created_at=item.created_at,
        rating_average=item.rating_average,
        rating_count=item.rating_count,
        my_rating=item.my_rating,
    )


def _is_admin(user: UserSummary) -> bool:
    return Capability.CHANNEL_APPROVE in capabilities_for(user.role)


def _visible_channel(db: Session, channel_id: int, user: UserSummary) -> channels.ChannelSummary:
    """404 unless the caller may see the channel (its existence is not revealed)."""
    summary = channels.describe_channel(db, channel_id)
    if summary is None or not channels.is_visible_to(summary, user.id, _is_admin(user)):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=CHANNEL_NOT_FOUND)
    return summary


def _readable(db: Session, question_id: int, user: UserSummary) -> service.BankQuestion:
    """The question if the caller may read it, else 404.

    Published questions are readable wherever their bank is; drafts and removed
    questions only by their author and admins.
    """
    item = service.get_bank_question(db, question_id, user.id)
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=QUESTION_NOT_FOUND)
    if item.status is not QuestionStatus.PUBLISHED and not (
        item.author_id == user.id or _is_admin(user)
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=QUESTION_NOT_FOUND)
    if item.channel_id is not None:
        try:
            _visible_channel(db, item.channel_id, user)
        except HTTPException:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=QUESTION_NOT_FOUND
            ) from None
    return item


@router.post("", status_code=status.HTTP_201_CREATED, response_model=QuestionResponse)
def contribute_question(
    payload: QuestionCreate,
    user: UserSummary = Depends(require_capability(Capability.QUESTION_CONTRIBUTE)),
    db: Session = Depends(get_db),
) -> QuestionResponse:
    """Add a question with its subject, topic, difficulty and type tags (UT_09).

    Submission is refused with "Please select the correct answer" when no correct
    answer is given (UT_10). With ``channel_id`` it goes to that channel's bank, which
    needs channel membership; without it, to the general bank. An Authorized Channel's
    bank is official, so only its authorized users (and admins) add to it (S03.1).
    """
    if payload.channel_id is not None:
        channel = _visible_channel(db, payload.channel_id, user)
        if not channels.is_member(db, payload.channel_id, user.id) and not _is_admin(user):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=NOT_A_MEMBER)
        if channel.channel_type is channels.ChannelType.AUTHORIZED and (
            Capability.OFFICIAL_CONTENT_MANAGE not in capabilities_for(user.role)
        ):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=AUTHORIZED_ONLY)
    created = service.create_question(
        db,
        author_id=user.id,
        subject=payload.subject,
        topic=payload.topic,
        difficulty=payload.difficulty,
        question_type=payload.question_type,
        text=payload.text,
        options=payload.options,
        correct_answer=payload.correct_answer,
        explanation=payload.explanation,
        expected_concepts=payload.expected_concepts,
        channel_id=payload.channel_id,
    )
    db.commit()
    return _response(service.get_bank_question(db, created.id, user.id))


@router.get("", response_model=list[QuestionResponse])
def search_questions(
    q: str | None = Query(default=None, max_length=100, description="Keyword"),
    subject: str | None = Query(default=None, max_length=100),
    topic: str | None = Query(default=None, max_length=100),
    difficulty: Difficulty | None = None,
    question_type: QuestionType | None = Query(default=None, alias="type"),
    channel_id: int | None = Query(default=None, description="Channel bank; omit for general"),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    user: UserSummary = Depends(current_user),
    db: Session = Depends(get_db),
) -> list[QuestionResponse]:
    """Browse or search one bank (IT_09). The keyword matches question text, subject,
    topic or explanation, ignoring case; the other filters narrow the results."""
    if channel_id is not None:
        _visible_channel(db, channel_id, user)
    found = service.search_bank(
        db,
        keyword=q,
        subject=subject,
        topic=topic,
        difficulty=difficulty,
        question_type=question_type,
        channel_id=channel_id,
        viewer_id=user.id,
        limit=limit,
        offset=offset,
    )
    return [_response(item) for item in found]


@router.get("/{question_id}", response_model=QuestionResponse)
def read_question(
    question_id: int, user: UserSummary = Depends(current_user), db: Session = Depends(get_db)
) -> QuestionResponse:
    """One question with its average rating and the caller's own rating."""
    return _response(_readable(db, question_id, user))


@router.put("/{question_id}/rating", response_model=QuestionResponse)
def rate_question(
    question_id: int,
    payload: RatingRequest,
    user: UserSummary = Depends(current_user),
    db: Session = Depends(get_db),
) -> QuestionResponse:
    """Save the caller's 1-5 star rating (IT_09); rating again replaces it."""
    item = _readable(db, question_id, user)
    if item.status is not QuestionStatus.PUBLISHED:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=QUESTION_NOT_FOUND)
    rated = service.rate_question(db, question_id, user.id, payload.rating)
    db.commit()
    return _response(rated)


@router.post(
    "/{question_id}/reports", status_code=status.HTTP_201_CREATED, response_model=ReportResponse
)
def report_question(
    question_id: int,
    payload: ReportRequest,
    user: UserSummary = Depends(require_capability(Capability.REPORT_SUBMIT)),
    db: Session = Depends(get_db),
) -> ReportResponse:
    """Report a question (for example "Incorrect answer") to moderation (IT_09).

    The report is logged as open in the moderation queue, scoped to the question's
    channel so channel moderators see it. Reporting the same question again while the
    first report is open returns that report instead of filing a duplicate.
    """
    item = _readable(db, question_id, user)
    report_id = find_open_report(
        db, reporter_id=user.id, target_type=ReportTarget.QUESTION, target_id=question_id
    )
    if report_id is None:
        report_id = file_report(
            db,
            reporter_id=user.id,
            target_type=ReportTarget.QUESTION,
            target_id=question_id,
            reason=payload.reason,
            details=(payload.details or "").strip() or None,
            channel_id=item.channel_id,
        )
        db.commit()
    return ReportResponse(
        report_id=report_id,
        question_id=question_id,
        reason=payload.reason,
        status="open",
        message=REPORT_LOGGED,
    )
