"""S11.2 - in-chat sharing of materials, questions and quizzes (issue #39, FR-11).

Test Plan cases are named after their IDs: ST_21, ST_22, plus one test per acceptance
criterion and sharing rule. The plan's steps run in the web UI; these call the
endpoints that UI uses: the message carries the preview card, and the card's
``api_path`` is what opening it reads.
"""

import pytest
from fastapi.testclient import TestClient

from app.modules.accounts import service as accounts
from app.modules.accounts.models import Role
from app.modules.channels import service as channels
from app.modules.chat import service as chat
from app.modules.materials import service as materials
from app.modules.materials.models import MaterialType
from app.modules.questions import service as questions
from app.modules.questions.models import Difficulty, QuestionType
from app.modules.quizzes import service as quizzes

PASSWORD = "Pass@1234"
API = "/api/v1"
CONVERSATIONS = f"{API}/conversations"
CHANNELS = f"{API}/channels"
OS_NOTES = "OS_Scheduling_Notes.pdf"
OPTIONS = ["A", "B", "C", "D"]


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
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def make_question(db, author_id: int, text: str = "Which algorithm can starve long jobs?", **kw):
    return questions.create_question(
        db,
        author_id=author_id,
        subject="OS",
        topic="Scheduling",
        difficulty=Difficulty.EASY,
        question_type=QuestionType.MULTIPLE_CHOICE,
        text=text,
        options=OPTIONS,
        correct_answer="A",
        explanation="Shortest job first keeps picking short jobs.",
        **kw,
    ).id


def make_quiz(db, author_id: int, *, publish: bool = True, channel_id=None, adaptive=False):
    ids = [make_question(db, author_id, f"Scheduling question {i}?") for i in range(1, 4)]
    quiz_id = quizzes.create_quiz(
        db,
        creator_id=author_id,
        title="CPU Scheduling Quiz",
        subject="OS",
        topic="Scheduling",
        difficulty="easy",
        duration_minutes=15,
        question_ids=ids,
        channel_id=channel_id,
        publish=publish,
        adaptive=adaptive,
    )
    db.commit()
    return quiz_id


@pytest.fixture()
def setup(client, db) -> dict:
    """Priya and Arjun share "OS Study Group"; Priya has notes and a published quiz."""
    ids = {
        "priya": make_user(db, "priya_s", "Priya S"),
        "arjun": make_user(db, "arjun_k", "Arjun K"),
        "outsider": make_user(db, "outsider_p", "Outsider P"),
    }
    headers = {
        f"{k}_h": auth(client, u)
        for k, u in (
            ("priya", "priya_s"),
            ("arjun", "arjun_k"),
            ("outsider", "outsider_p"),
        )
    }
    channel = client.post(
        CHANNELS, json={"name": "OS Study Group", "subject": "OS"}, headers=headers["priya_h"]
    ).json()
    channels.add_member(db, channel["id"], ids["arjun"])
    material = materials.create_material(
        db,
        uploader_id=ids["priya"],
        title=OS_NOTES,
        material_type=MaterialType.PDF,
        subject="OS",
        topic="Scheduling",
        semester="4",
    )
    db.commit()
    conversation = client.post(
        CONVERSATIONS, json={"user_id": ids["arjun"]}, headers=headers["priya_h"]
    ).json()
    return {
        **ids,
        **headers,
        "channel": channel["id"],
        "material": material.id,
        "quiz": make_quiz(db, ids["priya"]),
        "conversation": conversation["id"],
    }


def share(type_: str, id_: int) -> dict:
    return {"type": type_, "id": id_}


def send_dm(client, setup, who: str, body: str = "", shared=None):
    payload = {"body": body}
    if shared is not None:
        payload["shared_resource"] = shared
    return client.post(
        f"{CONVERSATIONS}/{setup['conversation']}/messages", json=payload, headers=setup[f"{who}_h"]
    )


def send_channel(client, setup, who: str, body: str = "", shared=None):
    payload = {"body": body}
    if shared is not None:
        payload["shared_resource"] = shared
    return client.post(
        f"{CHANNELS}/{setup['channel']}/messages", json=payload, headers=setup[f"{who}_h"]
    )


def dm_history(client, setup, who: str) -> list[dict]:
    response = client.get(
        f"{CONVERSATIONS}/{setup['conversation']}/messages", headers=setup[f"{who}_h"]
    )
    assert response.status_code == 200, response.text
    return response.json()["items"]


def channel_history(client, setup, who: str) -> list[dict]:
    response = client.get(f"{CHANNELS}/{setup['channel']}/messages", headers=setup[f"{who}_h"])
    assert response.status_code == 200, response.text
    return response.json()["items"]


# --------------------------------------------------------------------------- #
# Test Plan cases
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("chat_kind", ["direct", "channel"])
def test_ST_21_shared_material_stays_linked_and_opens_the_original(client, setup, chat_kind):
    sender = send_dm if chat_kind == "direct" else send_channel
    reader = dm_history if chat_kind == "direct" else channel_history

    sent = sender(client, setup, "priya", shared=share("material", setup["material"]))
    assert sent.status_code == 201, sent.text

    [message] = reader(client, setup, "arjun")
    card = message["shared_resource"]
    assert card["type"] == "material"
    assert card["id"] == setup["material"]
    assert card["available"] is True
    assert card["title"] == OS_NOTES
    assert card["web_path"] == f"#/materials/{setup['material']}"

    opened = client.get(card["api_path"], headers=setup["arjun_h"])
    assert opened.status_code == 200
    assert opened.json()["id"] == setup["material"]
    assert opened.json()["title"] == OS_NOTES


def test_ST_22_shared_quiz_opens_the_start_screen_and_can_be_attempted(client, setup):
    sent = send_dm(client, setup, "priya", "Try this one", share("quiz", setup["quiz"]))
    assert sent.status_code == 201, sent.text

    [message] = dm_history(client, setup, "arjun")
    card = message["shared_resource"]
    assert card["available"] is True
    assert card["title"] == "CPU Scheduling Quiz"
    assert card["web_path"] == f"#/quizzes/{setup['quiz']}/attempt"

    assert client.get(card["api_path"], headers=setup["arjun_h"]).status_code == 200
    attempt = client.post(f"{API}/quizzes/{setup['quiz']}/attempts", headers=setup["arjun_h"])
    assert attempt.status_code == 201, attempt.text
    assert attempt.json()["remaining_seconds"] > 0


# --------------------------------------------------------------------------- #
# Acceptance criteria
# --------------------------------------------------------------------------- #


def test_a_shared_resource_renders_as_a_preview_card(client, setup, db):
    question_id = make_question(db, setup["priya"])
    db.commit()
    for kind, rid in (("material", setup["material"]), ("question", question_id)):
        assert send_dm(client, setup, "priya", shared=share(kind, rid)).status_code == 201
    assert send_dm(client, setup, "priya", "Plain text").status_code == 201

    material_msg, question_msg, plain = dm_history(client, setup, "arjun")
    assert material_msg["body"] == ""
    assert material_msg["shared_resource"]["subtitle"] == "PDF · OS · Scheduling"
    question_card = question_msg["shared_resource"]
    assert question_card["title"] == "Which algorithm can starve long jobs?"
    assert question_card["subtitle"] == "OS · Scheduling · easy"
    assert plain["shared_resource"] is None


def test_opening_the_card_navigates_to_the_original_content(client, setup, db):
    question_id = make_question(db, setup["priya"])
    db.commit()
    for kind, rid in (
        ("material", setup["material"]),
        ("question", question_id),
        ("quiz", setup["quiz"]),
    ):
        response = send_channel(client, setup, "priya", shared=share(kind, rid))
        assert response.status_code == 201, response.text
    for message in channel_history(client, setup, "arjun"):
        card = message["shared_resource"]
        opened = client.get(card["api_path"], headers=setup["arjun_h"])
        assert opened.status_code == 200, card
        assert opened.json()["id"] == card["id"]


def test_the_link_keeps_working_after_the_content_is_edited(client, setup):
    send_dm(client, setup, "priya", shared=share("material", setup["material"]))
    edited = client.patch(
        f"{API}/materials/{setup['material']}",
        json={"title": "OS_Scheduling_Notes_v2.pdf"},
        headers=setup["priya_h"],
    )
    assert edited.status_code == 200, edited.text

    [message] = dm_history(client, setup, "arjun")
    card = message["shared_resource"]
    # A live link, not a copy: the card shows the edited original.
    assert card["available"] is True
    assert card["title"] == "OS_Scheduling_Notes_v2.pdf"
    opened = client.get(card["api_path"], headers=setup["arjun_h"])
    assert opened.json()["title"] == "OS_Scheduling_Notes_v2.pdf"


def test_the_message_stores_a_reference_not_a_copy(client, setup, db):
    send_dm(client, setup, "priya", shared=share("material", setup["material"]))
    [message], _ = chat.direct_messages_page(db, setup["conversation"])
    assert message.shared_resource_type == "material"
    assert message.shared_resource_id == setup["material"]
    assert message.body == ""


# --------------------------------------------------------------------------- #
# Sharing rules
# --------------------------------------------------------------------------- #


def test_a_removed_resource_shows_an_unavailable_card(client, setup, db):
    send_dm(client, setup, "priya", shared=share("material", setup["material"]))
    materials.set_removed(db, setup["material"], True)
    db.commit()

    [message] = dm_history(client, setup, "arjun")
    card = message["shared_resource"]
    assert card["available"] is False
    assert card["title"] is None
    assert card["subtitle"] == "This item is no longer available"
    assert card["api_path"] is None


def test_the_card_respects_what_the_reader_may_open(client, setup, db):
    """Priya shares a channel-only material with someone outside that channel."""
    channel_notes = materials.create_material(
        db,
        uploader_id=setup["priya"],
        title="Group-only notes",
        material_type=MaterialType.NOTES,
        subject="OS",
        topic="Scheduling",
        semester="4",
        channel_id=setup["channel"],
    )
    db.commit()
    conv = client.post(
        CONVERSATIONS, json={"user_id": setup["outsider"]}, headers=setup["priya_h"]
    ).json()
    response = client.post(
        f"{CONVERSATIONS}/{conv['id']}/messages",
        json={"shared_resource": share("material", channel_notes.id)},
        headers=setup["priya_h"],
    )
    assert response.status_code == 201
    assert response.json()["shared_resource"]["title"] == "Group-only notes"

    outsider_view = client.get(
        f"{CONVERSATIONS}/{conv['id']}/messages", headers=setup["outsider_h"]
    ).json()["items"]
    card = outsider_view[0]["shared_resource"]
    assert card["available"] is False
    assert card["title"] is None
    # Opening the original is still refused by its own endpoint.
    assert (
        client.get(f"{API}/materials/{channel_notes.id}", headers=setup["outsider_h"]).status_code
        == 403
    )


@pytest.mark.parametrize("kind", ["material", "question", "quiz"])
def test_you_cannot_share_what_does_not_exist(client, setup, kind):
    response = send_dm(client, setup, "priya", shared=share(kind, 999_999))
    assert response.status_code == 404
    assert response.json()["detail"] == "Shared item not found"
    assert dm_history(client, setup, "priya") == []


def test_you_cannot_share_what_you_may_not_open(client, setup, db):
    others_draft = make_quiz(db, setup["arjun"], publish=False)
    conv = client.post(
        CONVERSATIONS, json={"user_id": setup["priya"]}, headers=setup["outsider_h"]
    ).json()
    channel_quiz = make_quiz(db, setup["priya"], channel_id=setup["channel"])
    for kind, rid in (("quiz", others_draft), ("quiz", channel_quiz)):
        response = client.post(
            f"{CONVERSATIONS}/{conv['id']}/messages",
            json={"shared_resource": share(kind, rid)},
            headers=setup["outsider_h"],
        )
        assert response.status_code == 404
        assert response.json()["detail"] == "Shared item not found"


def test_an_adaptive_run_cannot_be_shared(client, setup, db):
    run = make_quiz(db, setup["priya"], adaptive=True)
    response = send_dm(client, setup, "priya", shared=share("quiz", run))
    assert response.status_code == 404


def test_a_question_card_never_shows_the_answer_key(client, setup, db):
    question_id = make_question(db, setup["priya"])
    db.commit()
    response = send_dm(client, setup, "priya", shared=share("question", question_id))
    card = response.json()["shared_resource"]
    assert "A" not in (card["title"], card["subtitle"])
    assert set(card) == {
        "type",
        "id",
        "available",
        "title",
        "subtitle",
        "web_path",
        "api_path",
        "channel_id",
    }


def test_a_blank_message_without_a_share_is_still_refused(client, setup):
    response = send_dm(client, setup, "priya", "   ")
    assert response.status_code == 422
    assert response.json()["detail"] == "Message cannot be empty"
    response = send_channel(client, setup, "priya", "")
    assert response.status_code == 422
    assert response.json()["detail"] == "Message cannot be empty"


def test_an_unknown_resource_type_is_refused(client, setup):
    response = send_dm(client, setup, "priya", shared={"type": "video", "id": 1})
    assert response.status_code == 422


def test_sharing_with_a_comment_keeps_both(client, setup):
    response = send_channel(
        client, setup, "priya", "Read this before Friday", share("material", setup["material"])
    )
    assert response.status_code == 201
    [message] = channel_history(client, setup, "arjun")
    assert message["body"] == "Read this before Friday"
    assert message["shared_resource"]["title"] == OS_NOTES


def test_a_share_can_be_a_thread_reply(client, setup):
    parent = send_channel(client, setup, "arjun", "Anyone have scheduling notes?").json()
    reply = client.post(
        f"{CHANNELS}/{setup['channel']}/messages",
        json={"parent_id": parent["id"], "shared_resource": share("material", setup["material"])},
        headers=setup["priya_h"],
    )
    assert reply.status_code == 201
    thread = client.get(
        f"{CHANNELS}/{setup['channel']}/messages/{parent['id']}/replies",
        headers=setup["arjun_h"],
    ).json()
    assert thread["items"][0]["shared_resource"]["title"] == OS_NOTES


def test_blocking_still_stops_shares(client, setup):
    client.put(f"{API}/blocks/{setup['priya']}", json={"confirm": True}, headers=setup["arjun_h"])
    response = send_dm(client, setup, "priya", shared=share("material", setup["material"]))
    assert response.status_code == 403
    assert response.json()["detail"] == "You can no longer send messages to this user"


def test_the_conversation_list_shows_a_shared_last_message(client, setup):
    send_dm(client, setup, "priya", shared=share("quiz", setup["quiz"]))
    [conv] = client.get(CONVERSATIONS, headers=setup["arjun_h"]).json()
    assert conv["last_message"]["shared_resource"]["title"] == "CPU Scheduling Quiz"
