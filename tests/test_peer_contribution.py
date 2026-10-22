"""S03.2 - peer contribution, rating and reporting in Normal Channels (issue #27, FR-03).

Test Plan cases are named after their IDs: IT_05, IT_06, ST_05, plus one test per
acceptance criterion. The plan's steps run in the web UI; these call the endpoints
that UI uses.
"""

import pytest
from fastapi.testclient import TestClient

from app.modules.accounts import service as accounts
from app.modules.accounts.models import Role
from app.modules.channels import service as channels
from app.modules.moderation import service as moderation

CHANNELS = "/api/v1/channels"
PASSWORD = "Pass@1234"
PDF = b"%PDF-1.4\n1 0 obj << /Type /Catalog >> endobj\ntrailer << >>\n%%EOF\n"
TAGS = {"subject": "DBMS", "topic": "ER Model", "semester": "4", "material_type": "notes"}
FOREIGN_KEY = {
    "text": "What is a foreign key?",
    "subject": "DBMS",
    "topic": "ER Model",
    "difficulty": "easy",
    "question_type": "short_answer",
    "correct_answer": "A column that references another table's primary key",
}


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
    """A Normal Channel created by Aditi, with Rahul and Meera as plain members."""
    ids = {
        "creator": make_user(db, "aditi_r", "Aditi R"),
        "member": make_user(db, "rahul_m", "Rahul M"),
        "peer": make_user(db, "meera_k", "Meera K"),
        "outsider": make_user(db, "outsider_p", "Outsider P"),
        "admin": make_user(db, "admin_a", "Admin A", Role.ADMIN),
    }
    headers = {
        k: auth(client, u)
        for k, u in (
            ("creator_h", "aditi_r"),
            ("member_h", "rahul_m"),
            ("peer_h", "meera_k"),
            ("outsider_h", "outsider_p"),
            ("admin_h", "admin_a"),
        )
    }
    channel = client.post(
        CHANNELS, json={"name": "DBMS study group", "subject": "DBMS"}, headers=headers["creator_h"]
    ).json()
    channels.add_member(db, channel["id"], ids["member"])
    channels.add_member(db, channel["id"], ids["peer"])
    db.commit()
    return {**ids, **headers, "channel": channel["id"]}


def posts_url(setup, post_id: int | None = None) -> str:
    base = f"{CHANNELS}/{setup['channel']}/posts"
    return f"{base}/{post_id}" if post_id is not None else base


def upload(client, headers, channel_id, filename="ER_Diagram_Notes.pdf"):
    return client.post(
        "/api/v1/materials",
        data={**TAGS, "channel_id": str(channel_id)},
        files={"file": (filename, PDF, "application/octet-stream")},
        headers=headers,
    )


# --------------------------------------------------------------------------- #
# Test Plan cases
# --------------------------------------------------------------------------- #


def test_IT_05_creator_creates_edits_and_deletes_a_post(client, setup):
    headers = setup["creator_h"]

    created = client.post(
        posts_url(setup), json={"body": "Share your DBMS normalization notes here"}, headers=headers
    )
    assert created.status_code == 201, created.text
    post = created.json()
    assert post["author_name"] == "Aditi R"
    assert post["is_edited"] is False

    edited = client.patch(
        posts_url(setup, post["id"]),
        json={"body": "Share your DBMS normalization and BCNF notes here"},
        headers=headers,
    )
    assert edited.status_code == 200
    assert edited.json()["body"] == "Share your DBMS normalization and BCNF notes here"
    assert edited.json()["is_edited"] is True

    deleted = client.delete(posts_url(setup, post["id"]), params={"confirm": True}, headers=headers)
    assert deleted.status_code == 204
    assert client.get(posts_url(setup), headers=headers).json()["total"] == 0


def test_IT_06_any_member_contributes_material_question_and_quiz(client, setup):
    member, peer, channel_id = setup["member_h"], setup["peer_h"], setup["channel"]

    material = upload(client, member, channel_id)
    question = client.post(
        "/api/v1/questions", json={**FOREIGN_KEY, "channel_id": channel_id}, headers=member
    )
    assert material.status_code == 201, material.text
    assert question.status_code == 201, question.text
    draft = client.post(
        "/api/v1/quizzes",
        json={
            "title": "ER model check",
            "topic": "ER Model",
            "difficulty": "easy",
            "question_count": 1,
            "duration_minutes": 5,
            "channel_id": channel_id,
            "question_ids": [question.json()["id"]],
        },
        headers=member,
    )
    assert draft.status_code == 201, draft.text
    published = client.post(
        f"/api/v1/quizzes/{draft.json()['id']}/publish",
        json={"channel_id": channel_id},
        headers=member,
    )
    assert published.status_code == 200, published.text

    # All three are saved and visible to another member of the channel.
    materials = client.get(
        "/api/v1/materials", params={"channel_id": channel_id}, headers=peer
    ).json()
    questions = client.get(
        "/api/v1/questions", params={"channel_id": channel_id}, headers=peer
    ).json()
    quizzes = client.get("/api/v1/quizzes", params={"channel_id": channel_id}, headers=peer).json()
    assert [m["title"] for m in materials["items"]] == ["ER_Diagram_Notes"]
    assert [q["text"] for q in questions] == ["What is a foreign key?"]
    assert [q["title"] for q in quizzes] == ["ER model check"]


def test_ST_05_peers_rate_and_report_channel_content(client, db, setup):
    channel_id = setup["channel"]
    material = upload(client, setup["member_h"], channel_id).json()
    duplicate = upload(client, setup["member_h"], channel_id, filename="ER_Notes_copy.pdf").json()
    peer = setup["peer_h"]

    rated = client.put(
        f"/api/v1/materials/{material['id']}/review", json={"rating": 4}, headers=peer
    )
    report = client.post(
        f"/api/v1/materials/{duplicate['id']}/reports", json={"reason": "duplicate"}, headers=peer
    )

    assert rated.status_code == 200
    assert (rated.json()["rating_average"], rated.json()["rating_count"]) == (4.0, 1)
    assert report.status_code == 201
    assert report.json()["reason"] == "duplicate"
    queue = [
        moderation.get_report(db, i) for i in moderation.open_reports(db, channel_id=channel_id)
    ]
    assert [(r.target_id, r.reporter_id) for r in queue] == [(duplicate["id"], setup["peer"])]


# --------------------------------------------------------------------------- #
# Acceptance criteria
# --------------------------------------------------------------------------- #


def test_any_member_can_post_not_only_the_creator(client, setup):
    response = client.post(
        posts_url(setup), json={"body": "Anyone has PYQs?"}, headers=setup["member_h"]
    )

    assert response.status_code == 201
    assert response.json()["author_name"] == "Rahul M"


def test_only_the_author_edits_or_deletes_a_post(client, setup):
    post = client.post(posts_url(setup), json={"body": "My post"}, headers=setup["member_h"]).json()

    # Not even the channel creator can rewrite someone else's post.
    edit = client.patch(
        posts_url(setup, post["id"]), json={"body": "x"}, headers=setup["creator_h"]
    )
    delete = client.delete(
        posts_url(setup, post["id"]), params={"confirm": True}, headers=setup["peer_h"]
    )
    listed = client.get(posts_url(setup), headers=setup["peer_h"]).json()["items"]

    assert (edit.status_code, edit.json()["detail"]) == (
        403,
        "Only the author can edit or delete this post",
    )
    assert delete.status_code == 403
    assert [(p["body"], p["can_edit"]) for p in listed] == [("My post", False)]


def test_deleting_a_post_needs_confirmation(client, setup):
    headers = setup["member_h"]
    post = client.post(posts_url(setup), json={"body": "Delete me"}, headers=headers).json()

    response = client.delete(posts_url(setup, post["id"]), headers=headers)

    assert (response.status_code, response.json()["detail"]) == (
        400,
        "Confirm deletion of this post",
    )
    assert client.get(posts_url(setup), headers=headers).json()["total"] == 1


def test_edited_posts_show_an_edited_indicator(client, setup):
    headers = setup["member_h"]
    post = client.post(posts_url(setup), json={"body": "First"}, headers=headers).json()
    client.patch(posts_url(setup, post["id"]), json={"body": "Second"}, headers=headers)

    listed = client.get(posts_url(setup), headers=setup["peer_h"]).json()["items"]

    assert listed[0]["is_edited"] is True
    assert listed[0]["edited_at"] is not None
    assert listed[0]["body"] == "Second"


def test_posts_are_for_channel_members(client, setup):
    outsider = setup["outsider_h"]

    post = client.post(posts_url(setup), json={"body": "Hi"}, headers=outsider)
    board = client.get(posts_url(setup), headers=outsider)

    assert (post.status_code, post.json()["detail"]) == (403, "Join this channel to post")
    assert (board.status_code, board.json()["detail"]) == (
        403,
        "Join this channel to view its content",
    )
    # Admins may read the board for moderation.
    assert client.get(posts_url(setup), headers=setup["admin_h"]).status_code == 200


def test_a_post_needs_text(client, setup):
    response = client.post(posts_url(setup), json={"body": "   "}, headers=setup["member_h"])

    assert (response.status_code, response.json()["detail"]) == (422, "Write something to post")


def test_a_post_from_another_channel_is_not_found(client, setup):
    other = client.post(
        CHANNELS, json={"name": "OS group", "subject": "OS"}, headers=setup["member_h"]
    ).json()
    post = client.post(
        f"{CHANNELS}/{other['id']}/posts", json={"body": "OS post"}, headers=setup["member_h"]
    ).json()

    response = client.patch(
        posts_url(setup, post["id"]), json={"body": "moved?"}, headers=setup["member_h"]
    )

    assert response.status_code == 404


def test_contributions_are_attributed_to_their_author(client, setup):
    channel_id, member, peer = setup["channel"], setup["member_h"], setup["peer_h"]
    upload(client, member, channel_id)
    question = client.post(
        "/api/v1/questions", json={**FOREIGN_KEY, "channel_id": channel_id}, headers=member
    ).json()
    draft = client.post(
        "/api/v1/quizzes",
        json={
            "topic": "ER Model",
            "difficulty": "easy",
            "question_count": 1,
            "duration_minutes": 5,
            "channel_id": channel_id,
            "question_ids": [question["id"]],
        },
        headers=member,
    ).json()
    client.post(
        f"/api/v1/quizzes/{draft['id']}/publish", json={"channel_id": channel_id}, headers=member
    )

    material = client.get("/api/v1/materials", params={"channel_id": channel_id}, headers=peer)
    bank = client.get("/api/v1/questions", params={"channel_id": channel_id}, headers=peer)
    quizzes = client.get("/api/v1/quizzes", params={"channel_id": channel_id}, headers=peer)
    quiz = client.get(f"/api/v1/quizzes/{draft['id']}", headers=peer)

    assert material.json()["items"][0]["uploader_name"] == "Rahul M"
    assert bank.json()[0]["author_name"] == "Rahul M"
    assert quizzes.json()[0]["creator_name"] == "Rahul M"
    assert quiz.json()["creator_name"] == "Rahul M"


def test_a_rating_updates_the_items_average(client, setup):
    channel_id = setup["channel"]
    material = upload(client, setup["member_h"], channel_id).json()
    question = client.post(
        "/api/v1/questions",
        json={**FOREIGN_KEY, "channel_id": channel_id},
        headers=setup["member_h"],
    ).json()

    client.put(
        f"/api/v1/materials/{material['id']}/review", json={"rating": 4}, headers=setup["peer_h"]
    )
    after = client.put(
        f"/api/v1/materials/{material['id']}/review", json={"rating": 2}, headers=setup["creator_h"]
    ).json()
    rated_question = client.put(
        f"/api/v1/questions/{question['id']}/rating", json={"rating": 5}, headers=setup["peer_h"]
    )

    assert after["rating_average"] == 3.0
    assert rated_question.json()["rating_average"] == 5.0


def test_a_duplicate_report_on_a_question_reaches_moderators(client, db, setup):
    channel_id = setup["channel"]
    question = client.post(
        "/api/v1/questions",
        json={**FOREIGN_KEY, "channel_id": channel_id},
        headers=setup["member_h"],
    ).json()

    response = client.post(
        f"/api/v1/questions/{question['id']}/reports",
        json={"reason": "duplicate"},
        headers=setup["peer_h"],
    )

    assert response.status_code == 201, response.text
    queue = [
        moderation.get_report(db, i) for i in moderation.open_reports(db, channel_id=channel_id)
    ]
    assert [r.target_id for r in queue] == [question["id"]]
