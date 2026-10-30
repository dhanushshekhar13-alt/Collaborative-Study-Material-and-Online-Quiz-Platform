"""Fill a database with a large, realistic dataset for the scalability suite (NFR-05).

Usage against a migrated database (never against production data)::

    alembic upgrade head
    SEED_USER_PASSWORD=... python -m scripts.seed_scale --factor 10

``factor`` scales every growing table together: factor 1 is the "small campus" end of
the tested range (50 users, 500 materials, 1,000 channel messages, ...) and factor 10
the large end. ``tests/test_scalability.py`` builds both in memory and checks that the
hot paths stay inside their response-time targets at each size.

Rows are written with bulk inserts rather than through the services: the point is
volume, and going through ``create_user`` would spend most of the time hashing
passwords. Every user shares one password hash. The data is deterministic for a given
factor, so timings are comparable between runs.
"""

import argparse
import os
import random
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, insert, select
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.models import load_models
from app.modules.accounts.models import Role, User
from app.modules.analytics.models import PerformanceAttempt, PerformanceTopic
from app.modules.channels.models import Channel, ChannelMembership, ChannelType, MemberRole
from app.modules.chat.models import (
    ChannelMessage,
    Conversation,
    ConversationParticipant,
    DirectMessage,
)
from app.modules.materials.models import MaterialReview, MaterialType, StudyMaterial
from app.modules.moderation.models import Report, ReportReason, ReportTarget
from app.modules.notifications.models import Notification, NotificationKind
from app.modules.questions.models import Difficulty, Question, QuestionType
from app.modules.quizzes.models import (
    AttemptAnswer,
    AttemptStatus,
    Quiz,
    QuizAttempt,
    QuizQuestion,
    QuizStatus,
)
from app.modules.reputation.models import ReputationEvent

load_models()  # the seed writes to every module's tables

SUBJECTS = {
    "Operating Systems": ["CPU Scheduling", "Paging", "Deadlocks", "File Systems"],
    "DBMS": ["Normalization", "Concurrency Control", "Indexing", "Transactions"],
    "Computer Networks": ["Routing", "TCP", "Subnetting", "DNS"],
    "Compilers": ["Parsing", "Lexing", "Code Generation", "Optimisation"],
}

# Rows per unit of ``factor``.
PER_FACTOR = {
    "users": 50,
    "channels": 10,
    "materials": 500,
    "reviews": 500,
    "questions": 200,
    "quizzes": 20,
    "attempts": 200,
    "channel_messages": 1000,
    "conversations": 20,
    "direct_messages": 1000,
    "notifications": 1000,
    "reputation_events": 500,
    "reports": 50,
}
QUESTIONS_PER_QUIZ = 5
PROBE = "scale_probe"
ADMIN = "scale_admin"


@dataclass(frozen=True)
class ScaleDataset:
    """What was created, and the ids the suite drives the hot paths with."""

    factor: int
    counts: dict[str, int]
    probe_id: int  # a student who belongs to the busiest channel and has history
    admin_id: int
    busy_channel_id: int  # the channel with the most messages and members
    busy_thread_id: int  # a top-level message in that channel with replies
    conversation_id: int  # one of the probe's direct conversations
    quiz_id: int  # a published quiz in the busy channel the probe can attempt
    material_id: int  # a general material anyone can open


def _bulk(db: Session, model, rows: list[dict]) -> list[int]:
    """Insert ``rows`` and return the new ids in insertion order."""
    if not rows:
        return []
    table = model.__table__
    before = db.scalar(select(func.coalesce(func.max(table.c.id), 0)))
    db.execute(insert(table), rows)
    return list(db.scalars(select(table.c.id).where(table.c.id > before).order_by(table.c.id)))


def _performance_ledger(db, attempts, attempt_rows, quiz_rows, quizzes) -> None:
    """Write what the analytics module would have folded in as each attempt was scored.

    Bulk-inserted attempts skip the ``ATTEMPT_SCORED`` event, so without this the first
    dashboard view would fold every attempt at once: a one-off catch-up, not the steady
    state the suite measures.
    """
    quiz_by_id = dict(zip(quizzes, quiz_rows, strict=True))
    ledger, totals = [], {}
    for attempt, row in zip(attempts, attempt_rows, strict=True):
        quiz = quiz_by_id[row["quiz_id"]]
        ledger.append(
            {
                "attempt_id": attempt,
                "user_id": row["user_id"],
                "quiz_id": row["quiz_id"],
                "quiz_title": quiz["title"],
                "subject": quiz["subject"],
                "topic": quiz["topic"],
                "score": row["score"],
                "max_score": row["max_score"],
                "submitted_at": row["submitted_at"],
            }
        )
        key = (row["user_id"], quiz["subject"], quiz["topic"])
        n, score, possible, last = totals.get(key, (0, 0.0, 0.0, None))
        newest = max(last, row["submitted_at"]) if last else row["submitted_at"]
        totals[key] = (n + 1, score + row["score"], possible + row["max_score"], newest)
    db.execute(insert(PerformanceAttempt.__table__), ledger)
    db.execute(
        insert(PerformanceTopic.__table__),
        [
            {
                "user_id": user,
                "subject": subject,
                "topic": topic,
                "attempts": n,
                "total_score": round(score, 6),
                "total_possible": round(possible, 6),
                "last_attempt_at": last,
            }
            for (user, subject, topic), (n, score, possible, last) in totals.items()
        ],
    )


def seed_scale(db: Session, factor: int = 1, password: str = "Scale@1234") -> ScaleDataset:
    """Create ``factor`` units of every growing table; commits and returns the ids."""
    if factor < 1:
        raise ValueError("factor must be at least 1")
    rnd = random.Random(f"nfr-05-{factor}")
    n = {name: per * factor for name, per in PER_FACTOR.items()}
    now = datetime.now(UTC)

    def when(i: int, total: int) -> datetime:
        """Spread rows over the last 120 days, oldest first."""
        return now - timedelta(days=120) + timedelta(days=120) * (i / max(total, 1))

    subjects = list(SUBJECTS)
    password_hash = hash_password(password)

    # Users: the probe student and an admin first, then the crowd.
    user_rows = [
        {
            "email": f"{PROBE}@scale.test",
            "username": PROBE,
            "display_name": "Scale Probe",
            "role": Role.STUDENT,
        },
        {
            "email": f"{ADMIN}@scale.test",
            "username": ADMIN,
            "display_name": "Scale Admin",
            "role": Role.ADMIN,
        },
    ]
    for i in range(n["users"] - 2):
        role = Role.AUTHORIZED_USER if i % 25 == 0 else Role.STUDENT
        user_rows.append(
            {
                "email": f"user{i}@scale.test",
                "username": f"scale_user_{i}",
                "display_name": f"Scale User {i}",
                "role": role,
            }
        )
    for i, row in enumerate(user_rows):
        row.update(password_hash=password_hash, created_at=when(i, len(user_rows)))
    users = _bulk(db, User, user_rows)
    probe, admin, crowd = users[0], users[1], users[2:]

    # Channels: the first is the busy one; every user joins a handful.
    channel_rows = []
    for i in range(n["channels"]):
        subject = subjects[i % len(subjects)]
        channel_rows.append(
            {
                "name": f"{subject} group {i}",
                "subject": subject,
                "topic": SUBJECTS[subject][i % 4],
                "channel_type": ChannelType.AUTHORIZED if i % 5 == 4 else ChannelType.NORMAL,
                "created_by": crowd[i % len(crowd)],
                "created_at": when(i, n["channels"]),
            }
        )
    channels = _bulk(db, Channel, channel_rows)
    busy = channels[0]
    members: dict[int, list[int]] = {c: [] for c in channels}
    member_rows = []
    for user in users:
        joined = {busy} | set(rnd.sample(channels, min(4, len(channels))))
        for channel in joined:
            members[channel].append(user)
            owner = channel_rows[channels.index(channel)]["created_by"] == user
            member_rows.append(
                {
                    "channel_id": channel,
                    "user_id": user,
                    "role": MemberRole.OWNER if owner else MemberRole.MEMBER,
                }
            )
    _bulk(db, ChannelMembership, member_rows)

    # Study materials: a fifth are general (no channel), the rest spread over channels.
    material_rows = []
    for i in range(n["materials"]):
        subject = subjects[i % len(subjects)]
        channel = None if i % 5 == 1 else channels[i % len(channels)]
        material_rows.append(
            {
                "channel_id": channel,
                "uploader_id": crowd[i % len(crowd)],
                "title": f"{subject} notes {i}",
                "material_type": MaterialType.LINK,
                "url": f"https://example.com/notes/{i}",
                "subject": subject,
                "topic": SUBJECTS[subject][(i // 4) % 4],
                "semester": str(i % 8 + 1),
                "created_at": when(i, n["materials"]),
            }
        )
    materials = _bulk(db, StudyMaterial, material_rows)
    seen_reviews = set()
    review_rows = []
    while len(review_rows) < n["reviews"]:
        pair = (rnd.choice(materials), rnd.choice(users))
        if pair in seen_reviews:
            continue
        seen_reviews.add(pair)
        review_rows.append(
            {
                "material_id": pair[0],
                "user_id": pair[1],
                "rating": rnd.randint(1, 5),
                "is_helpful": rnd.random() < 0.5,
            }
        )
    _bulk(db, MaterialReview, review_rows)

    # Question bank and quizzes built from it.
    question_rows = []
    for i in range(n["questions"]):
        subject = subjects[i % len(subjects)]
        question_rows.append(
            {
                "channel_id": None if i % 5 == 1 else channels[i % len(channels)],
                "author_id": crowd[i % len(crowd)],
                "subject": subject,
                "topic": SUBJECTS[subject][(i // 4) % 4],
                "difficulty": list(Difficulty)[i % 3],
                "question_type": QuestionType.MULTIPLE_CHOICE,
                "text": f"Question {i} about {subject}?",
                "options": ["A", "B", "C", "D"],
                "correct_answer": "A",
                "created_at": when(i, n["questions"]),
            }
        )
    questions = _bulk(db, Question, question_rows)
    quiz_rows = []
    for i in range(n["quizzes"]):
        subject = subjects[i % len(subjects)]
        quiz_rows.append(
            {
                # Quiz 0 sits in the busy channel; a quarter are general (no channel).
                "channel_id": busy
                if i == 0
                else None
                if i % 4 == 1
                else channels[i % len(channels)],
                "creator_id": crowd[i % len(crowd)],
                "title": f"{subject} quiz {i}",
                "subject": subject,
                "topic": SUBJECTS[subject][0],
                "difficulty": "medium",
                "duration_minutes": 30,
                "status": QuizStatus.PUBLISHED,
                "created_at": when(i, n["quizzes"]),
            }
        )
    quizzes = _bulk(db, Quiz, quiz_rows)
    quiz_questions: dict[int, list[int]] = {}
    qq_rows = []
    for i, quiz in enumerate(quizzes):
        picked = [questions[(i * QUESTIONS_PER_QUIZ + k) % len(questions)] for k in range(5)]
        quiz_questions[quiz] = picked
        qq_rows += [
            {"quiz_id": quiz, "question_id": q, "position": pos, "points": 1.0}
            for pos, q in enumerate(picked, start=1)
        ]
    _bulk(db, QuizQuestion, qq_rows)
    attempt_rows = []
    for i in range(n["attempts"]):
        user = probe if i % 20 == 0 else rnd.choice(users)
        started = when(i, n["attempts"])
        correct = rnd.randint(0, QUESTIONS_PER_QUIZ)
        attempt_rows.append(
            {
                "quiz_id": quizzes[i % len(quizzes)],
                "user_id": user,
                "status": AttemptStatus.SUBMITTED,
                "started_at": started,
                "submitted_at": started + timedelta(minutes=12),
                "score": float(correct),
                "max_score": float(QUESTIONS_PER_QUIZ),
            }
        )
    attempts = _bulk(db, QuizAttempt, attempt_rows)
    answer_rows = []
    for attempt, row in zip(attempts, attempt_rows, strict=True):
        for k, question in enumerate(quiz_questions[row["quiz_id"]]):
            right = k < row["score"]
            answer_rows.append(
                {
                    "attempt_id": attempt,
                    "question_id": question,
                    "response": "A" if right else "B",
                    "is_correct": right,
                    "score": 1.0 if right else 0.0,
                }
            )
    _bulk(db, AttemptAnswer, answer_rows)
    _performance_ledger(db, attempts, attempt_rows, quiz_rows, quizzes)

    # Channel chat: half the messages land in the busy channel; a tenth are replies.
    message_rows = []
    for i in range(n["channel_messages"]):
        channel = busy if i % 2 == 0 else rnd.choice(channels)
        message_rows.append(
            {
                "channel_id": channel,
                "sender_id": rnd.choice(members[channel]),
                "body": f"Message {i}",
                "created_at": when(i, n["channel_messages"]),
            }
        )
    top_level = _bulk(db, ChannelMessage, message_rows)
    pairs = zip(top_level, message_rows, strict=True)
    busy_thread = next(m for m, r in pairs if r["channel_id"] == busy)
    reply_rows = []
    for i in range(n["channel_messages"] // 10):
        parent = busy_thread if i % 2 == 0 else rnd.choice(top_level)
        channel = message_rows[top_level.index(parent)]["channel_id"]
        reply_rows.append(
            {
                "channel_id": channel,
                "sender_id": rnd.choice(members[channel]),
                "parent_id": parent,
                "body": f"Reply {i}",
                "created_at": now - timedelta(minutes=i),
            }
        )
    _bulk(db, ChannelMessage, reply_rows)

    # Direct conversations: the probe is in a quarter of them.
    conversations = _bulk(db, Conversation, [{} for _ in range(n["conversations"])])
    pairs: dict[int, tuple[int, int]] = {}
    participant_rows = []
    for i, conversation in enumerate(conversations):
        a = probe if i % 4 == 0 else crowd[(2 * i) % len(crowd)]
        b = crowd[(2 * i + 1) % len(crowd)]
        pairs[conversation] = (a, b)
        participant_rows += [
            {"conversation_id": conversation, "user_id": a},
            {"conversation_id": conversation, "user_id": b},
        ]
    _bulk(db, ConversationParticipant, participant_rows)
    dm_rows = []
    for i in range(n["direct_messages"]):
        conversation = conversations[i % len(conversations)]
        dm_rows.append(
            {
                "conversation_id": conversation,
                "sender_id": pairs[conversation][i % 2],
                "body": f"Direct message {i}",
                "created_at": when(i, n["direct_messages"]),
            }
        )
    _bulk(db, DirectMessage, dm_rows)

    # Notifications: a tenth go to the probe, most of them read.
    kinds = list(NotificationKind)
    notification_rows = [
        {
            "user_id": probe if i % 10 == 0 else rnd.choice(users),
            "kind": kinds[i % len(kinds)],
            "title": f"Notification {i}",
            "is_read": i % 3 != 0,
            "created_at": when(i, n["notifications"]),
        }
        for i in range(n["notifications"])
    ]
    _bulk(db, Notification, notification_rows)

    reputation_rows = [
        {
            "user_id": rnd.choice(crowd),
            "points": rnd.choice([2, 5, 10]),
            "reason": "material_rated",
            "source_type": "material",
            "source_id": rnd.choice(materials),
            "created_at": when(i, n["reputation_events"]),
        }
        for i in range(n["reputation_events"])
    ]
    _bulk(db, ReputationEvent, reputation_rows)
    totals: dict[int, int] = {}
    for row in reputation_rows:
        totals[row["user_id"]] = totals.get(row["user_id"], 0) + row["points"]
    users_table = User.__table__
    for user, points in totals.items():
        db.execute(users_table.update().where(users_table.c.id == user).values(reputation=points))

    report_rows = [
        {
            "reporter_id": rnd.choice(crowd),
            "target_type": ReportTarget.MATERIAL,
            "target_id": materials[i % len(materials)],
            "channel_id": material_rows[i % len(materials)]["channel_id"],
            "reason": list(ReportReason)[i % len(ReportReason)],
            "created_at": when(i, n["reports"]),
        }
        for i in range(n["reports"])
    ]
    _bulk(db, Report, report_rows)

    db.commit()
    return ScaleDataset(
        factor=factor,
        counts={
            **n,
            "channel_messages": len(message_rows) + len(reply_rows),
            "memberships": len(member_rows),
            "attempt_answers": len(answer_rows),
        },
        probe_id=probe,
        admin_id=admin,
        busy_channel_id=busy,
        busy_thread_id=busy_thread,
        conversation_id=conversations[0],
        quiz_id=quizzes[0],
        material_id=materials[1],
    )


def main() -> None:
    from app.core.database import SessionLocal

    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--factor", type=int, default=10)
    args = parser.parse_args()
    password = os.environ.get("SEED_USER_PASSWORD")
    if not password:
        raise SystemExit("Set SEED_USER_PASSWORD; the scale users sign in with it.")
    with SessionLocal() as db:
        if db.scalar(select(User.id).where(User.username == PROBE)) is not None:
            raise SystemExit("This database already holds the scale dataset.")
        data = seed_scale(db, args.factor, password)
    print(f"Seeded factor {data.factor}: {data.counts}")
    print(f"Sign in as {PROBE} (student) or {ADMIN} (admin) with SEED_USER_PASSWORD.")


if __name__ == "__main__":
    main()
