"""S06.2 - attempt persistence and quiz history (issue #29, FR-06, NFR-04).

Test Plan cases are named after their IDs: IT_11 and ST_12. The NFR-04 tests run the
real ``get_db`` session handling against a file database and inject a transient
database failure part-way through a submission.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from app.core import database
from app.main import TEMPORARY_FAILURE, create_app
from app.modules.quizzes import attempts
from app.modules.quizzes.models import AttemptAnswer, QuizAttempt
from tests.test_quiz_attempts import (
    API,
    Clock,
    auth,
    make_quiz,
    make_user,
    mcqs,
    save,
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
    return make_user(db, "quiz_author")


@pytest.fixture()
def student(client, db):
    make_user(db, "student_a")
    return auth(client, "student_a")


@pytest.fixture()
def five_mcq_quiz(db, author):
    ids = mcqs(db, author.id)
    return make_quiz(db, author.id, ids), ids


def take(client, headers, quiz_id, ids, right: int):
    """Attempt the quiz answering the first ``right`` questions correctly."""
    attempt = start(client, headers, quiz_id).json()
    answers = {qid: ("A" if i < right else "B") for i, qid in enumerate(ids)}
    response = submit(client, headers, attempt["id"], answers)
    assert response.status_code == 200, response.text
    return response.json()


def history(client, headers, **params):
    return client.get(f"{API}/attempts", headers=headers, params=params)


# --------------------------------------------------------------------------- #
# IT_11 - a submitted attempt is listed in My Quiz History and opens accurately
# --------------------------------------------------------------------------- #


def test_IT_11_submitted_attempt_is_listed_and_opens_with_an_accurate_breakdown(
    client, student, five_mcq_quiz, clock
):
    quiz_id, ids = five_mcq_quiz
    submitted = take(client, student, quiz_id, ids, right=4)
    clock.advance(days=2)  # revisit later

    listed = history(client, student)

    assert listed.status_code == 200
    body = listed.json()
    assert body["attempt_count"] == 1
    entry = body["attempts"][0]
    assert entry["attempt_id"] == submitted["id"]
    assert entry["quiz_title"] == "Normalization drill"
    assert (entry["score"], entry["max_score"], entry["percentage"]) == (4, 5, 80)
    assert entry["display"] == "4/5 (80%)"
    assert entry["submitted_at"] == submitted["result"]["submitted_at"]
    assert entry["started_at"] and entry["auto_submitted"] is False

    opened = client.get(f"{API}/attempts/{entry['attempt_id']}", headers=student).json()
    assert opened["result"] == submitted["result"]
    statuses = [a["status"] for a in opened["result"]["answers"]]
    assert statuses == ["correct"] * 4 + ["incorrect"]
    assert opened["result"]["answers"][4]["response"] == "B"


def test_history_lists_attempts_newest_first(client, student, five_mcq_quiz, clock):
    quiz_id, ids = five_mcq_quiz
    first = take(client, student, quiz_id, ids, right=2)
    clock.advance(hours=1)
    second = take(client, student, quiz_id, ids, right=5)

    listed = history(client, student).json()["attempts"]

    assert [e["attempt_id"] for e in listed] == [second["id"], first["id"]]
    assert [e["percentage"] for e in listed] == [100, 40]


def test_attempts_still_running_are_not_listed(client, student, five_mcq_quiz, clock):
    quiz_id, ids = five_mcq_quiz
    attempt = start(client, student, quiz_id).json()
    save(client, student, attempt["id"], {ids[0]: "A"})

    body = history(client, student).json()

    assert body["attempt_count"] == 0
    assert body["attempts"] == []
    assert body["average_accuracy"] is None and body["best_percentage"] is None


def test_an_attempt_whose_timer_ran_out_appears_auto_submitted(
    client, student, five_mcq_quiz, clock
):
    quiz_id, ids = five_mcq_quiz
    attempt = start(client, student, quiz_id).json()
    save(client, student, attempt["id"], {ids[0]: "A", ids[1]: "A"})
    clock.advance(minutes=11)  # the quiz lasts 10 minutes; the user never came back

    entry = history(client, student).json()["attempts"][0]

    assert entry["attempt_id"] == attempt["id"]
    assert entry["auto_submitted"] is True
    assert entry["display"] == "2/5 (40%)"
    assert entry["submitted_at"] == attempt["expires_at"]


def test_history_shows_only_the_callers_own_attempts(client, db, student, five_mcq_quiz, clock):
    quiz_id, ids = five_mcq_quiz
    take(client, student, quiz_id, ids, right=3)
    make_user(db, "student_b")
    other = auth(client, "student_b")

    assert history(client, other).json()["attempt_count"] == 0
    assert history(client, student).json()["attempt_count"] == 1


def test_history_can_be_filtered_to_one_quiz(client, db, student, author, five_mcq_quiz, clock):
    quiz_id, ids = five_mcq_quiz
    other_ids = mcqs(db, author.id, count=2)
    other_quiz = make_quiz(db, author.id, other_ids)
    take(client, student, quiz_id, ids, right=5)
    take(client, student, other_quiz, other_ids, right=1)

    body = history(client, student, quiz_id=other_quiz).json()

    assert body["attempt_count"] == 1
    assert body["attempts"][0]["quiz_id"] == other_quiz
    assert body["average_accuracy"] == 50
    assert history(client, student).json()["attempt_count"] == 2


def test_history_needs_a_signed_in_user(client):
    assert client.get(f"{API}/attempts").status_code == 401


# --------------------------------------------------------------------------- #
# ST_12 - aggregate accuracy across attempts
# --------------------------------------------------------------------------- #


def test_ST_12_average_accuracy_updates_to_82_5_after_a_fourth_attempt(
    client, db, student, author, clock
):
    ids = mcqs(db, author.id, count=10)
    quiz_id = make_quiz(db, author.id, ids)
    for right in (6, 8, 10):  # three prior attempts: 60%, 80%, 100%
        take(client, student, quiz_id, ids, right=right)
        clock.advance(minutes=30)
    assert history(client, student).json()["average_accuracy"] == 80

    take(client, student, quiz_id, ids, right=9)  # the 4th attempt scores 90%

    body = history(client, student).json()
    assert body["attempt_count"] == 4
    assert [e["percentage"] for e in body["attempts"]] == [90, 100, 80, 60]
    assert body["average_accuracy"] == 82.5
    assert body["best_percentage"] == 100


def test_each_attempt_counts_once_whatever_the_quiz_length(client, db, student, author, clock):
    short_ids = mcqs(db, author.id, count=2)
    long_ids = mcqs(db, author.id, count=10)
    take(client, student, make_quiz(db, author.id, short_ids), short_ids, right=2)  # 100%
    take(client, student, make_quiz(db, author.id, long_ids), long_ids, right=5)  # 50%

    # The mean of the percentages, not 7 correct out of 12 questions (58.3%).
    assert history(client, student).json()["average_accuracy"] == 75


def test_average_is_rounded_to_one_decimal(client, db, student, author, clock):
    ids = mcqs(db, author.id, count=3)
    quiz_id = make_quiz(db, author.id, ids)
    for right in (1, 1, 2):  # 33.33..., 33.33..., 66.66...
        take(client, student, quiz_id, ids, right=right)

    body = history(client, student).json()
    assert body["average_accuracy"] == 44.4
    assert [e["percentage"] for e in body["attempts"]] == [66.7, 33.3, 33.3]


# --------------------------------------------------------------------------- #
# NFR-04 - no committed attempt is lost during a simulated transient failure
# --------------------------------------------------------------------------- #


@pytest.fixture()
def real_sessions(tmp_path, monkeypatch):
    """The API with the real ``get_db`` over a file database, not the shared test session."""
    import app.models  # noqa: F401

    engine = create_engine(
        f"sqlite:///{tmp_path / 'nfr04.db'}", connect_args={"check_same_thread": False}
    )
    database.Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(database, "SessionLocal", factory)
    with TestClient(create_app()) as api:
        yield api, factory
    engine.dispose()


def _transient_error() -> OperationalError:
    return OperationalError("COMMIT", {}, Exception("server closed the connection unexpectedly"))


def _setup_quiz(factory) -> tuple[int, list[int]]:
    with factory() as session:
        quiz_author = make_user(session, "nfr_author")
        make_user(session, "nfr_student")
        ids = mcqs(session, quiz_author.id)
        quiz_id = make_quiz(session, quiz_author.id, ids)
    return quiz_id, ids


def test_NFR_04_no_committed_attempt_is_lost_when_a_submission_fails_midway(
    real_sessions, monkeypatch, clock
):
    api, factory = real_sessions
    quiz_id, ids = _setup_quiz(factory)
    headers = auth(api, "nfr_student")
    earlier = take(api, headers, quiz_id, ids, right=3)  # committed: 3/5
    running = start(api, headers, quiz_id).json()
    save(api, headers, running["id"], {ids[0]: "A", ids[1]: "A"})  # committed answers

    # The connection drops after scoring has been written but before the commit.
    real_finalise = attempts._finalise

    def failing_finalise(*args, **kwargs):
        real_finalise(*args, **kwargs)
        raise _transient_error()

    monkeypatch.setattr(attempts, "_finalise", failing_finalise)
    failed = submit(api, headers, running["id"], {ids[2]: "A"}, confirm=True)
    monkeypatch.setattr(attempts, "_finalise", real_finalise)

    assert failed.status_code == 503
    assert failed.json()["detail"] == TEMPORARY_FAILURE
    with factory() as session:  # what is really stored, seen from a new connection
        kept = session.get(QuizAttempt, earlier["id"])
        assert (kept.status, kept.score, kept.max_score) == ("submitted", 3, 5)
        half = session.get(QuizAttempt, running["id"])
        assert (half.status, half.score, half.submitted_at) == ("in_progress", None, None)
        saved = session.scalars(
            select(AttemptAnswer).where(AttemptAnswer.attempt_id == running["id"])
        ).all()
        assert {a.question_id: a.response for a in saved} == {ids[0]: "A", ids[1]: "A"}
        assert all(a.score is None for a in saved)  # no half-written scoring

    retried = submit(api, headers, running["id"], {ids[2]: "A"}, confirm=True)
    assert retried.status_code == 200
    assert retried.json()["result"]["display"] == "3/5 (60%)"
    body = history(api, headers).json()
    assert [e["attempt_id"] for e in body["attempts"]] == [running["id"], earlier["id"]]
    assert body["average_accuracy"] == 60


def test_NFR_04_a_failed_commit_keeps_earlier_attempts_and_can_be_retried(
    real_sessions, monkeypatch, clock
):
    api, factory = real_sessions
    quiz_id, ids = _setup_quiz(factory)
    headers = auth(api, "nfr_student")
    earlier = take(api, headers, quiz_id, ids, right=5)
    running = start(api, headers, quiz_id).json()

    real_commit = Session.commit
    failures = {"left": 1}

    def flaky_commit(self):
        if failures["left"]:
            failures["left"] -= 1
            raise _transient_error()
        real_commit(self)

    monkeypatch.setattr(Session, "commit", flaky_commit)
    answers = {qid: "A" for qid in ids}

    assert submit(api, headers, running["id"], answers).status_code == 503
    assert history(api, headers).json()["attempt_count"] == 1  # the earlier one is intact
    assert client_state(api, headers, running["id"])["status"] == "in_progress"

    retried = submit(api, headers, running["id"], answers)
    assert retried.status_code == 200
    body = history(api, headers).json()
    assert body["attempt_count"] == 2
    assert {e["attempt_id"]: e["percentage"] for e in body["attempts"]} == {
        earlier["id"]: 100,
        running["id"]: 100,
    }


def client_state(api, headers, attempt_id):
    return api.get(f"{API}/attempts/{attempt_id}", headers=headers).json()
