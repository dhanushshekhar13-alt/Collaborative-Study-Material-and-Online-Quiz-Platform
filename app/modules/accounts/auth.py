"""FastAPI route guards. Other modules reach these through ``accounts.service``.

Guards hand routes a ``UserSummary`` dataclass, never the ORM row, in line with the
module rules in ``docs/architecture.md``.
"""

from collections.abc import Callable

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.modules.accounts.models import Role, User
from app.modules.accounts.permissions import Capability, role_has
from app.modules.accounts.tokens import InvalidTokenError, decode_access_token

# ``auto_error=False`` so a missing header gets our own 401 with a Bearer challenge,
# rather than FastAPI's default 403.
_bearer = HTTPBearer(auto_error=False)

UNAUTHENTICATED_MESSAGE = "Not authenticated"
UNAUTHORIZED_MESSAGE = "Unauthorized"  # IT_28


def _unauthenticated(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def _forbidden() -> HTTPException:
    return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=UNAUTHORIZED_MESSAGE)


def current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
):
    """Resolve the caller from their bearer token, or reject the request with 401.

    The account is re-read on every request, so a suspension or role change takes
    effect immediately rather than when the token expires.
    """
    # Imported here: service re-exports this module, so a top-level import would cycle.
    from app.modules.accounts.service import _summary

    if credentials is None or not credentials.credentials:
        raise _unauthenticated(UNAUTHENTICATED_MESSAGE)
    try:
        claims = decode_access_token(credentials.credentials)
    except InvalidTokenError as exc:
        raise _unauthenticated(str(exc)) from exc

    user = db.get(User, claims.user_id)
    if user is None or user.is_suspended:
        raise _unauthenticated("Account is not active")
    return _summary(user)


def require_roles(*allowed: Role) -> Callable:
    """Guard a route so only ``allowed`` roles reach it; others get 403 Unauthorized."""

    def guard(user=Depends(current_user)):
        if user.role not in allowed:
            raise _forbidden()
        return user

    return guard


def require_capability(capability: Capability) -> Callable:
    """Guard a route by what the caller may do rather than by role name.

    Prefer this to ``require_roles``: if the capability is later granted to another
    role, the route keeps working without being edited.
    """

    def guard(user=Depends(current_user)):
        if not role_has(user.role, capability):
            raise _forbidden()
        return user

    return guard
