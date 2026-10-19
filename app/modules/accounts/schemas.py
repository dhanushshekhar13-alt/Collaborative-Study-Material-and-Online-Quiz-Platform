"""Request and response shapes for the accounts API."""

from datetime import datetime

from email_validator import EmailNotValidError, validate_email
from pydantic import BaseModel, Field, field_validator

from app.modules.accounts.models import Role

# Wording fixed by the Test Plan.
INVALID_EMAIL_MESSAGE = "Enter a valid email address"  # UT_02
INVALID_CREDENTIALS_MESSAGE = "Invalid username or password"  # IT_02
REGISTRATION_SUCCESS_MESSAGE = "Registration successful"  # UT_01

MIN_PASSWORD_LENGTH = 8


class RegistrationRequest(BaseModel):
    display_name: str = Field(min_length=1, max_length=100)
    username: str = Field(min_length=3, max_length=50, pattern=r"^[A-Za-z0-9_.-]+$")
    email: str = Field(max_length=255)
    password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=128)

    @field_validator("email")
    @classmethod
    def email_must_be_valid(cls, value: str) -> str:
        """Reject anything the email grammar does not accept, with the UT_02 wording."""
        try:
            return validate_email(value, check_deliverability=False).normalized.lower()
        except EmailNotValidError as exc:
            raise ValueError(INVALID_EMAIL_MESSAGE) from exc

    @field_validator("username")
    @classmethod
    def normalise_username(cls, value: str) -> str:
        return value.lower()

    @field_validator("display_name")
    @classmethod
    def strip_display_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Enter your name")
        return value


class AccountResponse(BaseModel):
    """A user as the API presents them. The password hash is never included."""

    id: int
    username: str | None
    email: str
    display_name: str
    role: Role
    created_at: datetime


class RegistrationResponse(BaseModel):
    message: str = REGISTRATION_SUCCESS_MESSAGE
    account: AccountResponse


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=255, description="Username or email")
    password: str = Field(min_length=1, max_length=128)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: datetime
    account: AccountResponse


class PermissionsResponse(BaseModel):
    role: Role
    capabilities: list[str]
