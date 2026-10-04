from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.modules.accounts.models import User
from app.modules.accounts.security import get_current_user, get_db
from app.modules.channels.models import Channel, ChannelMembership, ChannelType
from app.modules.channels.schemas import ChannelCreate, ChannelView

router = APIRouter(prefix="/channels", tags=["channels"])


@router.post("", response_model=ChannelView, status_code=status.HTTP_201_CREATED)
def create_channel(
    payload: ChannelCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Channel:
    if (
        payload.channel_type == ChannelType.AUTHORIZED
        and user.role.name not in {"authorized_user", "admin"}
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only authorized users/admins can create Authorized Channels",
        )

    channel = Channel(
        name=payload.name,
        subject=payload.subject,
        description=payload.description,
        channel_type=payload.channel_type,
        created_by_id=user.id,
    )
    db.add(channel)
    db.flush()
    db.add(ChannelMembership(channel_id=channel.id, user_id=user.id))
    db.commit()
    db.refresh(channel)
    return channel


@router.get("", response_model=list[ChannelView])
def list_channels(
    query: str | None = Query(default=None, max_length=120),
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> list[Channel]:
    statement = select(Channel)
    keyword = query.strip() if query else ""
    if keyword:
        pattern = f"%{keyword}%"
        statement = statement.where(
            or_(Channel.name.ilike(pattern), Channel.subject.ilike(pattern))
        )
    statement = statement.order_by(func.lower(Channel.name), Channel.id)
    return list(db.scalars(statement).all())
