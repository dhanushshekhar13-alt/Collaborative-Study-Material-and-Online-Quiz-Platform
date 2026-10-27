"""Public interface of the moderation queue module (S13.2): who may moderate what.

The rule lives here so the queue, the actions and any UI that asks agree:

* an admin (``report:resolve``) moderates every report on the platform;
* in an approved Authorized Channel, the members who manage its official content
  (authorized users) moderate its reports, and regular members never do (ST_25);
* in a Normal Channel, the channel's creator and its moderators do.

A report with no channel (content posted outside any channel) is for admins only.
"""

from sqlalchemy.orm import Session

from app.modules.accounts.service import Capability, UserSummary, capabilities_for
from app.modules.channels import service as channels
from app.modules.channels.service import ChannelType


def is_platform_moderator(user: UserSummary) -> bool:
    """Admins resolve reports anywhere."""
    return Capability.REPORT_RESOLVE in capabilities_for(user.role)


def can_moderate_channel(db: Session, user: UserSummary, channel_id: int | None) -> bool:
    """Whether ``user`` may act on reports scoped to ``channel_id``."""
    if is_platform_moderator(user):
        return True
    if channel_id is None:
        return False
    summary = channels.describe_channel(db, channel_id)
    if summary is None:
        return False
    if summary.channel_type is ChannelType.AUTHORIZED:
        return channels.manages_official_content(
            summary,
            is_member=channels.is_member(db, channel_id, user.id),
            has_capability=Capability.OFFICIAL_CONTENT_MANAGE in capabilities_for(user.role),
        )
    return channels.can_manage_members(channels.member_role(db, channel_id, user.id))


def moderated_channel_ids(db: Session, user: UserSummary) -> list[int] | None:
    """The channels whose reports ``user`` moderates; ``None`` means every channel."""
    if is_platform_moderator(user):
        return None
    return [
        summary.id
        for summary, _role in channels.channels_for_member(db, user.id)
        if can_moderate_channel(db, user, summary.id)
    ]
