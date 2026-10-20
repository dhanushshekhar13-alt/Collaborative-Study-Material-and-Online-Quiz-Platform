"""Public interface of the profiles module (S01.2).

``build_profile`` gathers everything the profile page shows in one read model. Counts
come straight from the owning modules' records, so the page always matches the
backend (ST_02); nothing here is cached or copied.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from sqlalchemy.orm import Session

from app.modules.accounts import service as accounts
from app.modules.materials import service as materials
from app.modules.questions import service as questions
from app.modules.quizzes import service as quizzes
from app.modules.reputation import service as reputation

RECENT_CONTRIBUTIONS = 10


class ContributionType(StrEnum):
    MATERIAL = "material"
    QUESTION = "question"
    QUIZ = "quiz"


@dataclass(frozen=True)
class Contribution:
    type: ContributionType
    id: int
    title: str
    created_at: datetime


@dataclass(frozen=True)
class Contributions:
    materials: int
    questions: int
    quizzes: int
    recent: list[Contribution]

    @property
    def total(self) -> int:
        return self.materials + self.questions + self.quizzes


@dataclass(frozen=True)
class Profile:
    account: accounts.ProfileDetails
    reputation: int
    contributions: Contributions
    badges: list[reputation.EarnedBadge]


def contributions_for(
    db: Session, user_id: int, limit: int = RECENT_CONTRIBUTIONS
) -> Contributions:
    """Counts per kind plus the newest ``limit`` contributions of any kind."""
    recent = [
        *(
            Contribution(ContributionType.MATERIAL, c.id, c.title, c.created_at)
            for c in materials.recent_uploaded_by(db, user_id, limit)
        ),
        *(
            Contribution(ContributionType.QUESTION, c.id, c.title, c.created_at)
            for c in questions.recent_authored_by(db, user_id, limit)
        ),
        *(
            Contribution(ContributionType.QUIZ, c.id, c.title, c.created_at)
            for c in quizzes.recent_created_by(db, user_id, limit)
        ),
    ]
    recent.sort(key=lambda c: (c.created_at, c.id), reverse=True)
    return Contributions(
        materials=materials.count_uploaded_by(db, user_id),
        questions=questions.count_authored_by(db, user_id),
        quizzes=quizzes.count_created_by(db, user_id),
        recent=recent[:limit],
    )


def build_profile(db: Session, user_id: int) -> Profile | None:
    """The full profile for ``user_id``, or ``None`` if there is no such user."""
    account = accounts.get_profile(db, user_id)
    if account is None:
        return None
    return Profile(
        account=account,
        reputation=reputation.total(db, user_id),
        contributions=contributions_for(db, user_id),
        badges=reputation.badges_for(db, user_id),
    )
