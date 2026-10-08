from email_validator import EmailNotValidError, validate_email
from pydantic import AliasPath, BaseModel, ConfigDict, EmailStr, Field, field_validator
from pydantic_core import PydanticCustomError


class RegistrationRequest(BaseModel):
    username: str = Field(min_length=3, max_length=50, pattern=r"^[A-Za-z0-9_.-]+$")
    email: EmailStr
    password: str = Field(min_length=12, max_length=128)
    display_name: str = Field(min_length=1, max_length=120)

    @field_validator("email", mode="before")
    @classmethod
    def validate_email_format(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        try:
            return validate_email(value, check_deliverability=False).normalized
        except EmailNotValidError as exc:
            raise PydanticCustomError(
                "email_format", "Enter a valid email address"
            ) from exc

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


class ProfileUpdateRequest(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=120)
    bio: str | None = Field(default=None, max_length=500)

    @field_validator("display_name", "bio", mode="before")
    @classmethod
    def strip_profile_text(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value


class RegistrationResponse(BaseModel):
    message: str = "Registration successful"
    user: UserProfile


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
