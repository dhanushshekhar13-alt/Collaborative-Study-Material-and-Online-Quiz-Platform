"""S04.1 - upload, search and download study materials (issue #22, FR-04).

Test Plan cases are named after their IDs: UT_07, UT_08, IT_07, IT_08. The plan's
steps run in the web UI (UI-2 #63); these call the endpoints that UI will use.
"""

import time

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.modules.accounts import service as accounts
from app.modules.accounts.models import Role
from app.modules.channels import service as channels
from app.modules.channels.models import ApprovalStatus, ChannelType
from app.modules.materials import service as materials
from app.modules.materials.models import MaterialType

API = "/api/v1/materials"
PASSWORD = "Pass@1234"
PDF = b"%PDF-1.4\n1 0 obj << /Type /Catalog >> endobj\ntrailer << >>\n%%EOF\n"
DOCX = b"PK\x03\x04" + b"\x00" * 64
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32

OS_TAGS = {
    "subject": "Operating Systems",
    "topic": "CPU Scheduling",
    "semester": "5",
    "material_type": "notes",
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
def users(client, db) -> dict[str, int]:
    ids = {name: make_user(db, name) for name in ("aditi_r", "rahul_m", "outsider_p")}
    ids["faculty_a"] = make_user(db, "faculty_a", Role.AUTHORIZED_USER)
    ids["admin_a"] = make_user(db, "admin_a", Role.ADMIN)
    return ids


@pytest.fixture()
def aditi(client, users):
    return auth(client, "aditi_r")


def upload(client, headers, *, filename="OS_Scheduling_Notes.pdf", content=PDF, **fields):
    data = {**OS_TAGS, **fields}
    data = {k: v for k, v in data.items() if v is not None}
    files = {"file": (filename, content, "application/octet-stream")} if filename else None
    return client.post(API, data=data, files=files, headers=headers)


def search(client, headers, **params):
    response = client.get(API, params=params, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


# --------------------------------------------------------------------------- #
# UT_07 - a fully tagged upload succeeds and is listed with every tag
# --------------------------------------------------------------------------- #


def test_UT_07_tagged_upload_succeeds_and_is_listed_with_all_tags(client, aditi):
    response = upload(client, aditi)

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["title"] == "OS_Scheduling_Notes"
    assert (body["subject"], body["topic"], body["semester"]) == (
        "Operating Systems",
        "CPU Scheduling",
        "5",
    )
    assert body["material_type"] == "notes"
    assert body["tags"] == ["Operating Systems", "CPU Scheduling", "Semester 5", "Notes"]
    assert body["file"] == {
        "filename": "OS_Scheduling_Notes.pdf",
        "content_type": "application/pdf",
        "size_bytes": len(PDF),
        "previewable": True,
    }
    assert body["uploader_name"] == "Aditi_R"

    listing = search(client, aditi, subject="Operating Systems", topic="CPU Scheduling")
    assert [m["id"] for m in listing["items"]] == [body["id"]]
    assert listing["items"][0]["tags"] == body["tags"]


def test_an_explicit_title_and_description_are_kept(client, aditi):
    body = upload(client, aditi, title="Round robin notes", description="Week 5").json()

    assert body["title"] == "Round robin notes"
    assert body["description"] == "Week 5"


def test_uploading_requires_sign_in(client, users):
    assert upload(client, {}).status_code == 401


# --------------------------------------------------------------------------- #
# UT_08 - a blank mandatory field blocks the upload
# --------------------------------------------------------------------------- #


def test_UT_08_blank_subject_blocks_the_upload(client, aditi):
    response = upload(client, aditi, filename="Notes.pdf", subject="  ")

    assert response.status_code == 422
    assert response.json()["detail"] == "Subject is required"
    assert search(client, aditi)["total"] == 0


@pytest.mark.parametrize(
    ("field", "message"),
    [
        ("subject", "Subject is required"),
        ("topic", "Topic is required"),
        ("semester", "Semester is required"),
        ("material_type", "Material type is required"),
    ],
)
def test_every_mandatory_tag_is_required(client, aditi, field, message):
    response = upload(client, aditi, **{field: None})

    assert response.status_code == 422
    assert response.json()["detail"] == message


def test_an_unknown_material_type_is_refused(client, aditi):
    response = upload(client, aditi, material_type="video")

    assert response.status_code == 422
    assert response.json()["detail"] == "Material type is required"


def test_a_file_is_required_unless_the_material_is_a_link(client, aditi):
    response = upload(client, aditi, filename=None)

    assert response.status_code == 422
    assert response.json()["detail"] == "Choose a file to upload"


# --------------------------------------------------------------------------- #
# Supported file types and the maximum size
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("filename", "content", "content_type"),
    [
        ("notes.pdf", PDF, "application/pdf"),
        (
            "slides.pptx",
            DOCX,
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ),
        (
            "notes.docx",
            DOCX,
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ),
        ("summary.txt", b"Round robin uses a time quantum.", "text/plain; charset=utf-8"),
        ("diagram.PNG", PNG, "image/png"),
    ],
)
def test_supported_file_types_are_accepted(client, aditi, filename, content, content_type):
    response = upload(client, aditi, filename=filename, content=content)

    assert response.status_code == 201, response.text
    assert response.json()["file"]["content_type"] == content_type


@pytest.mark.parametrize(
    ("filename", "content"),
    [("setup.exe", b"MZ\x90\x00"), ("page.html", b"<html><script>x</script>"), ("noext", PDF)],
)
def test_unsupported_file_types_are_refused(client, aditi, filename, content):
    response = upload(client, aditi, filename=filename, content=content)

    assert response.status_code == 422
    assert response.json()["detail"] == (
        "This file type is not supported. Upload a PDF, Word, PowerPoint, text, PNG or JPEG file"
    )


def test_a_renamed_file_is_refused_by_its_contents(client, aditi):
    response = upload(client, aditi, filename="notes.pdf", content=b"<html>not a pdf</html>")

    assert response.status_code == 422
    assert response.json()["detail"] == "The file's contents do not match its type"


def test_a_file_over_the_size_limit_is_refused(client, aditi, monkeypatch):
    monkeypatch.setattr(settings, "material_max_upload_mb", 1)
    too_big = PDF + b"0" * (1024 * 1024)

    response = upload(client, aditi, content=too_big)

    assert response.status_code == 413
    assert response.json()["detail"] == "File must be 1 MB or smaller"
    assert search(client, aditi)["total"] == 0


def test_an_empty_file_is_refused(client, aditi):
    response = upload(client, aditi, content=b"")

    assert response.status_code == 422
    assert response.json()["detail"] == "Choose a file to upload"


def test_client_paths_are_stripped_from_the_file_name(client, aditi):
    body = upload(client, aditi, filename="..\\..\\C:\\Users\\me\\OS Notes.pdf").json()

    assert body["file"]["filename"] == "OS Notes.pdf"


def test_a_link_material_needs_a_web_url(client, aditi):
    refused = upload(client, aditi, filename=None, material_type="link", url="ftp://x")
    assert refused.status_code == 422

    response = upload(
        client,
        aditi,
        filename=None,
        material_type="link",
        url="https://example.com/scheduling",
        title="Scheduling visualiser",
    )

    assert response.status_code == 201
    body = response.json()
    assert body["url"] == "https://example.com/scheduling"
    assert body["file"] is None and body["download_url"] is None


# --------------------------------------------------------------------------- #
# IT_07 - filter by subject and topic
# --------------------------------------------------------------------------- #


@pytest.fixture()
def library(db, users) -> dict[str, int]:
    def add(title, subject, topic):
        return materials.create_material(
            db,
            uploader_id=users["rahul_m"],
            title=title,
            material_type=MaterialType.NOTES,
            subject=subject,
            topic=topic,
            semester="5",
        ).id

    ids = {
        "fcfs": add("FCFS and SJF", "Operating Systems", "CPU Scheduling"),
        "rr": add("Round robin", "Operating Systems", "CPU Scheduling"),
        "paging": add("Paging", "Operating Systems", "Memory Management"),
        "normal": add("Normal forms", "DBMS", "Normalization"),
        "dbms_sched": add("Transaction scheduling", "DBMS", "CPU Scheduling"),
    }
    db.commit()
    return ids


def test_IT_07_subject_and_topic_return_only_materials_matching_both(client, aditi, library):
    result = search(client, aditi, subject="Operating Systems", topic="CPU Scheduling")

    assert {m["id"] for m in result["items"]} == {library["fcfs"], library["rr"]}
    assert result["total"] == 2
    assert all(
        (m["subject"], m["topic"]) == ("Operating Systems", "CPU Scheduling")
        for m in result["items"]
    )
    assert result["empty_state"] is None


def test_filters_ignore_case_and_surrounding_spaces(client, aditi, library):
    result = search(client, aditi, subject=" operating systems ", topic="cpu scheduling")

    assert result["total"] == 2


def test_keyword_search_covers_titles(client, aditi, library):
    result = search(client, aditi, q="round")

    assert [m["id"] for m in result["items"]] == [library["rr"]]


def test_results_are_paginated(client, aditi, library):
    first = search(client, aditi, limit=2)
    second = search(client, aditi, limit=2, offset=2)

    assert first["total"] == 5 and len(first["items"]) == 2
    assert not {m["id"] for m in first["items"]} & {m["id"] for m in second["items"]}


def test_an_empty_result_carries_a_helpful_empty_state(client, aditi, library):
    result = search(client, aditi, subject="Compiler Design")

    assert result["items"] == [] and result["total"] == 0
    assert result["empty_state"] == (
        "No materials match your search. Try a broader subject or topic, "
        "or upload the first material for it."
    )


def test_removed_materials_are_not_listed(client, db, aditi, library):
    materials.set_removed(db, library["rr"], True)
    db.commit()

    result = search(client, aditi, subject="Operating Systems", topic="CPU Scheduling")

    assert [m["id"] for m in result["items"]] == [library["fcfs"]]
    assert client.get(f"{API}/{library['rr']}", headers=aditi).status_code == 404


def test_search_and_filter_stay_inside_the_response_time_target(client, db, aditi, users):
    """NFR-01 target for this path: search or filter answers within 1 second with
    1,000 materials in the library (measured here in-process, end to end)."""
    for i in range(1000):
        materials.create_material(
            db,
            uploader_id=users["rahul_m"],
            title=f"Material {i}",
            material_type=MaterialType.NOTES,
            subject=["Operating Systems", "DBMS", "Networks", "Compilers"][i % 4],
            topic=["CPU Scheduling", "Paging", "Normalization", "Routing", "Parsing"][i % 5],
            semester=str(i % 8 + 1),
        )
    db.commit()

    for params in (
        {"subject": "Operating Systems", "topic": "CPU Scheduling"},
        {"q": "material 99"},
        {},
    ):
        started = time.perf_counter()
        result = search(client, aditi, **params)
        assert time.perf_counter() - started < 1.0, params
        assert result["total"] > 0


# --------------------------------------------------------------------------- #
# IT_08 - view and download a peer-shared material
# --------------------------------------------------------------------------- #


def test_IT_08_a_peer_material_previews_and_downloads(client, users, aditi):
    material = upload(client, aditi).json()
    rahul = auth(client, "rahul_m")

    listing = search(client, rahul)
    assert [m["id"] for m in listing["items"]] == [material["id"]]

    view = client.get(listing["items"][0]["preview_url"], headers=rahul)
    assert view.status_code == 200
    assert view.headers["content-type"] == "application/pdf"
    assert view.headers["content-disposition"].startswith("inline;")
    assert view.content == PDF

    download = client.get(listing["items"][0]["download_url"], headers=rahul)
    assert download.status_code == 200
    assert download.content == PDF
    assert download.headers["content-disposition"] == (
        "attachment; filename=\"OS_Scheduling_Notes.pdf\"; filename*=UTF-8''OS_Scheduling_Notes.pdf"
    )
    assert download.headers["x-content-type-options"] == "nosniff"


def test_non_previewable_files_download_but_do_not_preview(client, aditi):
    material = upload(client, aditi, filename="slides.pptx", content=DOCX).json()

    assert material["preview_url"] is None
    preview = client.get(f"{API}/{material['id']}/preview", headers=aditi)
    assert preview.status_code == 415
    assert preview.json()["detail"] == (
        "Preview isn't available for this file type. Download it instead"
    )
    assert client.get(material["download_url"], headers=aditi).content == DOCX


def test_text_previews_cannot_run_scripts(client, aditi):
    material = upload(
        client, aditi, filename="notes.txt", content=b"<script>alert(1)</script>"
    ).json()

    view = client.get(material["preview_url"], headers=aditi)

    assert view.headers["content-type"] == "text/plain; charset=utf-8"
    assert view.headers["content-security-policy"].startswith("default-src 'none'")


def test_unicode_file_names_download_under_their_name(client, aditi):
    material = upload(client, aditi, filename="Réseaux notes.pdf").json()

    download = client.get(material["download_url"], headers=aditi)

    assert "filename*=UTF-8''R%C3%A9seaux%20notes.pdf" in download.headers["content-disposition"]


def test_a_material_without_a_file_has_nothing_to_download(client, db, aditi, users):
    seeded = materials.create_material(
        db,
        uploader_id=users["rahul_m"],
        title="Seeded",
        material_type=MaterialType.NOTES,
        subject="DBMS",
    )
    db.commit()

    response = client.get(f"{API}/{seeded.id}/download", headers=aditi)

    assert response.status_code == 404
    assert response.json()["detail"] == "This material has no file to download"


def test_file_endpoints_require_sign_in(client, aditi):
    material = upload(client, aditi).json()

    assert client.get(f"{API}/{material['id']}/download").status_code == 401
    assert client.get(f"{API}/{material['id']}/preview").status_code == 401
    assert client.get(API).status_code == 401


# --------------------------------------------------------------------------- #
# Channel-scoped materials follow channel membership
# --------------------------------------------------------------------------- #


@pytest.fixture()
def dbms_channel(db, users) -> int:
    channel = channels.create_channel(
        db, name="DBMS Study Group", subject="DBMS", created_by=users["aditi_r"]
    )
    db.commit()
    return channel.id


def test_channel_materials_are_for_members_only(client, aditi, dbms_channel):
    material = upload(client, aditi, channel_id=str(dbms_channel)).json()
    assert material["channel_id"] == dbms_channel
    outsider = auth(client, "outsider_p")

    assert search(client, outsider)["total"] == 0
    for path in ("", "/preview", "/download"):
        response = client.get(f"{API}/{material['id']}{path}", headers=outsider)
        assert response.status_code == 403
        assert response.json()["detail"] == "Join this channel to view its content"
    assert client.get(API, params={"channel_id": dbms_channel}, headers=outsider).status_code == 403


def test_joining_opens_channel_materials_and_leaving_closes_them(client, aditi, dbms_channel):
    material = upload(client, aditi, channel_id=str(dbms_channel)).json()
    rahul = auth(client, "rahul_m")

    client.post(f"/api/v1/channels/{dbms_channel}/join", headers=rahul)
    assert [m["id"] for m in search(client, rahul)["items"]] == [material["id"]]
    assert search(client, rahul, channel_id=dbms_channel)["total"] == 1
    assert client.get(material["download_url"], headers=rahul).status_code == 200

    client.post(f"/api/v1/channels/{dbms_channel}/leave", headers=rahul)
    assert search(client, rahul)["total"] == 0
    assert client.get(material["download_url"], headers=rahul).status_code == 403


def test_a_channel_filter_returns_only_that_channels_materials(client, aditi, dbms_channel):
    upload(client, aditi)
    in_channel = upload(client, aditi, channel_id=str(dbms_channel)).json()

    result = search(client, aditi, channel_id=dbms_channel)

    assert [m["id"] for m in result["items"]] == [in_channel["id"]]


def test_only_members_can_upload_to_a_channel(client, dbms_channel, users):
    response = upload(client, auth(client, "rahul_m"), channel_id=str(dbms_channel))

    assert response.status_code == 403
    assert response.json()["detail"] == "Join this channel to add materials"


def test_a_hidden_channel_is_not_revealed(client, db, users, aditi):
    pending = channels.create_channel(
        db,
        name="Official OS",
        subject="OS",
        created_by=users["faculty_a"],
        channel_type=ChannelType.AUTHORIZED,
    )
    db.commit()

    assert upload(client, aditi, channel_id=str(pending.id)).status_code == 404
    assert client.get(API, params={"channel_id": pending.id}, headers=aditi).status_code == 404


def test_students_cannot_upload_into_an_authorized_channel(client, db, users, aditi):
    official = channels.create_channel(
        db,
        name="Official OS",
        subject="OS",
        created_by=users["faculty_a"],
        channel_type=ChannelType.AUTHORIZED,
    )
    channels.set_approval(db, official.id, ApprovalStatus.APPROVED)
    channels.add_member(db, official.id, users["aditi_r"])
    db.commit()

    student = upload(client, aditi, channel_id=str(official.id))
    faculty = upload(client, auth(client, "faculty_a"), channel_id=str(official.id))

    assert student.status_code == 403
    assert student.json()["detail"] == (
        "Only authorized users can add content to Authorized Channels"
    )
    assert faculty.status_code == 201


def test_admins_can_see_channel_materials_for_moderation(client, aditi, dbms_channel):
    material = upload(client, aditi, channel_id=str(dbms_channel)).json()
    admin = auth(client, "admin_a")

    assert [m["id"] for m in search(client, admin)["items"]] == [material["id"]]
    assert client.get(material["download_url"], headers=admin).status_code == 200


# --------------------------------------------------------------------------- #
# Service
# --------------------------------------------------------------------------- #


def test_listing_never_loads_file_bytes(db, users):
    created = materials.create_material(
        db,
        uploader_id=users["aditi_r"],
        title="Notes",
        material_type=MaterialType.PDF,
        subject="OS",
    )
    materials.attach_file(
        db, created.id, filename="n.pdf", content_type="application/pdf", data=PDF
    )
    db.commit()
    db.expire_all()

    page = materials.search_materials(db)

    assert page.items[0].file == materials.MaterialFileInfo("n.pdf", "application/pdf", len(PDF))
    assert materials.get_material_file(db, created.id).data == PDF


def test_existing_service_signatures_still_work(db, users):
    created = materials.create_material(
        db,
        uploader_id=users["aditi_r"],
        title="Notes",
        material_type=MaterialType.NOTES,
        subject="OS",
    )
    assert materials.get_material(db, created.id).title == "Notes"
    assert materials.count_uploaded_by(db, users["aditi_r"]) == 1
