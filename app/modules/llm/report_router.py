"""Reporting AI output (NFR-06), mounted under ``/api/v1/ai``.

    POST /ai/responses/{id}/reports    report an AI explanation, generated batch,
                                       topic quiz or answer evaluation

``{id}`` is the id every AI response already carries: ``id`` from ``POST /ai/ask``, a
batch's ``id`` from ``/ai/generations`` and ``ai_response_id`` on a descriptive
evaluation. The report lands in the moderation queue (``/api/v1/moderation/queue``)
as ``target_type: "ai_response"``, scoped to the request's channel so that channel's
moderators see it; one asked outside any channel goes to admins. A moderator who
upholds it withdraws the output (Remove Content), after which its topic quiz and
review screen can no longer be opened.

AI-generated questions that already joined a bank are reported like any other
question (``POST /questions/{id}/reports``); the queue marks them ``ai_generated``.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.modules.accounts.service import Capability, UserSummary, require_capability
from app.modules.llm import service
from app.modules.moderation.service import (
    ReportReason,
    ReportTarget,
    file_report,
    find_open_report,
)

router = APIRouter(prefix="/ai", tags=["ai"])

RESPONSE_NOT_FOUND = "AI response not found"
REPORT_LOGGED = "Report submitted for moderation"


class AiReportRequest(BaseModel):
    reason: ReportReason = Field(
        description='"incorrect" for a wrong explanation, answer key or score; '
        '"inappropriate" for unsuitable content'
    )
    details: str | None = Field(default=None, max_length=2000)


class AiReportResponse(BaseModel):
    report_id: int
    ai_response_id: int
    reason: ReportReason
    status: str
    message: str


@router.post(
    "/responses/{response_id}/reports",
    status_code=status.HTTP_201_CREATED,
    response_model=AiReportResponse,
    responses={404: {"description": "No such AI response, or it is someone else's"}},
)
def report_ai_response(
    response_id: int,
    payload: AiReportRequest,
    user: UserSummary = Depends(require_capability(Capability.REPORT_SUBMIT)),
    db: Session = Depends(get_db),
) -> AiReportResponse:
    """Report incorrect or inappropriate AI-generated content to moderation (NFR-06).

    Only the person the AI answered can report it (an AI response is private, so
    anyone else gets 404). The report is captured as open, with the reason and details,
    and routed to the moderators of the request's channel (admins when there is none).
    Reporting the same response again while the first report is open returns that
    report instead of filing a duplicate.
    """
    found = service.get_response(db, response_id)
    if found is None or found.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=RESPONSE_NOT_FOUND)
    report_id = find_open_report(
        db, reporter_id=user.id, target_type=ReportTarget.AI_RESPONSE, target_id=response_id
    )
    if report_id is None:
        report_id = file_report(
            db,
            reporter_id=user.id,
            target_type=ReportTarget.AI_RESPONSE,
            target_id=response_id,
            reason=payload.reason,
            details=(payload.details or "").strip() or None,
            channel_id=found.channel_id,
        )
        db.commit()
    return AiReportResponse(
        report_id=report_id,
        ai_response_id=response_id,
        reason=payload.reason,
        status="open",
        message=REPORT_LOGGED,
    )
