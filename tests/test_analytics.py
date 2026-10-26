"""S12.1 - performance tracking and analytics dashboard (issue #40, FR-12, NFR-03).

Test Plan cases are named after their IDs: UT_23 (subject-wise breakdown matches the
stored quiz history), UT_24 (strong and weak topics by accuracy threshold) and IT_23
(the trend includes a new attempt as soon as it is recorded). Attempts are taken
through the real ``/attempts`` endpoints; the dashboard is read from
``GET /api/v1/analytics/dashboard``. The charts themselves are checked in
``tests/test_web_ui.py``.
"""

import pytest
from sqlalchemy import func, select

from app.modules.analytics import service as analytics
from app.modules.analytics.models import PerformanceAttempt, PerformanceTopic
from app.modules.questions import service as questions
from app.modules.questions.models import Difficulty, QuestionType
from app.modules.quizzes import attempts
from app.modules.quizzes import service as quizzes
from app.modules.quizzes.models import AttemptStatus, Quiz, QuizAttempt
from tests.test_quiz_attempts import (
    API,
    OPTIONS,
    Clock,
    auth,
    make_user,
    start,
    submit,
)


@pytest.fixture()
def clock(monkeypatch) -> Clock:
    fake = Clock()
    monkeypatch.setattr(attempts, "utcnow", fake)
    return fake


@pytest.fixture()
def author(db):
    return make_user(db, "analytics_author")


@pytest.fixture()
def learner(db):
    return make_user(db, "learner")


@pytest.fixture()
def headers(client, learner):
    return auth(client, "learner")


def make_topic_quiz(db, author_id: int, subject: str, topic: str, count: int = 10) -> tuple:
    """A published quiz of ``count`` MCQs whose correct answer is always "A"."""
    ids = [
        questions.create_question(
            db,
            author_id=author_id,
            subject=subject,
            topic=topic,
            difficulty=Difficulty.EASY,
            question_type=QuestionType.MULTIPLE_CHOICE,
            text=f"{topic} question {i}?",
            options=OPTIONS,
            correct_answer="A",
        ).id
        for i in range(count)
    ]
    quiz_id = quizzes.create_quiz(
        db,
        creator_id=author_id,
        title=f"{topic} drill",
        subject=subject,
        topic=topic,
        difficulty="easy",
        duration_minutes=30,
        question_ids=ids,
        publish=True,
    )
    db.commit()
    return quiz_id, ids


def take(client, headers, quiz, right: int, clock: Clock | None = None) -> int:
    """Attempt ``quiz`` answering the first ``right`` questions correctly."""
    if clock is not None:
        clock.advance(hours=1)
    quiz_id, ids = quiz
    attempt_id = start(client, headers, quiz_id).json()["id"]
    answers = {qid: ("A" if i < right else "B") for i, qid in enumerate(ids)}
    response = submit(client, headers, attempt_id, answers)
    assert response.status_code == 200, response.text
    return attempt_id


def dashboard(client, headers) -> dict:
    response = client.get(f"{API}/analytics/dashboard", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def topic(body: dict, name: str) -> dict:
    return next(t for t in body["topics"] if t["topic"] == name)


def names(items: list[dict]) -> list[str]:
    return [t["topic"] for t in items]


def raw_totals(db, user_id: int) -> dict[tuple[str, str], tuple[int, float, float]]:
    """Per-topic figures summed straight from the quiz_attempts rows."""
    rows = db.execute(
        select(
            Quiz.subject,
            Quiz.topic,
            func.count(QuizAttempt.id),
            func.sum(QuizAttempt.score),
            func.sum(QuizAttempt.max_score),
        )
        .join(Quiz, Quiz.id == QuizAttempt.quiz_id)
        .where(QuizAttempt.user_id == user_id, QuizAttempt.status == AttemptStatus.SUBMITTED)
        .group_by(Quiz.subject, Quiz.topic)
    ).all()
    return {(s, t): (n, round(score, 6), round(possible, 6)) for s, t, n, score, possible in rows}


def stored_totals(db, user_id: int) -> dict[tuple[str, str], tuple[int, float, float]]:
    return {
        key: (t.attempts, t.total_score, t.total_possible)
        for key, t in analytics.stored(db, user_id).items()
    }


@pytest.fixture()
def dbms(db, author):
    return make_topic_quiz(db, author.id, "DBMS", "Normalization")


@pytest.fixture()
def indexing(db, author):
    return make_topic_quiz(db, author.id, "DBMS", "Indexing")


@pytest.fixture()
def scheduling(db, author):
    return make_topic_quiz(db, author.id, "Operating Systems", "Scheduling")


# --------------------------------------------------------------------------- #
# Stored aggregates: updated per attempt, reconcile, and recompute identically
# --------------------------------------------------------------------------- #


def test_each_attempt_updates_the_stored_accuracy_and_attempt_count(
    client, db, headers, learner, dbms, clock
):
    take(client, headers, dbms, right=6, clock=clock)

    # Stored when the attempt is scored, before anyone opens the dashboard.
    assert stored_totals(db, learner.id) == {("DBMS", "Normalization"): (1, 6.0, 10.0)}
    assert analytics.summary_for_user(db, learner.id).accuracy == pytest.approx(0.6)

    take(client, headers, dbms, right=9, clock=clock)

    assert stored_totals(db, learner.id) == {("DBMS", "Normalization"): (2, 15.0, 20.0)}
    summary = analytics.summary_for_user(db, learner.id)
    assert (summary.attempts, summary.accuracy) == (2, pytest.approx(0.75))
    assert db.scalar(select(func.count()).select_from(PerformanceAttempt)) == 2


def test_stored_figures_reconcile_exactly_with_the_attempt_records(
    client, db, headers, learner, dbms, indexing, scheduling, clock
):
    for quiz, right in [(dbms, 7), (indexing, 3), (scheduling, 10), (dbms, 8), (indexing, 4)]:
        take(client, headers, quiz, right, clock)

    assert stored_totals(db, learner.id) == raw_totals(db, learner.id)
    body = dashboard(client, headers)
    assert body["attempts"] == 5
    assert (body["total_score"], body["total_possible"]) == (32, 50)
    assert body["accuracy"] == 64.0

    check = client.get(f"{API}/analytics/reconciliation", headers=headers).json()
    assert check["matches"] is True
    assert len(check["topics"]) == 3
    for row in check["topics"]:
        assert row["stored_attempts"] == row["recomputed_attempts"]
        assert row["stored_score"] == row["recomputed_score"]
        assert row["stored_possible"] == row["recomputed_possible"]


def test_recomputation_from_raw_attempts_produces_the_same_aggregates(
    client, db, headers, learner, dbms, scheduling, clock
):
    for quiz, right in [(dbms, 5), (scheduling, 2), (dbms, 10), (scheduling, 7)]:
        take(client, headers, quiz, right, clock)
    before = dashboard(client, headers)

    assert analytics.stored(db, learner.id) == analytics.recompute(db, learner.id)

    analytics.rebuild_user(db, learner.id)
    db.commit()

    assert analytics.stored(db, learner.id) == analytics.recompute(db, learner.id)
    assert dashboard(client, headers) == before


def test_a_later_descriptive_evaluation_replaces_the_attempts_earlier_contribution(
    client, db, headers, learner, author, clock
):
    essay = questions.create_question(
        db,
        author_id=author.id,
        subject="DBMS",
        topic="Transactions",
        difficulty=Difficulty.MEDIUM,
        question_type=QuestionType.DESCRIPTIVE,
        text="Explain ACID properties",
        correct_answer="Atomicity, Consistency, Isolation and Durability",
        expected_concepts=["atomicity", "consistency", "isolation", "durability"],
    ).id
    quiz_id = quizzes.create_quiz(
        db,
        creator_id=author.id,
        title="ACID",
        subject="DBMS",
        topic="Transactions",
        difficulty="medium",
        duration_minutes=30,
        question_ids=[essay],
        publish=True,
    )
    db.commit()
    attempt_id = start(client, headers, quiz_id).json()["id"]
    submit(client, headers, attempt_id, {essay: "All four ACID properties ..."}, confirm=True)
    assert stored_totals(db, learner.id) == {("DBMS", "Transactions"): (1, 0.0, 1.0)}

    quizzes.record_descriptive_evaluation(
        db, attempt_id, essay, score=8, max_score=10, feedback={"summary": "good"}
    )
    db.commit()

    # Re-scored in place: still one attempt, now 0.8 of 1 point, never double counted.
    assert stored_totals(db, learner.id) == {("DBMS", "Transactions"): (1, 0.8, 1.0)}
    assert stored_totals(db, learner.id) == raw_totals(db, learner.id)


def test_the_dashboard_repairs_figures_that_drifted_from_the_attempts(
    client, db, headers, learner, dbms, indexing, clock
):
    take(client, headers, dbms, right=8, clock=clock)
    take(client, headers, indexing, right=4, clock=clock)
    # Simulate drift: a lost ledger row and a tampered total.
    db.delete(db.scalars(select(PerformanceAttempt)).first())
    row = db.scalars(select(PerformanceTopic).where(PerformanceTopic.topic == "Indexing")).one()
    row.total_score = 99
    db.commit()
    assert client.get(f"{API}/analytics/reconciliation", headers=headers).json()["matches"] is False

    body = dashboard(client, headers)

    assert (topic(body, "Normalization")["attempts"], topic(body, "Indexing")["total_score"]) == (
        1,
        4,
    )
    assert stored_totals(db, learner.id) == raw_totals(db, learner.id)
    assert client.get(f"{API}/analytics/reconciliation", headers=headers).json()["matches"] is True


def test_attempts_the_event_missed_or_that_were_deleted_are_reconciled_on_read(
    client, db, headers, learner, dbms, indexing, clock
):
    take(client, headers, dbms, right=8, clock=clock)
    take(client, headers, indexing, right=4, clock=clock)
    # An attempt recorded without analytics hearing about it (e.g. a bulk import).
    attempt_id = take(client, headers, dbms, right=6, clock=clock)
    analytics._fold(db, attempt_id, None)
    db.commit()
    assert stored_totals(db, learner.id)[("DBMS", "Normalization")][0] == 1

    body = dashboard(client, headers)

    assert topic(body, "Normalization")["attempts"] == 2
    assert stored_totals(db, learner.id) == raw_totals(db, learner.id)

    # The Indexing quiz is deleted along with its attempts: its figures go too.
    db.delete(db.get(Quiz, indexing[0]))
    db.commit()
    body = dashboard(client, headers)
    assert names(body["topics"]) == ["Normalization"]
    assert stored_totals(db, learner.id) == raw_totals(db, learner.id)


def test_an_attempt_closed_by_its_timer_is_counted(client, db, headers, learner, dbms, clock):
    quiz_id, ids = dbms
    attempt_id = start(client, headers, quiz_id).json()["id"]
    client.put(
        f"{API}/attempts/{attempt_id}/answers",
        json={"answers": {str(ids[0]): "A", str(ids[1]): "A"}},
        headers=headers,
    )
    clock.advance(hours=2)  # the 30-minute limit passes with nobody watching

    body = dashboard(client, headers)

    assert body["attempts"] == 1
    assert topic(body, "Normalization")["total_score"] == 2


# --------------------------------------------------------------------------- #
# UT_23 - subject-wise breakdown matches the stored quiz history
# --------------------------------------------------------------------------- #


def test_UT_23_dashboard_shows_DBMS_at_80_and_OS_at_60_matching_quiz_history(
    client, headers, dbms, indexing, scheduling, clock
):
    for quiz, right in [(dbms, 9), (indexing, 7), (scheduling, 5), (scheduling, 7)]:
        take(client, headers, quiz, right, clock)

    body = dashboard(client, headers)

    subjects = {s["subject"]: s for s in body["subjects"]}
    assert list(subjects) == ["DBMS", "Operating Systems"]
    assert subjects["DBMS"]["accuracy"] == 80.0
    assert subjects["Operating Systems"]["accuracy"] == 60.0
    assert names(subjects["DBMS"]["topics"]) == ["Indexing", "Normalization"]
    # Matches the quiz history the user sees under My Quiz History.
    history = client.get(f"{API}/attempts", headers=headers).json()["attempts"]
    by_subject: dict[str, list[float]] = {}
    for entry, quiz in zip(
        reversed(history), ["DBMS", "DBMS", "Operating Systems", "Operating Systems"], strict=True
    ):
        by_subject.setdefault(quiz, []).append(entry["percentage"])
    assert {k: sum(v) / len(v) for k, v in by_subject.items()} == {
        "DBMS": 80.0,
        "Operating Systems": 60.0,
    }


def test_each_subject_has_its_own_trend_over_time(client, headers, dbms, scheduling, clock):
    for quiz, right in [(dbms, 4), (scheduling, 9), (dbms, 6), (scheduling, 7), (dbms, 8)]:
        take(client, headers, quiz, right, clock)

    body = dashboard(client, headers)

    subjects = {s["subject"]: s for s in body["subjects"]}
    dbms_trend = subjects["DBMS"]["trend"]
    assert [p["percentage"] for p in dbms_trend["points"]] == [40, 60, 80]
    assert dbms_trend["direction"] == "improving" and dbms_trend["slope"] == 20
    os_trend = subjects["Operating Systems"]["trend"]
    assert [p["percentage"] for p in os_trend["points"]] == [90, 70]
    assert os_trend["direction"] == "declining"
    times = [p["submitted_at"] for p in body["trend"]["points"]]
    assert times == sorted(times) and len(times) == 5


# --------------------------------------------------------------------------- #
# IT_23 - the trend graph updates immediately after a new attempt
# --------------------------------------------------------------------------- #


def test_IT_23_trend_includes_the_new_attempt_and_the_updated_line(client, headers, dbms, clock):
    for right in (4, 5, 6):  # three prior attempts trending upward
        take(client, headers, dbms, right, clock)
    before = dashboard(client, headers)["trend"]
    assert [p["percentage"] for p in before["points"]] == [40, 50, 60]
    assert before["direction"] == "improving"
    assert (before["line_start"], before["line_end"]) == (40, 60)

    fourth = take(client, headers, dbms, right=9, clock=clock)
    after = dashboard(client, headers)["trend"]

    assert len(after["points"]) == 4
    assert after["points"][-1]["attempt_id"] == fourth
    assert after["points"][-1]["percentage"] == 90
    assert after["points"][-1]["running_accuracy"] == 60.0  # 24 of 40
    assert after["slope"] > before["slope"]
    assert after["line_end"] > before["line_end"]
    assert after["direction"] == "improving"


def test_trend_direction_needs_at_least_two_attempts(client, headers, dbms, clock):
    take(client, headers, dbms, right=7, clock=clock)
    trend = dashboard(client, headers)["trend"]
    assert trend["direction"] == "not_enough_data" and trend["slope"] is None
    assert len(trend["points"]) == 1


# --------------------------------------------------------------------------- #
# Empty state
# --------------------------------------------------------------------------- #


def test_a_new_user_with_no_attempts_sees_a_clear_empty_state(client, headers):
    body = dashboard(client, headers)

    assert body["empty"] is True
    assert body["empty_message"] == (
        "No quiz attempts yet. Attempt a quiz to see your performance here."
    )
    assert body["attempts"] == 0 and body["accuracy"] is None
    assert body["subjects"] == [] and body["topics"] == []
    assert body["strengths"] == [] and body["weak_areas"] == []
    assert body["trend"]["points"] == []


def test_a_running_attempt_does_not_end_the_empty_state(client, headers, dbms, clock):
    start(client, headers, dbms[0])
    assert dashboard(client, headers)["empty"] is True


# --------------------------------------------------------------------------- #
# UT_24 - strong and weak topics from per-topic accuracy
# --------------------------------------------------------------------------- #


def test_UT_24_normalization_at_90_is_a_strength_and_indexing_at_40_is_weak(
    client, headers, dbms, indexing, clock
):
    for quiz, right in [(dbms, 9), (indexing, 4), (dbms, 9), (indexing, 4)]:
        take(client, headers, quiz, right, clock)

    body = dashboard(client, headers)

    assert names(body["strengths"]) == ["Normalization"]
    assert names(body["weak_areas"]) == ["Indexing"]
    assert topic(body, "Normalization")["accuracy"] == 90.0
    assert topic(body, "Normalization")["standing"] == "strong"
    assert topic(body, "Indexing")["accuracy"] == 40.0
    assert topic(body, "Indexing")["standing"] == "weak"
    assert body["thresholds"] == {"min_attempts": 2, "strong_at": 75.0, "weak_below": 50.0}


def test_a_topic_between_the_thresholds_is_developing(client, headers, scheduling, clock):
    take(client, headers, scheduling, right=6, clock=clock)
    take(client, headers, scheduling, right=6, clock=clock)

    body = dashboard(client, headers)

    assert topic(body, "Scheduling")["standing"] == "developing"
    assert body["strengths"] == [] and body["weak_areas"] == []


def test_the_classification_updates_as_new_attempts_are_recorded(client, headers, indexing, clock):
    take(client, headers, indexing, right=3, clock=clock)
    take(client, headers, indexing, right=4, clock=clock)
    assert names(dashboard(client, headers)["weak_areas"]) == ["Indexing"]

    take(client, headers, indexing, right=10, clock=clock)
    assert topic(dashboard(client, headers), "Indexing")["standing"] == "developing"

    for _ in range(3):
        take(client, headers, indexing, right=10, clock=clock)
    body = dashboard(client, headers)
    assert topic(body, "Indexing")["accuracy"] == 78.3  # 47 of 60
    assert names(body["strengths"]) == ["Indexing"] and body["weak_areas"] == []


def test_topics_with_too_little_data_are_excluded_not_mislabelled(
    client, headers, dbms, indexing, clock
):
    take(client, headers, dbms, right=10, clock=clock)  # one perfect attempt
    take(client, headers, indexing, right=0, clock=clock)  # one failed attempt

    body = dashboard(client, headers)

    assert body["strengths"] == [] and body["weak_areas"] == []
    assert names(body["not_enough_data"]) == ["Indexing", "Normalization"]
    assert {t["standing"] for t in body["topics"]} == {"not_enough_data"}

    take(client, headers, dbms, right=10, clock=clock)  # now there is enough
    assert names(dashboard(client, headers)["strengths"]) == ["Normalization"]


@pytest.mark.parametrize(
    ("attempts_taken", "accuracy", "standing"),
    [
        (1, 100.0, analytics.Standing.NOT_ENOUGH_DATA),
        (2, None, analytics.Standing.NOT_ENOUGH_DATA),
        (2, 75.0, analytics.Standing.STRONG),
        (2, 74.9, analytics.Standing.DEVELOPING),
        (2, 50.0, analytics.Standing.DEVELOPING),
        (2, 49.9, analytics.Standing.WEAK),
    ],
)
def test_classification_thresholds(attempts_taken, accuracy, standing):
    assert analytics.classify(attempts_taken, accuracy) is standing


# --------------------------------------------------------------------------- #
# Access
# --------------------------------------------------------------------------- #


def test_the_dashboard_needs_a_signed_in_user(client):
    assert client.get(f"{API}/analytics/dashboard").status_code == 401
    assert client.get(f"{API}/analytics/reconciliation").status_code == 401


def test_each_user_sees_only_their_own_performance(client, db, headers, dbms, clock):
    make_user(db, "someone_else")
    other = auth(client, "someone_else")
    take(client, other, dbms, right=10, clock=clock)
    take(client, headers, dbms, right=3, clock=clock)

    mine = dashboard(client, headers)
    theirs = dashboard(client, other)

    assert (mine["attempts"], mine["accuracy"]) == (1, 30.0)
    assert (theirs["attempts"], theirs["accuracy"]) == (1, 100.0)
