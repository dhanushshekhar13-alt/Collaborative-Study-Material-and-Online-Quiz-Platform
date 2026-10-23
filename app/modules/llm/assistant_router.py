"""HTTP surface of Ask AI (S09.1, FR-09), mounted under ``/api/v1/ai``.

    POST /ai/ask                     explain a concept, or generate a quiz on a topic
    POST /ai/ask/{id}/start          start (or resume) the generated topic quiz

It shares ``get_llm_provider`` with the other AI routes, so one test override fakes the
LLM everywhere. A blank query is refused with 422 "Please enter a question" before
anything is recorded or sent to the LLM.
"""

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.modules.accounts.service import (
    Capability,
    UserSummary,
    current_user,
    require_capability,
)
from app.modules.llm import assistant, generation
from app.modules.llm.provider import LlmProvider
from app.modules.llm.router import get_llm_provider
from app.modules.questions.service import Difficulty

router = APIRouter(prefix="/ai", tags=["ask ai"])

NOT_CONFIGURED = "Ask AI is not available on this server right now."
PROVIDER_FAILED = "The AI service could not answer right now. Please try again in a moment."
PROVIDER_TIMEOUT = "The AI service took too long to respond. Please try again."
QUIZ_NOT_FOUND = "Topic quiz not found"


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

    @field_validator("query")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if assistant.is_blank(value):
            raise ValueError(assistant.EMPTY_QUERY)
        return value.strip()


class ExplanationResponse(BaseModel):
    text: str = Field(description="A simplified explanation")
    examples: list[str] = Field(description="At least one example")


class TopicQuizResponse(BaseModel):
    quiz_id: int
    title: str
    subject: str
    topic: str
    difficulty: Difficulty
    question_count: int
    duration_minutes: int
    generation_id: int = Field(
        description="Review these questions at GET /ai/generations/{id}; only approved "
        "ones join the question bank"
    )
    review_required: bool = Field(
        description="Always true: generated questions stay out of the bank until approved"
    )
    start_path: str = Field(description="POST here to start the quiz")


class AskResponse(BaseModel):
    id: int
    kind: str = Field(description='"explanation" or "quiz"')
    query: str
    topic: str
    explanation: ExplanationResponse | None = None
    quiz: TopicQuizResponse | None = None
    message: str
    response_ms: int = Field(description="Time the server spent answering, in milliseconds")


class StartResponse(BaseModel):
    attempt_id: int
    quiz_id: int
    resumed: bool
    attempt_path: str


def _failure(exc: generation.GenerationFailedError) -> HTTPException:
    if exc.timeout:
        return HTTPException(status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail=PROVIDER_TIMEOUT)
    return HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=PROVIDER_FAILED)


@router.post(
    "/ask",
    response_model=AskResponse,
    responses={
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
    """
    intent = assistant.classify(payload.query)
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
                db, provider, user_id=user.id, query=payload.query, topic=intent.topic
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
