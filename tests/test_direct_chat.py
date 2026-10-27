"""S11.1 - direct conversations with block and report (issue #38, FR-11).

Test Plan cases are named after their IDs: UT_21, UT_22, IT_21, IT_22, plus one test
per acceptance criterion. The plan's steps run in the web UI; these call the endpoints
that UI uses.
"""

import pytest
from fastapi.testclient import TestClient

from app.modules.accounts import service as accounts
from app.modules.accounts.models import Role
from app.modules.chat import service as chat
from app.modules.moderation import service as moderation
from app.modules.moderation.models import ReportReason, ReportStatus, ReportTarget

PASSWORD = "Pass@1234"
CONVERSATIONS = "/api/v1/conversations"
BLOCKS = "/api/v1/blocks"
UT_21_MESSAGE = "Hey, can you share your quiz notes?"


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


@pytest.fixture()
def users(client, db) -> dict:
    ids = {
        "priya": make_user(db, "priya_s", "Priya S"),
        "arjun": make_user(db, "arjun_k", "Arjun K"),
        "outsider": make_user(db, "outsider_p", "Outsider P"),
    }
    return {
        **ids,
        "priya_h": auth(client, "priya_s"),
        "arjun_h": auth(client, "arjun_k"),
        "outsider_h": auth(client, "outsider_p"),
    }


def start(client, headers, user_id) -> dict:
    response = client.post(CONVERSATIONS, json={"user_id": user_id}, headers=headers)
    assert response.status_code in (200, 201), response.text
    return response.json()


def send(client, headers, conversation_id, body):
    return client.post(
        f"{CONVERSATIONS}/{conversation_id}/messages", json={"body": body}, headers=headers
    )


def history(client, headers, conversation_id, **params):
    response = client.get(
        f"{CONVERSATIONS}/{conversation_id}/messages", params=params, headers=headers
    )
    assert response.status_code == 200, response.text
    return response.json()


def block(client, headers, user_id, confirm=True):
    return client.put(f"{BLOCKS}/{user_id}", json={"confirm": confirm}, headers=headers)


# --------------------------------------------------------------------------- #
# Test Plan cases
# --------------------------------------------------------------------------- #


def test_UT_21_start_a_private_conversation_and_send_a_message(client, users):
    created = client.post(CONVERSATIONS, json={"user_id": users["arjun"]}, headers=users["priya_h"])
    assert created.status_code == 201
    conv = created.json()
    assert conv["other_user"] == {
        "id": users["arjun"],
        "display_name": "Arjun K",
        "username": "arjun_k",
    }
    assert conv["blocked"] is False and conv["can_send"] is True

    sent = send(client, users["priya_h"], conv["id"], UT_21_MESSAGE)
    assert sent.status_code == 201
    assert sent.json()["sender_id"] == users["priya"]
    assert sent.json()["sender_name"] == "Priya S"

    # Visible to both users: Arjun sees the same conversation from his side.
    for headers in (users["priya_h"], users["arjun_h"]):
        assert [m["body"] for m in history(client, headers, conv["id"])["items"]] == [UT_21_MESSAGE]
    arjun_view = client.get(CONVERSATIONS, headers=users["arjun_h"]).json()
    assert [c["id"] for c in arjun_view] == [conv["id"]]
    assert arjun_view[0]["other_user"]["id"] == users["priya"]
    assert arjun_view[0]["last_message"]["body"] == UT_21_MESSAGE


def test_UT_22_blocked_user_can_no_longer_send_and_a_blocked_indicator_is_shown(client, users):
    conv = start(client, users["priya_h"], users["arjun"])
    assert send(client, users["arjun_h"], conv["id"], "hello").status_code == 201

    blocked = block(client, users["priya_h"], users["arjun"])
    assert blocked.status_code == 200
    assert blocked.json() == {
        "user": {"id": users["arjun"], "display_name": "Arjun K", "username": "arjun_k"},
        "blocked": True,
    }

    refused = send(client, users["arjun_h"], conv["id"], "are you there?")
    assert refused.status_code == 403
    assert refused.json()["detail"] == "You can no longer send messages to this user"

    priya_view = client.get(f"{CONVERSATIONS}/{conv['id']}", headers=users["priya_h"]).json()
    assert priya_view["blocked"] is True and priya_view["blocked_by_me"] is True
    assert priya_view["can_send"] is False
    arjun_view = client.get(f"{CONVERSATIONS}/{conv['id']}", headers=users["arjun_h"]).json()
    assert arjun_view["blocked"] is True and arjun_view["can_send"] is False
    assert arjun_view["blocked_by_me"] is False
    assert [m["body"] for m in history(client, users["priya_h"], conv["id"])["items"]] == ["hello"]


def test_IT_21_twenty_plus_messages_load_in_order_before_and_after_re_login(client, users):
    conv = start(client, users["priya_h"], users["arjun"])
    sent = []
    for i in range(25):
        who = users["priya_h"] if i % 2 == 0 else users["arjun_h"]
        response = send(client, who, conv["id"], f"message {i:02d}")
        assert response.status_code == 201
        sent.append(response.json()["id"])

    def full_history(headers) -> list[str]:
        page = history(client, headers, conv["id"], limit=10)
        bodies = [m["body"] for m in page["items"]]
        while page["has_more"]:
            page = history(client, headers, conv["id"], limit=10, before=page["items"][0]["id"])
            bodies = [m["body"] for m in page["items"]] + bodies
        return bodies

    expected = [f"message {i:02d}" for i in range(25)]
    before_logout = full_history(users["priya_h"])
    assert before_logout == expected

    # Log out and back in: a fresh token reads the same stored history.
    after_login = full_history(auth(client, "priya_s"))
    assert after_login == before_logout
    assert full_history(users["arjun_h"]) == expected


def test_IT_22_report_an_inappropriate_message_for_moderation(client, users, db):
    conv = start(client, users["arjun_h"], users["priya"])
    message = send(client, users["arjun_h"], conv["id"], "something offensive").json()

    response = client.post(
        f"{CONVERSATIONS}/{conv['id']}/messages/{message['id']}/reports",
        json={"reason": "inappropriate", "details": "Inappropriate content"},
        headers=users["priya_h"],
    )
    assert response.status_code == 201
    body = response.json()
    assert body["message_id"] == message["id"] and body["conversation_id"] == conv["id"]
    assert body["reason"] == "inappropriate" and body["status"] == "open"
    assert body["message"] == "Report submitted for moderation"

    # Logged with the message reference and routed to the moderators' open queue.
    assert body["report_id"] in moderation.open_reports(db)
    report = moderation.get_report(db, body["report_id"])
    assert report.target_type is ReportTarget.DIRECT_MESSAGE
    assert report.target_id == message["id"]
    assert report.reason is ReportReason.INAPPROPRIATE
    assert report.reporter_id == users["priya"]
    assert report.details == "Inappropriate content"
    assert chat.get_direct_message(db, report.target_id).body == "something offensive"


# --------------------------------------------------------------------------- #
# Acceptance criteria
# --------------------------------------------------------------------------- #


def test_a_conversation_can_be_started_with_any_user_and_is_reused(client, users, db):
    admin = make_user(db, "admin_a", "Admin A", Role.ADMIN)
    teacher = make_user(db, "teacher_t", "Teacher T", Role.AUTHORIZED_USER)
    for other in (admin, teacher, users["arjun"]):
        first = client.post(CONVERSATIONS, json={"user_id": other}, headers=users["priya_h"])
        assert first.status_code == 201
    # The same pair always shares one conversation, whoever starts it.
    again = client.post(CONVERSATIONS, json={"user_id": users["priya"]}, headers=users["arjun_h"])
    assert again.status_code == 200
    assert again.json()["other_user"]["id"] == users["priya"]
    assert len(client.get(CONVERSATIONS, headers=users["priya_h"]).json()) == 3


def test_starting_a_conversation_with_yourself_or_nobody_is_refused(client, users):
    me = client.post(CONVERSATIONS, json={"user_id": users["priya"]}, headers=users["priya_h"])
    assert me.status_code == 422
    assert me.json()["detail"] == "You can't start a conversation with yourself"
    nobody = client.post(CONVERSATIONS, json={"user_id": 9999}, headers=users["priya_h"])
    assert nobody.status_code == 404 and nobody.json()["detail"] == "User not found"
    assert client.post(CONVERSATIONS, json={"user_id": users["arjun"]}).status_code == 401


def test_messages_are_delivered_in_order_and_none_are_lost(client, users):
    conv = start(client, users["priya_h"], users["arjun"])
    ids = [send(client, users["priya_h"], conv["id"], f"m{i}").json()["id"] for i in range(12)]
    page = history(client, users["arjun_h"], conv["id"])
    assert [m["id"] for m in page["items"]] == ids
    assert page["has_more"] is False
    # Catching up with ``after`` returns exactly the newer messages, no gaps or repeats.
    newer = history(client, users["arjun_h"], conv["id"], after=ids[5])
    assert [m["id"] for m in newer["items"]] == ids[6:]
    newest = send(client, users["arjun_h"], conv["id"], "reply").json()["id"]
    assert [
        m["id"] for m in history(client, users["priya_h"], conv["id"], after=ids[-1])["items"]
    ] == [newest]


def test_blank_messages_are_refused_and_nothing_is_stored(client, users):
    conv = start(client, users["priya_h"], users["arjun"])
    for body in ("", "   \n"):
        refused = send(client, users["priya_h"], conv["id"], body)
        assert refused.status_code == 422
        assert refused.json()["detail"] == "Message cannot be empty"
    assert history(client, users["priya_h"], conv["id"])["items"] == []


def test_conversation_history_reloads_correctly_on_return(client, users):
    conv = start(client, users["priya_h"], users["arjun"])
    send(client, users["priya_h"], conv["id"], "first")
    send(client, users["arjun_h"], conv["id"], "second")
    # Returning via the conversation list reopens the same history.
    reopened = client.get(CONVERSATIONS, headers=users["priya_h"]).json()[0]
    assert reopened["id"] == conv["id"] and reopened["last_message"]["body"] == "second"
    items = history(client, users["priya_h"], conv["id"])["items"]
    assert [(m["sender_name"], m["body"]) for m in items] == [
        ("Priya S", "first"),
        ("Arjun K", "second"),
    ]


def test_only_participants_can_read_send_or_report(client, users):
    conv = start(client, users["priya_h"], users["arjun"])
    message = send(client, users["priya_h"], conv["id"], "private").json()
    outsider = users["outsider_h"]
    for response in (
        client.get(f"{CONVERSATIONS}/{conv['id']}", headers=outsider),
        client.get(f"{CONVERSATIONS}/{conv['id']}/messages", headers=outsider),
        send(client, outsider, conv["id"], "let me in"),
        client.post(
            f"{CONVERSATIONS}/{conv['id']}/messages/{message['id']}/reports",
            json={"reason": "spam"},
            headers=outsider,
        ),
    ):
        assert response.status_code == 404
        assert response.json()["detail"] == "Conversation not found"
    assert client.get(CONVERSATIONS, headers=outsider).json() == []


def test_blocking_stops_further_messages_in_both_directions(client, users):
    conv = start(client, users["priya_h"], users["arjun"])
    assert block(client, users["priya_h"], users["arjun"]).status_code == 200
    mine = send(client, users["priya_h"], conv["id"], "one last thing")
    assert mine.status_code == 403
    assert mine.json()["detail"] == "You have blocked this user. Unblock them to send messages"
    assert send(client, users["arjun_h"], conv["id"], "hi").status_code == 403
    assert history(client, users["arjun_h"], conv["id"])["items"] == []


def test_blocking_needs_confirmation(client, users):
    refused = block(client, users["priya_h"], users["arjun"], confirm=False)
    assert refused.status_code == 400
    assert refused.json()["detail"] == "Confirm blocking this user"
    assert client.get(BLOCKS, headers=users["priya_h"]).json() == []
    self_block = block(client, users["priya_h"], users["priya"])
    assert (
        self_block.status_code == 422 and self_block.json()["detail"] == "You can't block yourself"
    )
    assert block(client, users["priya_h"], 9999).status_code == 404


def test_the_block_takes_effect_immediately_and_persists(client, users, db):
    conv = start(client, users["priya_h"], users["arjun"])
    assert send(client, users["arjun_h"], conv["id"], "before").status_code == 201
    block(client, users["priya_h"], users["arjun"])
    # The very next message is refused; nothing needs a refresh or re-login.
    assert send(client, users["arjun_h"], conv["id"], "after").status_code == 403
    # Stored, so new sessions for both users still see it.
    assert chat.has_blocked(db, users["priya"], users["arjun"])
    assert send(client, auth(client, "arjun_k"), conv["id"], "new session").status_code == 403
    listed = client.get(BLOCKS, headers=auth(client, "priya_s")).json()
    assert [u["id"] for u in listed] == [users["arjun"]]
    # Blocking again changes nothing; starting a "new" conversation reuses the blocked one.
    assert block(client, users["priya_h"], users["arjun"]).status_code == 200
    assert chat.blocked_by(db, users["priya"]) == [users["arjun"]]
    again = client.post(CONVERSATIONS, json={"user_id": users["priya"]}, headers=users["arjun_h"])
    assert again.status_code == 200 and again.json()["can_send"] is False


def test_unblocking_lets_messages_through_again(client, users):
    conv = start(client, users["priya_h"], users["arjun"])
    block(client, users["priya_h"], users["arjun"])
    lifted = client.delete(f"{BLOCKS}/{users['arjun']}", headers=users["priya_h"])
    assert lifted.status_code == 200 and lifted.json()["blocked"] is False
    assert send(client, users["arjun_h"], conv["id"], "thanks").status_code == 201
    assert client.delete(f"{BLOCKS}/{users['arjun']}", headers=users["priya_h"]).status_code == 200


def test_a_reported_message_reaches_the_moderation_queue_with_its_reason(client, users, db):
    conv = start(client, users["arjun_h"], users["priya"])
    message = send(client, users["arjun_h"], conv["id"], "buy followers here").json()
    url = f"{CONVERSATIONS}/{conv['id']}/messages/{message['id']}/reports"

    missing = client.post(url, json={}, headers=users["priya_h"])
    assert missing.status_code == 422
    assert missing.json()["detail"] == "Select a reason for reporting this message"

    first = client.post(url, json={"reason": "spam"}, headers=users["priya_h"]).json()
    repeat = client.post(url, json={"reason": "spam"}, headers=users["priya_h"]).json()
    assert repeat["report_id"] == first["report_id"]
    assert moderation.open_reports(db) == [first["report_id"]]
    assert moderation.get_report(db, first["report_id"]).status is ReportStatus.OPEN


def test_you_cannot_report_your_own_message_or_one_from_another_conversation(client, users):
    conv = start(client, users["priya_h"], users["arjun"])
    other = start(client, users["priya_h"], users["outsider"])
    mine = send(client, users["priya_h"], conv["id"], "mine").json()
    elsewhere = send(client, users["outsider_h"], other["id"], "elsewhere").json()

    own = client.post(
        f"{CONVERSATIONS}/{conv['id']}/messages/{mine['id']}/reports",
        json={"reason": "spam"},
        headers=users["priya_h"],
    )
    assert own.status_code == 403 and own.json()["detail"] == "You can't report your own message"
    wrong = client.post(
        f"{CONVERSATIONS}/{conv['id']}/messages/{elsewhere['id']}/reports",
        json={"reason": "spam"},
        headers=users["priya_h"],
    )
    assert wrong.status_code == 404 and wrong.json()["detail"] == "Message not found"


def test_service_find_and_open_conversation(db):
    a = make_user(db, "a_user", "A")
    b = make_user(db, "b_user", "B")
    assert chat.find_conversation(db, a, b) is None
    conv = chat.open_conversation(db, a, b)
    assert chat.find_conversation(db, b, a) == conv
    assert chat.conversation_participants(db, conv) == sorted([a, b])
    with pytest.raises(ValueError):
        chat.open_conversation(db, a, a)
    with pytest.raises(ValueError):
        chat.block_user(db, a, a)
    with pytest.raises(ValueError):
        chat.direct_messages_page(db, conv, before=1, after=1)
