"""S09.2 - channel-scoped AI blending peer and generated questions (issue #35, FR-09).

Test Plan cases are named after their IDs: IT_18 and ST_17, plus one test per
acceptance criterion. Every test runs with the deterministic fake provider (or none);
nothing calls a real model.
"""

import pytest
from fastapi.testclient import TestClient

from app.modules.accounts import service as accounts
from app.modules.accounts.models import Role
from app.modules.channels import service as channels
from app.modules.channels.models import ApprovalStatus, ChannelType
from app.modules.llm.models import LlmRequest, LlmTask
from app.modules.llm.provider import FakeProvider
from app.modules.llm.router import get_llm_provider
from app.modules.questions import service as questions
from app.modules.questions.models import Question
from app.modules.questions.service import Difficulty, QuestionStatus, QuestionType

ASK = "/api/v1/ai/ask"
PASSWORD = "Pass@1234"
DBMS_TOPICS = ["Normalization", "Keys", "Transactions", "Indexing", "SQL Joins"]


class SpyProvider(FakeProvider):
    def __init__(self):
        self.calls: list[tuple[LlmTask, str, dict]] = []

    def complete(self, task, prompt, params):
        self.calls.append((task, prompt, params))
        return super().complete(task, prompt, params)


def make_user(db, username: str, name: str, role: Role = Role.STUDENT) -> int:
    user = accounts.create_user(
        db,
        email=f"{username}@test.com",
        display_name=name,
        password=PASSWORD,
        role=role,
        username=username,
    )
    db.commit()
    return user.id


def auth(client: TestClient, username: str) -> dict[str, str]:
    response = client.post(
        "/api/v1/accounts/login", json={"username": username, "password": PASSWORD}
    )
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def peer_question(db, author_id: int, channel_id: int, n: int, subject="DBMS", topic=None):
    return questions.create_question(
        db,
        author_id=author_id,
        subject=subject,
        topic=topic or DBMS_TOPICS[n % len(DBMS_TOPICS)],
        difficulty=Difficulty.MEDIUM,
        question_type=QuestionType.SHORT_ANSWER,
        text=f"Peer {subject} question {n}?",
        correct_answer=f"Answer {n}",
        channel_id=channel_id,
    ).id


@pytest.fixture()
def spy(client) -> SpyProvider:
    provider = SpyProvider()
    client.app.dependency_overrides[get_llm_provider] = lambda: provider
    return provider


@pytest.fixture()
def setup(client, db, spy) -> dict:
    """The "DBMS Study Group" channel with a member asking and a peer who contributed."""
    ids = {
        "owner": make_user(db, "aditi_r", "Aditi R"),
        "member": make_user(db, "rahul_m", "Rahul M"),
        "outsider": make_user(db, "outsider_p", "Outsider P"),
    }
    channel = channels.create_channel(
        db, name="DBMS Study Group", subject="DBMS", created_by=ids["owner"]
    )
    channels.add_member(db, channel.id, ids["member"])
    other = channels.create_channel(
        db, name="Biology circle", subject="Biology", created_by=ids["owner"]
    )
    db.commit()
    return {
        **ids,
        "channel": channel.id,
        "other": other.id,
        "member_h": auth(client, "rahul_m"),
        "outsider_h": auth(client, "outsider_p"),
    }


def five_peer_questions(db, setup) -> list[int]:
    """Channel question bank has 5 peer-contributed DBMS questions (ST_17)."""
    ids = [peer_question(db, setup["owner"], setup["channel"], n) for n in range(5)]
    db.commit()
    return ids


def ask(client, headers, query, **extra):
    return client.post(ASK, json={"query": query} | extra, headers=headers)


# --------------------------------------------------------------------------- #
# Test Plan cases
# --------------------------------------------------------------------------- #


def test_IT_18_channel_quiz_uses_the_channels_topic_and_bank(client, db, setup, spy):
    five_peer_questions(db, setup)
    # Other subjects exist, in another channel and in the general bank.
    peer_question(db, setup["owner"], setup["other"], 1, subject="Biology", topic="Cells")
    questions.create_question(
        db,
        author_id=setup["owner"],
        subject="Physics",
        topic="Optics",
        difficulty=Difficulty.MEDIUM,
        question_type=QuestionType.SHORT_ANSWER,
        text="What is refraction?",
        correct_answer="Bending of light",
    )
    db.commit()

    response = ask(
        client, setup["member_h"], "Generate a quiz for this channel", channel_id=setup["channel"]
    )

    assert response.status_code == 200, response.text
    body = response.json()
    quiz = body["quiz"]
    assert body["channel_id"] == setup["channel"]
    assert quiz["subject"] == "DBMS"
    assert {item["subject"] for item in quiz["items"]} == {"DBMS"}
    assert quiz["peer_count"] == 5
    # The provider was only asked about DBMS.
    assert all(params["subject"] == "DBMS" for _, _, params in spy.calls)


def test_ST_17_ten_questions_over_a_five_question_bank_blend_peer_and_generated(
    client, db, setup, spy
):
    peer_ids = five_peer_questions(db, setup)

    response = ask(
        client,
        setup["member_h"],
        "Give me a 10-question quiz for this channel",
        channel_id=setup["channel"],
    )

    assert response.status_code == 200, response.text
    quiz = response.json()["quiz"]
    assert quiz["question_count"] == 10
    assert (quiz["peer_count"], quiz["generated_count"]) == (5, 5)
    peer = [i for i in quiz["items"] if i["source"] == "peer"]
    generated = [i for i in quiz["items"] if i["source"] == "generated"]
    assert sorted(i["question_id"] for i in peer) == sorted(peer_ids)
    assert len(generated) == 5
    assert {i["subject"] for i in quiz["items"]} == {"DBMS"}
    assert quiz["review_required"] is True
    review = client.get(
        f"/api/v1/ai/generations/{quiz['generation_id']}", headers=setup["member_h"]
    )
    assert review.status_code == 200
    assert sorted(q["id"] for q in review.json()["questions"]) == sorted(
        i["question_id"] for i in generated
    )


# --------------------------------------------------------------------------- #
# Acceptance criteria
# --------------------------------------------------------------------------- #


def test_unrelated_subjects_never_appear_in_channel_output(client, db, setup):
    five_peer_questions(db, setup)
    biology = peer_question(db, setup["owner"], setup["channel"], 9, subject="Biology")
    db.commit()

    quiz = ask(
        client,
        setup["member_h"],
        "Generate a 8 question quiz for this channel",
        channel_id=setup["channel"],
    ).json()["quiz"]

    assert biology not in [i["question_id"] for i in quiz["items"]]
    assert {i["subject"] for i in quiz["items"]} == {"DBMS"}


def test_a_full_bank_needs_no_generation(client, db, setup, spy):
    for n in range(7):
        peer_question(db, setup["owner"], setup["channel"], n)
    db.commit()

    quiz = ask(
        client, setup["member_h"], "Generate a quiz for this channel", channel_id=setup["channel"]
    ).json()["quiz"]

    assert (quiz["question_count"], quiz["peer_count"], quiz["generated_count"]) == (5, 5, 0)
    assert quiz["generation_id"] is None
    assert quiz["review_required"] is False
    assert spy.calls == []


def test_an_empty_bank_is_filled_with_generated_questions(client, setup):
    quiz = ask(
        client, setup["member_h"], "Generate a quiz for this channel", channel_id=setup["channel"]
    ).json()["quiz"]

    assert (quiz["peer_count"], quiz["generated_count"]) == (0, 5)
    assert quiz["topic"] == "DBMS"


def test_a_named_topic_stays_within_the_channels_subject(client, db, setup, spy):
    keys = [peer_question(db, setup["owner"], setup["channel"], n, topic="Keys") for n in range(2)]
    peer_question(db, setup["owner"], setup["channel"], 3, topic="Transactions")
    db.commit()

    quiz = ask(client, setup["member_h"], "Quiz me on Keys", channel_id=setup["channel"]).json()[
        "quiz"
    ]

    peer = [i["question_id"] for i in quiz["items"] if i["source"] == "peer"]
    assert sorted(peer) == sorted(keys)
    assert {(i["subject"], i["topic"]) for i in quiz["items"]} == {("DBMS", "Keys")}
    assert {(p["subject"], p["topic"]) for _, _, p in spy.calls} == {("DBMS", "Keys")}


def test_peer_and_generated_questions_are_distinguishable(client, db, setup):
    five_peer_questions(db, setup)

    quiz = ask(
        client,
        setup["member_h"],
        "Give me a 7 question quiz for this channel",
        channel_id=setup["channel"],
    ).json()["quiz"]

    sources = {(i["source"], i["author_name"]) for i in quiz["items"]}
    assert sources == {("peer", "Aditi R"), ("generated", "Rahul M")}
    generated = [i["question_id"] for i in quiz["items"] if i["source"] == "generated"]
    for qid in generated:
        q = questions.get_questions(db, [qid])[qid]
        assert q.subject == "DBMS"
    stored = db.query(LlmRequest).filter(LlmRequest.task == LlmTask.GENERATE_QUIZ).one()
    assert stored.channel_id == setup["channel"]
    assert len(stored.output["peer_question_ids"]) == 5


def test_generated_drafts_stay_out_of_the_bank_until_approved(client, db, setup):
    five_peer_questions(db, setup)
    quiz = ask(
        client,
        setup["member_h"],
        "Give me a 10-question quiz for this channel",
        channel_id=setup["channel"],
    ).json()["quiz"]
    generated = [i["question_id"] for i in quiz["items"] if i["source"] == "generated"]

    bank = client.get(
        "/api/v1/questions", params={"channel_id": setup["channel"]}, headers=setup["member_h"]
    ).json()

    assert {q["id"] for q in bank}.isdisjoint(generated)
    stored = [db.get(Question, qid) for qid in generated]
    assert {(q.channel_id, q.status) for q in stored} == {(setup["channel"], QuestionStatus.DRAFT)}


def test_the_channel_quiz_can_be_started(client, db, setup):
    five_peer_questions(db, setup)
    made = ask(
        client, setup["member_h"], "Generate a quiz for this channel", channel_id=setup["channel"]
    ).json()

    started = client.post(f"/api/v1/ai/ask/{made['id']}/start", headers=setup["member_h"])

    assert started.status_code == 201, started.text
    attempt = client.get(started.json()["attempt_path"], headers=setup["member_h"])
    assert attempt.status_code == 200


def test_channel_explanations_are_scoped_to_the_channels_subject(client, setup, spy):
    response = ask(client, setup["member_h"], "Explain normalization", channel_id=setup["channel"])

    assert response.status_code == 200, response.text
    assert response.json()["kind"] == "explanation"
    assert response.json()["channel_id"] == setup["channel"]
    prompt = spy.calls[0][1]
    assert "'DBMS'" in prompt and "unrelated subjects" in prompt


def test_ask_ai_in_a_channel_needs_membership(client, setup):
    response = ask(
        client,
        setup["outsider_h"],
        "Generate a quiz for this channel",
        channel_id=setup["channel"],
    )
    unknown = ask(client, setup["member_h"], "Generate a quiz", channel_id=9999)

    assert (response.status_code, response.json()["detail"]) == (
        403,
        "Join this channel to use Ask AI in it",
    )
    assert unknown.status_code == 404


def test_a_bank_that_fills_the_quiz_works_without_an_llm(client, db, setup):
    for n in range(5):
        peer_question(db, setup["owner"], setup["channel"], n)
    db.commit()
    client.app.dependency_overrides[get_llm_provider] = lambda: None

    full = ask(
        client, setup["member_h"], "Generate a quiz for this channel", channel_id=setup["channel"]
    )
    short = ask(
        client,
        setup["member_h"],
        "Generate a 10-question quiz for this channel",
        channel_id=setup["channel"],
    )

    assert full.status_code == 200
    assert full.json()["quiz"]["generated_count"] == 0
    assert short.status_code == 503


def test_students_generated_drafts_never_join_an_authorized_channel_bank(client, db, setup):
    faculty = make_user(db, "faculty_a", "Faculty A", Role.AUTHORIZED_USER)
    official = channels.create_channel(
        db,
        name="DBMS (official)",
        subject="DBMS",
        created_by=faculty,
        channel_type=ChannelType.AUTHORIZED,
    )
    channels.set_approval(db, official.id, ApprovalStatus.APPROVED)
    channels.add_member(db, official.id, setup["member"])
    db.commit()

    quiz = ask(
        client, setup["member_h"], "Generate a quiz for this channel", channel_id=official.id
    ).json()["quiz"]

    generated = [i["question_id"] for i in quiz["items"] if i["source"] == "generated"]
    stored = [db.get(Question, qid) for qid in generated]
    assert len(generated) == 5
    assert {q.channel_id for q in stored} == {None}
