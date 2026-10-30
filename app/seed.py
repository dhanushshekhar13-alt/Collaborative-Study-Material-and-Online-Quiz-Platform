"""Seed a database with one test user per role and sample content (S00.2).

Usage::

    alembic upgrade head
    python -m app.seed

The password for every seeded user comes from ``SEED_USER_PASSWORD``. If it is unset,
a random one is generated and printed once, so no credential is ever committed.
Running the script twice does nothing the second time.
"""

import os
import secrets

from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.models import load_models
from app.modules.accounts import service as accounts
from app.modules.accounts.models import Role
from app.modules.channels import service as channels
from app.modules.channels.models import ApprovalStatus, ChannelType
from app.modules.chat import service as chat
from app.modules.chat.models import SharedResourceType
from app.modules.materials import service as materials
from app.modules.materials.models import MaterialType
from app.modules.questions import service as questions
from app.modules.questions.models import Difficulty, QuestionType
from app.modules.quizzes import service as quizzes
from app.modules.reputation import service as reputation

load_models()  # the seed writes to every module's tables

SEED_USERS = {
    Role.STUDENT: ("student@example.com", "Sample Student"),
    Role.AUTHORIZED_USER: ("faculty@example.com", "Sample Faculty"),
    Role.ADMIN: ("admin@example.com", "Sample Admin"),
}


def seed(db: Session, password: str) -> dict[str, int]:
    """Create the sample data; returns the ids created (empty if already seeded)."""
    if accounts.get_user_by_email(db, SEED_USERS[Role.ADMIN][0]) is not None:
        return {}

    users = {
        role: accounts.create_user(db, email=email, display_name=name, password=password, role=role)
        for role, (email, name) in SEED_USERS.items()
    }
    student, faculty, admin = (users[r].id for r in Role)

    peer = channels.create_channel(
        db, name="DBMS study group", created_by=student, subject="DBMS", topic="Normalization"
    )
    official = channels.create_channel(
        db,
        name="Operating Systems (official)",
        created_by=faculty,
        channel_type=ChannelType.AUTHORIZED,
        subject="Operating Systems",
        topic="Scheduling",
    )
    channels.set_approval(db, official.id, ApprovalStatus.APPROVED)
    channels.add_member(db, peer.id, faculty)
    channels.add_member(db, official.id, student)

    notes = materials.create_material(
        db,
        uploader_id=student,
        title="Normal forms cheat sheet",
        material_type=MaterialType.NOTES,
        subject="DBMS",
        topic="Normalization",
        semester="5",
        channel_id=peer.id,
    )
    materials.create_material(
        db,
        uploader_id=faculty,
        title="CPU scheduling lecture slides",
        material_type=MaterialType.LINK,
        subject="Operating Systems",
        topic="Scheduling",
        semester="5",
        channel_id=official.id,
        url="https://example.com/os-scheduling",
        is_official=True,
    )

    q1 = questions.create_question(
        db,
        author_id=faculty,
        channel_id=official.id,
        subject="Operating Systems",
        topic="Scheduling",
        difficulty=Difficulty.EASY,
        question_type=QuestionType.MULTIPLE_CHOICE,
        text="Which scheduling algorithm can cause starvation of long jobs?",
        options=["FCFS", "Round Robin", "Shortest Job First", "None"],
        correct_answer="Shortest Job First",
        explanation="SJF keeps picking short jobs, so a long job may wait indefinitely.",
    )
    q2 = questions.create_question(
        db,
        author_id=faculty,
        channel_id=official.id,
        subject="Operating Systems",
        topic="Scheduling",
        difficulty=Difficulty.MEDIUM,
        question_type=QuestionType.TRUE_FALSE,
        text="Round Robin with a very large time quantum behaves like FCFS.",
        correct_answer="true",
    )
    q3 = questions.create_question(
        db,
        author_id=faculty,
        channel_id=official.id,
        subject="Operating Systems",
        topic="Scheduling",
        difficulty=Difficulty.HARD,
        question_type=QuestionType.DESCRIPTIVE,
        text="Compare preemptive and non-preemptive scheduling.",
        expected_concepts=["preemption", "context switch", "response time", "starvation"],
    )
    quiz = quizzes.create_quiz(
        db,
        creator_id=faculty,
        channel_id=official.id,
        title="CPU scheduling basics",
        subject="Operating Systems",
        topic="Scheduling",
        difficulty="mixed",
        duration_minutes=15,
        question_ids=[q1.id, q2.id, q3.id],
        publish=True,
    )
    attempt = quizzes.start_attempt(db, quiz, student)
    quizzes.submit_attempt(db, attempt, {q1.id: "Shortest Job First", q2.id: "false"})

    chat.post_channel_message(
        db,
        channel_id=peer.id,
        sender_id=student,
        body="Uploaded a cheat sheet for normal forms.",
        shared_resource=(SharedResourceType.MATERIAL, notes.id),
    )
    conv = chat.open_conversation(db, student, faculty)
    chat.send_direct_message(db, conversation_id=conv, sender_id=student, body="Thanks!")

    reputation.award(
        db,
        user_id=student,
        points=5,
        reason="material_shared",
        source_type="material",
        source_id=notes.id,
    )

    db.commit()
    return {
        "student": student,
        "authorized_user": faculty,
        "admin": admin,
        "peer_channel": peer.id,
        "authorized_channel": official.id,
        "quiz": quiz,
    }


def main() -> None:
    password = os.environ.get("SEED_USER_PASSWORD") or secrets.token_urlsafe(12)
    with SessionLocal() as db:
        created = seed(db, password)
    if not created:
        print("Database already seeded; nothing to do.")
        return
    for role, (email, _) in SEED_USERS.items():
        print(f"{role.value:16} {email}")
    if "SEED_USER_PASSWORD" not in os.environ:
        print(f"Password for all seeded users: {password}")


if __name__ == "__main__":
    main()
