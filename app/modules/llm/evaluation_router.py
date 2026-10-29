"""HTTP surface for descriptive answer evaluation (S08.1, FR-08), under ``/api/v1``.

    POST /evaluations/descriptive   score a written answer against expected concepts
    POST /attempts/{id}/evaluate    evaluate a submitted attempt's written answers (S08.2)

It sits beside the S07.x routes in ``router.py`` and shares their provider dependency,
``get_llm_provider``, so one test override fakes the LLM for every AI route.

The caller must be able to read the question: a question in a channel they cannot see,
or a draft that is not theirs, is a 404 exactly as in the question bank.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.modules.accounts.service import Capability, UserSummary, capabilities_for, current_user
from app.modules.channels import service as channels
from app.modules.llm import evaluation
from app.modules.llm.provider import LlmProvider
from app.modules.llm.router import get_llm_provider
from app.modules.questions import service as questions
from app.modules.quizzes import service as quizzes

router = APIRouter(tags=["descriptive evaluation"])

QUESTION_NOT_FOUND = "Question not found"
ATTEMPT_NOT_FOUND = "Attempt not found"
NOT_SUBMITTED = "Submit the attempt before its written answers are evaluated"
NOT_DESCRIPTIVE = "Only descriptive questions can be evaluated"
MAX_ANSWER_LENGTH = 5000


class EvaluationRequest(BaseModel):
    question_id: int
    answer: str = Field(max_length=MAX_ANSWER_LENGTH)

    @field_validator("answer")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if evaluation.is_blank(value):
            raise ValueError(evaluation.EMPTY_ANSWER)
        return value


class EvaluationResponse(BaseModel):
    question_id: int
    score: float = Field(description="0 to 10 in steps of 0.5")
    max_score: float = Field(description="Always 10")
    display: str = Field(description='For example "5/10"')
    covered_concepts: list[str]
    missing_concepts: list[str] = Field(description="Each missing concept named individually")
    strengths: list[str] = Field(description="At least one")
    weaknesses: list[str] = Field(description="At least one")
    summary: str
    evaluator: str = Field(
        description='"llm", or "concept-match" when no real LLM is configured or it failed'
    )
    ai_response_id: int | None = Field(
        default=None,
        description="When the LLM scored the answer: report a wrong evaluation at "
        "POST /ai/responses/{id}/reports (NFR-06). None for concept matching.",
    )


def _ai_response_id(result: evaluation.DescriptiveEvaluation) -> int | None:
    """The reportable AI output behind a score; concept matching has none."""
    return result.llm_request_id if result.evaluator == "llm" else None


def _is_admin(user: UserSummary) -> bool:
    return Capability.CHANNEL_APPROVE in capabilities_for(user.role)


def _readable(db: Session, question_id: int, user: UserSummary) -> questions.BankQuestion:
    """The question if the caller may read it in the bank; 404 otherwise."""
    item = questions.get_bank_question(db, question_id)
    not_found = HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=QUESTION_NOT_FOUND)
    if item is None:
        raise not_found
    if item.status != "published" and not (item.author_id == user.id or _is_admin(user)):
        raise not_found
    if item.channel_id is not None:
        channel = channels.describe_channel(db, item.channel_id)
        if channel is None or not channels.is_visible_to(channel, user.id, _is_admin(user)):
            raise not_found
    return item


@router.post("/evaluations/descriptive", response_model=EvaluationResponse)
def evaluate_descriptive(
    payload: EvaluationRequest,
    user: UserSummary = Depends(current_user),
    db: Session = Depends(get_db),
    provider: LlmProvider | None = Depends(get_llm_provider),
) -> EvaluationResponse:
    """Score a written answer 0-10 against the question's expected concepts (FR-08).

    The feedback names each covered and missing concept and lists at least one
    strength and one weakness. A blank answer is refused with "Answer cannot be empty"
    (422) and nothing is sent to the LLM. Without a configured LLM, or if the LLM call
    fails, the answer is scored by deterministic concept matching (``evaluator``).
    """
    item = _readable(db, payload.question_id, user)
    question = item.question
    if question.is_objective:
        raise HTTPException(status_code=422, detail=NOT_DESCRIPTIVE)
    try:
        result = evaluation.evaluate(
            db,
            user_id=user.id,
            question=question,
            answer=payload.answer,
            provider=provider,
            channel_id=item.channel_id,
        )
    except (evaluation.EmptyAnswerError, evaluation.NothingToEvaluateError) as problem:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(problem)) from None
    db.commit()
    return EvaluationResponse(
        question_id=question.id,
        score=result.score,
        max_score=result.max_score,
        display=result.display,
        covered_concepts=list(result.covered_concepts),
        missing_concepts=list(result.missing_concepts),
        strengths=list(result.strengths),
        weaknesses=list(result.weaknesses),
        summary=result.summary,
        evaluator=result.evaluator,
        ai_response_id=_ai_response_id(result),
    )


# --------------------------------------------------------------------------- #
# S08.2 - evaluating a submitted quiz attempt
# --------------------------------------------------------------------------- #


class AttemptScorePartResponse(BaseModel):
    score: float
    max_score: float
    percentage: float
    display: str


class AttemptScoreBreakdownResponse(BaseModel):
    objective: AttemptScorePartResponse = Field(description="Marked only against the answer key")
    descriptive: AttemptScorePartResponse = Field(
        description="Evaluations scaled to question points"
    )
    total: AttemptScorePartResponse
    pending_evaluation: int
    explanation: str


class AnswerEvaluationResponse(EvaluationResponse):
    points: float = Field(description="The question's points in the quiz")
    points_earned: float = Field(description="score / max_score × points")


class NotEvaluatedResponse(BaseModel):
    question_id: int
    reason: str


class AttemptEvaluationResponse(BaseModel):
    attempt_id: int
    evaluated: list[AnswerEvaluationResponse] = Field(
        description="Written answers evaluated by this request"
    )
    not_evaluated: list[NotEvaluatedResponse] = Field(
        description="Written answers that could not be evaluated; they stay pending"
    )
    breakdown: AttemptScoreBreakdownResponse


def _part(part: quizzes.ScorePart) -> AttemptScorePartResponse:
    return AttemptScorePartResponse(
        score=part.score, max_score=part.max_score, percentage=part.percentage, display=part.display
    )


def _evaluation_response(question_id: int, result: evaluation.DescriptiveEvaluation) -> dict:
    return {
        "question_id": question_id,
        "score": result.score,
        "max_score": result.max_score,
        "display": result.display,
        "covered_concepts": list(result.covered_concepts),
        "missing_concepts": list(result.missing_concepts),
        "strengths": list(result.strengths),
        "weaknesses": list(result.weaknesses),
        "summary": result.summary,
        "evaluator": result.evaluator,
        "ai_response_id": _ai_response_id(result),
    }


@router.post(
    "/attempts/{attempt_id}/evaluate",
    response_model=AttemptEvaluationResponse,
    responses={409: {"description": "The attempt is still running"}},
)
def evaluate_attempt(
    attempt_id: int,
    user: UserSummary = Depends(current_user),
    db: Session = Depends(get_db),
    provider: LlmProvider | None = Depends(get_llm_provider),
) -> AttemptEvaluationResponse:
    """Evaluate the written answers of the caller's submitted attempt (FR-08, S08.2).

    Call it after submitting. Each descriptive answer is scored 0-10 with concept
    feedback, as in ``POST /evaluations/descriptive``, and stored in the attempt: its
    score is scaled to the question's points and added to the total, so
    ``GET /attempts/{id}`` and My Quiz History show it from then on. Objective marks
    are never changed (NFR-06). Answers already evaluated are kept as they are, so
    calling again only evaluates what is still pending. Someone else's attempt is a 404.
    """
    if quizzes.attempt_owner(db, attempt_id) != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=ATTEMPT_NOT_FOUND)
    try:
        found = evaluation.evaluate_attempt(
            db, user_id=user.id, attempt_id=attempt_id, provider=provider, commit=db.commit
        )
    except quizzes.NotSubmittedError:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=NOT_SUBMITTED) from None
    db.commit()
    b = found.breakdown
    return AttemptEvaluationResponse(
        attempt_id=attempt_id,
        evaluated=[
            AnswerEvaluationResponse(
                **_evaluation_response(e.question_id, e.evaluation),
                points=e.points,
                points_earned=e.points_earned,
            )
            for e in found.evaluated
        ],
        not_evaluated=[NotEvaluatedResponse(**vars(n)) for n in found.not_evaluated],
        breakdown=AttemptScoreBreakdownResponse(
            objective=_part(b.objective),
            descriptive=_part(b.descriptive),
            total=_part(b.total),
            pending_evaluation=b.pending_evaluation,
            explanation=b.explanation,
        ),
    )
