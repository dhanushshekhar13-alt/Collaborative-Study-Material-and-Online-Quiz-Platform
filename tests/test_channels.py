"""S02.1 - channel creation and classification (issue #20, FR-02).

Test Plan cases are named after their IDs: UT_03, UT_04, ST_04, ST_27.
"""

import pytest
from fastapi.testclient import TestClient

from app.modules.accounts import service as accounts
from app.modules.accounts.models import Role
from app.modules.channels import service as channels

API = "/api/v1/channels"
PASSWORD = "Pass@1234"


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


@pytest.fixture()
def student(client, db):
    make_user(db, "student_a")
    return auth(client, "student_a")


@pytest.fixture()
def faculty(client, db):
    make_user(db, "faculty_a", Role.AUTHORIZED_USER)
    return auth(client, "faculty_a")


@pytest.fixture()
def admin(client, db):
    make_user(db, "admin_a", Role.ADMIN)
    return auth(client, "admin_a")


def create(client, headers, **body):
    payload = {"name": "DBMS Study Group", "subject": "Database Management Systems", **body}
    return client.post(API, json=payload, headers=headers)


# --------------------------------------------------------------------------- #
# UT_03 - creating a Normal Channel
# --------------------------------------------------------------------------- #


def test_UT_03_student_creates_a_normal_channel_and_becomes_a_member(client, student):
    response = create(client, student, channel_type="normal")

    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "DBMS Study Group"
    assert body["type_label"] == "Normal"
    assert body["approval_status"] == "approved"
    assert body["member_count"] == 1
    assert body["my_role"] == "owner"

    page = client.get(f"{API}/{body['id']}", headers=student).json()
    assert page["my_role"] == "owner"


def test_normal_is_the_default_type(client, student):
    assert create(client, student).json()["type_label"] == "Normal"


def test_channel_name_is_required(client, student):
    response = create(client, student, name="   ")

    assert response.status_code == 422
    assert response.json()["detail"] == "Enter a channel name"


def test_subject_or_topic_is_required(client, student):
    response = client.post(API, json={"name": "No subject"}, headers=student)

    assert response.status_code == 422
    assert response.json()["detail"] == "Enter a subject or topic"


def test_a_topic_alone_is_enough(client, student):
    response = client.post(API, json={"name": "Graphs", "topic": "BFS"}, headers=student)

    assert response.status_code == 201
    assert response.json()["topic"] == "BFS"


def test_creating_a_channel_requires_sign_in(client):
    assert create(client, {}).status_code == 401


# --------------------------------------------------------------------------- #
# UT_04 - Authorized Channels are restricted, server side
# --------------------------------------------------------------------------- #


def test_UT_04_student_cannot_create_an_authorized_channel(client, student, db):
    response = create(client, student, name="Official CS301 Channel", channel_type="authorized")

    assert response.status_code == 403
    assert response.json()["detail"] == (
        "Only authorized users/admins can create Authorized Channels"
    )
    assert channels.list_channels(db, viewer_id=0, viewer_is_admin=True) == []


def test_UT_04_student_cannot_approve_an_authorized_channel(client, student, faculty):
    pending = create(client, faculty, name="Official CS301", channel_type="authorized").json()

    response = client.post(f"{API}/{pending['id']}/approve", headers=student)

    assert response.status_code == 403
    assert response.json()["detail"] == "Unauthorized"


def test_authorized_user_request_waits_for_admin_approval(client, faculty):
    body = create(client, faculty, name="Official CS301", channel_type="authorized").json()

    assert body["type_label"] == "Authorized"
    assert body["approval_status"] == "pending"
    assert body["is_verified"] is False


def test_an_admin_created_authorized_channel_is_approved_at_once(client, admin):
    body = create(client, admin, name="Official CS302", channel_type="authorized").json()

    assert body["approval_status"] == "approved"
    assert body["is_verified"] is True


# --------------------------------------------------------------------------- #
# ST_27 - admin approves a pending Authorized Channel
# --------------------------------------------------------------------------- #


def test_ST_27_admin_approves_a_pending_channel_and_it_becomes_visible(
    client, faculty, admin, student
):
    pending = create(client, faculty, name="Official CS301", channel_type="authorized").json()

    # Before approval: only the requester (and admins) can see it.
    assert [c["name"] for c in client.get(API, headers=student).json()] == []
    assert client.get(f"{API}/{pending['id']}", headers=student).status_code == 404
    assert [c["name"] for c in client.get(API, headers=faculty).json()] == ["Official CS301"]

    # Admin > Channel Requests > open > Approve.
    requests = client.get(f"{API}/requests", headers=admin).json()
    assert [r["name"] for r in requests] == ["Official CS301"]
    approved = client.post(f"{API}/{pending['id']}/approve", headers=admin)

    assert approved.status_code == 200
    assert approved.json()["approval_status"] == "approved"
    assert approved.json()["type_label"] == "Authorized"
    assert approved.json()["is_verified"] is True
    assert client.get(f"{API}/requests", headers=admin).json() == []
    # Now visible platform-wide as an Authorized Channel.
    listed = client.get(API, headers=student).json()
    assert [(c["name"], c["type_label"]) for c in listed] == [("Official CS301", "Authorized")]


def test_a_decided_request_cannot_be_decided_again(client, faculty, admin):
    pending = create(client, faculty, name="Official CS301", channel_type="authorized").json()
    client.post(f"{API}/{pending['id']}/approve", headers=admin)

    response = client.post(f"{API}/{pending['id']}/reject", headers=admin)

    assert response.status_code == 409


def test_a_rejected_channel_stays_hidden_from_others(client, faculty, admin, student):
    pending = create(client, faculty, name="Official CS301", channel_type="authorized").json()

    rejected = client.post(f"{API}/{pending['id']}/reject", headers=admin).json()

    assert rejected["approval_status"] == "rejected"
    assert client.get(API, headers=student).json() == []
    assert client.get(f"{API}/{pending['id']}", headers=faculty).status_code == 200


def test_channel_requests_are_admin_only(client, faculty):
    assert client.get(f"{API}/requests", headers=faculty).status_code == 403


# --------------------------------------------------------------------------- #
# ST_04 - the type label is consistent in search, lists and channel pages
# --------------------------------------------------------------------------- #


def test_ST_04_type_label_is_consistent_across_list_search_and_page(client, student, admin):
    normal = create(client, student, name="DBMS Study Group").json()
    official = create(
        client, admin, name="DBMS Official", subject="DBMS", channel_type="authorized"
    ).json()

    listed = {c["id"]: c for c in client.get(API, headers=student).json()}
    searched = {c["id"]: c for c in client.get(API, params={"q": "dbms"}, headers=student).json()}
    pages = {i: client.get(f"{API}/{i}", headers=student).json() for i in listed}

    expected = {
        normal["id"]: ("Normal", "channel-normal", False),
        official["id"]: ("Authorized", "channel-authorized", True),
    }
    for view in (listed, searched, pages):
        assert {
            i: (c["type_label"], c["type_style"], c["is_verified"]) for i, c in view.items()
        } == (expected)


def test_search_matches_name_subject_or_topic_and_filters_by_type(client, student, admin):
    create(client, student, name="Graphs", subject=None, topic="Dijkstra")
    create(client, student, name="Networks", subject="Computer Networks")
    create(
        client, admin, name="OS Official", subject="Operating Systems", channel_type="authorized"
    )

    def names(**params):
        return [c["name"] for c in client.get(API, params=params, headers=student).json()]

    assert names(q="dijkstra") == ["Graphs"]
    assert names(q="networks") == ["Networks"]
    assert names(type="authorized") == ["OS Official"]
    assert names(type="normal") == ["Graphs", "Networks"]


def test_an_unknown_channel_is_not_found(client, student):
    assert client.get(f"{API}/999", headers=student).status_code == 404
