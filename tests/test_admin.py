"""S14.2 - admin management of users, channels and content (issue #45, FR-14).

Test Plan cases are named after their IDs: IT_27 (suspend a user), IT_28 (a non-admin
cannot reach the admin dashboard), ST_27 (approve an Authorized Channel request) and
ST_28 (remove reported content: poster notified, action logged), plus one test per
acceptance criterion. The plan's steps run in the web UI; these call the endpoints that
UI uses.
"""

import pytest
from fastapi.testclient import TestClient

from app.modules.accounts import service as accounts
from app.modules.accounts.models import Role
from app.modules.materials import service as materials
from app.modules.materials.models import MaterialType
from app.modules.questions import service as questions
from app.modules.questions.models import Difficulty, QuestionType

API = "/api/v1"
ADMIN = f"{API}/admin"
PASSWORD = "Pass@1234"


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


def login(client: TestClient, username: str):
    return client.post(f"{API}/accounts/login", json={"username": username, "password": PASSWORD})


def auth(client: TestClient, username: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {login(client, username).json()['access_token']}"}


@pytest.fixture()
def people(client, db) -> dict:
    ids = {
        "admin": make_user(db, "admin_a", "Admin A", Role.ADMIN),
        "other_admin": make_user(db, "admin_b", "Admin B", Role.ADMIN),
        "faculty": make_user(db, "prof_k", "Prof K", Role.AUTHORIZED_USER),
        "student": make_user(db, "rahul_m", "Rahul M"),
        "spammer": make_user(db, "spam_user01", "Spam User"),
        "reporter1": make_user(db, "meera_k", "Meera K"),
        "reporter2": make_user(db, "aditi_r", "Aditi R"),
    }
    headers = {
        "admin_h": auth(client, "admin_a"),
        "faculty_h": auth(client, "prof_k"),
        "student_h": auth(client, "rahul_m"),
        "spammer_h": auth(client, "spam_user01"),
        "reporter1_h": auth(client, "meera_k"),
        "reporter2_h": auth(client, "aditi_r"),
    }
    return {**ids, **headers}


def suspend(client, people, user_id: int, **body):
    return client.post(
        f"{ADMIN}/users/{user_id}/suspend",
        json={"confirm": True, **body},
        headers=people["admin_h"],
    )


def audit(client, people, **params) -> list[dict]:
    response = client.get(f"{ADMIN}/audit-log", params=params, headers=people["admin_h"])
    assert response.status_code == 200, response.text
    return response.json()["items"]


def request_channel(client, people, name: str = "Official CS301") -> dict:
    response = client.post(
        f"{API}/channels",
        json={"name": name, "subject": "CS301", "channel_type": "authorized"},
        headers=people["faculty_h"],
    )
    assert response.status_code == 201, response.text
    assert response.json()["approval_status"] == "pending"
    return response.json()


@pytest.fixture()
def flagged_material(client, db, people) -> int:
    """A material by spam_user01 reported by two students (ST_28's precondition)."""
    material = materials.create_material(
        db,
        uploader_id=people["spammer"],
        title="Free exam answers here",
        material_type=MaterialType.LINK,
        subject="DBMS",
        url="https://example.com/spam",
    )
    db.commit()
    for who, reason in (("reporter1", "spam"), ("reporter2", "inappropriate")):
        response = client.post(
            f"{API}/materials/{material.id}/reports",
            json={"reason": reason},
            headers=people[f"{who}_h"],
        )
        assert response.status_code == 201, response.text
    return material.id


def remove(client, people, kind: str, target_id: int, reason: str = "Spam link"):
    return client.post(
        f"{ADMIN}/content/{kind}/{target_id}/remove",
        json={"reason": reason},
        headers=people["admin_h"],
    )


# --------------------------------------------------------------------------- #
# Test Plan cases
# --------------------------------------------------------------------------- #


def test_IT_27_admin_suspends_a_user_who_can_then_no_longer_log_in(client, people):
    assert login(client, "spam_user01").status_code == 200
    users = client.get(f"{ADMIN}/users", params={"q": "spam_user01"}, headers=people["admin_h"])
    target = users.json()[0]
    assert (target["username"], target["status"]) == ("spam_user01", "Active")

    response = suspend(client, people, target["id"], reason="Posting spam links")
    assert response.status_code == 200
    assert response.json()["status"] == "Suspended"

    assert login(client, "spam_user01").status_code == 401
    # A session opened before the suspension stops working too.
    assert client.get(f"{API}/accounts/me", headers=people["spammer_h"]).status_code == 401


def test_IT_28_a_student_opening_the_admin_dashboard_is_denied(client, people):
    response = client.get(f"{ADMIN}/dashboard", headers=people["student_h"])
    assert response.status_code == 403
    assert response.json() == {"detail": "Unauthorized"}
    # The dashboard itself renders for an admin.
    assert client.get(f"{ADMIN}/dashboard", headers=people["admin_h"]).status_code == 200


def test_ST_27_admin_approves_a_pending_authorized_channel(client, people):
    channel = request_channel(client, people)
    listed = client.get(f"{API}/channels", headers=people["student_h"]).json()
    assert channel["id"] not in [c["id"] for c in listed]

    requests = client.get(f"{ADMIN}/channel-requests", headers=people["admin_h"]).json()
    assert [(r["name"], r["requester"]["username"]) for r in requests] == [
        ("Official CS301", "prof_k")
    ]

    response = client.post(f"{API}/channels/{channel['id']}/approve", headers=people["admin_h"])
    assert response.status_code == 200
    assert response.json()["approval_status"] == "approved"

    listed = client.get(f"{API}/channels", headers=people["student_h"]).json()
    approved = next(c for c in listed if c["id"] == channel["id"])
    assert approved["type_label"] == "Authorized" and approved["is_verified"]


def test_ST_28_removing_reported_content_notifies_the_poster_and_is_logged(
    client, people, flagged_material
):
    queue = client.get(f"{ADMIN}/reports", headers=people["admin_h"]).json()
    assert queue[0]["target_type"] == "material"
    assert queue[0]["target_id"] == flagged_material
    assert queue[0]["report_count"] == 2
    assert queue[0]["poster"]["username"] == "spam_user01"

    response = remove(client, people, "material", flagged_material, "Rule violation: spam")
    assert response.status_code == 200
    assert response.json()["resolved_reports"] == 2 and response.json()["poster_notified"]

    # Removed platform-wide.
    material_url = f"{API}/materials/{flagged_material}"
    assert client.get(material_url, headers=people["student_h"]).status_code == 404
    # The poster is told why.
    inbox = client.get(f"{API}/notifications", headers=people["spammer_h"]).json()
    assert inbox["unread_count"] == 1
    note = inbox["items"][0]
    assert note["kind"] == "moderation"
    assert note["title"] == 'Your material "Free exam answers here" was removed'
    assert "Rule violation: spam" in note["body"]
    # And the action is in the audit log.
    entry = audit(client, people)[0]
    assert entry["action"] == "remove_content"
    assert (entry["target_type"], entry["target_id"]) == ("material", flagged_material)
    assert entry["actor"]["username"] == "admin_a"
    assert client.get(f"{ADMIN}/reports", headers=people["admin_h"]).json() == []


# --------------------------------------------------------------------------- #
# Acceptance criteria: users
# --------------------------------------------------------------------------- #


def test_suspending_sets_the_status_and_prevents_login(client, people):
    suspend(client, people, people["spammer"])
    roster = {
        u["username"]: u for u in client.get(f"{ADMIN}/users", headers=people["admin_h"]).json()
    }
    assert roster["spam_user01"]["status"] == "Suspended"
    assert roster["rahul_m"]["status"] == "Active"
    suspended = client.get(
        f"{ADMIN}/users", params={"status": "suspended"}, headers=people["admin_h"]
    ).json()
    assert [u["username"] for u in suspended] == ["spam_user01"]
    assert login(client, "spam_user01").status_code == 401


def test_suspension_is_reversible_by_an_admin(client, people):
    suspend(client, people, people["spammer"])
    response = client.post(
        f"{ADMIN}/users/{people['spammer']}/reinstate",
        json={"reason": "Appeal accepted"},
        headers=people["admin_h"],
    )
    assert response.status_code == 200
    assert response.json()["status"] == "Active"
    assert login(client, "spam_user01").status_code == 200
    again = client.post(f"{ADMIN}/users/{people['spammer']}/reinstate", headers=people["admin_h"])
    assert again.status_code == 409
    assert again.json()["detail"] == "This account is not suspended"


def test_suspension_needs_confirmation_and_a_valid_target(client, people):
    unconfirmed = client.post(
        f"{ADMIN}/users/{people['spammer']}/suspend", json={}, headers=people["admin_h"]
    )
    assert unconfirmed.status_code == 400
    assert unconfirmed.json()["detail"] == "Confirm suspending this account"
    assert login(client, "spam_user01").status_code == 200

    assert suspend(client, people, 9999).json()["detail"] == "User not found"
    self_suspend = suspend(client, people, people["admin"])
    assert (self_suspend.status_code, self_suspend.json()["detail"]) == (
        400,
        "You cannot suspend your own account",
    )
    other_admin = suspend(client, people, people["other_admin"])
    assert other_admin.json()["detail"] == "An admin account cannot be suspended"
    suspend(client, people, people["spammer"])
    twice = suspend(client, people, people["spammer"])
    assert (twice.status_code, twice.json()["detail"]) == (409, "This account is already suspended")


def test_suspend_and_reinstate_are_written_to_the_audit_log(client, people):
    suspend(client, people, people["spammer"], reason="Posting spam links")
    client.post(f"{ADMIN}/users/{people['spammer']}/reinstate", headers=people["admin_h"])
    entries = audit(client, people, target_type="user", target_id=people["spammer"])
    assert [e["action"] for e in entries] == ["unsuspend_user", "suspend_user"]
    assert entries[1]["note"] == "Posting spam links"
    assert all(e["actor"]["id"] == people["admin"] and e["created_at"] for e in entries)


# --------------------------------------------------------------------------- #
# Acceptance criteria: admin-only access
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "method,path",
    [
        ("get", "/dashboard"),
        ("get", "/users"),
        ("post", "/users/1/suspend"),
        ("post", "/users/1/reinstate"),
        ("get", "/channel-requests"),
        ("get", "/reports"),
        ("post", "/content/material/1/remove"),
        ("get", "/audit-log"),
    ],
)
def test_every_admin_route_refuses_non_admins_on_the_server(client, people, method, path):
    for who in ("student_h", "faculty_h"):
        response = getattr(client, method)(
            f"{ADMIN}{path}", headers=people[who], **({"json": {}} if method == "post" else {})
        )
        assert response.status_code == 403, (who, path)
        assert response.json() == {"detail": "Unauthorized"}
    assert getattr(client, method)(f"{ADMIN}{path}").status_code == 401


def test_a_refused_admin_action_changes_nothing(client, people, flagged_material):
    client.post(
        f"{ADMIN}/users/{people['spammer']}/suspend",
        json={"confirm": True},
        headers=people["student_h"],
    )
    client.post(
        f"{ADMIN}/content/material/{flagged_material}/remove",
        json={"reason": "x"},
        headers=people["student_h"],
    )
    assert login(client, "spam_user01").status_code == 200
    assert (
        client.get(f"{API}/materials/{flagged_material}", headers=people["student_h"]).status_code
        == 200
    )
    assert audit(client, people) == []


def test_the_dashboard_counts_the_work_waiting(client, people, flagged_material):
    request_channel(client, people)
    suspend(client, people, people["spammer"])
    board = client.get(f"{ADMIN}/dashboard", headers=people["admin_h"]).json()
    assert board == {
        "total_users": 7,
        "suspended_users": 1,
        "pending_channel_requests": 1,
        "open_reports": 2,
        "flagged_items": 1,
    }


# --------------------------------------------------------------------------- #
# Acceptance criteria: channel requests
# --------------------------------------------------------------------------- #


def test_pending_requests_are_listed_with_their_requester(client, people):
    first = request_channel(client, people, "Official CS301")
    second = request_channel(client, people, "Official OS Lab")
    requests = client.get(f"{ADMIN}/channel-requests", headers=people["admin_h"]).json()
    assert [r["id"] for r in requests] == [first["id"], second["id"]]
    assert requests[0]["requester"] == {
        "id": people["faculty"],
        "display_name": "Prof K",
        "username": "prof_k",
    }
    assert requests[0]["requested_at"]


def test_rejecting_keeps_the_channel_out_of_the_authorized_listing(client, people):
    channel = request_channel(client, people)
    response = client.post(
        f"{API}/channels/{channel['id']}/reject",
        json={"note": "Use the department channel instead"},
        headers=people["admin_h"],
    )
    assert response.status_code == 200
    assert response.json()["approval_status"] == "rejected"
    listed = client.get(f"{API}/channels", headers=people["student_h"]).json()
    assert channel["id"] not in [c["id"] for c in listed]
    assert client.get(f"{ADMIN}/channel-requests", headers=people["admin_h"]).json() == []


def test_channel_decisions_are_logged_and_the_requester_is_told(client, people):
    approved = request_channel(client, people, "Official CS301")
    rejected = request_channel(client, people, "Official OS Lab")
    client.post(f"{API}/channels/{approved['id']}/approve", headers=people["admin_h"])
    client.post(
        f"{API}/channels/{rejected['id']}/reject",
        json={"note": "Duplicate of CS301"},
        headers=people["admin_h"],
    )

    entries = audit(client, people, target_type="channel")
    assert [(e["action"], e["target_id"]) for e in entries] == [
        ("reject_channel", rejected["id"]),
        ("approve_channel", approved["id"]),
    ]
    assert entries[0]["note"] == "Duplicate of CS301"
    inbox = client.get(f"{API}/notifications", headers=people["faculty_h"]).json()["items"]
    assert [n["title"] for n in inbox] == [
        'Your Authorized Channel request "Official OS Lab" was rejected',
        'Your Authorized Channel "Official CS301" was approved',
    ]
    assert inbox[0]["body"] == "Duplicate of CS301"


def test_a_decided_request_cannot_be_decided_again_or_logged_twice(client, people):
    channel = request_channel(client, people)
    client.post(f"{API}/channels/{channel['id']}/approve", headers=people["admin_h"])
    again = client.post(f"{API}/channels/{channel['id']}/reject", headers=people["admin_h"])
    assert again.status_code == 409
    assert len(audit(client, people, target_type="channel")) == 1


# --------------------------------------------------------------------------- #
# Acceptance criteria: content removal
# --------------------------------------------------------------------------- #


def test_removed_content_is_no_longer_visible_on_the_platform(client, people, flagged_material):
    found = client.get(f"{API}/materials", params={"q": "exam"}, headers=people["student_h"])
    assert [m["id"] for m in found.json()["items"]] == [flagged_material]

    remove(client, people, "material", flagged_material)

    found = client.get(f"{API}/materials", params={"q": "exam"}, headers=people["student_h"])
    assert found.json()["items"] == []
    for who in ("student_h", "spammer_h", "admin_h"):
        response = client.get(f"{API}/materials/{flagged_material}", headers=people[who])
        assert response.status_code == 404


def test_a_question_can_be_removed_and_its_author_notified(client, db, people):
    question = questions.create_question(
        db,
        author_id=people["spammer"],
        subject="DBMS",
        topic="Normalization",
        difficulty=Difficulty.EASY,
        question_type=QuestionType.TRUE_FALSE,
        text="Visit my site for answers?",
        options=["true", "false"],
        correct_answer="true",
    )
    db.commit()
    client.post(
        f"{API}/questions/{question.id}/reports",
        json={"reason": "spam"},
        headers=people["reporter1_h"],
    )

    response = remove(client, people, "question", question.id, "Advertising")
    assert response.status_code == 200
    assert response.json()["resolved_reports"] == 1
    assert (
        client.get(f"{API}/questions/{question.id}", headers=people["student_h"]).status_code == 404
    )
    inbox = client.get(f"{API}/notifications", headers=people["spammer_h"]).json()["items"]
    assert inbox[0]["title"] == 'Your question "Visit my site for answers?" was removed'


def test_removal_needs_a_reason_and_existing_content(client, people, flagged_material):
    blank = remove(client, people, "material", flagged_material, "  ")
    assert blank.status_code == 422
    assert blank.json()["detail"] == "Give a reason for removing this content"
    assert remove(client, people, "material", 9999).status_code == 404

    assert remove(client, people, "material", flagged_material).status_code == 200
    twice = remove(client, people, "material", flagged_material)
    assert (twice.status_code, twice.json()["detail"]) == (404, "Content not found")
    assert len(audit(client, people, target_type="material")) == 1
    assert remove(client, people, "quiz", 1).status_code == 422


def test_the_audit_log_records_actor_target_and_time_newest_first(client, people, flagged_material):
    suspend(client, people, people["spammer"])
    remove(client, people, "material", flagged_material)
    entries = audit(client, people)
    assert [(e["action"], e["target_type"]) for e in entries] == [
        ("remove_content", "material"),
        ("suspend_user", "user"),
    ]
    for entry in entries:
        assert entry["actor"] == {
            "id": people["admin"],
            "display_name": "Admin A",
            "username": "admin_a",
        }
        assert entry["created_at"]
    assert entries[0]["report_id"] is not None

    page = client.get(f"{ADMIN}/audit-log", params={"limit": 1}, headers=people["admin_h"]).json()
    assert len(page["items"]) == 1 and page["has_more"]
    older = client.get(
        f"{ADMIN}/audit-log",
        params={"limit": 1, "before": page["items"][0]["id"]},
        headers=people["admin_h"],
    ).json()
    assert older["items"][0]["action"] == "suspend_user" and not older["has_more"]


# --------------------------------------------------------------------------- #
# Notifications inbox
# --------------------------------------------------------------------------- #


def test_notifications_are_private_and_can_be_marked_read(client, people, flagged_material):
    remove(client, people, "material", flagged_material)
    assert client.get(f"{API}/notifications", headers=people["student_h"]).json() == {
        "items": [],
        "unread_count": 0,
    }
    note_id = client.get(f"{API}/notifications", headers=people["spammer_h"]).json()["items"][0][
        "id"
    ]
    assert (
        client.post(f"{API}/notifications/{note_id}/read", headers=people["student_h"]).status_code
        == 404
    )
    assert (
        client.post(f"{API}/notifications/{note_id}/read", headers=people["spammer_h"]).status_code
        == 204
    )
    inbox = client.get(f"{API}/notifications", headers=people["spammer_h"]).json()
    assert inbox["unread_count"] == 0 and inbox["items"][0]["is_read"]
    assert client.get(f"{API}/notifications").status_code == 401
