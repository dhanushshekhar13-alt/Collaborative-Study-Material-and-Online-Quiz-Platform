"""S14.1 - notifications with user preferences (issue #44, FR-14).

Test Plan cases are named after their IDs: UT_27 and UT_28, plus one test per
acceptance criterion. The plan's steps run in the web UI (the notification bell and
Notification Settings); these call the endpoints that UI uses.
"""

import pytest
from fastapi.testclient import TestClient

from app.modules.accounts import service as accounts
from app.modules.accounts.models import Role
from app.modules.notifications import service as notifications
from app.modules.notifications.service import NotificationKind
from app.modules.questions import service as questions
from app.modules.questions.service import Difficulty, QuestionType

PASSWORD = "Pass@1234"
API = "/api/v1/notifications"


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
def world(client, db) -> dict:
    """Priya's DBMS channel with Rahul and Meera as members; Omar is not in it."""
    ids = {
        "priya": make_user(db, "priya_s", "Priya S"),
        "rahul": make_user(db, "rahul_m", "Rahul M"),
        "meera": make_user(db, "meera_k", "Meera K"),
        "omar": make_user(db, "omar_f", "Omar F"),
    }
    h = {
        key: auth(client, username)
        for key, username in (
            ("priya", "priya_s"),
            ("rahul", "rahul_m"),
            ("meera", "meera_k"),
            ("omar", "omar_f"),
        )
    }
    channel = client.post(
        "/api/v1/channels", json={"name": "DBMS Study Group", "subject": "DBMS"}, headers=h["priya"]
    ).json()["id"]
    for who in ("rahul", "meera"):
        client.post(f"/api/v1/channels/{channel}/join", headers=h[who])
    return {"ids": ids, "h": h, "channel": channel, "client": client}


def client_of(world) -> TestClient:
    return world["client"]


def send(client, world, who: str, body: str) -> int:
    response = client.post(
        f"/api/v1/channels/{world['channel']}/messages",
        json={"body": body},
        headers=world["h"][who],
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def reply(db, world, who: str, parent_id: int, body: str) -> int:
    """Reply in the message's thread through the S10.2 chat endpoint."""
    response = client_of(world).post(
        f"/api/v1/channels/{world['channel']}/messages",
        json={"body": body, "parent_id": parent_id},
        headers=world["h"][who],
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def inbox(client, world, who: str) -> dict:
    response = client.get(API, headers=world["h"][who])
    assert response.status_code == 200
    return response.json()


def publish_quiz(client, db, world, title="Normalization Quiz") -> int:
    for i in range(3):
        questions.create_question(
            db,
            author_id=world["ids"]["priya"],
            subject="DBMS",
            topic="Normalization",
            difficulty=Difficulty.MEDIUM,
            question_type=QuestionType.TRUE_FALSE,
            text=f"Normalization statement {i}",
            options=["True", "False"],
            correct_answer="True",
        )
    db.commit()
    quiz = client.post(
        "/api/v1/quizzes",
        json={
            "title": title,
            "subject": "DBMS",
            "difficulty": "medium",
            "question_count": 3,
            "duration_minutes": 10,
        },
        headers=world["h"]["priya"],
    )
    assert quiz.status_code == 201, quiz.text
    published = client.post(
        f"/api/v1/quizzes/{quiz.json()['id']}/publish",
        json={"channel_id": world["channel"]},
        headers=world["h"]["priya"],
    )
    assert published.status_code == 200, published.text
    return quiz.json()["id"]


def kinds(client, world, who: str) -> list[str]:
    return [n["kind"] for n in inbox(client, world, who)["items"]]


# --------------------------------------------------------------------------- #
# Test Plan cases
# --------------------------------------------------------------------------- #


def test_UT_27_reply_mentioning_the_user_notifies_them_with_a_link(client, db, world):
    original = send(client, world, "rahul", "Is 3NF always lossless?")
    answer = reply(db, world, "meera", original, "@rahul_m yes, with dependency preservation")

    box = inbox(client, world, "rahul")
    assert box["unread_count"] == 1
    [note] = box["items"]
    # Replied to and mentioned in one message: one notification, not two.
    assert note["kind"] == "reply"
    assert note["title"] == "Meera K replied to your message in DBMS Study Group"
    assert note["link"] == f"/app/#/channels/{world['channel']}?message={answer}"
    assert note["is_read"] is False


def test_UT_28_disabled_quiz_updates_stop_while_other_categories_continue(client, db, world):
    publish_quiz(client, db, world, "Warm-up Quiz")
    assert kinds(client, world, "rahul") == ["quiz_update"]

    off = client.put(
        f"{API}/preferences",
        json={"preferences": {"quiz_update": False}},
        headers=world["h"]["rahul"],
    )
    assert off.status_code == 200
    assert {p["kind"]: p["enabled"] for p in off.json()}["quiz_update"] is False

    publish_quiz(client, db, world, "Normalization Quiz")
    send(client, world, "meera", "@rahul_m did you see the new quiz?")

    assert kinds(client, world, "rahul") == ["mention", "quiz_update"]
    # Meera did not switch anything off, so she gets both quiz updates.
    assert kinds(client, world, "meera") == ["quiz_update", "quiz_update"]


# --------------------------------------------------------------------------- #
# Acceptance criteria
# --------------------------------------------------------------------------- #


def test_a_reply_notifies_the_author_of_the_message(client, db, world):
    original = send(client, world, "rahul", "Who has the ER notes?")
    reply(db, world, "priya", original, "Uploaded them to the channel")
    [note] = inbox(client, world, "rahul")["items"]
    assert (note["kind"], note["title"]) == (
        "reply",
        "Priya S replied to your message in DBMS Study Group",
    )
    # Replying to yourself notifies nobody.
    reply(db, world, "rahul", original, "Found them, thanks")
    assert inbox(client, world, "rahul")["unread_count"] == 1


def test_a_mention_notifies_each_named_member_once(client, db, world):
    message = send(client, world, "priya", "@rahul_m and @Meera_K, also @rahul_m: review by Friday")
    for who in ("rahul", "meera"):
        [note] = inbox(client, world, who)["items"]
        assert note["kind"] == "mention"
        assert note["title"] == "Priya S mentioned you in DBMS Study Group"
        assert note["link"] == f"/app/#/channels/{world['channel']}?message={message}"
    assert inbox(client, world, "priya")["items"] == []


def test_mentions_reach_channel_members_only(client, db, world):
    send(client, world, "priya", "ping @omar_f and @nobody_here, mail priya@rahul_m.com")
    assert inbox(client, world, "omar")["items"] == []
    assert inbox(client, world, "rahul")["items"] == []


def test_unread_count_and_mark_read(client, db, world):
    send(client, world, "priya", "@rahul_m first")
    send(client, world, "meera", "@rahul_m second")
    h = world["h"]["rahul"]
    assert client.get(f"{API}/unread-count", headers=h).json() == {"unread_count": 2}

    newest, oldest = inbox(client, world, "rahul")["items"]
    assert client.post(f"{API}/{oldest['id']}/read", headers=h).status_code == 204
    box = inbox(client, world, "rahul")
    assert box["unread_count"] == 1
    assert [n["is_read"] for n in box["items"]] == [False, True]
    assert [
        n["id"] for n in client.get(API, params={"unread_only": True}, headers=h).json()["items"]
    ] == [newest["id"]]

    assert client.post(f"{API}/read-all", headers=h).json() == {"unread_count": 0}
    assert client.get(f"{API}/unread-count", headers=h).json() == {"unread_count": 0}


def test_someone_elses_notification_cannot_be_marked(client, db, world):
    send(client, world, "priya", "@rahul_m hello")
    [note] = inbox(client, world, "rahul")["items"]
    response = client.post(f"{API}/{note['id']}/read", headers=world["h"]["meera"])
    assert response.status_code == 404
    assert response.json()["detail"] == "Notification not found"
    assert client.get(API).status_code == 401


def test_quiz_and_channel_updates_generate_notifications(client, db, world):
    quiz_id = publish_quiz(client, db, world)
    post = client.post(
        f"/api/v1/channels/{world['channel']}/posts",
        json={"body": "Exam syllabus is out"},
        headers=world["h"]["meera"],
    )
    assert post.status_code == 201

    rahul = inbox(client, world, "rahul")["items"]
    assert [(n["kind"], n["title"]) for n in rahul] == [
        ("channel_activity", "Meera K posted in DBMS Study Group"),
        ("quiz_update", 'New quiz "Normalization Quiz" in DBMS Study Group'),
    ]
    assert rahul[1]["link"] == f"/app/#/channels/{world['channel']}?quiz={quiz_id}"
    # The quiz's author and the post's author are not told about their own work.
    assert kinds(client, world, "priya") == ["channel_activity"]
    assert kinds(client, world, "meera") == ["quiz_update"]
    # Omar is not a member and hears nothing.
    assert kinds(client, world, "omar") == []


def test_disabling_a_category_stops_only_that_category(client, db, world):
    client.put(
        f"{API}/preferences", json={"preferences": {"mention": False}}, headers=world["h"]["rahul"]
    )
    send(client, world, "priya", "@rahul_m are you there?")
    assert kinds(client, world, "rahul") == []

    original = send(client, world, "rahul", "Back now")
    reply(db, world, "priya", original, "Great")
    client.post(
        f"/api/v1/channels/{world['channel']}/posts",
        json={"body": "Notes"},
        headers=world["h"]["priya"],
    )
    assert kinds(client, world, "rahul") == ["channel_activity", "reply"]


def test_a_reply_that_mentions_still_arrives_when_replies_are_off(client, db, world):
    original = send(client, world, "rahul", "Question about BCNF")
    client.put(
        f"{API}/preferences", json={"preferences": {"reply": False}}, headers=world["h"]["rahul"]
    )
    reply(db, world, "meera", original, "Plain reply")
    reply(db, world, "meera", original, "@rahul_m see the textbook, chapter 7")
    assert kinds(client, world, "rahul") == ["mention"]


def test_preferences_persist_across_sessions(client, db, world):
    h = world["h"]["rahul"]
    defaults = client.get(f"{API}/preferences", headers=h).json()
    assert [p["label"] for p in defaults] == [
        "Replies",
        "Mentions",
        "Quiz Updates",
        "Channel Updates",
        "Direct Messages",
        "Moderation",
    ]
    assert all(p["enabled"] for p in defaults)

    client.put(
        f"{API}/preferences",
        json={"preferences": {"quiz_update": False, "channel_activity": False}},
        headers=h,
    )
    client.put(f"{API}/preferences", json={"preferences": {"channel_activity": True}}, headers=h)

    fresh_session = auth(client, "rahul_m")
    saved = {
        p["kind"]: p["enabled"]
        for p in client.get(f"{API}/preferences", headers=fresh_session).json()
    }
    assert saved == {
        "reply": True,
        "mention": True,
        "quiz_update": False,
        "channel_activity": True,
        "direct_message": True,
        "moderation": True,
    }
    assert (
        notifications.is_enabled(db, world["ids"]["rahul"], NotificationKind.QUIZ_UPDATE) is False
    )


def test_unknown_category_is_rejected(client, world):
    response = client.put(
        f"{API}/preferences", json={"preferences": {"gossip": False}}, headers=world["h"]["rahul"]
    )
    assert response.status_code == 422
