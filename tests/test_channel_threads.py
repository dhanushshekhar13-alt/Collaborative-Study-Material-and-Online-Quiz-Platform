# ruff: noqa: F811 - `setup` is the S10.1 fixture, imported so pytest can inject it.
"""S10.2 - threaded replies and membership-gated chat (issue #37, FR-10, NFR-02).

Test Plan cases are named after their IDs: IT_19 (a reply is threaded under its
parent) and IT_20 (a non-member is denied the chat), plus one test per acceptance
criterion. The plan's steps run in the web UI; these call the endpoints that UI uses.
"""

from app.modules.chat import service as chat
from app.modules.chat.models import ChannelMessage
from tests.test_channel_chat import CHANNELS, ER_NOTES, bodies, send, setup, url  # noqa: F401

REPLY = "Yes, uploading now!"


def reply(client, setup, who: str, parent_id: int, body: str = REPLY):
    return client.post(
        url(setup), json={"body": body, "parent_id": parent_id}, headers=setup[f"{who}_h"]
    )


def thread_url(setup, message_id: int) -> str:
    return f"{url(setup)}/{message_id}/replies"


def thread(client, setup, who: str, message_id: int, **params) -> dict:
    response = client.get(thread_url(setup, message_id), params=params, headers=setup[f"{who}_h"])
    assert response.status_code == 200, response.text
    return response.json()


# --------------------------------------------------------------------------- #
# Test Plan cases
# --------------------------------------------------------------------------- #


def test_IT_19_a_reply_is_threaded_under_its_parent_message(client, setup):
    parent = send(client, setup, "member", ER_NOTES).json()

    response = reply(client, setup, "peer", parent["id"])
    assert response.status_code == 201
    sent = response.json()
    assert sent["parent_id"] == parent["id"]
    assert sent["body"] == REPLY
    assert sent["sender_name"] == "Meera K"

    # Another member opens the thread: the reply sits under the original message.
    opened = thread(client, setup, "creator", parent["id"])
    assert opened["parent"]["id"] == parent["id"]
    assert opened["parent"]["body"] == ER_NOTES
    assert opened["parent"]["reply_count"] == 1
    assert [(m["id"], m["parent_id"], m["body"]) for m in opened["items"]] == [
        (sent["id"], parent["id"], REPLY)
    ]


def test_IT_20_a_non_member_opening_the_chat_directly_is_denied(client, setup):
    parent = send(client, setup, "member", ER_NOTES).json()
    reply(client, setup, "peer", parent["id"])

    for path in (url(setup), thread_url(setup, parent["id"])):
        response = client.get(path, headers=setup["outsider_h"])
        assert response.status_code == 403
        assert response.json()["detail"] == "Join the channel to view this chat"
        # Nothing of the conversation leaks into the refusal.
        assert ER_NOTES not in response.text and REPLY not in response.text


# --------------------------------------------------------------------------- #
# Acceptance criteria
# --------------------------------------------------------------------------- #


def test_a_reply_is_displayed_nested_not_in_the_main_stream(client, setup):
    parent = send(client, setup, "member", ER_NOTES).json()
    reply(client, setup, "peer", parent["id"])
    send(client, setup, "creator", "Quiz on Friday?")

    stream = client.get(url(setup), headers=setup["member_h"]).json()
    assert bodies(stream) == [ER_NOTES, "Quiz on Friday?"]
    assert all(m["parent_id"] is None for m in stream["items"])
    assert bodies(thread(client, setup, "member", parent["id"])) == [REPLY]


def test_the_reply_is_stored_with_a_link_to_its_parent(client, setup, db):
    parent = send(client, setup, "member", ER_NOTES).json()
    sent = reply(client, setup, "peer", parent["id"]).json()

    stored = db.get(ChannelMessage, sent["id"])
    assert stored.parent_id == parent["id"]
    assert stored.channel_id == setup["channel"]
    assert stored.sender_id == setup["peer"]
    assert chat.get_channel_message(db, setup["channel"], sent["id"]).parent_id == parent["id"]


def test_thread_reply_counts_are_visible_on_the_parent(client, setup):
    busy = send(client, setup, "member", ER_NOTES).json()
    quiet = send(client, setup, "creator", "Anyone free at 5?").json()
    reply(client, setup, "peer", busy["id"], "Uploading now")
    last = reply(client, setup, "creator", busy["id"], "Thanks Meera").json()

    stream = {m["id"]: m for m in client.get(url(setup), headers=setup["peer_h"]).json()["items"]}
    assert stream[busy["id"]]["reply_count"] == 2
    assert stream[busy["id"]]["last_reply_at"] == last["created_at"]
    assert stream[quiet["id"]]["reply_count"] == 0
    assert stream[quiet["id"]]["last_reply_at"] is None


def test_the_restriction_is_enforced_server_side(client, setup):
    """No UI involved: a direct API call by a non-member can't read or post a reply."""
    parent = send(client, setup, "member", ER_NOTES).json()

    denied = reply(client, setup, "outsider", parent["id"], "sneaking in")
    assert denied.status_code == 403
    assert denied.json()["detail"] == "Join the channel to send messages"
    assert thread(client, setup, "member", parent["id"])["items"] == []

    # Admins moderate through reports, not by reading private chats.
    assert client.get(thread_url(setup, parent["id"]), headers=setup["admin_h"]).status_code == 403
    assert client.get(thread_url(setup, parent["id"])).status_code == 401


def test_access_to_threads_follows_membership_on_every_request(client, setup):
    parent = send(client, setup, "member", ER_NOTES).json()
    reply(client, setup, "peer", parent["id"])
    left = client.post(f"{CHANNELS}/{setup['channel']}/leave", headers=setup["peer_h"])
    assert left.status_code == 204
    response = client.get(thread_url(setup, parent["id"]), headers=setup["peer_h"])
    assert response.status_code == 403
    assert reply(client, setup, "peer", parent["id"], "still here?").status_code == 403


# --------------------------------------------------------------------------- #
# Thread rules
# --------------------------------------------------------------------------- #


def test_replying_to_a_reply_joins_the_same_thread(client, setup):
    parent = send(client, setup, "member", ER_NOTES).json()
    first = reply(client, setup, "peer", parent["id"]).json()

    second = reply(client, setup, "creator", first["id"], "Got it, thanks").json()
    assert second["parent_id"] == parent["id"]
    # A reply's id opens the thread it belongs to.
    opened = thread(client, setup, "member", first["id"])
    assert opened["parent"]["id"] == parent["id"]
    assert bodies(opened) == [REPLY, "Got it, thanks"]


def test_a_reply_needs_a_visible_parent_in_the_same_channel(client, setup, db):
    other = client.post(
        CHANNELS, json={"name": "OS Revision", "subject": "OS"}, headers=setup["member_h"]
    ).json()
    elsewhere = client.post(
        f"{CHANNELS}/{other['id']}/messages", json={"body": "OS notes"}, headers=setup["member_h"]
    ).json()
    gone = send(client, setup, "member", "deleted soon").json()
    db.get(ChannelMessage, gone["id"]).is_deleted = True
    db.commit()

    for parent_id in (9999, elsewhere["id"], gone["id"]):
        response = reply(client, setup, "peer", parent_id)
        assert response.status_code == 404
        assert response.json()["detail"] == "Message not found"
        assert client.get(thread_url(setup, parent_id), headers=setup["peer_h"]).status_code == 404
    assert bodies(client.get(url(setup), headers=setup["peer_h"]).json()) == []


def test_a_blank_reply_is_refused(client, setup):
    parent = send(client, setup, "member", ER_NOTES).json()
    response = reply(client, setup, "peer", parent["id"], "   ")
    assert response.status_code == 422
    assert response.json()["detail"] == "Message cannot be empty"
    assert thread(client, setup, "member", parent["id"])["items"] == []


def test_deleted_replies_are_hidden_and_not_counted(client, setup, db):
    parent = send(client, setup, "member", ER_NOTES).json()
    kept = reply(client, setup, "peer", parent["id"], "kept").json()
    gone = reply(client, setup, "peer", parent["id"], "gone").json()
    db.get(ChannelMessage, gone["id"]).is_deleted = True
    db.commit()

    opened = thread(client, setup, "member", parent["id"])
    assert [m["id"] for m in opened["items"]] == [kept["id"]]
    assert opened["parent"]["reply_count"] == 1


def test_a_long_thread_pages_like_the_main_stream(client, setup, db):
    parent = send(client, setup, "member", ER_NOTES).json()
    for n in range(7):
        chat.post_channel_message(
            db,
            channel_id=setup["channel"],
            sender_id=setup["peer"],
            body=f"reply {n}",
            parent_id=parent["id"],
        )
    db.commit()

    newest = thread(client, setup, "member", parent["id"], limit=3)
    assert bodies(newest) == ["reply 4", "reply 5", "reply 6"] and newest["has_more"]
    older = thread(client, setup, "member", parent["id"], limit=3, before=newest["items"][0]["id"])
    assert bodies(older) == ["reply 1", "reply 2", "reply 3"] and older["has_more"]
    oldest = thread(client, setup, "member", parent["id"], limit=3, before=older["items"][0]["id"])
    assert bodies(oldest) == ["reply 0"] and not oldest["has_more"]
    polled = thread(client, setup, "member", parent["id"], after=newest["items"][-1]["id"])
    assert polled["items"] == []
    both = client.get(
        thread_url(setup, parent["id"]), params={"before": 9, "after": 1}, headers=setup["member_h"]
    )
    assert both.status_code == 422
    assert newest["parent"]["reply_count"] == 7
