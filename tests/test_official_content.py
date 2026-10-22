"""S03.1 - official content management in Authorized Channels (issue #26, FR-03).

Test Plan cases are named after their IDs: UT_05, UT_06, ST_06, plus one test per
acceptance criterion. The plan's steps run in the web UI; these call the endpoints
that UI uses (``can_manage_official_content`` decides whether it shows the option).
"""

import pytest
from fastapi.testclient import TestClient

from app.modules.accounts import service as accounts
from app.modules.accounts.models import Role
from app.modules.channels import service as channels
from app.modules.channels.models import ApprovalStatus, ChannelType

API = "/api/v1/materials"
PASSWORD = "Pass@1234"
PDF = b"%PDF-1.4\n1 0 obj << /Type /Catalog >> endobj\ntrailer << >>\n%%EOF\n"
TAGS = {"subject": "DBMS", "topic": "Normalization", "semester": "4", "material_type": "notes"}
QUESTION = {
    "text": "Which normal form removes transitive dependencies?",
    "subject": "DBMS",
    "topic": "Normalization",
    "difficulty": "medium",
    "question_type": "multiple_choice",
    "options": ["1NF", "2NF", "3NF", "BCNF"],
    "correct_answer": "3NF",
}


def make_user(db, username: str, role: Role = Role.STUDENT) -> int:
    user = accounts.create_user(
        db,
        email=f"{username}@test.com",
        display_name=username.title(),
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
    """One Authorized and one Normal Channel, each with a regular member; an
    authorized user is also in the Authorized Channel (ST_06 pre-condition)."""
    ids = {
        "faculty": make_user(db, "faculty_a", Role.AUTHORIZED_USER),
        "faculty_2": make_user(db, "faculty_b", Role.AUTHORIZED_USER),
        "student": make_user(db, "aditi_r", Role.STUDENT),
        "peer": make_user(db, "rahul_m", Role.STUDENT),
        "admin": make_user(db, "admin_a", Role.ADMIN),
    }
    official = channels.create_channel(
        db,
        name="DBMS (official)",
        subject="DBMS",
        created_by=ids["faculty"],
        channel_type=ChannelType.AUTHORIZED,
    )
    channels.set_approval(db, official.id, ApprovalStatus.APPROVED)
    channels.add_member(db, official.id, ids["student"])
    channels.add_member(db, official.id, ids["faculty_2"])
    normal = channels.create_channel(
        db, name="DBMS study group", subject="DBMS", created_by=ids["peer"]
    )
    channels.add_member(db, normal.id, ids["student"])
    db.commit()
    return {
        **ids,
        "official": official.id,
        "normal": normal.id,
        "faculty_h": auth(client, "faculty_a"),
        "faculty_2_h": auth(client, "faculty_b"),
        "student_h": auth(client, "aditi_r"),
        "peer_h": auth(client, "rahul_m"),
        "admin_h": auth(client, "admin_a"),
    }


def upload(client, headers, channel_id, filename="Unit1_Notes_Official.pdf", **fields):
    data = {**TAGS, "channel_id": str(channel_id), **fields}
    files = {"file": (filename, PDF, "application/octet-stream")}
    return client.post(API, data=data, files=files, headers=headers)


def official_material(client, setup) -> dict:
    response = upload(client, setup["faculty_h"], setup["official"], is_official="true")
    assert response.status_code == 201, response.text
    return response.json()


# --------------------------------------------------------------------------- #
# Test Plan cases
# --------------------------------------------------------------------------- #


def test_UT_05_authorized_user_publishes_official_material(client, setup):
    response = upload(client, setup["faculty_h"], setup["official"], is_official="true")

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["title"] == "Unit1_Notes_Official"
    assert body["channel_id"] == setup["official"]
    assert body["is_official"] is True
    assert body["content_label"] == "Official/Verified"
    assert body["tags"][0] == "Official/Verified"
    listed = client.get(
        API, params={"channel_id": setup["official"]}, headers=setup["student_h"]
    ).json()
    assert [(m["id"], m["content_label"]) for m in listed["items"]] == [
        (body["id"], "Official/Verified")
    ]


def test_UT_06_regular_member_cannot_manage_official_content(client, setup):
    material = official_material(client, setup)
    student = setup["student_h"]

    channel = client.get(f"/api/v1/channels/{setup['official']}", headers=student).json()
    shown = client.get(f"{API}/{material['id']}", headers=student).json()
    edit = client.patch(f"{API}/{material['id']}", json={"title": "Hacked"}, headers=student)
    remove = client.delete(f"{API}/{material['id']}", headers=student)
    add = upload(client, student, setup["official"])

    assert channel["my_role"] == "member"
    assert channel["can_manage_official_content"] is False
    assert shown["can_manage"] is False
    assert (edit.status_code, edit.json()["detail"]) == (403, "Insufficient permissions")
    assert (remove.status_code, remove.json()["detail"]) == (403, "Insufficient permissions")
    assert add.status_code == 403
    assert client.get(f"{API}/{material['id']}", headers=student).json()["title"] == (
        "Unit1_Notes_Official"
    )


def test_ST_06_privileges_differ_between_authorized_and_normal_channels(client, setup):
    student, faculty = setup["student_h"], setup["faculty_h"]

    # 1. A regular member in both channel types.
    blocked_upload = upload(client, student, setup["official"])
    blocked_question = client.post(
        "/api/v1/questions", json={**QUESTION, "channel_id": setup["official"]}, headers=student
    )
    peer_upload = upload(client, student, setup["normal"], filename="My_DBMS_Notes.pdf")
    peer_question = client.post(
        "/api/v1/questions", json={**QUESTION, "channel_id": setup["normal"]}, headers=student
    )
    assert blocked_upload.status_code == 403
    assert blocked_question.status_code == 403
    assert peer_upload.status_code == 201
    assert peer_upload.json()["content_label"] == "Peer"
    assert peer_question.status_code == 201
    peer_id = peer_upload.json()["id"]
    assert (
        client.patch(f"{API}/{peer_id}", json={"topic": "BCNF"}, headers=student).status_code == 200
    )

    # 2. An authorized user doing the same in the Authorized Channel.
    material = upload(client, faculty, setup["official"]).json()
    question = client.post(
        "/api/v1/questions", json={**QUESTION, "channel_id": setup["official"]}, headers=faculty
    )
    edited = client.patch(f"{API}/{material['id']}", json={"topic": "3NF"}, headers=faculty)
    removed = client.delete(f"{API}/{material['id']}", headers=faculty)

    assert material["is_official"] is True
    assert question.status_code == 201
    assert edited.status_code == 200 and edited.json()["topic"] == "3NF"
    assert removed.status_code == 204


# --------------------------------------------------------------------------- #
# Acceptance criteria
# --------------------------------------------------------------------------- #


def test_everything_published_in_an_authorized_channel_is_official(client, setup):
    body = upload(client, setup["faculty_h"], setup["official"]).json()

    assert body["is_official"] is True
    assert body["content_style"] == "content-official"


def test_official_cannot_be_claimed_outside_an_authorized_channel(client, setup):
    response = upload(client, setup["student_h"], setup["normal"], is_official="true")

    assert response.status_code == 422
    assert response.json()["detail"] == (
        "Official content can only be published in an Authorized Channel"
    )


def test_authorized_user_can_edit_official_content(client, setup):
    material = official_material(client, setup)

    # Any authorized member of the channel can maintain it, not only the uploader.
    response = client.patch(
        f"{API}/{material['id']}",
        json={"title": "Unit 1 Notes (revised)", "description": "Fixed BCNF example"},
        headers=setup["faculty_2_h"],
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["title"] == "Unit 1 Notes (revised)"
    assert body["description"] == "Fixed BCNF example"
    assert body["is_official"] is True
    assert body["can_manage"] is True


def test_editing_keeps_required_tags(client, setup):
    material = official_material(client, setup)

    response = client.patch(
        f"{API}/{material['id']}", json={"subject": "  "}, headers=setup["faculty_h"]
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "Subject is required"


def test_authorized_user_can_remove_official_content(client, setup):
    material = official_material(client, setup)

    response = client.delete(f"{API}/{material['id']}", headers=setup["faculty_h"])

    assert response.status_code == 204
    student = setup["student_h"]
    assert client.get(f"{API}/{material['id']}", headers=student).status_code == 404
    assert client.get(material["download_url"], headers=student).status_code == 404
    listed = client.get(API, params={"channel_id": setup["official"]}, headers=student).json()
    assert listed["total"] == 0


def test_admins_can_manage_official_content(client, setup):
    material = official_material(client, setup)

    response = client.delete(f"{API}/{material['id']}", headers=setup["admin_h"])

    assert response.status_code == 204


def test_authorized_user_outside_the_channel_cannot_manage_it(client, db, setup):
    make_user(db, "faculty_c", Role.AUTHORIZED_USER)
    outsider = auth(client, "faculty_c")
    material = official_material(client, setup)

    channel = client.get(f"/api/v1/channels/{setup['official']}", headers=outsider).json()
    response = client.patch(f"{API}/{material['id']}", json={"title": "x"}, headers=outsider)

    assert channel["can_manage_official_content"] is False
    # Not a member, so the channel's content is closed before permissions are checked.
    assert response.status_code == 403


def test_official_content_is_distinguished_from_peer_content(client, setup):
    official = official_material(client, setup)
    peer = upload(client, setup["student_h"], setup["normal"], filename="Peer.pdf").json()
    student = setup["student_h"]

    items = {m["id"]: m for m in client.get(API, headers=student).json()["items"]}
    only_official = client.get(API, params={"official": "true"}, headers=student).json()
    only_peer = client.get(API, params={"official": "false"}, headers=student).json()

    assert (items[official["id"]]["content_label"], items[official["id"]]["content_style"]) == (
        "Official/Verified",
        "content-official",
    )
    assert (items[peer["id"]]["content_label"], items[peer["id"]]["content_style"]) == (
        "Peer",
        "content-peer",
    )
    assert "Official/Verified" not in items[peer["id"]]["tags"]
    assert [m["id"] for m in only_official["items"]] == [official["id"]]
    assert [m["id"] for m in only_peer["items"]] == [peer["id"]]


def test_manage_option_is_shown_only_to_authorized_users(client, setup):
    def flag(headers, channel_id):
        return client.get(f"/api/v1/channels/{channel_id}", headers=headers).json()[
            "can_manage_official_content"
        ]

    assert flag(setup["faculty_h"], setup["official"]) is True
    assert flag(setup["admin_h"], setup["official"]) is True
    assert flag(setup["student_h"], setup["official"]) is False
    # Normal Channels hold no official content, even for authorized users.
    assert flag(setup["peer_h"], setup["normal"]) is False
    mine = client.get("/api/v1/channels/mine", headers=setup["faculty_h"]).json()
    assert {c["id"]: c["can_manage_official_content"] for c in mine} == {setup["official"]: True}


def test_regular_member_still_contributes_in_a_normal_channel(client, setup):
    student = setup["student_h"]

    material = upload(client, student, setup["normal"], filename="My_Notes.pdf")
    question = client.post(
        "/api/v1/questions", json={**QUESTION, "channel_id": setup["normal"]}, headers=student
    )

    assert material.status_code == 201
    assert material.json()["is_official"] is False
    assert material.json()["can_manage"] is True
    assert question.status_code == 201


def test_peer_material_is_managed_by_its_uploader_only(client, setup):
    material = upload(client, setup["student_h"], setup["normal"], filename="Mine.pdf").json()

    other = client.delete(f"{API}/{material['id']}", headers=setup["peer_h"])
    own = client.delete(f"{API}/{material['id']}", headers=setup["student_h"])

    assert (other.status_code, other.json()["detail"]) == (403, "Insufficient permissions")
    assert own.status_code == 204


def test_official_flag_survives_edits(client, setup):
    material = official_material(client, setup)

    response = client.patch(
        f"{API}/{material['id']}", json={"is_official": False}, headers=setup["faculty_h"]
    )

    # Unknown fields are ignored and nothing else was sent.
    assert response.status_code == 422
    assert client.get(f"{API}/{material['id']}", headers=setup["faculty_h"]).json()["is_official"]
