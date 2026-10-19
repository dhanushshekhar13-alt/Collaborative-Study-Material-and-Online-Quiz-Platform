"""S00.2: module service interfaces and objective quiz scoring."""

import pytest

from app.core.security import hash_password, verify_password
from app.modules.accounts import service as accounts
from app.modules.analytics import service as analytics
from app.modules.channels import service as channels
from app.modules.channels.models import ApprovalStatus, ChannelType, MemberRole
from app.modules.chat import service as chat
from app.modules.notifications import service as notifications
from app.modules.notifications.models import NotificationKind
from app.modules.questions import service as questions
from app.modules.questions.models import Difficulty, QuestionType
from app.modules.quizzes import service as quizzes
from app.modules.quizzes.scoring import score_objective


def _question(qtype, answer, **kw):
    return questions.QuestionView(
        id=1,
        subject="s",
        topic="t",
        difficulty=Difficulty.EASY,
        question_type=qtype,
        text="q",
        options=(),
        correct_answer=answer,
        explanation=None,
        expected_concepts=(),
        **kw,
    )


def test_password_hash_round_trip():
    stored = hash_password("correct horse")
    assert stored.startswith("scrypt$") and "correct horse" not in stored
    assert verify_password("correct horse", stored)
    assert not verify_password("wrong", stored)
    assert not verify_password("x", "not-a-hash")


@pytest.mark.parametrize(
    ("qtype", "answer", "response", "expected"),
    [
        (QuestionType.MULTIPLE_CHOICE, "Shortest Job First", " shortest  job first ", 1.0),
        (QuestionType.MULTIPLE_CHOICE, "FCFS", "SJF", 0.0),
        (QuestionType.TRUE_FALSE, "true", "True", 1.0),
        (QuestionType.TRUE_FALSE, "true", "no", 0.0),
        (QuestionType.SHORT_ANSWER, "deadlock", None, 0.0),
    ],
)
def test_objective_scoring(qtype, answer, response, expected):
    assert score_objective(_question(qtype, answer), response) == expected


def test_descriptive_answers_are_not_scored_here():
    with pytest.raises(ValueError):
        score_objective(_question(QuestionType.DESCRIPTIVE, None), "essay")


def _setup_quiz(db):
    teacher = accounts.create_user(db, email="T@x.com", display_name="T", password="pw")
    student = accounts.create_user(db, email="s@x.com", display_name="S", password="pw")
    channel = channels.create_channel(db, name="c", created_by=teacher.id)
    q = questions.create_question(
        db,
        author_id=teacher.id,
        subject="OS",
        topic="Sched",
        difficulty=Difficulty.EASY,
        question_type=QuestionType.MULTIPLE_CHOICE,
        text="?",
        options=["a", "b"],
        correct_answer="a",
        channel_id=channel.id,
    )
    d = questions.create_question(
        db,
        author_id=teacher.id,
        subject="OS",
        topic="Sched",
        difficulty=Difficulty.HARD,
        question_type=QuestionType.DESCRIPTIVE,
        text="explain",
        expected_concepts=["x"],
    )
    quiz = quizzes.create_quiz(
        db,
        creator_id=teacher.id,
        title="Q",
        subject="OS",
        topic="Sched",
        difficulty="easy",
        duration_minutes=5,
        question_ids=[q.id, d.id],
        channel_id=channel.id,
    )
    return teacher, student, channel, q, quiz


def test_attempt_is_scored_and_analytics_reads_it(db):
    _, student, _, q, quiz = _setup_quiz(db)
    attempt = quizzes.start_attempt(db, quiz, student.id)
    result = quizzes.submit_attempt(db, attempt, {q.id: "a"})
    assert (result.score, result.max_score) == (1.0, 2.0)
    with pytest.raises(ValueError):
        quizzes.submit_attempt(db, attempt, {})
    summary = analytics.summary_for_user(db, student.id)
    assert summary.attempts == 1 and summary.accuracy == 0.5


def test_chat_activity_does_not_change_quiz_scores(db):
    teacher, student, channel, q, quiz = _setup_quiz(db)
    first = quizzes.submit_attempt(db, quizzes.start_attempt(db, quiz, student.id), {q.id: "a"})
    for i in range(5):
        chat.post_channel_message(db, channel_id=channel.id, sender_id=student.id, body=str(i))
    conv = chat.open_conversation(db, student.id, teacher.id)
    chat.send_direct_message(db, conversation_id=conv, sender_id=teacher.id, body="hi")
    second = quizzes.submit_attempt(db, quizzes.start_attempt(db, quiz, student.id), {q.id: "a"})
    assert (first.score, first.max_score) == (second.score, second.max_score)


def test_accounts_and_channels_services(db):
    user = accounts.create_user(db, email=" A@B.com ", display_name="A", password="pw")
    assert accounts.get_user_by_email(db, "a@b.com") == user
    official = channels.create_channel(
        db, name="o", created_by=user.id, channel_type=ChannelType.AUTHORIZED
    )
    assert official.approval_status is ApprovalStatus.PENDING
    assert channels.member_role(db, official.id, user.id) is MemberRole.OWNER
    other = accounts.create_user(db, email="b@b.com", display_name="B", password="pw")
    channels.add_member(db, official.id, other.id)
    channels.add_member(db, official.id, other.id)  # idempotent
    assert channels.is_member(db, official.id, other.id)


def test_conversation_is_reused_and_notifications_respect_preferences(db):
    a = accounts.create_user(db, email="a@x.com", display_name="A", password="pw")
    b = accounts.create_user(db, email="b@x.com", display_name="B", password="pw")
    assert chat.open_conversation(db, a.id, b.id) == chat.open_conversation(db, b.id, a.id)
    assert notifications.notify(db, user_id=a.id, kind=NotificationKind.REPLY, title="r")
    notifications.set_preference(db, a.id, NotificationKind.REPLY, False)
    assert notifications.notify(db, user_id=a.id, kind=NotificationKind.REPLY, title="r") is None
