"""S07.1 - LLM question generation with mandatory review (issue #30, FR-07, NFR-06).

Test Plan cases are named after their IDs: UT_13, UT_14, IT_13. Every test runs with
the deterministic fake provider (or a scripted one); nothing calls a real model.
"""

import json
import urllib.error

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.core.config import Settings
from app.modules.accounts import service as accounts
from app.modules.accounts.models import Role
from app.modules.llm import generation
from app.modules.llm.models import LlmRequest, LlmRequestStatus, LlmTask
from app.modules.llm.provider import (
    FakeProvider,
    HttpProvider,
    LlmError,
    LlmTimeoutError,
    provider_from_settings,
)
from app.modules.llm.router import (
    NOT_CONFIGURED,
    PROVIDER_FAILED,
    PROVIDER_TIMEOUT,
    get_llm_provider,
)
from app.modules.questions.service import Difficulty

API = "/api/v1/ai/generations"
PASSWORD = "Pass@1234"
DBMS = {"subject": "DBMS", "topic": "Normalization", "difficulty": "medium"}


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


class ScriptedProvider:
    """Returns the given answers in turn (or raises them if they are exceptions)."""

    name = "scripted"

    def __init__(self, *answers):
        self.answers, self.calls = list(answers), []

    def complete(self, task, prompt, params):
        self.calls.append(params)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer if isinstance(answer, str) else json.dumps(answer)


def item(n: int, **overrides) -> dict:
    options = [f"Option {n}{k}" for k in "ABCD"]
    base = {
        "subject": "DBMS",
        "topic": "Normalization",
        "difficulty": "medium",
        "question": f"Normalization question {n}?",
        "options": options,
        "correct_answer": options[1],
        "explanation": f"Because of rule {n}.",
    }
    return base | overrides


@pytest.fixture()
def student(client, db):
    make_user(db, "gen_student")
    use_provider(client, FakeProvider())
    return auth(client, "gen_student")


def generate(client, headers, **body):
    return client.post(API, json=DBMS | body, headers=headers)


# --------------------------------------------------------------------------- #
# Generation (UT_13) and topic/difficulty matching
# --------------------------------------------------------------------------- #


def test_UT_13_generates_questions_with_options_answer_and_explanation(client, student):
    response = generate(client, student)

    assert response.status_code == 201, response.text
    batch = response.json()
    assert 5 <= len(batch["questions"]) <= 10
    for q in batch["questions"]:
        assert len(q["options"]) >= 2
        assert q["correct_answer"] in q["options"]
        assert q["explanation"]
        assert (q["subject"], q["topic"], q["difficulty"]) == ("DBMS", "Normalization", "medium")
        assert q["review_status"] == "pending"


@pytest.mark.parametrize("count", [5, 7, 10])
def test_the_requested_number_of_questions_is_returned(client, student, count):
    response = generate(client, student, count=count)
    assert response.status_code == 201
    assert len(response.json()["questions"]) == count


@pytest.mark.parametrize("count", [4, 11])
def test_counts_outside_five_to_ten_are_refused(client, student, count):
    assert generate(client, student, count=count).status_code == 422


def test_subject_and_topic_are_required(client, student):
    response = generate(client, student, topic="  ")
    assert response.status_code == 422
    assert response.json()["detail"] == "Select a topic"


def test_generated_content_matches_selected_subject_topic_and_difficulty(client, db, student):
    provider = ScriptedProvider(
        {
            "questions": [
                item(1),
                item(2, topic="Indexing"),  # outside the selected topic
                item(3, difficulty="hard"),  # wrong difficulty
                item(4, subject="Operating Systems"),
                item(5),
                item(6),
                item(7, correct_answer="Not an option"),
                item(8, explanation=""),
            ]
        },
        {"questions": [item(9), item(10)]},
    )
    use_provider(client, provider)

    response = generate(client, student, count=5)

    assert response.status_code == 201, response.text
    texts = [q["text"] for q in response.json()["questions"]]
    assert texts == [f"Normalization question {n}?" for n in (1, 5, 6, 9, 10)]
    # The shortfall was asked for again, and only for the missing number.
    assert [call["count"] for call in provider.calls] == [5, 2]
    log = db.query(LlmRequest).one()
    assert log.output["rejected"] == 5 and log.output["accepted"] == 5


def test_validate_item_rejects_off_topic_and_incomplete_items():
    ok = generation.validate_item(
        item(1, topic=" normalization "),
        subject="DBMS",
        topic="Normalization",
        difficulty=Difficulty.MEDIUM,
    )
    assert ok is not None and ok.topic == "Normalization"
    for bad in (
        item(1, topic="Transactions"),
        item(1, options=["Same", "same"], correct_answer="Same"),
        item(1, options=["Only one"], correct_answer="Only one"),
        item(1, question=""),
        "not an object",
    ):
        assert (
            generation.validate_item(
                bad, subject="DBMS", topic="Normalization", difficulty=Difficulty.MEDIUM
            )
            is None
        )


def test_a_fenced_json_answer_is_accepted(client, student):
    fenced = "```json\n" + json.dumps({"questions": [item(n) for n in range(5)]}) + "\n```"
    use_provider(client, ScriptedProvider(fenced))
    assert generate(client, student).status_code == 201


# --------------------------------------------------------------------------- #
# Failures surface clear errors (NFR-06)
# --------------------------------------------------------------------------- #


def test_a_provider_failure_returns_a_clear_error_and_is_logged(client, db, student):
    use_provider(client, ScriptedProvider(LlmError("boom")))

    response = generate(client, student)

    assert response.status_code == 502
    assert response.json()["detail"] == PROVIDER_FAILED
    log = db.query(LlmRequest).one()
    assert log.status is LlmRequestStatus.FAILED and log.error == "boom"


def test_a_provider_timeout_returns_a_clear_error(client, student):
    use_provider(client, ScriptedProvider(LlmTimeoutError("slow")))

    response = generate(client, student)

    assert response.status_code == 504
    assert response.json()["detail"] == PROVIDER_TIMEOUT


def test_unusable_output_after_every_retry_is_a_clear_error(client, db, student):
    use_provider(client, ScriptedProvider("not json", "{}", {"questions": [item(1)]}))

    response = generate(client, student)

    assert response.status_code == 502
    assert response.json()["detail"] == PROVIDER_FAILED
    assert db.query(LlmRequest).one().status is LlmRequestStatus.FAILED


def test_with_no_provider_configured_the_feature_says_so(client, db):
    make_user(db, "no_llm")
    client.app.dependency_overrides[get_llm_provider] = lambda: None

    response = generate(client, auth(client, "no_llm"))

    assert response.status_code == 503
    assert response.json()["detail"] == NOT_CONFIGURED


def test_provider_selection_follows_configuration():
    assert provider_from_settings(Settings(_env_file=None, llm_provider="none")) is None
    assert isinstance(
        provider_from_settings(Settings(_env_file=None, llm_provider="fake")), FakeProvider
    )
    assert provider_from_settings(Settings(_env_file=None, llm_provider="openai")) is None
    real = provider_from_settings(
        Settings(_env_file=None, llm_provider="anthropic", llm_api_key=SecretStr("k"))
    )
    assert isinstance(real, HttpProvider) and real.name == "anthropic"


def test_the_http_provider_maps_timeouts_and_errors(monkeypatch):
    provider = HttpProvider("openai", model="m", api_key="k", base_url="", timeout=0.01)

    def timeout(*args, **kwargs):
        raise TimeoutError

    monkeypatch.setattr("urllib.request.urlopen", timeout)
    with pytest.raises(LlmTimeoutError):
        provider.complete(LlmTask.GENERATE_QUESTIONS, "p", {})

    def unreachable(*args, **kwargs):
        raise urllib.error.URLError("no route")

    monkeypatch.setattr("urllib.request.urlopen", unreachable)
    with pytest.raises(LlmError) as err:
        provider.complete(LlmTask.GENERATE_QUESTIONS, "p", {})
    assert "k" not in str(err.value).split()  # the key is never echoed


def test_the_fake_provider_is_deterministic():
    params = {"subject": "DBMS", "topic": "Normalization", "difficulty": "easy", "count": 5}
    first = FakeProvider().complete(LlmTask.GENERATE_QUESTIONS, "", params)
    assert first == FakeProvider().complete(LlmTask.GENERATE_QUESTIONS, "", params)


# --------------------------------------------------------------------------- #
# Mandatory review (UT_14) and edit/reject (IT_13)
# --------------------------------------------------------------------------- #


def bank_texts(client, headers, **filters) -> list[str]:
    response = client.get("/api/v1/questions", params=filters, headers=headers)
    assert response.status_code == 200
    return [q["text"] for q in response.json()]


def test_UT_14_add_to_quiz_is_unavailable_until_every_question_is_reviewed(client, student):
    batch = generate(client, student).json()
    assert batch["can_add_to_quiz"] is False and batch["pending_count"] == 5

    refused = client.post(f"{API}/{batch['id']}/quiz", json={}, headers=student)
    assert refused.status_code == 409
    assert "Approve or reject every generated question" in refused.json()["detail"]

    # Drafts are invisible to the bank and cannot be put in a quiz another way.
    assert bank_texts(client, student, topic="Normalization") == []
    direct = client.post(
        "/api/v1/quizzes",
        json={
            "topic": "Normalization",
            "difficulty": "medium",
            "question_count": 1,
            "duration_minutes": 10,
            "question_ids": [batch["questions"][0]["id"]],
        },
        headers=student,
    )
    assert direct.status_code >= 400

    ids = [q["id"] for q in batch["questions"]]
    for qid in ids[:-1]:
        client.post(f"{API}/{batch['id']}/questions/{qid}/approve", headers=student)
    still = client.get(f"{API}/{batch['id']}", headers=student).json()
    assert still["can_add_to_quiz"] is False and still["pending_count"] == 1

    done = client.post(f"{API}/{batch['id']}/questions/{ids[-1]}/reject", headers=student).json()
    assert done["can_add_to_quiz"] is True and done["pending_count"] == 0

    added = client.post(
        f"{API}/{batch['id']}/quiz", json={"title": "AI DBMS quiz"}, headers=student
    )
    assert added.status_code == 201, added.text
    assert added.json()["question_ids"] == ids[:-1]


def test_IT_13_a_flawed_question_can_be_edited_and_saved_with_the_correction(client, student):
    batch = generate(client, student).json()
    flawed = batch["questions"][0]
    correction = {
        "text": "Which normal form removes partial dependencies?",
        "options": ["1NF", "2NF", "3NF", "BCNF"],
        "correct_answer": "2nf",
        "explanation": "2NF removes partial dependencies on a composite key.",
    }

    saved = client.put(
        f"{API}/{batch['id']}/questions/{flawed['id']}", json=correction, headers=student
    )

    assert saved.status_code == 200, saved.text
    edited = next(q for q in saved.json()["questions"] if q["id"] == flawed["id"])
    assert edited["text"] == correction["text"]
    assert edited["correct_answer"] == "2NF"
    assert edited["edited"] is True and edited["review_status"] == "pending"

    client.post(f"{API}/{batch['id']}/questions/{flawed['id']}/approve", headers=student)
    assert correction["text"] in bank_texts(client, student, topic="Normalization")


def test_IT_13_a_rejected_question_is_discarded_and_never_reaches_bank_or_quiz(client, student):
    batch = generate(client, student).json()
    ids = [q["id"] for q in batch["questions"]]
    rejected = batch["questions"][0]

    client.post(f"{API}/{batch['id']}/questions/{rejected['id']}/reject", headers=student)
    for qid in ids[1:]:
        client.post(f"{API}/{batch['id']}/questions/{qid}/approve", headers=student)

    texts = bank_texts(client, student, topic="Normalization")
    assert rejected["text"] not in texts and len(texts) == 4
    quiz = client.post(f"{API}/{batch['id']}/quiz", json={}, headers=student).json()
    assert rejected["id"] not in quiz["question_ids"]
    detail = client.get(f"/api/v1/quizzes/{quiz['quiz_id']}", headers=student).json()
    assert rejected["id"] not in [q["id"] for q in detail["questions"]]
    # A quiz assembled from the bank cannot pick it up either.
    preview = client.post(
        "/api/v1/quizzes/preview",
        json={
            "topic": "Normalization",
            "difficulty": "medium",
            "question_count": 4,
            "duration_minutes": 10,
        },
        headers=student,
    )
    assert preview.status_code == 200, preview.text
    assert rejected["id"] not in [q["id"] for q in preview.json()["questions"]]


def test_a_reviewed_question_cannot_be_reviewed_or_edited_again(client, student):
    batch = generate(client, student).json()
    qid = batch["questions"][0]["id"]
    client.post(f"{API}/{batch['id']}/questions/{qid}/reject", headers=student)

    again = client.post(f"{API}/{batch['id']}/questions/{qid}/approve", headers=student)
    edit = client.put(
        f"{API}/{batch['id']}/questions/{qid}",
        json={"text": "x", "options": ["a", "b"], "correct_answer": "a", "explanation": "e"},
        headers=student,
    )
    assert again.status_code == 409 and edit.status_code == 409


def test_an_edit_must_keep_the_question_answerable(client, student):
    batch = generate(client, student).json()
    qid = batch["questions"][0]["id"]
    bad = client.put(
        f"{API}/{batch['id']}/questions/{qid}",
        json={"text": "Q", "options": ["a", "b"], "correct_answer": "c", "explanation": "e"},
        headers=student,
    )
    assert bad.status_code == 422
    assert bad.json()["detail"] == "The correct answer must be one of the options"


def test_nothing_approved_means_no_quiz(client, student):
    batch = generate(client, student).json()
    for q in batch["questions"]:
        client.post(f"{API}/{batch['id']}/questions/{q['id']}/reject", headers=student)
    response = client.post(f"{API}/{batch['id']}/quiz", json={}, headers=student)
    assert response.status_code == 422


def test_a_batch_is_private_to_the_user_who_generated_it(client, db, student):
    batch = generate(client, student).json()
    make_user(db, "someone_else")
    other = auth(client, "someone_else")
    qid = batch["questions"][0]["id"]

    assert client.get(f"{API}/{batch['id']}", headers=other).status_code == 404
    assert (
        client.post(f"{API}/{batch['id']}/questions/{qid}/approve", headers=other).status_code
        == 404
    )
    assert client.get(API, headers=other).json() == []
    assert [g["id"] for g in client.get(API, headers=student).json()] == [batch["id"]]


def test_a_question_from_another_batch_is_not_found(client, student):
    first = generate(client, student).json()
    second = generate(client, student).json()
    foreign = second["questions"][0]["id"]
    response = client.post(f"{API}/{first['id']}/questions/{foreign}/approve", headers=student)
    assert response.status_code == 404


def test_generating_into_a_channel_bank_needs_membership(client, db, student):
    channel = client.post(
        "/api/v1/channels",
        json={"name": "DBMS group", "subject": "DBMS"},
        headers=student,
    )
    assert channel.status_code == 201, channel.text
    channel_id = channel.json()["id"]
    make_user(db, "not_member")

    refused = generate(client, auth(client, "not_member"), channel_id=channel_id)
    assert refused.status_code in (403, 404)

    batch = generate(client, student, channel_id=channel_id).json()
    qid = batch["questions"][0]["id"]
    client.post(f"{API}/{batch['id']}/questions/{qid}/approve", headers=student)
    assert bank_texts(client, student, channel_id=channel_id) == [batch["questions"][0]["text"]]
    assert bank_texts(client, student, topic="Normalization") == []  # not the general bank


def test_generation_requires_sign_in(client):
    assert client.post(API, json=DBMS).status_code == 401


def test_only_authorized_users_generate_into_an_authorized_channel_bank(client, db, student):
    """S03.1: an Authorized Channel's bank is official content, AI questions included."""
    from app.modules.channels import service as channels
    from app.modules.channels.models import ApprovalStatus, ChannelType

    faculty = make_user(db, "gen_faculty", Role.AUTHORIZED_USER)
    member = accounts.get_user_by_email(db, "gen_student@test.com")
    official = channels.create_channel(
        db,
        name="DBMS (official)",
        subject="DBMS",
        created_by=faculty.id,
        channel_type=ChannelType.AUTHORIZED,
    )
    channels.set_approval(db, official.id, ApprovalStatus.APPROVED)
    channels.add_member(db, official.id, member.id)
    db.commit()

    refused = generate(client, student, channel_id=official.id)
    assert refused.status_code == 403
    assert (
        refused.json()["detail"] == "Only authorized users can add content to Authorized Channels"
    )

    allowed = generate(client, auth(client, "gen_faculty"), channel_id=official.id)
    assert allowed.status_code == 201, allowed.text
