"""Recommendations targeting weak topics (S12.2, FR-12, UC-13).

How the list is built
---------------------
1. **Focus topics** come from the performance figures (``service.dashboard``, which
   reconciles them with the raw attempts first, so a just-finished attempt counts):
   every topic below ``WEAK_BELOW`` accuracy. That is the dashboard's weak topics plus
   a topic with a single low-scoring attempt (IT_24: one 30% quiz is enough to start
   recommending for it, while the dashboard still waits for ``MIN_ATTEMPTS`` before
   *labelling* it). Weakest first.
2. **Candidates** for each focus topic are the peer-shared materials, questions and
   quizzes the user can see (general content plus their channels) tagged with that
   subject and topic. The user's own contributions are never recommended.
3. **Ranking**: items the user has not consumed come first, grouped by focus topic
   (weakest topic first) and alternating quiz, material, question within a topic, each
   kind best first (quizzes by attempts, materials and questions by rating). Items the
   user already consumed follow at the end, so they are de-prioritised, not hidden.
   *Consumed* means: a quiz attempted, a question answered in a quiz, a material rated,
   reviewed or marked helpful, or any item opened from the recommendations.
4. **No weak topics** (UC-13 alternate flow): popular items from the same scope.

Nothing is stored except which recommended items were opened; the list is worked out
on every request, so it refreshes as soon as new performance data arrives.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from itertools import zip_longest

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.analytics.models import RecommendationOpen
from app.modules.channels import service as channels
from app.modules.materials import service as materials
from app.modules.questions import service as questions
from app.modules.quizzes import service as quizzes

# Unconsumed items taken per focus topic before moving on to the next topic.
PER_TOPIC = 6
# Candidates read per kind and topic before ranking.
CANDIDATES = 20

GENERAL_NO_ATTEMPTS = (
    "Attempt a quiz so we can find your weak topics. Meanwhile, here are popular picks."
)
GENERAL_NO_WEAK = "No weak topics right now. Here are popular picks to keep you going."
GENERAL_NOTHING_SHARED = (
    "Nothing has been shared yet for your weak topics ({topics}). Here are popular picks meanwhile."
)
TARGETED = "Picked for the topics you are finding hardest."


class ItemType(StrEnum):
    MATERIAL = "material"
    QUESTION = "question"
    QUIZ = "quiz"


class Mode(StrEnum):
    TARGETED = "targeted"
    GENERAL = "general"


@dataclass(frozen=True)
class FocusTopic:
    subject: str
    topic: str
    accuracy: float
    attempts: int
    reason: str


@dataclass(frozen=True)
class Recommendation:
    item_type: ItemType
    item_id: int
    title: str
    subject: str
    topic: str | None
    channel_id: int | None
    consumed: bool
    reason: str
    rating_average: float | None = None
    rating_count: int = 0
    attempt_count: int | None = None
    difficulty: str | None = None
    api_path: str = ""


@dataclass(frozen=True)
class Recommendations:
    mode: Mode
    message: str
    focus_topics: list[FocusTopic]
    items: list[Recommendation]


def _headline(text: str, limit: int = 90) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def focus_topics(topics) -> list[FocusTopic]:
    """Topics below the weak threshold, weakest first (ties: more attempts first)."""
    from app.modules.analytics.service import MIN_ATTEMPTS, WEAK_BELOW

    found = []
    for t in topics:
        if t.accuracy is None or t.accuracy >= WEAK_BELOW:
            continue
        if t.attempts >= MIN_ATTEMPTS:
            reason = f"Weak area: {t.accuracy:g}% over {t.attempts} attempts"
        else:
            reason = f"Low score in your latest {t.topic} quiz ({t.accuracy:g}%)"
        found.append(FocusTopic(t.subject, t.topic, t.accuracy, t.attempts, reason))
    return sorted(found, key=lambda f: (f.accuracy, -f.attempts, f.subject, f.topic))


def _opened(db: Session, user_id: int) -> set[tuple[str, int]]:
    rows = db.execute(
        select(RecommendationOpen.item_type, RecommendationOpen.item_id).where(
            RecommendationOpen.user_id == user_id
        )
    )
    return {(t, i) for t, i in rows}


@dataclass
class _Consumed:
    opened: set[tuple[str, int]]
    quizzes: set[int]
    questions: set[int]

    def __call__(self, kind: ItemType, item_id: int, material_used: bool = False) -> bool:
        if (kind.value, item_id) in self.opened or material_used:
            return True
        if kind is ItemType.QUIZ:
            return item_id in self.quizzes
        if kind is ItemType.QUESTION:
            return item_id in self.questions
        return False


def _candidates(
    db: Session,
    user_id: int,
    scope: list[int],
    consumed: _Consumed,
    reason: str,
    subject: str | None,
    topic: str | None,
) -> tuple[list[Recommendation], list[Recommendation]]:
    """(fresh, consumed) items for one topic (or everything, when both are None)."""
    quiz_items = [
        Recommendation(
            ItemType.QUIZ,
            q.id,
            q.title,
            q.subject,
            q.topic,
            q.channel_id,
            consumed(ItemType.QUIZ, q.id),
            reason,
            attempt_count=q.attempt_count,
            difficulty=q.difficulty,
            api_path=f"/api/v1/quizzes/{q.id}",
        )
        for q in quizzes.published_in_scope(
            db,
            channel_ids=scope,
            subject=subject,
            topic=topic,
            exclude_creator_id=user_id,
            limit=CANDIDATES,
        )
    ]

    page = materials.search_materials(
        db, channel_ids=scope, include_general=True, subject=subject, topic=topic, limit=50
    )
    own = [m for m in page.items if m.uploader_id != user_id]
    feedback = materials.feedback_for(db, [m.id for m in own], user_id)
    own.sort(
        key=lambda m: (
            -(feedback[m.id].rating_average or 0),
            -feedback[m.id].helpful_count,
            -(m.created_at.timestamp() if m.created_at else 0),
        )
    )
    material_items = []
    for m in own[:CANDIDATES]:
        f = feedback[m.id]
        used = f.my_rating is not None or f.my_review is not None or f.marked_helpful
        material_items.append(
            Recommendation(
                ItemType.MATERIAL,
                m.id,
                m.title,
                m.subject,
                m.topic,
                m.channel_id,
                consumed(ItemType.MATERIAL, m.id, used),
                reason,
                rating_average=round(f.rating_average, 2) if f.rating_average else None,
                rating_count=f.rating_count,
                api_path=f"/api/v1/materials/{m.id}",
            )
        )

    question_items = [
        Recommendation(
            ItemType.QUESTION,
            b.question.id,
            _headline(b.question.text),
            b.question.subject,
            b.question.topic,
            b.channel_id,
            consumed(ItemType.QUESTION, b.question.id),
            reason,
            rating_average=b.rating_average,
            rating_count=b.rating_count,
            difficulty=b.question.difficulty.value,
            api_path=f"/api/v1/questions/{b.question.id}",
        )
        for b in questions.published_in_scope(
            db,
            channel_ids=scope,
            subject=subject,
            topic=topic,
            exclude_author_id=user_id,
            limit=CANDIDATES,
        )
    ]

    fresh: list[Recommendation] = []
    used_items: list[Recommendation] = []
    # Alternate kinds so one kind cannot crowd out the others within a topic.
    for row in zip_longest(quiz_items, material_items, question_items):
        for item in row:
            if item is None:
                continue
            (used_items if item.consumed else fresh).append(item)
    return fresh, used_items


def recommend(db: Session, user_id: int, *, limit: int = 12) -> Recommendations:
    """The user's recommendations, worked out from their current performance."""
    from app.modules.analytics.service import dashboard

    board = dashboard(db, user_id)
    focus = focus_topics(board.topics)
    scope = [summary.id for summary, _role in channels.channels_for_member(db, user_id)]
    consumed = _Consumed(
        _opened(db, user_id),
        quizzes.attempted_quiz_ids(db, user_id),
        quizzes.answered_question_ids(db, user_id),
    )

    if focus:
        fresh: list[Recommendation] = []
        used: list[Recommendation] = []
        for f in focus:
            topic_fresh, topic_used = _candidates(
                db, user_id, scope, consumed, f.reason, f.subject, f.topic
            )
            fresh.extend(topic_fresh[:PER_TOPIC])
            used.extend(topic_used)
        if fresh or used:
            return Recommendations(Mode.TARGETED, TARGETED, focus, (fresh + used)[:limit])

    if focus:
        message = GENERAL_NOTHING_SHARED.format(topics=", ".join(f.topic for f in focus))
    elif board.attempts == 0:
        message = GENERAL_NO_ATTEMPTS
    else:
        message = GENERAL_NO_WEAK
    fresh, used = _candidates(
        db, user_id, scope, consumed, "Popular with other learners", None, None
    )
    return Recommendations(Mode.GENERAL, message, focus, (fresh + used)[:limit])


def record_open(db: Session, user_id: int, item_type: ItemType, item_id: int) -> None:
    """Remember that the user opened a recommended item (idempotent)."""
    row = db.scalar(
        select(RecommendationOpen).where(
            RecommendationOpen.user_id == user_id,
            RecommendationOpen.item_type == item_type.value,
            RecommendationOpen.item_id == item_id,
        )
    )
    if row is None:
        db.add(RecommendationOpen(user_id=user_id, item_type=item_type.value, item_id=item_id))
    else:
        row.opened_at = datetime.now(UTC)
    db.flush()
