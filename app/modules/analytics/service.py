"""Public interface of the analytics module (FR-12).

Analytics owns no tables: it derives performance from the quizzes module's attempt
results, so it can be rebuilt or changed without touching any stored data.
"""

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.modules.quizzes import service as quizzes


@dataclass(frozen=True)
class PerformanceSummary:
    attempts: int
    total_score: float
    total_possible: float

    @property
    def accuracy(self) -> float:
        return self.total_score / self.total_possible if self.total_possible else 0.0


def summary_for_user(db: Session, user_id: int) -> PerformanceSummary:
    results = quizzes.attempts_for_user(db, user_id)
    return PerformanceSummary(
        attempts=len(results),
        total_score=sum(r.score for r in results),
        total_possible=sum(r.max_score for r in results),
    )
