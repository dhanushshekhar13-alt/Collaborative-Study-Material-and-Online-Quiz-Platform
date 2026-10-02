from pydantic import AliasPath, BaseModel, ConfigDict, EmailStr, Field, field_validator


class RegistrationRequest(BaseModel):
    username: str = Field(min_length=3, max_length=50, pattern=r"^[A-Za-z0-9_.-]+$")
    email: EmailStr
    password: str = Field(min_length=12, max_length=128)
    display_name: str = Field(min_length=1, max_length=120)

    @field_validator("username", "display_name", mode="before")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()


class LoginRequest(BaseModel):
    username_or_email: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=1, max_length=128)

    @field_validator("username_or_email")
    @classmethod
    def strip_identity(cls, value: str) -> str:
        return value.strip()


class UserProfile(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    email: EmailStr
    display_name: str
    bio: str | None
    role: str = Field(validation_alias=AliasPath("role", "name"))


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
