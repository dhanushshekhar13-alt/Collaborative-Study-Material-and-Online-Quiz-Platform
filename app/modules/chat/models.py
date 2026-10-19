"""Chat data (FR-10, FR-11): channel threads, direct conversations, blocks, shares."""

import enum

from sqlalchemy import CheckConstraint, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.schema import CreatedAtMixin, enum_column


class SharedResourceType(enum.StrEnum):
    MATERIAL = "material"
    QUESTION = "question"
    QUIZ = "quiz"


class ChannelMessage(CreatedAtMixin, Base):
    __tablename__ = "channel_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    channel_id: Mapped[int] = mapped_column(
        ForeignKey("channels.id", ondelete="CASCADE"), index=True
    )
    sender_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("channel_messages.id", ondelete="CASCADE"), index=True
    )
    body: Mapped[str] = mapped_column(Text)
    # A shared material/question/quiz stays a link to the original (FR-11).
    shared_resource_type: Mapped[SharedResourceType | None] = mapped_column(
        enum_column(SharedResourceType, "channel_message_resource")
    )
    shared_resource_id: Mapped[int | None] = mapped_column(Integer)
    is_deleted: Mapped[bool] = mapped_column(default=False, server_default="0")


class Conversation(CreatedAtMixin, Base):
    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(primary_key=True)


class ConversationParticipant(Base):
    __tablename__ = "conversation_participants"
    __table_args__ = (
        UniqueConstraint("conversation_id", "user_id", name="uq_conversation_participant"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)


class DirectMessage(CreatedAtMixin, Base):
    __tablename__ = "direct_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    sender_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    body: Mapped[str] = mapped_column(Text)
    shared_resource_type: Mapped[SharedResourceType | None] = mapped_column(
        enum_column(SharedResourceType, "direct_message_resource")
    )
    shared_resource_id: Mapped[int | None] = mapped_column(Integer)


class UserBlock(CreatedAtMixin, Base):
    __tablename__ = "user_blocks"
    __table_args__ = (
        UniqueConstraint("blocker_id", "blocked_id", name="uq_user_block"),
        CheckConstraint("blocker_id <> blocked_id", name="ck_user_block_not_self"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    blocker_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    blocked_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    reason: Mapped[str | None] = mapped_column(String(255))
