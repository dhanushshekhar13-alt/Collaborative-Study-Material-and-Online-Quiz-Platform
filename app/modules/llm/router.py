"""HTTP surface of the AI features, mounted under ``/api/v1/ai``.

S07.1 (FR-07) - question generation with mandatory review:

    POST /ai/generations                               generate 5-10 draft questions
    GET  /ai/generations                               my recent batches
    GET  /ai/generations/{id}                          the review screen for one batch
    PUT  /ai/generations/{id}/questions/{qid}          save a correction to a pending question
    POST /ai/generations/{id}/questions/{qid}/approve  approve: it joins the bank
    POST /ai/generations/{id}/questions/{qid}/reject   reject: discarded for good
    POST /ai/generations/{id}/quiz                     Add to Quiz (only when fully reviewed)

A batch is private to the user who generated it. Provider failures and timeouts come
back as 502/504 with a message the UI can show as is; with ``LLM_PROVIDER=none`` the
endpoints answer 503.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.orm import Session

from app.core import config
from app.core.database import get_db
from app.modules.accounts.service import (
    Capability,
    UserSummary,
    capabilities_for,
    current_user,
    require_capability,
)
from app.modules.channels import service as channels
from app.modules.llm import generation
from app.modules.llm.provider import LlmProvider, provider_from_settings
from app.modules.questions.service import Difficulty, NotADraftError, QuestionType
from app.modules.quizzes import service as quizzes

router = APIRouter(prefix="/ai", tags=["ai"])

# User-facing wording (S07.1: a failure must surface a clear error, not a blank screen).
NOT_CONFIGURED = "AI question generation is not available on this server right now."
PROVIDER_FAILED = (
    "The AI service could not generate questions right now. Please try again in a moment."
)
PROVIDER_TIMEOUT = "The AI service took too long to respond. Please try again."
GENERATION_NOT_FOUND = "Generated question set not found"
QUESTION_NOT_FOUND = "Question not found in this set"
ALREADY_REVIEWED = "This question has already been reviewed"
CHANNEL_NOT_FOUND = "Channel not found"
NOT_A_MEMBER = "Join the channel to add questions to its bank"
OPTIONS_MESSAGE = "Enter at least two different options"
ANSWER_NOT_AN_OPTION = "The correct answer must be one of the options"


def get_llm_provider() -> LlmProvider | None:
    """The configured provider; tests override this dependency with a fake."""
    return provider_from_settings(config.settings)


def _provider(provider: LlmProvider | None = Depends(get_llm_provider)) -> LlmProvider:
    if provider is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=NOT_CONFIGURED)
    return provider


def _required(value: str | None, message: str) -> str:
    value = " ".join((value or "").split())
    if not value:
        raise ValueError(message)
    return value


class GenerateRequest(BaseModel):
    subject: str = Field(max_length=100)
    topic: str = Field(max_length=100)
    difficulty: Difficulty
    count: int = Field(
        default=generation.MIN_QUESTIONS,
        ge=generation.MIN_QUESTIONS,
        le=generation.MAX_QUESTIONS,
        description="How many questions to generate (5-10)",
    )
    channel_id: int | None = Field(
        default=None, description="Channel bank approved questions go to; omit for general"
    )

    @model_validator(mode="after")
    def tags_present(self) -> "GenerateRequest":
        self.subject = _required(self.subject, "Select a subject")
        self.topic = _required(self.topic, "Select a topic")
        return self


class QuestionEdit(BaseModel):
    text: str = Field(max_length=5000)
    options: list[str] = Field(max_length=10)
    correct_answer: str = Field(max_length=5000)
    explanation: str = Field(max_length=5000)

    @model_validator(mode="after")
    def answerable(self) -> "QuestionEdit":
        self.text = _required(self.text, "Enter the question text")
        self.explanation = _required(self.explanation, "Enter an explanation")
        options = [" ".join(o.split()) for o in self.options if o and o.strip()]
        if len(options) < 2 or len({o.lower() for o in options}) != len(options):
            raise ValueError(OPTIONS_MESSAGE)
        answer = " ".join(self.correct_answer.split()).lower()
        match = [o for o in options if o.lower() == answer]
        if not match:
            raise ValueError(ANSWER_NOT_AN_OPTION)
        self.options, self.correct_answer = options, match[0]
        return self


class AddToQuizRequest(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    duration_minutes: int = Field(default=15, ge=1, le=300, description="Timer length")


class GeneratedQuestionResponse(BaseModel):
    id: int
    text: str
    subject: str
    topic: str
    difficulty: Difficulty
    question_type: QuestionType
    options: list[str]
    correct_answer: str | None
    explanation: str | None
    review_status: str = Field(description="pending, approved or rejected")
    edited: bool


class GenerationResponse(BaseModel):
    id: int
    subject: str
    topic: str
    difficulty: Difficulty
    channel_id: int | None
    questions: list[GeneratedQuestionResponse]
    pending_count: int
    can_add_to_quiz: bool = Field(
        description="False until every question is approved or rejected (and one approved)"
    )


class AddToQuizResponse(BaseModel):
    quiz_id: int
    question_ids: list[int]
    status: str
    message: str


def _response(g: generation.Generation) -> GenerationResponse:
    return GenerationResponse(
        id=g.id,
        subject=g.subject,
        topic=g.topic,
        difficulty=g.difficulty,
        channel_id=g.channel_id,
        questions=[
            GeneratedQuestionResponse(
                id=item.question.id,
                text=item.question.text,
                subject=item.question.subject,
                topic=item.question.topic,
                difficulty=item.question.difficulty,
                question_type=item.question.question_type,
                options=list(item.question.options),
                correct_answer=item.question.correct_answer,
                explanation=item.question.explanation,
                review_status=item.review_status,
                edited=item.edited,
            )
            for item in g.questions
        ],
        pending_count=g.pending_count,
        can_add_to_quiz=g.can_add_to_quiz,
    )


def _is_admin(user: UserSummary) -> bool:
    return Capability.CHANNEL_APPROVE in capabilities_for(user.role)


def check_channel_bank(db: Session, channel_id: int, user: UserSummary) -> None:
    """The caller may add to this channel's bank: it is visible and they are a member."""
    summary = channels.describe_channel(db, channel_id)
    if summary is None or not channels.is_visible_to(summary, user.id, _is_admin(user)):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=CHANNEL_NOT_FOUND)
    if not channels.is_member(db, channel_id, user.id) and not _is_admin(user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=NOT_A_MEMBER)


def failure(exc: generation.GenerationFailedError) -> HTTPException:
    if exc.timeout:
        return HTTPException(status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail=PROVIDER_TIMEOUT)
    return HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=PROVIDER_FAILED)


def _own(db: Session, generation_id: int, user: UserSummary) -> generation.Generation:
    found = generation.get_generation(db, generation_id)
    if found is None or found.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=GENERATION_NOT_FOUND)
    return found


def _review(action, db: Session, g: generation.Generation, question_id: int, **kwargs):
    try:
        updated = action(db, g, question_id, **kwargs)
    except LookupError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=QUESTION_NOT_FOUND
        ) from None
    except NotADraftError:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=ALREADY_REVIEWED) from None
    db.commit()
    return _response(updated)


@router.post("/generations", status_code=status.HTTP_201_CREATED, response_model=GenerationResponse)
def generate_questions(
    payload: GenerateRequest,
    user: UserSummary = Depends(require_capability(Capability.QUESTION_CONTRIBUTE)),
    provider: LlmProvider = Depends(_provider),
    db: Session = Depends(get_db),
) -> GenerationResponse:
    """Generate 5-10 questions, each with options, a marked correct answer and an
    explanation, for the selected subject, topic and difficulty (UT_13).

    They are saved as drafts for the review screen and reach no bank or quiz until
    approved (UT_14). A provider error or timeout returns 502/504 with a clear message.
    """
    if payload.channel_id is not None:
        check_channel_bank(db, payload.channel_id, user)
    try:
        created = generation.generate(
            db,
            provider,
            user_id=user.id,
            subject=payload.subject,
            topic=payload.topic,
            difficulty=payload.difficulty,
            count=payload.count,
            channel_id=payload.channel_id,
        )
    except generation.GenerationFailedError as exc:
        db.commit()  # keep the audit record of the failed request
        raise failure(exc) from None
    db.commit()
    return _response(created)


@router.get("/generations", response_model=list[GenerationResponse])
def my_generations(
    user: UserSummary = Depends(current_user), db: Session = Depends(get_db)
) -> list[GenerationResponse]:
    """The caller's recent generated batches, newest first."""
    return [_response(g) for g in generation.list_generations(db, user.id)]


@router.get("/generations/{generation_id}", response_model=GenerationResponse)
def review_screen(
    generation_id: int, user: UserSummary = Depends(current_user), db: Session = Depends(get_db)
) -> GenerationResponse:
    """The review screen: every question with its review status, and whether Add to
    Quiz is available yet."""
    return _response(_own(db, generation_id, user))


@router.put(
    "/generations/{generation_id}/questions/{question_id}", response_model=GenerationResponse
)
def edit_question(
    generation_id: int,
    question_id: int,
    payload: QuestionEdit,
    user: UserSummary = Depends(current_user),
    db: Session = Depends(get_db),
) -> GenerationResponse:
    """Save a correction to a flawed question (IT_13). It stays pending until approved."""
    return _review(
        generation.edit,
        db,
        _own(db, generation_id, user),
        question_id,
        text=payload.text,
        options=payload.options,
        correct_answer=payload.correct_answer,
        explanation=payload.explanation,
    )


@router.post(
    "/generations/{generation_id}/questions/{question_id}/approve",
    response_model=GenerationResponse,
)
def approve_question(
    generation_id: int,
    question_id: int,
    user: UserSummary = Depends(current_user),
    db: Session = Depends(get_db),
) -> GenerationResponse:
    """Approve a question: it joins the general or channel bank."""
    return _review(generation.approve, db, _own(db, generation_id, user), question_id)


@router.post(
    "/generations/{generation_id}/questions/{question_id}/reject",
    response_model=GenerationResponse,
)
def reject_question(
    generation_id: int,
    question_id: int,
    user: UserSummary = Depends(current_user),
    db: Session = Depends(get_db),
) -> GenerationResponse:
    """Reject a question: it is discarded and never appears in a bank or quiz (IT_13)."""
    return _review(generation.reject, db, _own(db, generation_id, user), question_id)


@router.post(
    "/generations/{generation_id}/quiz",
    status_code=status.HTTP_201_CREATED,
    response_model=AddToQuizResponse,
)
def add_to_quiz(
    generation_id: int,
    payload: AddToQuizRequest,
    user: UserSummary = Depends(require_capability(Capability.QUIZ_PUBLISH)),
    db: Session = Depends(get_db),
) -> AddToQuizResponse:
    """Add to Quiz: a draft quiz from the approved questions (publish it with
    ``POST /quizzes/{id}/publish``). Refused with 409 while any question is still
    pending review (UT_14)."""
    g = _own(db, generation_id, user)
    try:
        ids = generation.approved_for_quiz(g)
    except generation.NotReviewedError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from None
    except generation.NothingApprovedError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    quiz_id = quizzes.create_quiz(
        db,
        creator_id=user.id,
        title=(payload.title or "").strip() or f"{g.topic} ({g.difficulty.value}) - AI generated",
        subject=g.subject,
        topic=g.topic,
        difficulty=g.difficulty.value,
        duration_minutes=payload.duration_minutes,
        question_ids=ids,
        channel_id=g.channel_id,
        publish=False,
    )
    db.commit()
    return AddToQuizResponse(
        quiz_id=quiz_id,
        question_ids=ids,
        status="draft",
        message="Quiz draft created from the approved questions",
    )
