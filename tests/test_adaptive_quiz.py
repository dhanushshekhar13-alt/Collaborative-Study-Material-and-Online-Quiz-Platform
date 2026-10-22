"""S07.2 - adaptive difficulty constrained to the selected topic (issue #31, FR-07).

Test Plan cases are named after their IDs: IT_14, ST_13, ST_14. Questions come from
the bank or from the deterministic fake provider; nothing calls a real model.
"""

import json

import pytest
from fastapi.testclient import TestClient

from app.modules.accounts import service as accounts
from app.modules.accounts.models import Role
from app.modules.llm.adaptive import level_after
from app.modules.llm.models import AdaptiveSession, LlmRequest, LlmTask
from app.modules.llm.provider import FakeProvider, LlmError
from app.modules.llm.router import get_llm_provider
from app.modules.questions import service as questions
from app.modules.questions.service import Difficulty, QuestionType

API = "/api/v1/ai/adaptive-quizzes"
PASSWORD = "Pass@1234"
E, M, H = Difficulty.EASY, Difficulty.MEDIUM, Difficulty.HARD


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


def use_provider(client: TestClient, provider) -> None:
    client.app.dependency_overrides[get_llm_provider] = lambda: provider


class OffTopicFirst:
    """Wraps the fake provider but answers every first round with another topic."""

    name = "off-topic-first"

    def __init__(self):
        self.inner, self.calls = FakeProvider(), []

    def complete(self, task, prompt, params):
        self.calls.append(dict(params))
        text = self.inner.complete(task, prompt, params)
        if params.get("round", 0) == 0:
            payload = json.loads(text)
            for item in payload["questions"]:
                item["topic"] = "Computer Networks"
            return json.dumps(payload)
        return text


@pytest.fixture()
def student(client, db):
    make_user(db, "adaptive_student")
    use_provider(client, FakeProvider())
    return auth(client, "adaptive_student")


def start(client, headers, **body):
    payload = {"subject": "Computer Science", "topic": "DBMS", "start_difficulty": "medium"}
    response = client.post(API, json=payload | body, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def correct_answer(db, question_id: int) -> str:
    return questions.get_questions(db, [question_id])[question_id].correct_answer


def wrong_answer(db, question_id: int) -> str:
    view = questions.get_questions(db, [question_id])[question_id]
    return next(o for o in view.options if o != view.correct_answer)


def answer(client, db, headers, state, *, right: bool):
    qid = state["current_question"]["question_id"]
    response = client.post(
        f"{API}/{state['id']}/answers",
        json={
            "question_id": qid,
            "response": correct_answer(db, qid) if right else wrong_answer(db, qid),
        },
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return response.json()


# --------------------------------------------------------------------------- #
# The adaptation rule
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("start_level", "results", "expected"),
    [
        (M, [], M),
        (M, [True, True], M),
        (M, [True, True, True], H),
        (M, [False, False], E),
        (M, [False], M),
        (M, [True, True, False, True, True], M),  # a miss breaks the streak
        (M, [False, True, False], M),  # so does a hit
        (H, [True, True, True], H),  # cannot go above hard
        (E, [False, False, False, False], E),  # or below easy
        (M, [True, True, True, True, True, True], H),
        (M, [True, True, True, False, False], M),  # up then down again
        (E, [True, True, True, True, True, True], H),
    ],
)
def test_level_after_follows_the_adaptation_rule(start_level, results, expected):
    assert level_after(start_level, results).difficulty is expected


def test_streaks_restart_after_a_change():
    level = level_after(M, [True, True, True, True])
    assert (level.difficulty, level.correct_streak, level.incorrect_streak) == (H, 1, 0)


# --------------------------------------------------------------------------- #
# Test Plan cases
# --------------------------------------------------------------------------- #


def test_IT_14_three_correct_answers_raise_the_next_questions_difficulty(client, db, student):
    state = start(client, student)
    assert state["current_question"]["difficulty"] == "medium"

    for _ in range(3):
        feedback = answer(client, db, student, state, right=True)
        state = feedback["state"]

    assert feedback["correct"] is True
    assert feedback["difficulty_change"] == "raised"
    assert feedback["message"] == "Difficulty raised to Hard"
    fourth = state["current_question"]
    assert fourth["position"] == 4
    assert fourth["difficulty"] == "hard"
    assert fourth["topic"] == "DBMS"
    assert [h["difficulty"] for h in state["history"][:3]] == ["medium"] * 3


def test_ST_13_two_incorrect_answers_lower_the_next_questions_difficulty(client, db, student):
    state = start(client, student)

    first = answer(client, db, student, state, right=False)
    assert first["difficulty_change"] is None and first["current_difficulty"] == "medium"
    second = answer(client, db, student, first["state"], right=False)

    assert second["correct"] is False
    assert second["difficulty_change"] == "lowered"
    assert second["message"] == "Difficulty lowered to Easy"
    third = second["state"]["current_question"]
    assert third["position"] == 3
    assert third["difficulty"] == "easy"
    assert third["topic"] == "DBMS"


def test_ST_14_every_question_in_a_full_adaptive_quiz_keeps_the_topic(client, db, student):
    state = start(client, student, topic="Operating Systems", question_count=10)
    pattern = [True, True, True, True, False, False, False, False, True, False]

    for right in pattern:
        feedback = answer(client, db, student, state, right=right)
        state = feedback["state"]

    assert state["finished"] is True and state["current_question"] is None
    assert len(state["history"]) == 10
    assert {h["topic"] for h in state["history"]} == {"Operating Systems"}
    reached = {h["difficulty"] for h in state["history"]}
    assert reached == {"easy", "medium", "hard"}
    assert state["outcome"]["score"] == sum(pattern)
    assert state["outcome"]["display"] == f"{sum(pattern)}/10 (50%)"
    assert feedback["message"] == "Quiz complete"


# --------------------------------------------------------------------------- #
# Other acceptance criteria
# --------------------------------------------------------------------------- #


def test_the_current_difficulty_is_discoverable(client, db, student):
    state = start(client, student, start_difficulty="easy")
    assert state["current_difficulty"] == "easy"
    assert state["current_difficulty_label"] == "Difficulty: Easy"
    assert state["current_question"]["difficulty"] == "easy"

    again = client.get(f"{API}/{state['id']}", headers=student).json()
    assert again["current_difficulty"] == "easy"
    assert again["current_question"] == state["current_question"]  # not re-served

    after = answer(client, db, student, state, right=True)["state"]
    assert after["correct_streak"] == 1 and after["incorrect_streak"] == 0


def test_an_off_topic_generated_question_is_rejected_and_regenerated(client, db, student):
    provider = OffTopicFirst()
    use_provider(client, provider)

    state = start(client, student)

    assert state["current_question"]["topic"] == "DBMS"
    assert [c["round"] for c in provider.calls] == [0, 1]  # rejected, then regenerated
    assert "Computer Networks" not in state["current_question"]["text"]


def test_topic_containment_holds_at_every_difficulty_reached(client, db, student):
    provider = OffTopicFirst()
    use_provider(client, provider)
    state = start(client, student, topic="Operating Systems", question_count=12)

    # medium -> hard (3 right) -> medium (2 wrong) -> easy (2 wrong) -> medium (3 right)
    for right in [True, True, True, False, False, False, False, True, True, True]:
        state = answer(client, db, student, state, right=right)["state"]

    levels = [h["difficulty"] for h in state["history"]]
    assert {"easy", "medium", "hard"} <= set(levels)
    assert {h["topic"] for h in state["history"]} == {"Operating Systems"}
    assert len(provider.calls) == 2 * len(levels)  # every first answer was off topic


def test_bank_questions_are_used_first_and_ai_drafts_never_reach_the_bank(client, db, student):
    owner = make_user(db, "bank_owner")
    for i in range(2):
        questions.create_question(
            db,
            author_id=owner.id,
            subject="Computer Science",
            topic="DBMS",
            difficulty=Difficulty.MEDIUM,
            question_type=QuestionType.TRUE_FALSE,
            text=f"Bank statement {i}",
            options=["True", "False"],
            correct_answer="True",
        )
    db.commit()

    state = start(client, student)
    assert state["current_question"]["source"] == "peer"
    state = answer(client, db, student, state, right=False)["state"]
    assert state["current_question"]["source"] == "peer"
    state = answer(client, db, student, state, right=True)["state"]
    assert state["current_question"]["source"] == "llm"  # bank exhausted at medium

    bank = client.get("/api/v1/questions", params={"topic": "DBMS"}, headers=student).json()
    assert sorted(q["text"] for q in bank) == ["Bank statement 0", "Bank statement 1"]


def test_without_an_llm_the_bank_alone_runs_the_quiz_until_it_runs_out(client, db, student):
    use_provider(client, None)
    owner = make_user(db, "bank_owner2")
    questions.create_question(
        db,
        author_id=owner.id,
        subject="Computer Science",
        topic="DBMS",
        difficulty=Difficulty.MEDIUM,
        question_type=QuestionType.TRUE_FALSE,
        text="Only bank statement",
        options=["True", "False"],
        correct_answer="True",
    )
    db.commit()

    state = start(client, student)
    feedback = answer(client, db, student, state, right=True)

    assert feedback["next_question_error"] and "not available" in feedback["next_question_error"]
    retry = client.get(f"{API}/{state['id']}", headers=student)
    assert retry.status_code == 503
    assert "No more medium questions on DBMS" in retry.json()["detail"]


def test_a_failed_next_question_never_loses_the_answer(client, db, student):
    state = start(client, student)

    class Broken:
        name = "broken"

        def complete(self, task, prompt, params):
            raise LlmError("down")

    use_provider(client, Broken())
    feedback = answer(client, db, student, state, right=True)
    assert feedback["correct"] is True
    assert feedback["next_question_error"].startswith("Your answer is saved")
    assert feedback["state"]["answered_count"] == 1

    use_provider(client, FakeProvider())
    resumed = client.get(f"{API}/{state['id']}", headers=student).json()
    assert resumed["answered_count"] == 1
    assert resumed["current_question"]["position"] == 2


def test_only_the_question_shown_can_be_answered_and_only_once(client, db, student):
    state = start(client, student)
    qid = state["current_question"]["question_id"]

    wrong = client.post(
        f"{API}/{state['id']}/answers",
        json={"question_id": qid + 999, "response": "x"},
        headers=student,
    )
    assert wrong.status_code == 409

    answer(client, db, student, state, right=True)
    twice = client.post(
        f"{API}/{state['id']}/answers",
        json={"question_id": qid, "response": "x"},
        headers=student,
    )
    assert twice.status_code == 409

    blank = client.post(
        f"{API}/{state['id']}/answers",
        json={"question_id": qid, "response": "  "},
        headers=student,
    )
    assert blank.status_code == 422


def test_answers_after_the_last_question_are_refused(client, db, student):
    state = start(client, student, question_count=5)
    for _ in range(5):
        last = answer(client, db, student, state, right=True)
        state = last["state"]
    assert state["finished"] is True
    refused = client.post(
        f"{API}/{state['id']}/answers",
        json={"question_id": state["history"][-1]["question_id"], "response": "x"},
        headers=student,
    )
    assert refused.status_code == 409


def test_generated_requests_are_audited_as_adaptive(client, db, student):
    start(client, student)
    assert {r.task for r in db.query(LlmRequest).all()} == {LlmTask.ADAPT_DIFFICULTY}


def test_an_adaptive_quiz_is_private(client, db, student):
    state = start(client, student)
    make_user(db, "nosy")
    other = auth(client, "nosy")
    assert client.get(f"{API}/{state['id']}", headers=other).status_code == 404
    qid = state["current_question"]["question_id"]
    response = client.post(
        f"{API}/{state['id']}/answers", json={"question_id": qid, "response": "x"}, headers=other
    )
    assert response.status_code == 404


@pytest.mark.parametrize("count", [4, 21])
def test_quiz_length_is_bounded(client, student, count):
    response = client.post(
        API, json={"subject": "CS", "topic": "DBMS", "question_count": count}, headers=student
    )
    assert response.status_code == 422


def test_channel_bank_needs_membership(client, db, student):
    channel = client.post(
        "/api/v1/channels", json={"name": "OS group", "subject": "OS"}, headers=student
    ).json()
    make_user(db, "outsider")
    response = client.post(
        API,
        json={"subject": "CS", "topic": "DBMS", "channel_id": channel["id"]},
        headers=auth(client, "outsider"),
    )
    assert response.status_code in (403, 404)


def test_an_adaptive_run_is_not_listed_or_publishable_as_an_authored_quiz(client, db, student):
    state = start(client, student)
    mine = client.get("/api/v1/quizzes", params={"mine": True}, headers=student)
    assert mine.status_code == 200
    assert all(not q["title"].startswith("Adaptive quiz") for q in mine.json())

    quiz_id = db.get(AdaptiveSession, state["id"]).quiz_id
    published = client.post(f"/api/v1/quizzes/{quiz_id}/publish", json={}, headers=student)
    assert published.status_code == 422
