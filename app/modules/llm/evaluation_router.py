"""HTTP surface for descriptive answer evaluation (S08.1, FR-08), under ``/api/v1``.

    POST /evaluations/descriptive   score a written answer against expected concepts

The caller must be able to read the question: a question in a channel they cannot see,
or a draft that is not theirs, is a 404 exactly as in the question bank.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import get_db
from app.modules.accounts.service import Capability, UserSummary, capabilities_for, current_user
from app.modules.channels import service as channels
from app.modules.llm import evaluation
from app.modules.llm.client import LlmClient, provider_client
from app.modules.questions import service as questions

router = APIRouter(tags=["descriptive evaluation"])

QUESTION_NOT_FOUND = "Question not found"
NOT_DESCRIPTIVE = "Only descriptive questions can be evaluated"
MAX_ANSWER_LENGTH = 5000


def get_llm_client() -> LlmClient | None:
    """The configured LLM client, or ``None`` when the LLM is disabled (overridable)."""
    return provider_client(get_settings())


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
        description='"llm", or "concept-match" when no LLM is configured or the LLM failed'
    )


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
    client: LlmClient | None = Depends(get_llm_client),
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
            client=client,
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
    )
