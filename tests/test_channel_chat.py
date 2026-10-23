"""S10.1 - channel messaging with persistence and history (issue #36, FR-10).

Test Plan cases are named after their IDs: UT_19, UT_20, ST_19, ST_20, plus one test
per acceptance criterion. The plan's steps run in the web UI; these call the endpoints
that UI uses.
"""

import pytest
from fastapi.testclient import TestClient

from app.modules.accounts import service as accounts
from app.modules.accounts.models import Role
from app.modules.channels import service as channels
from app.modules.channels.models import ChannelType
from app.modules.chat import service as chat

CHANNELS = "/api/v1/channels"
PASSWORD = "Pass@1234"
ER_NOTES = "Can someone share the ER diagram notes?"


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


@pytest.fixture()
def setup(client, db) -> dict:
    """ "DBMS Study Group", created by Aditi, with Rahul and Meera as members."""
    ids = {
        "creator": make_user(db, "aditi_r", "Aditi R"),
        "member": make_user(db, "rahul_m", "Rahul M"),
        "peer": make_user(db, "meera_k", "Meera K"),
        "outsider": make_user(db, "outsider_p", "Outsider P"),
        "admin": make_user(db, "admin_a", "Admin A", Role.ADMIN),
    }
    headers = {
        f"{k}_h": auth(client, u)
        for k, u in (
            ("creator", "aditi_r"),
            ("member", "rahul_m"),
            ("peer", "meera_k"),
            ("outsider", "outsider_p"),
            ("admin", "admin_a"),
        )
    }
    channel = client.post(
        CHANNELS, json={"name": "DBMS Study Group", "subject": "DBMS"}, headers=headers["creator_h"]
    ).json()
    channels.add_member(db, channel["id"], ids["member"])
    channels.add_member(db, channel["id"], ids["peer"])
    db.commit()
    return {**ids, **headers, "channel": channel["id"]}


def url(setup) -> str:
    return f"{CHANNELS}/{setup['channel']}/messages"


def send(client, setup, who: str, body: str):
    return client.post(url(setup), json={"body": body}, headers=setup[f"{who}_h"])


def bodies(page: dict) -> list[str]:
    return [m["body"] for m in page["items"]]


# --------------------------------------------------------------------------- #
# Test Plan cases
# --------------------------------------------------------------------------- #


def test_UT_19_a_member_sends_a_message_all_members_see_with_sender_and_time(client, setup):
    response = send(client, setup, "member", ER_NOTES)
    assert response.status_code == 201
    sent = response.json()
    assert sent["body"] == ER_NOTES
    assert sent["sender_id"] == setup["member"]
    assert sent["sender_name"] == "Rahul M"
    assert sent["created_at"]

    for who in ("creator", "member", "peer"):
        page = client.get(url(setup), headers=setup[f"{who}_h"]).json()
        assert page["items"] == [sent], who


def test_UT_20_an_empty_message_is_refused_and_nothing_is_posted(client, setup, db):
    for blank in ("", "   ", "\n\t "):
        response = send(client, setup, "member", blank)
        assert response.status_code == 422
        assert response.json()["detail"] == "Message cannot be empty"
    missing = client.post(url(setup), json={}, headers=setup["member_h"])
    assert missing.status_code == 422
    assert missing.json()["detail"] == "Message cannot be empty"

    assert client.get(url(setup), headers=setup["member_h"]).json()["items"] == []
    assert chat.channel_history(db, setup["channel"]) == []


def test_ST_19_messages_keep_their_order_and_details_after_logout_and_login(client, setup):
    script = [
        ("creator", "Welcome to the DBMS group"),
        ("member", ER_NOTES),
        ("peer", "Yes, uploading now!"),
        ("member", "Thanks Meera"),
        ("creator", "Quiz on normalization tomorrow"),
    ]
    for who, body in script:
        assert send(client, setup, who, body).status_code == 201
    before = client.get(url(setup), headers=setup["member_h"]).json()["items"]
    assert [m["body"] for m in before] == [body for _, body in script]

    # Logging out discards the token; logging back in issues a new one.
    fresh = auth(client, "rahul_m")
    after = client.get(url(setup), headers=fresh).json()["items"]
    assert after == before
    assert [m["sender_name"] for m in after] == [
        "Aditi R",
        "Rahul M",
        "Meera K",
        "Rahul M",
        "Aditi R",
    ]
    assert all(m["created_at"] for m in after)


def test_ST_20_scrolling_back_through_500_messages_loads_every_one_in_order(client, setup, db):
    senders = [setup["creator"], setup["member"], setup["peer"]]
    for i in range(520):
        chat.post_channel_message(
            db, channel_id=setup["channel"], sender_id=senders[i % 3], body=f"message {i}"
        )
    db.commit()

    pages = []
    page = client.get(url(setup), params={"limit": 100}, headers=setup["member_h"]).json()
    pages.append(page)
    while page["has_more"]:
        oldest = page["items"][0]["id"]
        page = client.get(
            url(setup), params={"limit": 100, "before": oldest}, headers=setup["member_h"]
        ).json()
        pages.append(page)

    assert len(pages) == 6
    assert all(len(p["items"]) == 100 for p in pages[:-1])
    # Prepend each older page, as the UI does when scrolling up.
    seen = [m for p in reversed(pages) for m in p["items"]]
    assert [m["body"] for m in seen] == [f"message {i}" for i in range(520)]
    assert len({m["id"] for m in seen}) == 520


# --------------------------------------------------------------------------- #
# Acceptance criteria
# --------------------------------------------------------------------------- #


def test_a_sent_message_carries_the_server_sender_not_one_from_the_client(client, setup):
    response = client.post(
        url(setup),
        json={"body": "hi", "sender_id": setup["creator"]},
        headers=setup["peer_h"],
    )
    assert response.status_code == 201
    assert response.json()["sender_id"] == setup["peer"]
    assert response.json()["sender_name"] == "Meera K"


def test_a_message_is_stored_trimmed_and_too_long_ones_are_refused(client, setup):
    assert send(client, setup, "member", "  hello  ").json()["body"] == "hello"
    assert send(client, setup, "member", "x" * 2000).status_code == 201
    assert send(client, setup, "member", "x" * 2001).status_code == 422


def test_messages_persist_and_reload_in_the_order_they_were_sent(client, setup, db):
    for i in range(5):
        send(client, setup, ("creator", "member", "peer")[i % 3], f"m{i}")
    db.expire_all()
    page = client.get(url(setup), headers=setup["peer_h"]).json()
    assert bodies(page) == ["m0", "m1", "m2", "m3", "m4"]
    ids = [m["id"] for m in page["items"]]
    assert ids == sorted(ids)
    assert page["has_more"] is False


def test_opening_the_chat_shows_the_newest_messages_oldest_first(client, setup):
    for i in range(7):
        send(client, setup, "member", f"m{i}")
    page = client.get(url(setup), params={"limit": 3}, headers=setup["member_h"]).json()
    assert bodies(page) == ["m4", "m5", "m6"]
    assert page["has_more"] is True


def test_polling_after_the_newest_message_returns_only_new_ones(client, setup):
    for i in range(3):
        send(client, setup, "member", f"m{i}")
    newest = client.get(url(setup), headers=setup["peer_h"]).json()["items"][-1]["id"]
    assert client.get(url(setup), params={"after": newest}, headers=setup["peer_h"]).json() == {
        "items": [],
        "has_more": False,
        "limit": 50,
    }
    send(client, setup, "creator", "new one")
    send(client, setup, "member", "another")
    page = client.get(
        url(setup), params={"after": newest, "limit": 1}, headers=setup["peer_h"]
    ).json()
    assert bodies(page) == ["new one"]
    assert page["has_more"] is True


def test_before_and_after_cannot_be_combined(client, setup):
    response = client.get(url(setup), params={"before": 5, "after": 1}, headers=setup["member_h"])
    assert response.status_code == 422
    assert response.json()["detail"] == "Use either before or after, not both"


def test_messages_from_other_channels_never_appear(client, setup, db):
    other = channels.create_channel(db, name="OS group", created_by=setup["member"], subject="OS")
    chat.post_channel_message(db, channel_id=other.id, sender_id=setup["member"], body="elsewhere")
    db.commit()
    send(client, setup, "member", "here")
    assert bodies(client.get(url(setup), headers=setup["member_h"]).json()) == ["here"]


def test_deleted_messages_are_not_shown(client, setup, db):
    from app.modules.chat.models import ChannelMessage

    kept = send(client, setup, "member", "kept").json()["id"]
    gone = send(client, setup, "member", "gone").json()["id"]
    db.get(ChannelMessage, gone).is_deleted = True
    db.commit()
    page = client.get(url(setup), headers=setup["member_h"]).json()
    assert [m["id"] for m in page["items"]] == [kept]


# --------------------------------------------------------------------------- #
# Who may use the chat
# --------------------------------------------------------------------------- #


def test_only_members_read_or_send_in_the_chat(client, setup):
    send(client, setup, "member", ER_NOTES)
    read = client.get(url(setup), headers=setup["outsider_h"])
    assert read.status_code == 403
    assert read.json()["detail"] == "Join the channel to view this chat"
    write = send(client, setup, "outsider", "let me in")
    assert write.status_code == 403
    assert write.json()["detail"] == "Join the channel to send messages"
    # Admins moderate through reports (S11.1, S14.2), not by reading private chats.
    assert client.get(url(setup), headers=setup["admin_h"]).status_code == 403


def test_access_follows_membership_on_every_request(client, setup):
    send(client, setup, "member", "before leaving")
    assert (
        client.post(f"{CHANNELS}/{setup['channel']}/leave", headers=setup["member_h"]).status_code
        == 204
    )
    assert client.get(url(setup), headers=setup["member_h"]).status_code == 403
    assert send(client, setup, "member", "after leaving").status_code == 403

    assert (
        client.post(f"{CHANNELS}/{setup['channel']}/join", headers=setup["outsider_h"]).status_code
        == 200
    )
    page = client.get(url(setup), headers=setup["outsider_h"]).json()
    assert bodies(page) == ["before leaving"]


def test_the_chat_needs_a_signed_in_user(client, setup):
    assert client.get(url(setup)).status_code == 401
    assert client.post(url(setup), json={"body": "hi"}).status_code == 401


def test_a_missing_or_hidden_channel_is_not_found(client, setup, db):
    assert client.get(f"{CHANNELS}/9999/messages", headers=setup["member_h"]).status_code == 404
    pending = channels.create_channel(
        db,
        name="Faculty OS notes",
        created_by=setup["creator"],
        channel_type=ChannelType.AUTHORIZED,
        subject="OS",
    )
    db.commit()
    hidden = f"{CHANNELS}/{pending.id}/messages"
    response = client.get(hidden, headers=setup["outsider_h"])
    assert response.status_code == 404
    assert response.json()["detail"] == "Channel not found"
    # Its requester (and owner) can already talk in it while it waits for approval.
    assert client.post(hidden, json={"body": "hi"}, headers=setup["creator_h"]).status_code == 201


def test_channel_messages_page_rejects_two_cursors(db):
    with pytest.raises(ValueError):
        chat.channel_messages_page(db, 1, before=5, after=1)
