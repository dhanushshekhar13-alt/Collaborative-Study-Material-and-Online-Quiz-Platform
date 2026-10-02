from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.accounts.models import Role, User
from app.modules.accounts.schemas import (
    LoginRequest,
    RegistrationRequest,
    TokenResponse,
    UserProfile,
)
from app.modules.accounts.security import (
    create_access_token,
    get_current_user,
    get_db,
    hash_password,
    verify_password,
)

router = APIRouter(prefix="/auth", tags=["accounts"])


@router.post("/register", response_model=UserProfile, status_code=status.HTTP_201_CREATED)
def register(payload: RegistrationRequest, db: Session = Depends(get_db)) -> User:
    username = payload.username.strip()
    email = str(payload.email).strip().lower()
    duplicate = db.scalar(
        select(User.id).where(
            or_(func.lower(User.username) == username.lower(), func.lower(User.email) == email)
        )
    )
    if duplicate is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A user with that username or email already exists",
        )

    student_role = db.scalar(select(Role).where(Role.name == "student"))
    if student_role is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Account roles are not initialized; apply the database migrations",
        )

    user = User(
        username=username,
        email=email,
        password_hash=hash_password(payload.password),
        display_name=payload.display_name.strip(),
        role_id=student_role.id,
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A user with that username or email already exists",
        ) from exc
    db.refresh(user)
    return user


@router.post("/token", response_model=TokenResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)) -> TokenResponse:
    identity = payload.username_or_email
    user = db.scalar(
        select(User)
        .where(
            or_(User.username == identity, func.lower(User.email) == identity.lower())
        )
    )
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    access_token, lifetime = create_access_token(user.id)
    return TokenResponse(access_token=access_token, expires_in=lifetime)


@router.get("/me", response_model=UserProfile)
def read_current_user(user: User = Depends(get_current_user)) -> User:
    return user
