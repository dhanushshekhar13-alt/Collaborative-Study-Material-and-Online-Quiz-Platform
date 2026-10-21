"""HTTP surface for taking a quiz (S06.1, FR-06), mounted under ``/api/v1``.

    POST /quizzes/{quiz_id}/attempts   start (or resume) an attempt
    GET  /attempts/{attempt_id}        questions, saved answers, time left, result
    PUT  /attempts/{attempt_id}/answers save answers while the timer runs
    POST /attempts/{attempt_id}/submit  final submission, scored immediately

The attempt routes live at ``/attempts`` so they never collide with the quiz-building
routes under ``/quizzes/{quiz_id}`` (S05.2). Only the person taking the attempt can see
it; anyone else gets the same 404 as for an attempt that does not exist.
"""

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.modules.accounts.service import (
    Capability,
    UserSummary,
    capabilities_for,
    current_user,
    require_capability,
)
from app.modules.channels import service as channels
from app.modules.quizzes import attempts
from app.modules.quizzes.models import AttemptStatus

router = APIRouter(tags=["quiz attempts"])

QUIZ_NOT_FOUND = "Quiz not found"
ATTEMPT_NOT_FOUND = "Attempt not found"
NOT_A_MEMBER = "Join the channel to attempt this quiz"
NO_QUESTIONS = "This quiz has no questions yet"
ALREADY_SUBMITTED = "This attempt has already been submitted"


class AnswersRequest(BaseModel):
    answers: dict[int, str | None] = Field(
        default_factory=dict,
        description="Question id → response. A blank or null response clears it.",
    )


class SubmitRequest(AnswersRequest):
    confirm: bool = Field(
        default=False,
        description='Submit even though some questions are unanswered (only under "warn")',
    )


class AttemptQuestionResponse(BaseModel):
    question_id: int
    position: int
    text: str
    question_type: str
    options: list[str]
    points: float
    response: str | None


class AnswerOutcomeResponse(BaseModel):
    question_id: int
    position: int
    response: str | None
    status: attempts.AnswerStatus
    score: float | None
    points: float
    correct_answer: str | None
    explanation: str | None


class ResultResponse(BaseModel):
    score: float
    max_score: float
    percentage: float
    display: str = Field(description='For example "4/5 (80%)"')
    correct: int
    incorrect: int
    unattempted: int
    pending_evaluation: int = Field(description="Descriptive answers awaiting S08.1")
    submitted_at: datetime | None
    answers: list[AnswerOutcomeResponse]


class AttemptResponse(BaseModel):
    id: int
    quiz_id: int
    quiz_title: str
    status: AttemptStatus
    started_at: datetime
    expires_at: datetime | None = Field(description="null for an untimed quiz")
    remaining_seconds: int | None
    auto_submitted: bool
    unanswered_rule: str
    answered_count: int
    unanswered_count: int
    unanswered_question_ids: list[int]
    warning: str | None = Field(description='For example "2 questions are unanswered"')
    questions: list[AttemptQuestionResponse]
    result: ResultResponse | None


class UnansweredResponse(BaseModel):
    detail: str
    unanswered_count: int
    unanswered_question_ids: list[int]
    unanswered_rule: str
    attempt: AttemptResponse


def _response(state: attempts.AttemptState) -> AttemptResponse:
    outcome = state.outcome
    open_ = state.status is AttemptStatus.IN_PROGRESS
    return AttemptResponse(
        id=state.id,
        quiz_id=state.quiz_id,
        quiz_title=state.quiz_title,
        status=state.status,
        started_at=state.started_at,
        expires_at=state.expires_at,
        remaining_seconds=state.remaining_seconds,
        auto_submitted=state.auto_submitted,
        unanswered_rule=settings.quiz_unanswered_rule,
        answered_count=len(state.questions) - state.unanswered_count,
        unanswered_count=state.unanswered_count,
        unanswered_question_ids=list(state.unanswered_question_ids),
        warning=(
            attempts.unanswered_warning(state.unanswered_count)
            if open_ and state.unanswered_count
            else None
        ),
        questions=[
            AttemptQuestionResponse(
                question_id=q.question_id,
                position=q.position,
                text=q.text,
                question_type=q.question_type,
                options=list(q.options),
                points=q.points,
                response=q.response,
            )
            for q in state.questions
        ],
        result=(
            ResultResponse(
                score=outcome.score,
                max_score=outcome.max_score,
                percentage=outcome.percentage,
                display=outcome.display,
                correct=outcome.correct,
                incorrect=outcome.incorrect,
                unattempted=outcome.unattempted,
                pending_evaluation=outcome.pending_evaluation,
                submitted_at=outcome.submitted_at,
                answers=[AnswerOutcomeResponse(**vars(a)) for a in outcome.answers],
            )
            if outcome
            else None
        ),
    )


def _is_admin(user: UserSummary) -> bool:
    return Capability.CHANNEL_APPROVE in capabilities_for(user.role)


def _attemptable(db: Session, quiz_id: int, user: UserSummary) -> attempts.QuizAccessInfo:
    """The quiz if the caller may attempt it.

    Published quizzes only (a draft is visible to nobody but its author, who previews
    it in S05.2). A channel quiz needs channel membership; official quizzes in an
    Authorized Channel follow exactly the same rules and scoring as peer quizzes.
    """
    quiz = attempts.quiz_access_info(db, quiz_id)
    if quiz is None or not quiz.is_published:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=QUIZ_NOT_FOUND)
    if quiz.channel_id is not None:
        channel = channels.describe_channel(db, quiz.channel_id)
        if channel is None or not channels.is_visible_to(channel, user.id, _is_admin(user)):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=QUIZ_NOT_FOUND)
        if not channels.is_member(db, quiz.channel_id, user.id):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=NOT_A_MEMBER)
    if quiz.question_count == 0:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=NO_QUESTIONS)
    return quiz


def _own(
    db: Session, attempt_id: int, user: UserSummary, grace: timedelta = timedelta(0)
) -> attempts.AttemptState:
    """The caller's attempt, closed first if its timer ran out; 404 for anyone else's."""
    if attempts.owner_of(db, attempt_id) != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=ATTEMPT_NOT_FOUND)
    if attempts.close_if_expired(db, attempt_id, grace):
        db.commit()
    return attempts.state(db, attempt_id)


def _not_in_quiz(question_id: int) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail=f"Question {question_id} is not part of this quiz",
    )


@router.post(
    "/quizzes/{quiz_id}/attempts",
    status_code=status.HTTP_201_CREATED,
    response_model=AttemptResponse,
    responses={200: {"description": "Resumed the caller's attempt that is still running"}},
)
def start_attempt(
    quiz_id: int,
    response: Response,
    user: UserSummary = Depends(require_capability(Capability.QUIZ_ATTEMPT)),
    db: Session = Depends(get_db),
) -> AttemptResponse:
    """Start the quiz; the timer starts now and ``remaining_seconds`` counts it down.

    If the caller already has a running attempt at this quiz it is resumed (200) with
    its answers and remaining time, so reloading the page does not restart the clock.
    Non-members cannot attempt a channel quiz (403).
    """
    _attemptable(db, quiz_id, user)
    running = attempts.open_attempt_for(db, quiz_id, user.id)
    if running is not None:
        response.status_code = status.HTTP_200_OK
        attempt_id = running
    else:
        attempt_id = attempts.start(db, quiz_id, user.id)
    db.commit()
    return _response(attempts.state(db, attempt_id))


@router.get("/attempts/{attempt_id}", response_model=AttemptResponse)
def read_attempt(
    attempt_id: int, user: UserSummary = Depends(current_user), db: Session = Depends(get_db)
) -> AttemptResponse:
    """The attempt with its time left; reading it after the time limit auto-submits it
    with the answers saved so far (ST_11) and returns the result."""
    return _response(_own(db, attempt_id, user))


@router.put("/attempts/{attempt_id}/answers", response_model=AttemptResponse)
def save_answers(
    attempt_id: int,
    payload: AnswersRequest,
    user: UserSummary = Depends(current_user),
    db: Session = Depends(get_db),
) -> AttemptResponse:
    """Save answers as the user goes, so they count if the timer runs out.

    After the time limit nothing more is saved: the attempt is auto-submitted with
    what was saved before it and the result is returned.
    """
    found = _own(db, attempt_id, user)
    if found.status is AttemptStatus.SUBMITTED:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=ALREADY_SUBMITTED)
    try:
        attempts.save_answers(db, attempt_id, payload.answers)
    except KeyError as missing:
        db.rollback()
        raise _not_in_quiz(missing.args[0]) from None
    db.commit()
    return _response(attempts.state(db, attempt_id))


@router.post(
    "/attempts/{attempt_id}/submit",
    response_model=AttemptResponse,
    responses={
        409: {
            "model": UnansweredResponse,
            "description": 'Unanswered questions under the "warn" rule: confirm to submit',
        },
        422: {
            "model": UnansweredResponse,
            "description": 'Unanswered questions under the "block" rule',
        },
    },
)
def submit_attempt(
    attempt_id: int,
    payload: SubmitRequest,
    user: UserSummary = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Submit the attempt and get the score straight away (UT_11).

    Objective questions are scored deterministically, without the LLM. If questions
    are unanswered the response names how many, e.g. "2 questions are unanswered"
    (UT_12): under the ``warn`` rule submission waits for ``confirm: true`` (409 until
    then); under ``block`` it is refused (422) until they are answered. Answers in the
    request are saved either way. Once the time limit has passed the attempt is
    auto-submitted regardless of the rule; only answered questions are scored and the
    rest are marked unattempted (ST_11).
    """
    found = _own(db, attempt_id, user, attempts.SUBMIT_GRACE)
    if found.status is AttemptStatus.SUBMITTED:
        if found.auto_submitted:
            return _response(found)  # the timer already submitted it: show the result
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=ALREADY_SUBMITTED)

    rule = settings.quiz_unanswered_rule
    try:
        missing = attempts.submit(
            db,
            attempt_id,
            payload.answers,
            allow_unanswered=rule == "warn" and payload.confirm,
        )
    except KeyError as unknown:
        db.rollback()
        raise _not_in_quiz(unknown.args[0]) from None
    db.commit()
    current = attempts.state(db, attempt_id)
    if current.status is AttemptStatus.IN_PROGRESS:
        body = UnansweredResponse(
            detail=attempts.unanswered_warning(len(missing)),
            unanswered_count=len(missing),
            unanswered_question_ids=missing,
            unanswered_rule=rule,
            attempt=_response(current),
        )
        code = status.HTTP_409_CONFLICT if rule == "warn" else 422
        return JSONResponse(status_code=code, content=body.model_dump(mode="json"))
    return _response(current)
