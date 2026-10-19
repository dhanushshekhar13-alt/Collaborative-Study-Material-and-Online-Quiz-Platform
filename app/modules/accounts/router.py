"""HTTP surface of the accounts module, mounted at ``/api/v1/accounts``."""

from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.modules.accounts import service
from app.modules.accounts.permissions import Capability, capabilities_for
from app.modules.accounts.schemas import (
    INVALID_CREDENTIALS_MESSAGE,
    AccountResponse,
    LoginRequest,
    PermissionsResponse,
    RegistrationRequest,
    RegistrationResponse,
    TokenResponse,
)
from app.modules.accounts.service import UserSummary, current_user, require_capability
from app.modules.accounts.tokens import create_access_token, decode_access_token

router = APIRouter(prefix="/accounts", tags=["accounts"])


def _account(details: service.AccountDetails) -> AccountResponse:
    return AccountResponse(**asdict(details))


@router.post("/register", status_code=status.HTTP_201_CREATED, response_model=RegistrationResponse)
def register(payload: RegistrationRequest, db: Session = Depends(get_db)) -> RegistrationResponse:
    """Create a Student account and confirm it (UT_01)."""
    try:
        details = service.register_account(
            db,
            display_name=payload.display_name,
            username=payload.username,
            email=payload.email,
            password=payload.password,
        )
        db.commit()
    except service.DuplicateAccountError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except IntegrityError as exc:
        # Two sign-ups for the same name at the same moment: the unique index decides.
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This username or email is already registered",
        ) from exc
    return RegistrationResponse(account=_account(details))


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)) -> TokenResponse:
    """Exchange a username (or email) and password for a time-limited token."""
    user = service.authenticate(db, payload.username, payload.password)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=INVALID_CREDENTIALS_MESSAGE,
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = create_access_token(user.id, user.role.value)
    return TokenResponse(
        access_token=token,
        expires_at=decode_access_token(token).expires_at,
        account=_account(service.get_account(db, user.id)),
    )


@router.get("/me", response_model=AccountResponse)
def read_me(
    user: UserSummary = Depends(current_user), db: Session = Depends(get_db)
) -> AccountResponse:
    """The signed-in user's own account; the first thing the dashboard loads."""
    return _account(service.get_account(db, user.id))


@router.get("/me/permissions", response_model=PermissionsResponse)
def read_my_permissions(user: UserSummary = Depends(current_user)) -> PermissionsResponse:
    """What this user may do, so the UI can render the right controls.

    A convenience for the client only: every protected route re-checks on the server,
    so a client that ignores this list gains nothing.
    """
    return PermissionsResponse(
        role=user.role,
        capabilities=sorted(capability.value for capability in capabilities_for(user.role)),
    )


@router.get(
    "/admin/users",
    response_model=list[AccountResponse],
    dependencies=[Depends(require_capability(Capability.USER_MANAGE))],
)
def list_users(db: Session = Depends(get_db)) -> list[AccountResponse]:
    """Admin-only roster (IT_28). Full admin management of users is FR-14."""
    return [_account(details) for details in service.list_accounts(db)]


@router.get("/session-policy", tags=["operations"])
def session_policy() -> dict[str, int]:
    """How long an issued token stays valid, in minutes."""
    return {"access_token_expire_minutes": settings.access_token_expire_minutes}
