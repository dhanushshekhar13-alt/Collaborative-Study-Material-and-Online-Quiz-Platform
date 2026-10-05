from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.modules.channels.models import ChannelType


class ChannelCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    subject: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=1000)
    channel_type: ChannelType = ChannelType.NORMAL

    @field_validator("name", "subject", mode="before")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("description", mode="before")
    @classmethod
    def strip_description(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None


class ChannelView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    subject: str
    description: str | None
    channel_type: ChannelType
    created_by_id: int
    created_at: datetime


class ChannelMembershipView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    channel_id: int
    user_id: int
    joined_at: datetime
