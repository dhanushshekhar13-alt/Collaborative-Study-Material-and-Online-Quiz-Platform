from fastapi import Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.accounts.models import User
from app.modules.accounts.security import get_current_user, get_db
from app.modules.channels.models import Channel, ChannelMembership


def require_channel_access(
    channel_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Channel:
    """Return a channel only to its members, its creator, or moderators/admins."""
    channel = db.get(Channel, channel_id)
    if channel is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Channel not found")

    is_privileged = user.role.name in {"moderator", "admin"}
    is_creator = channel.created_by_id == user.id
    is_member = db.scalar(
        select(ChannelMembership.id).where(
            ChannelMembership.channel_id == channel_id,
            ChannelMembership.user_id == user.id,
        )
    ) is not None
    if not (is_privileged or is_creator or is_member):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Join this channel to access its content",
        )
    return channel
