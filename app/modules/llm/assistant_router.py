"""HTTP surface of Ask AI (S09.1, S09.2, FR-09), mounted under ``/api/v1/ai``.

    POST /ai/ask                     explain a concept, or generate a quiz on a topic
    POST /ai/ask/{id}/start          start (or resume) the generated topic quiz

It shares ``get_llm_provider`` with the other AI routes, so one test override fakes the
LLM everywhere. A blank query is refused with 422 "Please enter a question" before
anything is recorded or sent to the LLM. With ``channel_id`` the request is scoped to
that channel's subject and question bank (S09.2).
"""

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field, field_validator
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
from app.modules.llm import assistant, generation
from app.modules.llm.provider import LlmProvider
from app.modules.llm.router import get_llm_provider
from app.modules.questions.service import Difficulty

router = APIRouter(prefix="/ai", tags=["ask ai"])

NOT_CONFIGURED = "Ask AI is not available on this server right now."
PROVIDER_FAILED = "The AI service could not answer right now. Please try again in a moment."
PROVIDER_TIMEOUT = "The AI service took too long to respond. Please try again."
QUIZ_NOT_FOUND = "Topic quiz not found"
CHANNEL_NOT_FOUND = "Channel not found"
CHANNEL_MEMBERS_ONLY = "Join this channel to use Ask AI in it"


class AskRequest(BaseModel):
    query: str = Field(
        max_length=assistant.MAX_QUERY_LENGTH,
        description='For example "Explain B-Trees with an example" or '
        '"Give me a quiz on Binary Search Trees"',
    )
    subject: str | None = Field(
        default=None, max_length=100, description="Subject tag for a quiz (default General)"
    )
    difficulty: Difficulty = Field(
        default=Difficulty.MEDIUM, description="Difficulty for a quiz request"
    )
    question_count: int | None = Field(
        default=None,
        ge=generation.MIN_QUESTIONS,
        le=generation.MAX_QUESTIONS,
        description="Quiz length (5-10); a number in the query such as '8 questions' "
        "is used when this is omitted; default 5",
    )
    channel_id: int | None = Field(
        default=None,
        description='"Ask AI in this channel" (S09.2): scope the answer to the channel\'s '
        "subject and question bank. Needs membership.",
    )

    @field_validator("query")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if assistant.is_blank(value):
            raise ValueError(assistant.EMPTY_QUERY)
        return value.strip()


class ExplanationResponse(BaseModel):
    text: str = Field(description="A simplified explanation")
    examples: list[str] = Field(description="At least one example")


class QuizItemResponse(BaseModel):
    question_id: int
    source: str = Field(description='"peer" (from the channel bank) or "generated" (AI)')
    author_id: int
    author_name: str | None
    subject: str
    topic: str


class TopicQuizResponse(BaseModel):
    quiz_id: int
    title: str
    subject: str
    topic: str
    difficulty: Difficulty
    question_count: int
    duration_minutes: int
    generation_id: int | None = Field(
        description="Review the generated questions at GET /ai/generations/{id}; only "
        "approved ones join the question bank. None when no question was generated."
    )
    review_required: bool = Field(
        description="True when the quiz has generated questions: they stay out of the "
        "bank until approved"
    )
    start_path: str = Field(description="POST here to start the quiz")
    # Channel-scoped quizzes (S09.2): where each question came from, so the reviewer
    # can tell peer questions from generated ones.
    channel_id: int | None = None
    peer_count: int = 0
    generated_count: int = 0
    items: list[QuizItemResponse] = Field(default_factory=list)


class AskResponse(BaseModel):
    id: int
    kind: str = Field(description='"explanation" or "quiz"')
    query: str
    topic: str
    explanation: ExplanationResponse | None = None
    quiz: TopicQuizResponse | None = None
    message: str
    response_ms: int = Field(description="Time the server spent answering, in milliseconds")
    channel_id: int | None = Field(default=None, description="The channel it was scoped to")


class StartResponse(BaseModel):
    attempt_id: int
    quiz_id: int
    resumed: bool
    attempt_path: str


def _failure(exc: generation.GenerationFailedError) -> HTTPException:
    if exc.timeout:
        return HTTPException(status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail=PROVIDER_TIMEOUT)
    return HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=PROVIDER_FAILED)


def _is_admin(user: UserSummary) -> bool:
    return Capability.CHANNEL_APPROVE in capabilities_for(user.role)


def _channel_scope(db: Session, channel_id: int, user: UserSummary) -> assistant.ChannelScope:
    """The channel if the caller may ask AI in it: 404 if hidden, 403 if not a member."""
    summary = channels.describe_channel(db, channel_id)
    if summary is None or not channels.is_visible_to(summary, user.id, _is_admin(user)):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=CHANNEL_NOT_FOUND)
    if not channels.is_member(db, channel_id, user.id) and not _is_admin(user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=CHANNEL_MEMBERS_ONLY)
    # Generated drafts may join an Authorized Channel's bank only through its authorized
    # users (S03.1); anyone else's stay out of every channel bank.
    writable = summary.channel_type is not channels.ChannelType.AUTHORIZED or (
        Capability.OFFICIAL_CONTENT_MANAGE in capabilities_for(user.role)
    )
    return assistant.ChannelScope(
        id=summary.id,
        name=summary.name,
        subject=summary.subject or summary.topic,
        topic=summary.topic,
        bank_writable=writable,
    )


def _channel_quiz_response(db: Session, made: assistant.ChannelQuiz) -> AskResponse:
    names: dict[int, str | None] = {}
    for q in made.questions:
        if q.author_id not in names:
            author = accounts.get_user(db, q.author_id)
            names[q.author_id] = author.display_name if author else None
    generated = made.generated_count
    message = (
        f"Your {len(made.questions)}-question quiz on {made.topic} is ready: "
        f"{made.peer_count} from this channel's question bank and {generated} generated."
    )
    if generated:
        message += " Approve the generated ones on the review screen to add them to the bank."
    return AskResponse(
        id=made.request_id,
        kind="quiz",
        query=made.query,
        topic=made.topic,
        channel_id=made.channel_id,
        quiz=TopicQuizResponse(
            quiz_id=made.quiz_id,
            title=made.title,
            subject=made.subject,
            topic=made.topic,
            difficulty=made.difficulty,
            question_count=len(made.questions),
            duration_minutes=made.duration_minutes,
            generation_id=made.generation_id,
            review_required=generated > 0,
            start_path=f"/api/v1/ai/ask/{made.request_id}/start",
            channel_id=made.channel_id,
            peer_count=made.peer_count,
            generated_count=generated,
            items=[
                QuizItemResponse(
                    question_id=q.question_id,
                    source=q.source,
                    author_id=q.author_id,
                    author_name=names[q.author_id],
                    subject=q.subject,
                    topic=q.topic,
                )
                for q in made.questions
            ],
        ),
        message=message,
        response_ms=made.response_ms,
    )


@router.post(
    "/ask",
    response_model=AskResponse,
    responses={
        403: {"description": "Not a member of the channel"},
        404: {"description": "Channel not found"},
        422: {"description": "Blank query (Please enter a question) or no quiz topic"},
        502: {"description": "The AI service failed"},
        503: {"description": "No LLM is configured"},
        504: {"description": "The AI service timed out"},
    },
)
def ask(
    payload: AskRequest,
    user: UserSummary = Depends(current_user),
    provider: LlmProvider | None = Depends(get_llm_provider),
    db: Session = Depends(get_db),
) -> AskResponse:
    """Ask the assistant to explain a concept or to generate a quiz on a topic.

    *Explanation* (UT_17, ST_18): a simplified explanation with at least one example,
    checked to be about the concept asked. *Quiz* (IT_17): a private practice quiz on
    the named topic, ready to start with ``POST /ai/ask/{id}/start``. Its questions are
    drafts that join no bank until approved on the review screen (S07.1). A blank query
    is refused with "Please enter a question" and nothing is sent (UT_18).

    *In a channel* (``channel_id``, S09.2): the answer is scoped to the channel's
    subject and bank. A quiz uses the bank's peer questions first and generates only the
    shortfall in the channel's subject (IT_18, ST_17); ``quiz.items`` marks each one
    ``peer`` or ``generated``. "Generate a quiz for this channel" uses the channel's
    own topic.
    """
    intent = assistant.classify(payload.query)
    if payload.channel_id is not None:
        scope = _channel_scope(db, payload.channel_id, user)
        if intent.kind == "quiz":
            try:
                made = assistant.request_channel_quiz(
                    db,
                    provider,
                    user_id=user.id,
                    query=payload.query,
                    channel=scope,
                    topic=intent.topic,
                    difficulty=payload.difficulty,
                    question_count=payload.question_count or intent.question_count,
                )
            except assistant.ProviderNeededError:
                db.rollback()
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=NOT_CONFIGURED
                ) from None
            except generation.GenerationFailedError as exc:
                db.commit()
                raise _failure(exc) from None
            db.commit()
            return _channel_quiz_response(db, made)
    else:
        scope = None
    if intent.kind == "quiz" and not intent.topic:
        raise HTTPException(status_code=422, detail=assistant.NO_QUIZ_TOPIC)
    if provider is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=NOT_CONFIGURED)
    try:
        if intent.kind == "quiz":
            made = assistant.request_quiz(
                db,
                provider,
                user_id=user.id,
                query=payload.query,
                topic=intent.topic,
                subject=payload.subject,
                difficulty=payload.difficulty,
                question_count=payload.question_count or intent.question_count,
            )
        else:
            found = assistant.explain(
                db,
                provider,
                user_id=user.id,
                query=payload.query,
                topic=intent.topic,
                channel=scope,
            )
    except generation.GenerationFailedError as exc:
        db.commit()  # keep the audit record of the failed request
        raise _failure(exc) from None
    db.commit()
    if intent.kind == "quiz":
        return AskResponse(
            id=made.request_id,
            kind="quiz",
            query=made.query,
            topic=made.topic,
            quiz=TopicQuizResponse(
                quiz_id=made.quiz_id,
                title=made.title,
                subject=made.subject,
                topic=made.topic,
                difficulty=made.difficulty,
                question_count=len(made.question_ids),
                duration_minutes=made.duration_minutes,
                generation_id=made.generation_id,
                review_required=True,
                start_path=f"/api/v1/ai/ask/{made.request_id}/start",
            ),
            message=(
                f"Your {len(made.question_ids)}-question quiz on {made.topic} is ready. "
                "Start it now; approve its questions on the review screen to add them "
                "to the question bank."
            ),
            response_ms=made.response_ms,
        )
    return AskResponse(
        id=found.request_id,
        kind="explanation",
        query=found.query,
        topic=found.topic,
        explanation=ExplanationResponse(text=found.text, examples=list(found.examples)),
        message=f"Here is a simple explanation of {found.topic}.",
        response_ms=found.response_ms,
        channel_id=scope.id if scope else None,
    )


@router.post(
    "/ask/{ask_id}/start",
    status_code=status.HTTP_201_CREATED,
    response_model=StartResponse,
    responses={200: {"description": "Resumed the caller's attempt that is still running"}},
)
def start_topic_quiz(
    ask_id: int,
    response: Response,
    user: UserSummary = Depends(require_capability(Capability.QUIZ_ATTEMPT)),
    db: Session = Depends(get_db),
) -> StartResponse:
    """Start the quiz generated by ``POST /ai/ask``; answer and submit it with the
    usual ``/attempts/{id}`` routes. Only the requester can start it (404 otherwise)."""
    try:
        started = assistant.start_quiz(db, ask_id, user.id)
    except assistant.AskNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=QUIZ_NOT_FOUND) from None
    db.commit()
    if started.resumed:
        response.status_code = status.HTTP_200_OK
    return StartResponse(
        attempt_id=started.attempt_id,
        quiz_id=started.quiz_id,
        resumed=started.resumed,
        attempt_path=f"/api/v1/attempts/{started.attempt_id}",
    )
