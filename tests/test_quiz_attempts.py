"""S06.1 - quiz attempt with automatic scoring and timer (issue #28, FR-06).

Test Plan cases are named after their IDs: UT_11, UT_12, ST_11, IT_12. Quizzes are
built with the quizzes service here; the quiz-building endpoints are S05.2 (#25).
"""

import ast
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.modules.accounts import service as accounts
from app.modules.accounts.models import Role
from app.modules.channels import service as channels
from app.modules.channels.models import ApprovalStatus, ChannelType
from app.modules.questions import service as questions
from app.modules.questions.models import Difficulty, QuestionType
from app.modules.quizzes import attempts
from app.modules.quizzes import service as quizzes
from app.modules.quizzes.models import AttemptAnswer, QuizAttempt

API = "/api/v1"
PASSWORD = "Pass@1234"
OPTIONS = ["A", "B", "C", "D"]


def make_user(db, username: str, role: Role = Role.STUDENT):
    user = accounts.create_user(
        db,
        email=f"{username}@test.com",
        display_name=username.title(),
        password=PASSWORD,
        role=role,
        username=username,
    )
    db.commit()
    return user


def auth(client: TestClient, username: str) -> dict[str, str]:
    response = client.post(
        "/api/v1/accounts/login", json={"username": username, "password": PASSWORD}
    )
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


class Clock:
    """Stands in for the attempt clock so the timer can be run forward."""

    def __init__(self) -> None:
        self.now = datetime(2026, 10, 21, 9, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **delta) -> None:
        self.now += timedelta(**delta)


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


def mcqs(db, author_id: int, count: int = 5, channel_id: int | None = None) -> list[int]:
    """``count`` MCQs whose correct answer is always "A"."""
    return [
        questions.create_question(
            db,
            author_id=author_id,
            subject="DBMS",
            topic="Normalization",
            difficulty=Difficulty.EASY,
            question_type=QuestionType.MULTIPLE_CHOICE,
            text=f"Question {i}?",
            options=OPTIONS,
            correct_answer="A",
            explanation=f"A is right for question {i}.",
            channel_id=channel_id,
        ).id
        for i in range(1, count + 1)
    ]


def make_quiz(
    db,
    author_id: int,
    question_ids: list[int],
    *,
    minutes: int = 10,
    channel_id: int | None = None,
    publish: bool = True,
) -> int:
    quiz_id = quizzes.create_quiz(
        db,
        creator_id=author_id,
        title="Normalization drill",
        subject="DBMS",
        topic="Normalization",
        difficulty="easy",
        duration_minutes=minutes,
        question_ids=question_ids,
        channel_id=channel_id,
        publish=publish,
    )
    db.commit()
    return quiz_id


@pytest.fixture()
def five_mcq_quiz(db, author):
    ids = mcqs(db, author.id)
    return make_quiz(db, author.id, ids), ids


def start(client, headers, quiz_id):
    return client.post(f"{API}/quizzes/{quiz_id}/attempts", headers=headers)


def submit(client, headers, attempt_id, answers=None, confirm=False):
    return client.post(
        f"{API}/attempts/{attempt_id}/submit",
        json={"answers": {str(k): v for k, v in (answers or {}).items()}, "confirm": confirm},
        headers=headers,
    )


def save(client, headers, attempt_id, answers):
    return client.put(
        f"{API}/attempts/{attempt_id}/answers",
        json={"answers": {str(k): v for k, v in answers.items()}},
        headers=headers,
    )


# --------------------------------------------------------------------------- #
# UT_11 - objective questions are scored automatically and shown immediately
# --------------------------------------------------------------------------- #


def test_UT_11_four_of_five_is_scored_80_percent_and_shown_immediately(
    client, student, five_mcq_quiz, clock
):
    quiz_id, ids = five_mcq_quiz
    attempt = start(client, student, quiz_id)
    assert attempt.status_code == 201
    answers = {qid: "A" for qid in ids[:4]} | {ids[4]: "B"}

    response = submit(client, student, attempt.json()["id"], answers)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "submitted"
    result = body["result"]
    assert (result["score"], result["max_score"], result["percentage"]) == (4, 5, 80)
    assert result["display"] == "4/5 (80%)"
    assert (result["correct"], result["incorrect"], result["unattempted"]) == (4, 1, 0)
    assert [a["status"] for a in result["answers"]] == ["correct"] * 4 + ["incorrect"]
    wrong = result["answers"][4]
    assert wrong["response"] == "B" and wrong["correct_answer"] == "A"
    assert wrong["explanation"] == "A is right for question 5."


def test_objective_scoring_is_deterministic_and_never_uses_the_llm(
    client, db, student, five_mcq_quiz, clock
):
    quiz_id, ids = five_mcq_quiz
    answers = {qid: "A" for qid in ids[:3]} | {ids[3]: "c", ids[4]: " a "}
    results = []
    for _ in range(3):
        attempt = start(client, student, quiz_id).json()
        results.append(submit(client, student, attempt["id"], answers).json()["result"]["score"])
    assert results == [4, 4, 4]  # " a " matches "A"; "c" does not

    # NFR-06: the scoring path imports nothing from the LLM module.
    for name in ("attempts.py", "scoring.py", "service.py"):
        tree = ast.parse(Path("app/modules/quizzes", name).read_text())
        imported = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)} | {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        assert not any(m and m.startswith("app.modules.llm") for m in imported), name


def test_true_false_and_short_answer_are_scored_too(client, db, author, student, clock):
    tf = questions.create_question(
        db,
        author_id=author.id,
        subject="DBMS",
        topic="Keys",
        difficulty=Difficulty.EASY,
        question_type=QuestionType.TRUE_FALSE,
        text="A primary key can be null.",
        options=["True", "False"],
        correct_answer="False",
    ).id
    short = questions.create_question(
        db,
        author_id=author.id,
        subject="DBMS",
        topic="Keys",
        difficulty=Difficulty.EASY,
        question_type=QuestionType.SHORT_ANSWER,
        text="Which normal form removes partial dependencies?",
        correct_answer="2NF",
    ).id
    quiz_id = make_quiz(db, author.id, [tf, short])
    attempt = start(client, student, quiz_id).json()
    result = submit(client, student, attempt["id"], {tf: "false", short: "2nf"}).json()["result"]
    assert result["display"] == "2/2 (100%)"


def test_descriptive_answers_are_stored_for_evaluation_not_scored(
    client, db, author, student, clock
):
    [mcq] = mcqs(db, author.id, 1)
    essay = questions.create_question(
        db,
        author_id=author.id,
        subject="DBMS",
        topic="Normalization",
        difficulty=Difficulty.MEDIUM,
        question_type=QuestionType.DESCRIPTIVE,
        text="Explain 3NF.",
        expected_concepts=["transitive dependency"],
    ).id
    quiz_id = make_quiz(db, author.id, [mcq, essay])
    attempt = start(client, student, quiz_id).json()
    result = submit(
        client, student, attempt["id"], {mcq: "A", essay: "No transitive dependencies."}
    ).json()["result"]
    assert result["pending_evaluation"] == 1
    assert result["answers"][1]["status"] == "pending_evaluation"
    assert result["answers"][1]["score"] is None
    stored = db.query(AttemptAnswer).filter_by(question_id=essay).one()
    assert stored.response == "No transitive dependencies." and stored.score is None


def test_the_answer_key_is_hidden_until_the_attempt_is_submitted(
    client, student, five_mcq_quiz, clock
):
    quiz_id, _ = five_mcq_quiz
    body = start(client, student, quiz_id).json()
    assert body["result"] is None
    assert "correct_answer" not in body["questions"][0]
    assert body["questions"][0]["options"] == OPTIONS


def test_submission_and_result_stay_inside_the_response_time_target(
    client, db, author, student, clock
):
    """NFR-01: submission with result display within 3 s; measured here on 50 questions."""
    ids = mcqs(db, author.id, 50)
    quiz_id = make_quiz(db, author.id, ids)
    attempt = start(client, student, quiz_id).json()
    started = time.perf_counter()
    response = submit(client, student, attempt["id"], {qid: "A" for qid in ids})
    elapsed = time.perf_counter() - started
    assert response.status_code == 200
    assert response.json()["result"]["display"] == "50/50 (100%)"
    assert elapsed < 3.0


# --------------------------------------------------------------------------- #
# UT_12 - warning about unanswered questions, per the configured rule
# --------------------------------------------------------------------------- #


def test_UT_12_warning_names_the_unanswered_count_before_final_submission(
    client, db, student, five_mcq_quiz, clock
):
    quiz_id, ids = five_mcq_quiz
    attempt_id = start(client, student, quiz_id).json()["id"]

    response = submit(client, student, attempt_id, {qid: "A" for qid in ids[:3]})

    assert response.status_code == 409
    body = response.json()
    assert body["detail"] == "2 questions are unanswered"
    assert body["unanswered_count"] == 2
    assert body["unanswered_question_ids"] == ids[3:]
    assert body["unanswered_rule"] == "warn"
    assert body["attempt"]["status"] == "in_progress"
    assert body["attempt"]["warning"] == "2 questions are unanswered"
    assert body["attempt"]["answered_count"] == 3  # the three answers were kept

    confirmed = submit(client, student, attempt_id, confirm=True)
    assert confirmed.status_code == 200
    result = confirmed.json()["result"]
    assert result["display"] == "3/5 (60%)"
    assert result["unattempted"] == 2


def test_the_warning_is_also_shown_while_answering(client, student, five_mcq_quiz, clock):
    quiz_id, ids = five_mcq_quiz
    attempt_id = start(client, student, quiz_id).json()["id"]
    body = save(client, student, attempt_id, {ids[0]: "A", ids[1]: "B", ids[2]: "C", ids[3]: "D"})
    assert body.json()["warning"] == "1 question is unanswered"
    body = save(client, student, attempt_id, {ids[4]: "A"})
    assert body.json()["warning"] is None and body.json()["unanswered_count"] == 0


def test_UT_12_block_rule_refuses_submission_until_all_are_answered(
    client, student, five_mcq_quiz, clock, monkeypatch
):
    monkeypatch.setattr(settings, "quiz_unanswered_rule", "block")
    quiz_id, ids = five_mcq_quiz
    attempt_id = start(client, student, quiz_id).json()["id"]

    refused = submit(client, student, attempt_id, {qid: "A" for qid in ids[:3]}, confirm=True)
    assert refused.status_code == 422
    assert refused.json()["detail"] == "2 questions are unanswered"
    assert refused.json()["unanswered_rule"] == "block"
    assert refused.json()["attempt"]["status"] == "in_progress"

    done = submit(client, student, attempt_id, {ids[3]: "A", ids[4]: "B"})
    assert done.status_code == 200
    assert done.json()["result"]["display"] == "4/5 (80%)"


def test_a_complete_attempt_submits_without_any_warning(client, student, five_mcq_quiz, clock):
    quiz_id, ids = five_mcq_quiz
    attempt_id = start(client, student, quiz_id).json()["id"]
    response = submit(client, student, attempt_id, {qid: "A" for qid in ids})
    assert response.status_code == 200 and response.json()["warning"] is None


def test_blank_answers_count_as_unanswered(client, student, five_mcq_quiz, clock):
    quiz_id, ids = five_mcq_quiz
    attempt_id = start(client, student, quiz_id).json()["id"]
    answers = {qid: "A" for qid in ids[:4]} | {ids[4]: "   "}
    assert submit(client, student, attempt_id, answers).json()["detail"] == (
        "1 question is unanswered"
    )


# --------------------------------------------------------------------------- #
# ST_11 - the timer: remaining time, auto-submit at zero
# --------------------------------------------------------------------------- #


def test_ST_11_quiz_auto_submits_at_timeout_scoring_only_answered_questions(
    client, db, author, student, clock
):
    ids = mcqs(db, author.id)
    quiz_id = make_quiz(db, author.id, ids, minutes=1)
    attempt = start(client, student, quiz_id).json()
    assert attempt["remaining_seconds"] == 60

    clock.advance(seconds=20)
    saved = save(client, student, attempt["id"], {ids[0]: "A", ids[1]: "B"})
    assert saved.json()["remaining_seconds"] == 40

    clock.advance(seconds=41)  # the timer reaches zero without a manual submit
    body = client.get(f"{API}/attempts/{attempt['id']}", headers=student).json()

    assert body["status"] == "submitted" and body["auto_submitted"] is True
    assert body["remaining_seconds"] == 0
    result = body["result"]
    assert [a["status"] for a in result["answers"]] == [
        "correct",
        "incorrect",
        "unattempted",
        "unattempted",
        "unattempted",
    ]
    assert result["display"] == "1/5 (20%)"
    assert (result["correct"], result["incorrect"], result["unattempted"]) == (1, 1, 3)
    row = db.get(QuizAttempt, attempt["id"])
    assert row.score == 1.0 and row.auto_submitted


def test_late_answers_are_not_counted_after_the_timer_ends(client, db, author, student, clock):
    ids = mcqs(db, author.id)
    quiz_id = make_quiz(db, author.id, ids, minutes=1)
    attempt_id = start(client, student, quiz_id).json()["id"]
    save(client, student, attempt_id, {ids[0]: "A"})
    clock.advance(minutes=2)

    late = submit(client, student, attempt_id, {qid: "A" for qid in ids}, confirm=True)

    assert late.status_code == 200
    body = late.json()
    assert body["auto_submitted"] is True
    assert body["result"]["display"] == "1/5 (20%)"
    # And saving after the limit closes the attempt instead of adding answers.
    assert save(client, student, attempt_id, {ids[1]: "A"}).status_code == 409


def test_a_submit_arriving_just_after_zero_still_counts(client, db, author, student, clock):
    ids = mcqs(db, author.id)
    quiz_id = make_quiz(db, author.id, ids, minutes=1)
    attempt_id = start(client, student, quiz_id).json()["id"]
    clock.advance(seconds=62)  # inside the latency grace period
    body = submit(client, student, attempt_id, {qid: "A" for qid in ids}).json()
    assert body["auto_submitted"] is False
    assert body["result"]["display"] == "5/5 (100%)"


def test_saving_after_the_limit_auto_submits(client, db, author, student, clock):
    ids = mcqs(db, author.id)
    quiz_id = make_quiz(db, author.id, ids, minutes=1)
    attempt_id = start(client, student, quiz_id).json()["id"]
    save(client, student, attempt_id, {ids[0]: "A"})
    clock.advance(seconds=61)
    response = save(client, student, attempt_id, {ids[1]: "A"})
    assert response.status_code == 409
    assert response.json()["detail"] == "This attempt has already been submitted"
    body = client.get(f"{API}/attempts/{attempt_id}", headers=student).json()
    assert body["auto_submitted"] and body["result"]["display"] == "1/5 (20%)"


def test_remaining_time_is_visible_throughout_the_attempt(client, student, five_mcq_quiz, clock):
    quiz_id, ids = five_mcq_quiz
    attempt = start(client, student, quiz_id).json()
    assert attempt["remaining_seconds"] == 600
    assert attempt["expires_at"].startswith("2026-10-21T09:10:00")
    for elapsed, expected in ((90, 510), (300, 210), (209, 1)):
        clock.advance(seconds=elapsed)
        body = client.get(f"{API}/attempts/{attempt['id']}", headers=student).json()
        assert body["remaining_seconds"] == expected
    body = save(client, student, attempt["id"], {ids[0]: "A"}).json()
    assert body["remaining_seconds"] == 1


def test_reopening_a_running_quiz_resumes_the_same_timer(client, student, five_mcq_quiz, clock):
    quiz_id, ids = five_mcq_quiz
    first = start(client, student, quiz_id).json()
    save(client, student, first["id"], {ids[0]: "A"})
    clock.advance(minutes=4)
    again = start(client, student, quiz_id)
    assert again.status_code == 200
    assert again.json()["id"] == first["id"]
    assert again.json()["remaining_seconds"] == 360
    assert again.json()["questions"][0]["response"] == "A"


def test_a_new_attempt_can_start_after_the_previous_one_is_submitted(
    client, student, five_mcq_quiz, clock
):
    quiz_id, ids = five_mcq_quiz
    first = start(client, student, quiz_id).json()
    submit(client, student, first["id"], {qid: "A" for qid in ids})
    second = start(client, student, quiz_id)
    assert second.status_code == 201 and second.json()["id"] != first["id"]


def test_the_time_limit_is_fixed_when_the_attempt_starts(client, db, author, student, clock):
    """A running attempt keeps its limit even if the quiz duration changes later."""
    ids = mcqs(db, author.id)
    quiz_id = make_quiz(db, author.id, ids, minutes=1)
    attempt_id = start(client, student, quiz_id).json()["id"]
    from app.modules.quizzes.models import Quiz

    db.get(Quiz, quiz_id).duration_minutes = 30
    db.commit()
    clock.advance(seconds=61)
    assert client.get(f"{API}/attempts/{attempt_id}", headers=student).json()["auto_submitted"]


def test_a_submitted_attempt_cannot_be_submitted_again(client, student, five_mcq_quiz, clock):
    quiz_id, ids = five_mcq_quiz
    attempt_id = start(client, student, quiz_id).json()["id"]
    submit(client, student, attempt_id, {qid: "A" for qid in ids})
    again = submit(client, student, attempt_id, {qid: "B" for qid in ids})
    assert again.status_code == 409
    assert again.json()["detail"] == "This attempt has already been submitted"


# --------------------------------------------------------------------------- #
# IT_12 - official quizzes; channel membership
# --------------------------------------------------------------------------- #


def _channel(db, owner_id: int, channel_type=ChannelType.NORMAL):
    channel = channels.create_channel(
        db, name="DBMS group", created_by=owner_id, channel_type=channel_type, subject="DBMS"
    )
    if channel_type is ChannelType.AUTHORIZED:
        channels.set_approval(db, channel.id, ApprovalStatus.APPROVED)
    db.commit()
    return channel


def test_IT_12_official_quiz_is_attempted_and_scored_like_a_peer_quiz(client, db, clock):
    faculty = make_user(db, "faculty_a", Role.AUTHORIZED_USER)
    peer = make_user(db, "peer_a")
    member = make_user(db, "member_a")
    official = _channel(db, faculty.id, ChannelType.AUTHORIZED)
    normal = _channel(db, peer.id)
    channels.add_member(db, official.id, member.id)
    channels.add_member(db, normal.id, member.id)
    official_quiz = make_quiz(
        db, faculty.id, mcqs(db, faculty.id, channel_id=official.id), channel_id=official.id
    )
    peer_quiz = make_quiz(
        db, peer.id, mcqs(db, peer.id, channel_id=normal.id), channel_id=normal.id
    )
    headers = auth(client, "member_a")

    results = []
    for quiz_id in (official_quiz, peer_quiz):
        attempt = start(client, headers, quiz_id)
        assert attempt.status_code == 201
        ids = [q["question_id"] for q in attempt.json()["questions"]]
        answers = {qid: "A" for qid in ids[:4]} | {ids[4]: "D"}
        body = submit(client, headers, attempt.json()["id"], answers).json()
        results.append(
            {
                k: body["result"][k]
                for k in ("score", "max_score", "percentage", "display", "correct", "incorrect")
            }
        )

    assert results[0] == results[1]
    assert results[0]["display"] == "4/5 (80%)"


def test_non_members_cannot_attempt_a_channel_quiz(client, db, author, student, clock):
    channel = _channel(db, author.id)
    quiz_id = make_quiz(
        db, author.id, mcqs(db, author.id, channel_id=channel.id), channel_id=channel.id
    )
    response = start(client, student, quiz_id)
    assert response.status_code == 403
    assert response.json()["detail"] == "Join the channel to attempt this quiz"
    assert db.query(QuizAttempt).count() == 0


def test_a_member_who_leaves_cannot_start_again(client, db, author, clock):
    channel = _channel(db, author.id)
    leaver = make_user(db, "leaver_a")
    channels.add_member(db, channel.id, leaver.id)
    db.commit()
    quiz_id = make_quiz(
        db, author.id, mcqs(db, author.id, channel_id=channel.id), channel_id=channel.id
    )
    headers = auth(client, "leaver_a")
    first = start(client, headers, quiz_id).json()
    submit(client, headers, first["id"], {q["question_id"]: "A" for q in first["questions"]})
    channels.remove_member(db, channel.id, leaver.id)
    db.commit()
    assert start(client, headers, quiz_id).status_code == 403


def test_a_quiz_in_a_hidden_channel_is_not_revealed(client, db, student, clock):
    faculty = make_user(db, "faculty_b", Role.AUTHORIZED_USER)
    pending = channels.create_channel(
        db, name="Pending", created_by=faculty.id, channel_type=ChannelType.AUTHORIZED
    )
    db.commit()
    quiz_id = make_quiz(
        db, faculty.id, mcqs(db, faculty.id, channel_id=pending.id), channel_id=pending.id
    )
    response = start(client, student, quiz_id)
    assert response.status_code == 404 and response.json()["detail"] == "Quiz not found"


def test_general_quizzes_are_open_to_any_signed_in_user(client, student, five_mcq_quiz, clock):
    quiz_id, _ = five_mcq_quiz
    assert start(client, student, quiz_id).status_code == 201
    assert client.post(f"{API}/quizzes/{quiz_id}/attempts").status_code == 401


def test_drafts_and_missing_quizzes_cannot_be_attempted(client, db, author, student, clock):
    draft = make_quiz(db, author.id, mcqs(db, author.id), publish=False)
    for quiz_id in (draft, 9999):
        response = start(client, student, quiz_id)
        assert response.status_code == 404 and response.json()["detail"] == "Quiz not found"


def test_an_empty_quiz_cannot_be_started(client, db, author, student, clock):
    quiz_id = make_quiz(db, author.id, [])
    response = start(client, student, quiz_id)
    assert response.status_code == 422
    assert response.json()["detail"] == "This quiz has no questions yet"


def test_only_the_person_taking_it_can_see_or_change_an_attempt(
    client, db, student, five_mcq_quiz, clock
):
    quiz_id, ids = five_mcq_quiz
    attempt_id = start(client, student, quiz_id).json()["id"]
    make_user(db, "student_b")
    other = auth(client, "student_b")
    for response in (
        client.get(f"{API}/attempts/{attempt_id}", headers=other),
        save(client, other, attempt_id, {ids[0]: "A"}),
        submit(client, other, attempt_id, {ids[0]: "A"}, confirm=True),
        client.get(f"{API}/attempts/9999", headers=student),
    ):
        assert response.status_code == 404
        assert response.json()["detail"] == "Attempt not found"


def test_answers_must_belong_to_the_quiz(client, db, author, student, five_mcq_quiz, clock):
    quiz_id, _ = five_mcq_quiz
    [stranger] = mcqs(db, author.id, 1)
    attempt_id = start(client, student, quiz_id).json()["id"]
    response = save(client, student, attempt_id, {stranger: "A"})
    assert response.status_code == 422
    assert response.json()["detail"] == f"Question {stranger} is not part of this quiz"
    assert submit(client, student, attempt_id, {stranger: "A"}).status_code == 422
