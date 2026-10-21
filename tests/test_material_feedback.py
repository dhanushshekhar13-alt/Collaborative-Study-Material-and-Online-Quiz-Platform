"""S04.2 - rate, review and report materials (issue #23, FR-04).

Test Plan cases are named after their IDs: ST_07 (rating, review, helpful) and ST_08
(reporting). The plan's steps run in the web UI (UI-2 #63); these call the endpoints
that UI will use.
"""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.modules.accounts import service as accounts
from app.modules.accounts.models import Role
from app.modules.channels import service as channels
from app.modules.materials import service as materials
from app.modules.materials.models import MaterialType
from app.modules.moderation import service as moderation
from app.modules.moderation.service import ReportReason, ReportStatus, ReportTarget

API = "/api/v1/materials"
PASSWORD = "Pass@1234"


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
def users(client, db) -> dict[str, int]:
    names = ("uploader_u", "aditi_r", "rahul_m", "priya_s", "outsider_p")
    return {name: make_user(db, name) for name in names}


@pytest.fixture()
def material(db, users) -> int:
    created = materials.create_material(
        db,
        uploader_id=users["uploader_u"],
        title="Normalization notes",
        material_type=MaterialType.NOTES,
        subject="DBMS",
        topic="Normalization",
        semester="4",
    )
    db.commit()
    return created.id


def review(client, headers, material_id, **body):
    return client.put(f"{API}/{material_id}/review", json=body, headers=headers)


def report(client, headers, material_id, **body):
    return client.post(f"{API}/{material_id}/reports", json=body, headers=headers)


# --------------------------------------------------------------------------- #
# ST_07 - rate, review and mark helpful
# --------------------------------------------------------------------------- #


def test_ST_07_rating_updates_the_average_and_helpful_counts_once(client, material):
    # Precondition: an initial rating of 4.0 from two ratings.
    assert review(client, auth(client, "rahul_m"), material, rating=3).status_code == 200
    assert review(client, auth(client, "priya_s"), material, rating=5).status_code == 200
    before = client.get(f"{API}/{material}", headers=auth(client, "aditi_r")).json()
    assert (before["rating_average"], before["rating_count"]) == (4.0, 2)
    assert before["helpful_count"] == 0

    aditi = auth(client, "aditi_r")
    rated = review(client, aditi, material, rating=5, review="Very clear explanation")
    assert rated.status_code == 200, rated.text
    body = rated.json()
    assert round(body["rating_average"], 1) == 4.3
    assert body["rating_count"] == 3
    assert (body["my_rating"], body["my_review"]) == (5, "Very clear explanation")

    helpful = client.put(f"{API}/{material}/helpful", headers=aditi)
    assert helpful.status_code == 200, helpful.text
    assert helpful.json() == {"material_id": material, "helpful_count": 1, "marked_helpful": True}


def test_submitting_a_rating_updates_the_average_correctly(client, material):
    for user, stars in (("aditi_r", 1), ("rahul_m", 2), ("priya_s", 4)):
        body = review(client, auth(client, user), material, rating=stars).json()
    assert body["rating_average"] == pytest.approx(7 / 3)
    assert body["rating_count"] == 3


def test_rating_again_replaces_the_earlier_rating(client, material):
    aditi = auth(client, "aditi_r")
    review(client, aditi, material, rating=1, review="Unclear")
    body = review(client, aditi, material, rating=5, review="Clear after the update").json()

    assert (body["rating_average"], body["rating_count"]) == (5.0, 1)
    assert body["my_review"] == "Clear after the update"


@pytest.mark.parametrize("body", [{}, {"rating": 0}, {"rating": 6}, {"review": "Good"}])
def test_a_rating_from_1_to_5_is_required(client, material, body):
    response = review(client, auth(client, "aditi_r"), material, **body)

    assert response.status_code == 422
    assert response.json()["detail"] == "Select a rating from 1 to 5 stars"


def test_the_helpful_count_cannot_be_inflated_by_repeating(client, material):
    aditi = auth(client, "aditi_r")
    for _ in range(3):
        body = client.put(f"{API}/{material}/helpful", headers=aditi).json()
    assert body["helpful_count"] == 1

    client.put(f"{API}/{material}/helpful", headers=auth(client, "rahul_m"))
    detail = client.get(f"{API}/{material}", headers=aditi).json()
    assert detail["helpful_count"] == 2
    assert detail["marked_helpful"] is True


def test_marking_helpful_does_not_need_or_add_a_rating(client, material):
    aditi = auth(client, "aditi_r")
    client.put(f"{API}/{material}/helpful", headers=aditi)
    body = client.get(f"{API}/{material}", headers=aditi).json()

    assert (body["helpful_count"], body["rating_count"], body["rating_average"]) == (1, 0, None)


def test_a_helpful_mark_can_be_taken_back(client, material):
    aditi = auth(client, "aditi_r")
    client.put(f"{API}/{material}/helpful", headers=aditi)
    body = client.delete(f"{API}/{material}/helpful", headers=aditi).json()

    assert (body["helpful_count"], body["marked_helpful"]) == (0, False)


def test_rating_keeps_the_helpful_mark(client, material):
    aditi = auth(client, "aditi_r")
    client.put(f"{API}/{material}/helpful", headers=aditi)
    body = review(client, aditi, material, rating=4).json()

    assert (body["helpful_count"], body["marked_helpful"], body["my_rating"]) == (1, True, 4)


def test_uploaders_cannot_rate_or_mark_their_own_material(client, material):
    own = auth(client, "uploader_u")
    message = "You can't rate or mark your own material as helpful"

    for response in (
        review(client, own, material, rating=5),
        client.put(f"{API}/{material}/helpful", headers=own),
    ):
        assert response.status_code == 403
        assert response.json()["detail"] == message


def test_review_text_is_displayed_with_its_author(client, material):
    review(client, auth(client, "aditi_r"), material, rating=5, review="Very clear explanation")
    review(client, auth(client, "rahul_m"), material, rating=4, review="  ")
    response = client.get(f"{API}/{material}/reviews", headers=auth(client, "priya_s"))

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 1  # a blank review is a rating only
    [entry] = body["items"]
    assert entry["review"] == "Very clear explanation"
    assert (entry["author_name"], entry["rating"]) == ("Aditi_R", 5)
    detail = client.get(f"{API}/{material}", headers=auth(client, "priya_s")).json()
    assert (detail["review_count"], detail["rating_count"]) == (1, 2)


def test_search_results_carry_ratings(client, material):
    review(client, auth(client, "aditi_r"), material, rating=4)
    client.put(f"{API}/{material}/helpful", headers=auth(client, "rahul_m"))
    listing = client.get(API, headers=auth(client, "priya_s")).json()

    [item] = listing["items"]
    assert (item["rating_average"], item["rating_count"], item["helpful_count"]) == (4.0, 1, 1)
    assert (item["my_rating"], item["marked_helpful"]) == (None, False)


def test_rating_needs_sign_in(client, material):
    assert review(client, {}, material, rating=5).status_code == 401
    assert client.put(f"{API}/{material}/helpful").status_code == 401
    assert report(client, {}, material, reason="duplicate").status_code == 401


def test_removed_materials_cannot_be_rated_or_reported(client, db, material):
    materials.set_removed(db, material, True)
    db.commit()
    aditi = auth(client, "aditi_r")

    assert review(client, aditi, material, rating=5).status_code == 404
    assert client.put(f"{API}/{material}/helpful", headers=aditi).status_code == 404
    assert report(client, aditi, material, reason="duplicate").status_code == 404


# --------------------------------------------------------------------------- #
# ST_08 - report a material
# --------------------------------------------------------------------------- #


def test_ST_08_report_is_recorded_and_appears_in_the_moderation_queue(client, db, users, material):
    started = datetime.now(UTC) - timedelta(minutes=1)
    response = report(client, auth(client, "aditi_r"), material, reason="duplicate")

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["reason"] == "duplicate"
    assert body["reporter_id"] == users["aditi_r"]
    assert body["status"] == "open"
    assert body["message"] == "Report submitted for moderation"
    reported_at = datetime.fromisoformat(body["reported_at"])
    assert (reported_at.replace(tzinfo=reported_at.tzinfo or UTC)) >= started

    assert body["report_id"] in moderation.open_reports(db)
    stored = moderation.get_report(db, body["report_id"])
    assert (stored.target_type, stored.target_id) == (ReportTarget.MATERIAL, material)
    assert (stored.reporter_id, stored.reason) == (users["aditi_r"], ReportReason.DUPLICATE)
    assert stored.status is ReportStatus.OPEN
    assert stored.created_at is not None


def test_a_reason_must_be_selected_before_submitting(client, db, material):
    for body in ({}, {"details": "Same as the week 3 notes"}):
        response = report(client, auth(client, "aditi_r"), material, **body)
        assert response.status_code == 422
        assert response.json()["detail"] == "Select a reason for reporting this material"
    assert moderation.open_reports(db) == []


def test_an_unknown_reason_is_refused(client, material):
    response = report(client, auth(client, "aditi_r"), material, reason="boring")
    assert response.status_code == 422


def test_reporting_again_while_open_does_not_duplicate(client, db, material):
    aditi = auth(client, "aditi_r")
    first = report(client, aditi, material, reason="duplicate").json()
    second = report(client, aditi, material, reason="incorrect").json()

    assert second["report_id"] == first["report_id"]
    assert len(moderation.open_reports(db)) == 1


def test_channel_material_reports_go_to_that_channels_queue(client, db, users):
    channel = channels.create_channel(
        db, name="DBMS Study Group", subject="DBMS", created_by=users["uploader_u"]
    )
    channels.add_member(db, channel.id, users["aditi_r"])
    scoped = materials.create_material(
        db,
        uploader_id=users["uploader_u"],
        title="ER diagrams",
        material_type=MaterialType.NOTES,
        subject="DBMS",
        channel_id=channel.id,
    )
    db.commit()

    outsider = report(client, auth(client, "outsider_p"), scoped.id, reason="spam")
    assert outsider.status_code == 403
    body = report(client, auth(client, "aditi_r"), scoped.id, reason="spam").json()
    assert moderation.open_reports(db, channel_id=channel.id) == [body["report_id"]]


# --------------------------------------------------------------------------- #
# Service interface
# --------------------------------------------------------------------------- #


def test_feedback_service_covers_unrated_materials(db, users, material):
    assert materials.feedback_for(db, []) == {}
    assert materials.feedback_for(db, [material])[material] == materials.MaterialFeedback()
    with pytest.raises(ValueError):
        materials.rate_material(db, material, users["aditi_r"], 9)
