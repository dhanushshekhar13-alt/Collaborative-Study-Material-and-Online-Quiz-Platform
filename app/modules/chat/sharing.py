"""In-chat sharing (S11.2, FR-11): a shared resource is a live link, not a copy.

A message stores only the resource's type and id (``shared_resource_type`` and
``shared_resource_id``). The preview card is built from the original every time the
message is read, so an edited title shows up in old messages and the link keeps
working; a resource that was removed, or that the reader may not open, shows an
"unavailable" card instead of leaking its content.

The read rules mirror the owning modules' own ``GET`` endpoints, which still check
access when the link is opened:

* material: active, and for a channel material the reader is a member (or an admin);
* question: published (drafts and removed questions only for their author or an
  admin), in a channel the reader can see;
* quiz: published (a creator also sees their own draft), not an adaptive run, and for
  a channel quiz the reader is a member (or an admin).
"""

from dataclasses import dataclass

from fastapi import HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.modules.accounts.service import Capability, UserSummary, capabilities_for
from app.modules.channels import service as channels
from app.modules.chat.models import SharedResourceType
from app.modules.materials import service as materials
from app.modules.questions import service as questions
from app.modules.quizzes import service as quizzes

UNAVAILABLE = "This item is no longer available"
SHARED_NOT_FOUND = "Shared item not found"
_TITLE_LIMIT = 120
_MATERIAL_LABELS = {
    "notes": "Notes",
    "pdf": "PDF",
    "presentation": "Presentation",
    "link": "Link",
    "other": "Material",
}


@dataclass(frozen=True)
class SharedCard:
    """The preview card a reader sees for a shared resource."""

    type: SharedResourceType
    id: int
    available: bool
    title: str | None = None
    subtitle: str | None = None
    # Where opening the card goes: the web UI route and the API resource it reads.
    web_path: str | None = None
    api_path: str | None = None
    channel_id: int | None = None


def _is_admin(user: UserSummary) -> bool:
    return Capability.CHANNEL_APPROVE in capabilities_for(user.role)


def _channel_visible(db: Session, channel_id: int, user: UserSummary) -> bool:
    summary = channels.describe_channel(db, channel_id)
    return summary is not None and channels.is_visible_to(summary, user.id, _is_admin(user))


def _channel_member(db: Session, channel_id: int, user: UserSummary) -> bool:
    if not _channel_visible(db, channel_id, user):
        return False
    return _is_admin(user) or channels.is_member(db, channel_id, user.id)


def _headline(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= _TITLE_LIMIT else text[: _TITLE_LIMIT - 1].rstrip() + "…"


def _subtitle(*parts: str | None) -> str:
    return " · ".join(p for p in parts if p)


def _material(db: Session, material_id: int, user: UserSummary) -> SharedCard | None:
    m = materials.describe_material(db, material_id)
    if m is None or (m.channel_id is not None and not _channel_member(db, m.channel_id, user)):
        return None
    return SharedCard(
        SharedResourceType.MATERIAL,
        m.id,
        True,
        title=m.title,
        subtitle=_subtitle(_MATERIAL_LABELS.get(m.material_type.value), m.subject, m.topic),
        web_path=f"#/materials/{m.id}",
        api_path=f"/api/v1/materials/{m.id}",
        channel_id=m.channel_id,
    )


def _question(db: Session, question_id: int, user: UserSummary) -> SharedCard | None:
    q = questions.get_bank_question(db, question_id)
    if q is None:
        return None
    if q.status is not questions.QuestionStatus.PUBLISHED and not (
        q.author_id == user.id or _is_admin(user)
    ):
        return None
    if q.channel_id is not None and not _channel_visible(db, q.channel_id, user):
        return None
    # The card shows the question, never its answer key.
    return SharedCard(
        SharedResourceType.QUESTION,
        q.question.id,
        True,
        title=_headline(q.question.text),
        subtitle=_subtitle(q.question.subject, q.question.topic, q.question.difficulty.value),
        web_path=f"#/questions/{q.question.id}",
        api_path=f"/api/v1/questions/{q.question.id}",
        channel_id=q.channel_id,
    )


def _quiz(db: Session, quiz_id: int, user: UserSummary) -> SharedCard | None:
    quiz = quizzes.describe_quiz(db, quiz_id)
    if quiz is None or quiz.is_adaptive or quiz.status is quizzes.QuizStatus.REMOVED:
        return None
    own = quiz.creator_id == user.id
    if quiz.status is not quizzes.QuizStatus.PUBLISHED and not own:
        return None
    if quiz.channel_id is not None and not own and not _channel_member(db, quiz.channel_id, user):
        return None
    return SharedCard(
        SharedResourceType.QUIZ,
        quiz.id,
        True,
        title=quiz.title,
        subtitle=_subtitle(
            quiz.subject,
            quiz.topic,
            f"{quiz.question_count} questions",
            f"{quiz.duration_minutes} min",
        ),
        # Straight to the quiz's start screen (ST_22).
        web_path=f"#/quizzes/{quiz.id}/attempt",
        api_path=f"/api/v1/quizzes/{quiz.id}",
        channel_id=quiz.channel_id,
    )


_RESOLVERS = {
    SharedResourceType.MATERIAL: _material,
    SharedResourceType.QUESTION: _question,
    SharedResourceType.QUIZ: _quiz,
}


def readable_card(
    db: Session, resource_type: SharedResourceType, resource_id: int, user: UserSummary
) -> SharedCard | None:
    """The card if ``user`` may open the resource now, else None (it is not revealed)."""
    return _RESOLVERS[resource_type](db, resource_id, user)


def card_for(
    db: Session, resource_type: SharedResourceType, resource_id: int, user: UserSummary
) -> SharedCard:
    """The card a reader sees: live details, or an unavailable card with no content."""
    card = readable_card(db, resource_type, resource_id, user)
    return card or SharedCard(resource_type, resource_id, False, title=None, subtitle=UNAVAILABLE)


# --------------------------------------------------------------------------- #
# Request and response shapes, used by the channel and direct chat routers
# --------------------------------------------------------------------------- #


class SharedResourceRequest(BaseModel):
    type: SharedResourceType = Field(description='"material", "question" or "quiz"')
    id: int = Field(ge=1)


class SharedCardResponse(BaseModel):
    type: SharedResourceType
    id: int
    # False when the item was removed or the reader may not open it; the card then
    # carries no content, only the "no longer available" note in ``subtitle``.
    available: bool
    title: str | None
    subtitle: str | None
    web_path: str | None = Field(description="Web UI route that opens the original")
    api_path: str | None = Field(description="API resource of the original")
    channel_id: int | None


def card_response(card: SharedCard) -> SharedCardResponse:
    return SharedCardResponse(
        type=card.type,
        id=card.id,
        available=card.available,
        title=card.title,
        subtitle=card.subtitle,
        web_path=card.web_path,
        api_path=card.api_path,
        channel_id=card.channel_id,
    )


def checked_share(
    db: Session, share: SharedResourceRequest | None, user: UserSummary
) -> tuple[SharedResourceType, int] | None:
    """The resource to attach to a new message; 404 unless the sender may open it."""
    if share is None:
        return None
    if readable_card(db, share.type, share.id, user) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=SHARED_NOT_FOUND)
    return share.type, share.id


def message_card(
    db: Session,
    resource_type: SharedResourceType | None,
    resource_id: int | None,
    user: UserSummary,
) -> SharedCardResponse | None:
    if resource_type is None or resource_id is None:
        return None
    return card_response(card_for(db, resource_type, resource_id, user))
