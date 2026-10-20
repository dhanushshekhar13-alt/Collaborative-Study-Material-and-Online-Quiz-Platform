"""HTTP surface of the profiles module, mounted at ``/api/v1/profiles`` (S01.2)."""

from datetime import datetime

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Request,
    Response,
    UploadFile,
    status,
)
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.modules.accounts import service as accounts
from app.modules.accounts.service import UserSummary, current_user
from app.modules.profiles import service
from app.modules.profiles.service import ContributionType

router = APIRouter(prefix="/profiles", tags=["profiles"])

NOT_FOUND_MESSAGE = "Profile not found"
NO_PICTURE_MESSAGE = "This user has no profile picture"
MAX_BIO_LENGTH = 500


class ProfileUpdate(BaseModel):
    """Fields to change; omitted fields stay as they are, blank bio/institution clears it."""

    display_name: str | None = Field(default=None, min_length=1, max_length=100)
    bio: str | None = Field(default=None, max_length=MAX_BIO_LENGTH)
    institution: str | None = Field(default=None, max_length=150)

    @field_validator("display_name")
    @classmethod
    def display_name_not_blank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("Enter your name")
        return value


class ContributionResponse(BaseModel):
    type: ContributionType
    id: int
    title: str
    created_at: datetime


class ContributionsResponse(BaseModel):
    total: int
    materials: int
    questions: int
    quizzes: int
    recent: list[ContributionResponse]


class BadgeResponse(BaseModel):
    code: str
    name: str
    description: str | None
    earned_at: datetime


class ProfileResponse(BaseModel):
    """Everything the profile page shows. New users get zeros and empty lists."""

    id: int
    username: str | None
    display_name: str
    role: accounts.Role
    bio: str | None
    institution: str | None
    member_since: datetime
    picture_url: str | None = Field(
        description="Where to load the picture from; changes whenever the picture does."
    )
    reputation: int
    contributions: ContributionsResponse
    badges: list[BadgeResponse]


def _response(request: Request, profile: service.Profile) -> ProfileResponse:
    account = profile.account
    picture_url = None
    if account.picture_updated_at is not None:
        path = request.app.url_path_for("read_profile_picture", user_id=account.id)
        picture_url = f"{path}?v={int(account.picture_updated_at.timestamp() * 1000)}"
    c = profile.contributions
    return ProfileResponse(
        id=account.id,
        username=account.username,
        display_name=account.display_name,
        role=account.role,
        bio=account.bio,
        institution=account.institution,
        member_since=account.created_at,
        picture_url=picture_url,
        reputation=profile.reputation,
        contributions=ContributionsResponse(
            total=c.total,
            materials=c.materials,
            questions=c.questions,
            quizzes=c.quizzes,
            recent=[ContributionResponse(**vars(item)) for item in c.recent],
        ),
        badges=[BadgeResponse(**vars(badge)) for badge in profile.badges],
    )


def _profile_or_404(request: Request, db: Session, user_id: int) -> ProfileResponse:
    profile = service.build_profile(db, user_id)
    if profile is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NOT_FOUND_MESSAGE)
    return _response(request, profile)


@router.get("/me", response_model=ProfileResponse)
def read_my_profile(
    request: Request, user: UserSummary = Depends(current_user), db: Session = Depends(get_db)
) -> ProfileResponse:
    """The signed-in user's profile: details, contributions, reputation and badges."""
    return _profile_or_404(request, db, user.id)


@router.patch("/me", response_model=ProfileResponse)
def update_my_profile(
    payload: ProfileUpdate,
    request: Request,
    user: UserSummary = Depends(current_user),
    db: Session = Depends(get_db),
) -> ProfileResponse:
    """Edit display name, bio or institution. Saved immediately and kept across sessions."""
    changes = {name: getattr(payload, name) for name in payload.model_fields_set}
    if changes.get("display_name", "") is None:
        del changes["display_name"]
    accounts.update_profile(db, user.id, **changes)
    db.commit()
    return _profile_or_404(request, db, user.id)


@router.put("/me/picture", response_model=ProfileResponse)
async def upload_my_picture(
    request: Request,
    file: UploadFile = File(description="JPEG, PNG, GIF or WebP, at most 2 MB"),
    user: UserSummary = Depends(current_user),
    db: Session = Depends(get_db),
) -> ProfileResponse:
    """Set or replace the profile picture. Anything not an image, or over 2 MB, is refused."""
    # Read one byte past the limit so an oversized file is detected without loading it all.
    data = await file.read(accounts.MAX_PICTURE_BYTES + 1)
    try:
        accounts.set_profile_picture(db, user.id, data)
    except accounts.InvalidPictureError as exc:
        db.rollback()
        code = (
            status.HTTP_413_CONTENT_TOO_LARGE
            if exc.too_large
            else status.HTTP_422_UNPROCESSABLE_CONTENT
        )
        raise HTTPException(status_code=code, detail=str(exc)) from exc
    db.commit()
    return _profile_or_404(request, db, user.id)


@router.delete("/me/picture", response_model=ProfileResponse)
def remove_my_picture(
    request: Request, user: UserSummary = Depends(current_user), db: Session = Depends(get_db)
) -> ProfileResponse:
    """Remove the profile picture; the UI falls back to initials."""
    accounts.remove_profile_picture(db, user.id)
    db.commit()
    return _profile_or_404(request, db, user.id)


@router.get("/{user_id}", response_model=ProfileResponse)
def read_profile(
    user_id: int,
    request: Request,
    _: UserSummary = Depends(current_user),
    db: Session = Depends(get_db),
) -> ProfileResponse:
    """Another user's profile, as any signed-in user sees it. Email is never included."""
    return _profile_or_404(request, db, user_id)


@router.get(
    "/{user_id}/picture",
    response_class=Response,
    responses={200: {"content": {"image/*": {}}, "description": "The picture"}},
)
def read_profile_picture(user_id: int, db: Session = Depends(get_db)) -> Response:
    """The picture's bytes. Public so an ``<img>`` tag can load it without a token."""
    picture = accounts.get_profile_picture(db, user_id)
    if picture is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NO_PICTURE_MESSAGE)
    return Response(
        content=picture.data,
        media_type=picture.content_type,
        headers={
            "Cache-Control": "public, max-age=300",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'",
        },
    )
