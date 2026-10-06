from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.accounts.models import User
from app.modules.accounts.security import get_current_user, get_db
from app.modules.channels.models import Channel, ChannelMembership, ChannelType
from app.modules.channels.schemas import (
    ChannelCreate,
    ChannelMemberView,
    ChannelMembershipView,
    ChannelView,
)

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


@router.get("/mine", response_model=list[ChannelView])
def list_my_channels(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[Channel]:
    statement = (
        select(Channel)
        .join(ChannelMembership, ChannelMembership.channel_id == Channel.id)
        .where(ChannelMembership.user_id == user.id)
        .order_by(func.lower(Channel.name), Channel.id)
    )
    return list(db.scalars(statement).all())


@router.post(
    "/{channel_id}/memberships",
    response_model=ChannelMembershipView,
    status_code=status.HTTP_201_CREATED,
)
def join_channel(
    channel_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ChannelMembership:
    channel = db.get(Channel, channel_id)
    if channel is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Channel not found")

    membership = db.scalar(
        select(ChannelMembership).where(
            ChannelMembership.channel_id == channel_id,
            ChannelMembership.user_id == user.id,
        )
    )
    if membership is not None:
        return membership

    membership = ChannelMembership(channel_id=channel_id, user_id=user.id)
    db.add(membership)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        membership = db.scalar(
            select(ChannelMembership).where(
                ChannelMembership.channel_id == channel_id,
                ChannelMembership.user_id == user.id,
            )
        )
        if membership is None:
            raise
        return membership
    db.refresh(membership)
    return membership


@router.delete("/{channel_id}/memberships/me", status_code=status.HTTP_204_NO_CONTENT)
def leave_channel(
    channel_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> None:
    membership = db.scalar(
        select(ChannelMembership).where(
            ChannelMembership.channel_id == channel_id,
            ChannelMembership.user_id == user.id,
        )
    )
    if membership is None:
        channel_exists = db.get(Channel, channel_id) is not None
        detail = "You are not a member of this channel" if channel_exists else "Channel not found"
        code = status.HTTP_409_CONFLICT if channel_exists else status.HTTP_404_NOT_FOUND
        raise HTTPException(status_code=code, detail=detail)
    db.delete(membership)
    db.commit()


@router.get("/{channel_id}/members", response_model=list[ChannelMemberView])
def list_channel_members(
    channel_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[ChannelMemberView]:
    channel = db.get(Channel, channel_id)
    if channel is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Channel not found")

    is_moderator = user.role.name in {"moderator", "admin"}
    is_creator = channel.created_by_id == user.id
    is_member = db.scalar(
        select(ChannelMembership.id).where(
            ChannelMembership.channel_id == channel_id,
            ChannelMembership.user_id == user.id,
        )
    ) is not None
    if not (is_moderator or is_creator or is_member):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Join this channel to view its members",
        )

    rows = db.execute(
        select(
            User.id,
            User.username,
            User.display_name,
            ChannelMembership.joined_at,
        )
        .join(ChannelMembership, ChannelMembership.user_id == User.id)
        .where(ChannelMembership.channel_id == channel_id)
        .order_by(func.lower(User.display_name), User.id)
    )
    return [
        ChannelMemberView(
            user_id=row.id,
            username=row.username,
            display_name=row.display_name,
            joined_at=row.joined_at,
        )
        for row in rows
    ]


@router.delete(
    "/{channel_id}/members/{member_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def remove_channel_member(
    channel_id: int,
    member_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> None:
    channel = db.get(Channel, channel_id)
    if channel is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Channel not found")
    if channel.created_by_id != user.id and user.role.name not in {"moderator", "admin"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the channel creator, moderators, or admins can remove members",
        )

    membership = db.scalar(
        select(ChannelMembership).where(
            ChannelMembership.channel_id == channel_id,
            ChannelMembership.user_id == member_id,
        )
    )
    if membership is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Channel member not found",
        )
    db.delete(membership)
    db.commit()
