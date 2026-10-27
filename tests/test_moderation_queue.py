"""S13.2 - report queue and moderator actions (issue #43, FR-13).

Test Plan cases are named after their IDs: IT_25, IT_26, ST_25, plus one test per
acceptance criterion. The plan's steps run in the web UI; these call the endpoints
that UI uses.
"""

import pytest
from fastapi.testclient import TestClient

from app.modules.accounts import service as accounts
from app.modules.accounts.models import Role
from app.modules.channels import service as channels
from app.modules.channels.service import ApprovalStatus, ChannelType, MemberRole
from app.modules.moderation import service as moderation
from app.modules.moderation.service import ModerationActionType, ReportTarget

PASSWORD = "Pass@1234"
QUEUE = "/api/v1/moderation/queue"
PDF = b"%PDF-1.4\n1 0 obj << /Type /Catalog >> endobj\ntrailer << >>\n%%EOF\n"
TAGS = {"subject": "DBMS", "topic": "ER Model", "semester": "4", "material_type": "notes"}
QUESTION = {
    "text": "What is a foreign key?",
    "subject": "DBMS",
    "topic": "ER Model",
    "difficulty": "easy",
    "question_type": "short_answer",
    "correct_answer": "A column that references another table's primary key",
}


def make_user(db, username: str, role: Role = Role.STUDENT) -> int:
    user = accounts.create_user(
        db,
        email=f"{username}@test.com",
        display_name=username.replace("_", " ").title(),
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
    """A Normal Channel run by Aditi (Vikram moderates it) and an approved Authorized
    Channel run by Prof. Rao, each with regular members, plus an admin."""
    ids = {
        "owner": make_user(db, "aditi_r"),
        "mod": make_user(db, "vikram_s"),
        "member": make_user(db, "rahul_m"),
        "peer": make_user(db, "meera_k"),
        "prof": make_user(db, "prof_rao", Role.AUTHORIZED_USER),
        "admin": make_user(db, "admin_a", Role.ADMIN),
    }
    normal = channels.create_channel(db, name="DBMS study group", created_by=ids["owner"])
    for key, role in (
        ("mod", MemberRole.MODERATOR),
        ("member", MemberRole.MEMBER),
        ("peer", MemberRole.MEMBER),
    ):
        channels.add_member(db, normal.id, ids[key], role)
    official = channels.create_channel(
        db,
        name="CS301 Databases",
        created_by=ids["prof"],
        channel_type=ChannelType.AUTHORIZED,
    )
    channels.set_approval(db, official.id, ApprovalStatus.APPROVED)
    channels.add_member(db, official.id, ids["member"])
    channels.add_member(db, official.id, ids["peer"])
    db.commit()
    headers = {
        key: auth(client, u)
        for key, u in (
            ("owner", "aditi_r"),
            ("mod", "vikram_s"),
            ("member", "rahul_m"),
            ("peer", "meera_k"),
            ("prof", "prof_rao"),
            ("admin", "admin_a"),
        )
    }
    return {"ids": ids, "h": headers, "normal": normal.id, "official": official.id}


def upload(client, headers, channel_id, title="ER_Diagram_Notes.pdf") -> int:
    response = client.post(
        "/api/v1/materials",
        data={**TAGS, "channel_id": str(channel_id)},
        files={"file": (title, PDF, "application/octet-stream")},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def ask(client, headers, channel_id) -> int:
    response = client.post(
        "/api/v1/questions", json={**QUESTION, "channel_id": channel_id}, headers=headers
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def report_material(client, headers, material_id, reason="inappropriate", details=None):
    return client.post(
        f"/api/v1/materials/{material_id}/reports",
        json={"reason": reason, "details": details},
        headers=headers,
    )


def report_question(client, headers, question_id, reason="incorrect"):
    return client.post(
        f"/api/v1/questions/{question_id}/reports", json={"reason": reason}, headers=headers
    )


# --------------------------------------------------------------------------- #
# Test Plan cases
# --------------------------------------------------------------------------- #


def test_IT_25_report_reaches_the_moderation_queue(client, world):
    h = world["h"]
    material = upload(client, h["owner"], world["normal"])
    filed = report_material(client, h["member"], material, details="Offensive cover page")
    assert filed.status_code == 201

    queue = client.get(QUEUE, headers=h["admin"])
    assert queue.status_code == 200
    [item] = queue.json()
    assert item["target_type"] == "material"
    assert item["target_id"] == material
    assert item["content"]["title"] == "ER_Diagram_Notes"
    assert item["content"]["link"] == f"/api/v1/materials/{material}"
    assert item["channel_name"] == "DBMS study group"
    [report] = item["reports"]
    assert report["reporter"] == {"id": world["ids"]["member"], "name": "Rahul M"}
    assert report["reason"] == "inappropriate"
    assert report["details"] == "Offensive cover page"
    assert report["status"] == "open"


def test_IT_26_remove_content_hides_it_and_resolves_the_report(client, world):
    h = world["h"]
    material = upload(client, h["owner"], world["normal"])
    report_id = report_material(client, h["member"], material).json()["report_id"]

    removed = client.post(
        f"/api/v1/moderation/reports/{report_id}/remove",
        json={"confirm": True, "note": "Offensive content"},
        headers=h["admin"],
    )
    assert removed.status_code == 200, removed.text
    body = removed.json()
    assert body["status"] == "resolved"
    assert body["content"]["available"] is False
    assert body["resolved_by"]["id"] == world["ids"]["admin"]

    # Hidden from the platform: the direct link and the channel's materials.
    assert client.get(f"/api/v1/materials/{material}", headers=h["peer"]).status_code == 404
    listing = client.get(
        "/api/v1/materials", params={"channel_id": world["normal"]}, headers=h["peer"]
    )
    assert material not in [m["id"] for m in listing.json()["items"]]
    assert client.get(QUEUE, headers=h["admin"]).json() == []


def test_ST_25_authorized_user_resolves_where_a_regular_member_cannot(client, world):
    h = world["h"]
    question = ask(client, h["prof"], world["official"])
    report_id = report_question(client, h["peer"], question).json()["report_id"]
    url = f"/api/v1/moderation/reports/{report_id}"

    refused = client.post(f"{url}/remove", json={"confirm": True}, headers=h["member"])
    assert refused.status_code == 403
    assert refused.json()["detail"] == "Only a moderator of this channel can act on its reports"
    assert client.post(f"{url}/dismiss", json={}, headers=h["member"]).status_code == 403
    assert client.get(url, headers=h["member"]).status_code == 403

    resolved = client.post(f"{url}/remove", json={"confirm": True}, headers=h["prof"])
    assert resolved.status_code == 200
    assert resolved.json()["status"] == "resolved"
    assert client.get(f"/api/v1/questions/{question}", headers=h["peer"]).status_code == 404


# --------------------------------------------------------------------------- #
# Acceptance criteria
# --------------------------------------------------------------------------- #


def test_duplicate_reports_on_one_item_are_grouped(client, world):
    h = world["h"]
    material = upload(client, h["owner"], world["normal"])
    report_material(client, h["member"], material, reason="inappropriate")
    report_material(client, h["peer"], material, reason="spam")
    # The same reporter again while open is not a new report.
    report_material(client, h["member"], material, reason="inappropriate")
    question = ask(client, h["owner"], world["normal"])
    report_question(client, h["peer"], question)

    queue = client.get(QUEUE, headers=h["owner"]).json()
    assert [(i["target_type"], i["report_count"]) for i in queue] == [
        ("material", 2),
        ("question", 1),
    ]
    assert queue[0]["reasons"] == ["inappropriate", "spam"]
    assert {r["reporter"]["name"] for r in queue[0]["reports"]} == {"Rahul M", "Meera K"}


def test_one_decision_closes_every_duplicate_report(client, world):
    h = world["h"]
    material = upload(client, h["owner"], world["normal"])
    first = report_material(client, h["member"], material).json()["report_id"]
    second = report_material(client, h["peer"], material, reason="spam").json()["report_id"]

    client.post(
        f"/api/v1/moderation/reports/{second}/remove", json={"confirm": True}, headers=h["mod"]
    )
    detail = client.get(f"/api/v1/moderation/reports/{first}", headers=h["mod"]).json()
    assert detail["status"] == "resolved"
    assert [r["status"] for r in detail["reports"]] == ["resolved", "resolved"]


def test_reporter_sees_confirmation_and_follows_the_outcome(client, world):
    h = world["h"]
    material = upload(client, h["owner"], world["normal"])
    filed = report_material(client, h["member"], material)
    assert filed.status_code == 201
    assert filed.json()["message"]
    assert filed.json()["status"] == "open"

    mine = client.get("/api/v1/moderation/reports/mine", headers=h["member"]).json()
    assert [(r["report_id"], r["status"]) for r in mine] == [(filed.json()["report_id"], "open")]

    client.post(
        f"/api/v1/moderation/reports/{filed.json()['report_id']}/dismiss",
        json={"note": "Not offensive"},
        headers=h["owner"],
    )
    mine = client.get("/api/v1/moderation/reports/mine", headers=h["member"]).json()
    assert mine[0]["status"] == "dismissed"
    # Nobody else's reports show up.
    assert client.get("/api/v1/moderation/reports/mine", headers=h["peer"]).json() == []


def test_dismiss_clears_the_report_and_keeps_the_content(client, world):
    h = world["h"]
    material = upload(client, h["owner"], world["normal"])
    report_id = report_material(client, h["member"], material).json()["report_id"]

    cleared = client.post(
        f"/api/v1/moderation/reports/{report_id}/dismiss",
        json={"note": "Looks fine"},
        headers=h["mod"],
    )
    assert cleared.status_code == 200
    assert cleared.json()["status"] == "dismissed"
    assert cleared.json()["content"]["available"] is True
    assert client.get(f"/api/v1/materials/{material}", headers=h["peer"]).status_code == 200
    assert client.get(QUEUE, headers=h["mod"]).json() == []


def test_every_moderation_action_is_logged_with_actor_and_time(client, db, world):
    h = world["h"]
    material = upload(client, h["owner"], world["normal"])
    first = report_material(client, h["member"], material).json()["report_id"]
    client.post(f"/api/v1/moderation/reports/{first}/dismiss", json={}, headers=h["mod"])
    second = report_material(client, h["peer"], material).json()["report_id"]
    client.post(
        f"/api/v1/moderation/reports/{second}/remove",
        json={"confirm": True, "note": "Copied slides"},
        headers=h["owner"],
    )

    history = client.get(f"/api/v1/moderation/reports/{second}", headers=h["admin"]).json()[
        "history"
    ]
    assert [(a["action"], a["actor"]["id"], a["report_id"]) for a in history] == [
        ("dismiss_report", world["ids"]["mod"], first),
        ("remove_content", world["ids"]["owner"], second),
    ]
    assert all(a["at"] for a in history)
    assert history[1]["note"] == "Copied slides"
    logged = moderation.actions_on(db, ReportTarget.MATERIAL, material)
    assert [a.action for a in logged] == [
        ModerationActionType.DISMISS_REPORT,
        ModerationActionType.REMOVE_CONTENT,
    ]


# --------------------------------------------------------------------------- #
# Rules around the queue
# --------------------------------------------------------------------------- #


def test_regular_members_have_no_queue(client, world):
    response = client.get(QUEUE, headers=world["h"]["member"])
    assert response.status_code == 403
    assert response.json()["detail"] == "Only moderators can review reports"
    assert client.get(QUEUE).status_code == 401


def test_channel_moderators_see_only_their_channels(client, world):
    h = world["h"]
    normal_item = upload(client, h["owner"], world["normal"])
    official_item = ask(client, h["prof"], world["official"])
    report_material(client, h["member"], normal_item)
    report_question(client, h["peer"], official_item)

    def targets(headers, **params):
        return [i["target_id"] for i in client.get(QUEUE, params=params, headers=headers).json()]

    assert targets(h["owner"]) == [normal_item]
    assert targets(h["mod"]) == [normal_item]
    assert targets(h["prof"]) == [official_item]
    assert targets(h["admin"]) == [normal_item, official_item]
    assert targets(h["admin"], channel_id=world["official"]) == [official_item]
    other = client.get(QUEUE, params={"channel_id": world["official"]}, headers=h["owner"])
    assert other.status_code == 403


def test_removal_needs_confirmation(client, world):
    h = world["h"]
    material = upload(client, h["owner"], world["normal"])
    report_id = report_material(client, h["member"], material).json()["report_id"]
    response = client.post(
        f"/api/v1/moderation/reports/{report_id}/remove", json={}, headers=h["admin"]
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "Confirm removing this content"
    assert client.get(f"/api/v1/materials/{material}", headers=h["peer"]).status_code == 200


def test_a_closed_report_cannot_be_decided_again(client, world):
    h = world["h"]
    material = upload(client, h["owner"], world["normal"])
    report_id = report_material(client, h["member"], material).json()["report_id"]
    url = f"/api/v1/moderation/reports/{report_id}"
    client.post(f"{url}/dismiss", json={}, headers=h["admin"])
    again = client.post(f"{url}/remove", json={"confirm": True}, headers=h["admin"])
    assert again.status_code == 409
    assert again.json()["detail"] == "This report has already been closed"


def test_unknown_report_is_404(client, world):
    response = client.get("/api/v1/moderation/reports/999", headers=world["h"]["admin"])
    assert response.status_code == 404
    assert response.json()["detail"] == "Report not found"
