"""S01.2 - profile with contribution history and reputation (issue #19, FR-01).

Test Plan cases are named after their IDs: ST_01 (edit profile, persists across
sessions) and ST_02 (contributions, reputation and badges match backend records).
The plan writes the steps against the profile page (UI-1 #62); until it exists each
case runs against the endpoints that page will call.
"""

import struct
import zlib

from fastapi.testclient import TestClient

from app.modules.accounts import service as accounts
from app.modules.materials import service as materials
from app.modules.materials.models import MaterialType
from app.modules.questions import service as questions
from app.modules.questions.models import Difficulty, QuestionSource, QuestionType
from app.modules.quizzes import service as quizzes
from app.modules.reputation import service as reputation

ACCOUNTS = "/api/v1/accounts"
PROFILES = "/api/v1/profiles"
PASSWORD = "Pass@1234"

# A minimal valid JPEG (SOI ... EOI) standing in for the plan's "profile2.jpg".
JPEG = bytes.fromhex("ffd8ffe000104a46494600010100000100010000ffd9")


def png() -> bytes:
    """A real 1x1 PNG, so the type check is exercised with a second format."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    header = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00"))
        + chunk(b"IEND", b"")
    )


def sign_up(client: TestClient, username: str = "aditi_r") -> dict:
    response = client.post(
        f"{ACCOUNTS}/register",
        json={
            "display_name": username.replace("_", " ").title(),
            "username": username,
            "email": f"{username}@test.com",
            "password": PASSWORD,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["account"]


def log_in(client: TestClient, username: str = "aditi_r") -> dict[str, str]:
    response = client.post(f"{ACCOUNTS}/login", json={"username": username, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def upload(client: TestClient, headers, data: bytes, name: str = "profile2.jpg", ctype=None):
    files = {"file": (name, data, ctype or "image/jpeg")}
    return client.put(f"{PROFILES}/me/picture", files=files, headers=headers)


def add_material(db, user_id: int, title: str) -> int:
    return materials.create_material(
        db,
        uploader_id=user_id,
        title=title,
        material_type=MaterialType.NOTES,
        subject="DBMS",
    ).id


def add_question(db, user_id: int, text: str, source=QuestionSource.PEER) -> int:
    return questions.create_question(
        db,
        author_id=user_id,
        subject="DBMS",
        topic="Normalization",
        difficulty=Difficulty.EASY,
        question_type=QuestionType.MULTIPLE_CHOICE,
        text=text,
        options=["1NF", "2NF"],
        correct_answer="1NF",
        source=source,
    ).id


# --------------------------------------------------------------------------- #
# ST_01 - edit bio and picture; they persist after logout and login
# --------------------------------------------------------------------------- #


def test_ST_01_edited_bio_and_picture_persist_after_logout_and_login(client):
    sign_up(client)
    headers = log_in(client)

    saved = client.patch(
        f"{PROFILES}/me", json={"bio": "CS student, interested in DBMS"}, headers=headers
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["bio"] == "CS student, interested in DBMS"

    uploaded = upload(client, headers, JPEG, "profile2.jpg")
    assert uploaded.status_code == 200, uploaded.text
    assert uploaded.json()["picture_url"]

    # "Log out": the client discards its token. Log back in for a fresh session.
    del headers
    headers = log_in(client)
    profile = client.get(f"{PROFILES}/me", headers=headers)
    assert profile.status_code == 200
    body = profile.json()
    assert body["bio"] == "CS student, interested in DBMS"
    assert body["picture_url"] == uploaded.json()["picture_url"]

    picture = client.get(body["picture_url"])
    assert picture.status_code == 200
    assert picture.headers["content-type"] == "image/jpeg"
    assert picture.content == JPEG


def test_profile_fields_are_saved_in_the_database_not_only_returned(client, db):
    account = sign_up(client)
    headers = log_in(client)
    client.patch(
        f"{PROFILES}/me",
        json={"bio": "CS student", "institution": "RVCE", "display_name": "Aditi R"},
        headers=headers,
    )
    stored = accounts.get_profile(db, account["id"])
    assert (stored.bio, stored.institution, stored.display_name) == (
        "CS student",
        "RVCE",
        "Aditi R",
    )


def test_a_partial_update_leaves_other_fields_alone_and_blank_clears(client):
    sign_up(client)
    headers = log_in(client)
    client.patch(f"{PROFILES}/me", json={"bio": "Bio", "institution": "RVCE"}, headers=headers)

    body = client.patch(f"{PROFILES}/me", json={"institution": "  "}, headers=headers).json()
    assert body["bio"] == "Bio"
    assert body["institution"] is None
    assert body["display_name"] == "Aditi R"


def test_profile_edits_are_validated(client):
    sign_up(client)
    headers = log_in(client)
    blank_name = client.patch(f"{PROFILES}/me", json={"display_name": "  "}, headers=headers)
    assert blank_name.status_code == 422
    assert blank_name.json()["detail"] == "Enter your name"

    long_bio = client.patch(f"{PROFILES}/me", json={"bio": "x" * 501}, headers=headers)
    assert long_bio.status_code == 422


def test_replacing_the_picture_changes_its_url_and_content(client):
    sign_up(client)
    headers = log_in(client)
    first = upload(client, headers, JPEG).json()["picture_url"]
    second = upload(client, headers, png(), "me.png", "image/png").json()["picture_url"]

    assert second != first
    picture = client.get(second)
    assert picture.headers["content-type"] == "image/png"
    assert picture.content == png()


def test_removing_the_picture(client):
    account = sign_up(client)
    headers = log_in(client)
    upload(client, headers, JPEG)

    removed = client.delete(f"{PROFILES}/me/picture", headers=headers)
    assert removed.status_code == 200
    assert removed.json()["picture_url"] is None
    assert client.get(f"{PROFILES}/{account['id']}/picture").status_code == 404


# --------------------------------------------------------------------------- #
# Invalid or oversized uploads are rejected with a clear message
# --------------------------------------------------------------------------- #


def test_an_oversized_picture_is_rejected_with_a_clear_message(client):
    sign_up(client)
    headers = log_in(client)
    too_big = JPEG[:-2] + b"\x00" * accounts.MAX_PICTURE_BYTES + JPEG[-2:]

    response = upload(client, headers, too_big)
    assert response.status_code == 413
    assert response.json()["detail"] == "Profile picture must be 2 MB or smaller"


def test_a_picture_exactly_at_the_limit_is_accepted(client):
    sign_up(client)
    headers = log_in(client)
    at_limit = JPEG + b"\x00" * (accounts.MAX_PICTURE_BYTES - len(JPEG))
    assert upload(client, headers, at_limit).status_code == 200


def test_a_file_that_is_not_an_image_is_rejected_whatever_its_name(client):
    sign_up(client)
    headers = log_in(client)

    response = upload(client, headers, b"#!/bin/sh\necho hi\n", "profile2.jpg", "image/jpeg")
    assert response.status_code == 422
    assert response.json()["detail"] == "Profile picture must be a JPEG, PNG, GIF or WebP image"


def test_svg_is_rejected_because_it_can_carry_script(client):
    sign_up(client)
    headers = log_in(client)
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'

    response = upload(client, headers, svg, "a.svg", "image/svg+xml")
    assert response.status_code == 422
    assert response.json()["detail"] == "Profile picture must be a JPEG, PNG, GIF or WebP image"


def test_an_empty_file_is_rejected(client):
    sign_up(client)
    headers = log_in(client)
    response = upload(client, headers, b"")
    assert response.status_code == 422
    assert response.json()["detail"] == "Choose an image to upload"


def test_a_rejected_upload_keeps_the_previous_picture(client):
    sign_up(client)
    headers = log_in(client)
    before = upload(client, headers, JPEG).json()["picture_url"]

    upload(client, headers, b"not an image")
    after = client.get(f"{PROFILES}/me", headers=headers).json()["picture_url"]
    assert after == before
    assert client.get(after).content == JPEG


def test_the_picture_is_served_with_its_detected_type_and_no_sniffing(client):
    sign_up(client)
    headers = log_in(client)
    # The client claims PNG; the bytes are JPEG. The stored type follows the bytes.
    url = upload(client, headers, JPEG, "x.png", "image/png").json()["picture_url"]

    picture = client.get(url)
    assert picture.headers["content-type"] == "image/jpeg"
    assert picture.headers["x-content-type-options"] == "nosniff"


# --------------------------------------------------------------------------- #
# ST_02 - contributions, reputation and badges match backend records
# --------------------------------------------------------------------------- #


def test_ST_02_profile_shows_three_contributions_fifty_points_and_one_badge(client, db):
    account = sign_up(client)
    user_id = account["id"]
    for title in ("ER diagrams", "Normal forms", "SQL joins"):
        add_material(db, user_id, title)
    reputation.award(db, user_id=user_id, points=30, reason="material_upload")
    reputation.award(db, user_id=user_id, points=20, reason="helpful_rating")
    badge = reputation.ensure_badge(db, "first_upload", "First Upload", "Shared a material")
    reputation.grant_badge(db, user_id, badge)
    db.commit()

    headers = log_in(client)
    body = client.get(f"{PROFILES}/me", headers=headers).json()

    assert body["contributions"]["total"] == 3
    assert body["contributions"]["materials"] == 3
    assert body["reputation"] == 50
    assert [b["name"] for b in body["badges"]] == ["First Upload"]

    # ... and those numbers are the backend's own records.
    assert body["contributions"]["materials"] == materials.count_uploaded_by(db, user_id)
    assert body["reputation"] == reputation.total(db, user_id)
    assert len(body["badges"]) == len(reputation.badges_for(db, user_id))
    assert {c["title"] for c in body["contributions"]["recent"]} == {
        "ER diagrams",
        "Normal forms",
        "SQL joins",
    }


def test_a_brand_new_user_gets_empty_sections(client):
    sign_up(client)
    headers = log_in(client)
    body = client.get(f"{PROFILES}/me", headers=headers).json()

    assert body["bio"] is None
    assert body["institution"] is None
    assert body["picture_url"] is None
    assert body["reputation"] == 0
    assert body["contributions"] == {
        "total": 0,
        "materials": 0,
        "questions": 0,
        "quizzes": 0,
        "recent": [],
    }
    assert body["badges"] == []


def test_contribution_history_covers_materials_questions_and_quizzes(client, db):
    user_id = sign_up(client)["id"]
    add_material(db, user_id, "ER diagrams")
    question = add_question(db, user_id, "Which normal form removes repeating groups?")
    quizzes.create_quiz(
        db,
        creator_id=user_id,
        title="Normalization basics",
        subject="DBMS",
        topic="Normalization",
        difficulty="easy",
        duration_minutes=10,
        question_ids=[question],
        publish=True,
    )
    db.commit()

    body = client.get(f"{PROFILES}/me", headers=log_in(client)).json()["contributions"]
    assert (body["materials"], body["questions"], body["quizzes"], body["total"]) == (1, 1, 1, 3)
    assert sorted(c["type"] for c in body["recent"]) == ["material", "question", "quiz"]


def test_removed_drafted_and_unpublished_work_is_not_counted(client, db):
    user_id = sign_up(client)["id"]
    removed = add_material(db, user_id, "Withdrawn notes")
    materials.set_removed(db, removed, True)
    add_question(db, user_id, "Draft from the LLM", source=QuestionSource.LLM)
    quizzes.create_quiz(
        db,
        creator_id=user_id,
        title="Unpublished",
        subject="DBMS",
        topic="SQL",
        difficulty="easy",
        duration_minutes=5,
        question_ids=[],
    )
    db.commit()

    body = client.get(f"{PROFILES}/me", headers=log_in(client)).json()["contributions"]
    assert body["total"] == 0
    assert body["recent"] == []


def test_recent_history_is_capped_and_newest_first(client, db):
    user_id = sign_up(client)["id"]
    ids = [add_material(db, user_id, f"Notes {n}") for n in range(12)]
    db.commit()

    body = client.get(f"{PROFILES}/me", headers=log_in(client)).json()["contributions"]
    assert body["materials"] == 12
    assert len(body["recent"]) == 10
    assert [c["id"] for c in body["recent"]] == list(reversed(ids))[:10]


# --------------------------------------------------------------------------- #
# Access
# --------------------------------------------------------------------------- #


def test_profile_endpoints_require_sign_in(client):
    assert client.get(f"{PROFILES}/me").status_code == 401
    assert client.patch(f"{PROFILES}/me", json={"bio": "x"}).status_code == 401
    assert client.put(f"{PROFILES}/me/picture", files={"file": ("a.jpg", JPEG)}).status_code == 401
    assert client.delete(f"{PROFILES}/me/picture").status_code == 401
    assert client.get(f"{PROFILES}/1").status_code == 401


def test_another_users_profile_is_visible_without_their_email(client):
    other = sign_up(client, "rahul_k")
    sign_up(client)
    headers = log_in(client)

    response = client.get(f"{PROFILES}/{other['id']}", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["username"] == "rahul_k"
    assert "email" not in body


def test_an_unknown_profile_is_not_found(client):
    sign_up(client)
    response = client.get(f"{PROFILES}/999", headers=log_in(client))
    assert response.status_code == 404
    assert response.json()["detail"] == "Profile not found"


def test_editing_only_changes_the_callers_own_profile(client, db):
    other = sign_up(client, "rahul_k")
    sign_up(client)
    client.patch(f"{PROFILES}/me", json={"bio": "mine"}, headers=log_in(client))
    assert accounts.get_profile(db, other["id"]).bio is None
