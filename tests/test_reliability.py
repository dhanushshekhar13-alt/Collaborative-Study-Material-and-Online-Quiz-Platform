"""NFR-04 - reliability: never lose a committed attempt or upload (issue #48).

Verified by fault injection, as the NFR asks: the service is killed mid-submission and
mid-upload (a child process running the real app exits with ``os._exit`` after the
writes are flushed but before they commit), and transient database failures are
injected part-way through a request. Each test then opens the database from a new
connection, as a restarted server would, and checks that

* nothing committed earlier is lost and nothing half-written is left behind;
* the system is consistent and the same request succeeds when retried;
* an upload is one transaction (the material and its file commit together), as is a
  submission (scores, attempt state and analytics figures), each with a single commit.

The file-database tests use the real ``get_db`` session handling, not the shared test
session. ``tests/test_quiz_history.py`` holds the earlier NFR-04 submission tests.
"""

import hashlib
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from alembic import command
from app.core import database, events
from app.main import TEMPORARY_FAILURE, create_app
from app.modules.analytics import service as analytics
from app.modules.analytics.models import PerformanceAttempt
from app.modules.materials import service as materials
from app.modules.materials.models import MaterialFile, StudyMaterial
from app.modules.quizzes.models import AttemptAnswer, QuizAttempt
from tests.test_quiz_attempts import (
    API,
    PASSWORD,
    auth,
    make_quiz,
    make_user,
    mcqs,
    save,
    start,
    submit,
)

ROOT = Path(__file__).resolve().parents[1]
SECRET = "test-signing-secret"  # the conftest signing key, so parent and child agree
PDF = b"%PDF-1.4\n1 0 obj << /Type /Catalog >> endobj\ntrailer << >>\n%%EOF\n"
TAGS = {"subject": "DBMS", "topic": "Transactions", "semester": "4", "material_type": "notes"}
KILLED = 137


@pytest.fixture()
def live(tmp_path, monkeypatch):
    """The API with real per-request sessions over a file database another process
    (the one we kill) can open too."""
    import app.models  # noqa: F401

    path = tmp_path / "nfr04.db"
    engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
    database.Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(database, "SessionLocal", factory)
    with TestClient(create_app()) as api:
        yield api, factory, path
    engine.dispose()


def reopened(path: Path) -> Session:
    """A session on a brand-new engine: what a restarted server sees."""
    engine = create_engine(f"sqlite:///{path}")
    return Session(engine, expire_on_commit=False)


def transient() -> OperationalError:
    return OperationalError("COMMIT", {}, Exception("server closed the connection unexpectedly"))


def upload(api, headers, title="ACID_Notes.pdf", content=PDF):
    return api.post(
        f"{API}/materials",
        data=TAGS,
        files={"file": (title, content, "application/octet-stream")},
        headers=headers,
    )


def setup_quiz(factory, prefix="rel") -> tuple[int, list[int]]:
    with factory() as session:
        author = make_user(session, f"{prefix}_author")
        make_user(session, f"{prefix}_student")
        ids = mcqs(session, author.id)
        quiz_id = make_quiz(session, author.id, ids)
    return quiz_id, ids


# --------------------------------------------------------------------------- #
# Killing the service mid-request (a child process running the real app)
# --------------------------------------------------------------------------- #

CHILD = textwrap.dedent(
    """
    import os, sys
    from fastapi.testclient import TestClient
    from app.main import create_app

    mode, username, password, target = sys.argv[1:5]
    api = TestClient(create_app())
    token = api.post(
        "/api/v1/accounts/login", json={"username": username, "password": password}
    ).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}


    def kill_after(module, name):
        real = getattr(module, name)

        def dying(db, *args, **kwargs):
            real(db, *args, **kwargs)
            db.flush()  # the writes reach the database, uncommitted
            os._exit(137)  # the process dies here, as with kill -9

        setattr(module, name, dying)


    if mode == "submit":
        from app.modules.quizzes import attempts

        kill_after(attempts, "_finalise")
        answers = {q: "A" for q in target.split(",")[1:]}
        api.post(
            f"/api/v1/attempts/{target.split(',')[0]}/submit",
            json={"answers": answers, "confirm": True},
        headers=headers,
        )
    else:
        from app.modules.materials import service

        kill_after(service, "attach_file")
        api.post(
            "/api/v1/materials",
            data={"subject": "DBMS", "topic": "Transactions", "semester": "4",
                  "material_type": "notes"},
            files={"file": (target, b"%PDF-1.4 killed mid-upload", "application/pdf")},
            headers=headers,
        )
    sys.exit("the request finished: the kill point was never reached")
    """
)


def run_and_kill(path: Path, mode: str, username: str, target: str) -> None:
    env = {
        **os.environ,
        "DATABASE_URL": f"sqlite:///{path}",
        "AUTH_TOKEN_SECRET": SECRET,
        "LLM_PROVIDER": "none",
    }
    done = subprocess.run(
        [sys.executable, "-c", CHILD, mode, username, PASSWORD, target],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert done.returncode == KILLED, done.stderr


def test_killing_the_service_mid_submission_loses_no_committed_attempt(live):
    api, factory, path = live
    quiz_id, ids = setup_quiz(factory, "kill")
    headers = auth(api, "kill_student")
    earlier = start(api, headers, quiz_id).json()["id"]
    assert submit(api, headers, earlier, dict.fromkeys(ids, "A")).status_code == 200
    running = start(api, headers, quiz_id).json()["id"]
    save(api, headers, running, {ids[0]: "A", ids[1]: "B"})  # committed answers

    run_and_kill(path, "submit", "kill_student", ",".join(map(str, [running, ids[2]])))

    with reopened(path) as session:
        kept = session.get(QuizAttempt, earlier)
        assert (kept.status, kept.score, kept.max_score) == ("submitted", 5, 5)
        half = session.get(QuizAttempt, running)
        assert (half.status, half.score, half.submitted_at) == ("in_progress", None, None)
        rows = session.scalars(select(AttemptAnswer).where(AttemptAnswer.attempt_id == running))
        stored = {r.question_id: (r.response, r.score) for r in rows}
        assert stored == {ids[0]: ("A", None), ids[1]: ("B", None)}  # nothing half-scored
        analytics_rows = session.scalar(select(func.count()).select_from(PerformanceAttempt))
        assert analytics_rows == 1  # only the committed attempt
        assert session.execute(text("PRAGMA integrity_check")).scalar() == "ok"

    # Recovered: the same submission goes through once the service is back.
    retried = submit(api, headers, running, {ids[2]: "A"}, confirm=True)
    assert retried.status_code == 200, retried.text
    assert retried.json()["result"]["display"] == "2/5 (40%)"
    listed = api.get(f"{API}/attempts", headers=headers).json()
    assert [a["attempt_id"] for a in listed["attempts"]] == [running, earlier]


def test_killing_the_service_mid_upload_leaves_no_partial_material(live):
    api, factory, path = live
    with factory() as session:
        make_user(session, "kill_uploader")
    headers = auth(api, "kill_uploader")
    kept = upload(api, headers, "Kept_Notes.pdf").json()

    run_and_kill(path, "upload", "kill_uploader", "Lost_Notes.pdf")

    with reopened(path) as session:
        titles = session.scalars(select(StudyMaterial.title)).all()
        assert titles == ["Kept_Notes"]  # no material without its file, or the reverse
        files = session.execute(select(MaterialFile.material_id, MaterialFile.data)).all()
        assert files == [(kept["id"], PDF)]
        assert session.execute(text("PRAGMA integrity_check")).scalar() == "ok"

    download = api.get(f"{API}/materials/{kept['id']}/download", headers=headers)
    assert download.content == PDF
    retried = upload(api, headers, "Lost_Notes.pdf")
    assert retried.status_code == 201, retried.text


def test_committed_attempts_and_uploads_survive_a_restart(live):
    api, factory, path = live
    quiz_id, ids = setup_quiz(factory, "restart")
    headers = auth(api, "restart_student")
    attempt = start(api, headers, quiz_id).json()["id"]
    submit(api, headers, attempt, {ids[0]: "A", ids[1]: "A", ids[2]: "A"}, confirm=True)
    material = upload(api, headers).json()

    # A new app on a new engine, as after a restart.
    engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
    database.SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    try:
        with TestClient(create_app()) as restarted:
            again = auth(restarted, "restart_student")
            result = restarted.get(f"{API}/attempts/{attempt}", headers=again).json()
            assert result["status"] == "submitted"
            assert result["result"]["display"] == "3/5 (60%)"
            got = restarted.get(f"{API}/materials/{material['id']}/download", headers=again)
            assert got.content == PDF
            assert got.headers["X-Content-SHA256"] == hashlib.sha256(PDF).hexdigest()
    finally:
        engine.dispose()


# --------------------------------------------------------------------------- #
# Transient failures part-way through an upload
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("where", ["while storing the file", "at commit"])
def test_a_transient_failure_mid_upload_saves_nothing_and_can_be_retried(live, monkeypatch, where):
    api, factory, path = live
    with factory() as session:
        make_user(session, "flaky_uploader")
    headers = auth(api, "flaky_uploader")
    earlier = upload(api, headers, "Earlier.pdf").json()

    if where == "at commit":
        real_commit = Session.commit
        left = {"n": 1}

        def flaky_commit(self):
            if left["n"]:
                left["n"] -= 1
                raise transient()
            real_commit(self)

        monkeypatch.setattr(Session, "commit", flaky_commit)
    else:
        real_attach = materials.attach_file

        def failing_attach(db, *args, **kwargs):
            real_attach(db, *args, **kwargs)
            monkeypatch.setattr(materials, "attach_file", real_attach)
            raise transient()

        monkeypatch.setattr(materials, "attach_file", failing_attach)

    failed = upload(api, headers, "Retry.pdf")

    assert failed.status_code == 503
    assert failed.json()["detail"] == TEMPORARY_FAILURE
    with reopened(path) as session:
        assert session.scalars(select(StudyMaterial.title)).all() == ["Earlier"]
        assert session.scalar(select(func.count()).select_from(MaterialFile)) == 1

    retried = upload(api, headers, "Retry.pdf")
    assert retried.status_code == 201, retried.text
    with reopened(path) as session:
        assert sorted(session.scalars(select(StudyMaterial.title))) == ["Earlier", "Retry"]
        assert session.scalar(select(func.count()).select_from(MaterialFile)) == 2
    assert api.get(f"{API}/materials/{earlier['id']}/download", headers=headers).content == PDF


# --------------------------------------------------------------------------- #
# Uploads and attempt writes are transactional
# --------------------------------------------------------------------------- #


def _count_commits(monkeypatch) -> list[int]:
    seen: list[int] = []
    real_commit = Session.commit

    def counting(self):
        seen.append(1)
        real_commit(self)

    monkeypatch.setattr(Session, "commit", counting)
    return seen


def test_an_upload_is_one_transaction(live, monkeypatch):
    api, factory, _ = live
    with factory() as session:
        make_user(session, "tx_uploader")
    headers = auth(api, "tx_uploader")
    commits = _count_commits(monkeypatch)

    created = upload(api, headers)

    assert created.status_code == 201
    assert len(commits) == 1  # material, tags and file bytes commit together
    assert created.json()["file"]["sha256"] == hashlib.sha256(PDF).hexdigest()


def test_a_submission_is_one_transaction(live, monkeypatch):
    api, factory, _ = live
    quiz_id, ids = setup_quiz(factory, "tx")
    headers = auth(api, "tx_student")
    attempt = start(api, headers, quiz_id).json()["id"]
    commits = _count_commits(monkeypatch)

    done = submit(api, headers, attempt, dict.fromkeys(ids, "A"))

    assert done.status_code == 200
    assert len(commits) == 1  # answers, scores, attempt state and analytics together
    with factory() as session:
        assert session.scalar(
            select(func.count())
            .select_from(PerformanceAttempt)
            .where(PerformanceAttempt.attempt_id == attempt)
        )


def test_a_failing_reaction_never_costs_the_user_their_submission(live, monkeypatch):
    """A bug in a subscriber (here analytics) is isolated in a savepoint: the attempt
    still commits, the subscriber's half-done writes are undone, and analytics catches
    up from the stored attempts when the dashboard is next read."""
    api, factory, path = live
    quiz_id, ids = setup_quiz(factory, "react")
    headers = auth(api, "react_student")
    attempt = start(api, headers, quiz_id).json()["id"]
    real_record = analytics.record_attempt

    def broken(db, attempt_id):
        real_record(db, attempt_id)
        db.flush()
        raise RuntimeError("analytics bug")

    monkeypatch.setattr(analytics, "record_attempt", broken)
    done = submit(api, headers, attempt, dict.fromkeys(ids, "A"))
    monkeypatch.setattr(analytics, "record_attempt", real_record)

    assert done.status_code == 200, done.text
    with reopened(path) as session:
        assert session.get(QuizAttempt, attempt).status == "submitted"
        assert session.scalar(select(func.count()).select_from(PerformanceAttempt)) == 0

    dashboard = api.get(f"{API}/analytics/dashboard", headers=headers)
    assert dashboard.status_code == 200, dashboard.text
    assert dashboard.json()["attempts"] == 1  # reconciled from the stored attempt


def test_a_database_failure_inside_a_reaction_still_rolls_the_request_back(live, monkeypatch):
    api, factory, path = live
    quiz_id, ids = setup_quiz(factory, "dbdown")
    headers = auth(api, "dbdown_student")
    attempt = start(api, headers, quiz_id).json()["id"]

    def database_gone(db, attempt_id):
        raise transient()

    monkeypatch.setattr(analytics, "record_attempt", database_gone)
    failed = submit(api, headers, attempt, dict.fromkeys(ids, "A"))

    assert failed.status_code == 503
    with reopened(path) as session:
        assert session.get(QuizAttempt, attempt).status == "in_progress"


def test_handlers_are_isolated_from_each_other(db):
    calls = []

    def broken(session, **payload):
        calls.append("broken")
        raise ValueError("bug")

    def working(session, **payload):
        calls.append(("working", payload))

    event = "test.reliability_event"
    events.subscribe(event, broken)
    events.subscribe(event, working)
    try:
        events.publish(event, db, value=1)
    finally:
        events._handlers.pop(event, None)
    assert calls == ["broken", ("working", {"value": 1})]


# --------------------------------------------------------------------------- #
# Stored files are checked on every download
# --------------------------------------------------------------------------- #


def test_a_damaged_file_is_refused_rather_than_served(live):
    api, factory, _ = live
    with factory() as session:
        make_user(session, "damaged_uploader")
    headers = auth(api, "damaged_uploader")
    material = upload(api, headers).json()
    with factory() as session:
        row = session.get(MaterialFile, material["id"])
        row.data = PDF.replace(b"Catalog", b"Catalo9")  # bit rot
        session.commit()

    for path in ("download", "preview"):
        got = api.get(f"{API}/materials/{material['id']}/{path}", headers=headers)
        assert got.status_code == 500
        assert got.json()["detail"].startswith("This file is damaged")


def test_migration_0013_backfills_checksums_for_existing_files(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'backfill.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    config = Config("alembic.ini")
    command.upgrade(config, "0012")
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO users (id, email, display_name, password_hash, role) "
                "VALUES (1, 'a@test.com', 'A', 'x', 'student')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO study_materials (id, uploader_id, title, material_type, subject, "
                "status) VALUES (1, 1, 'Old', 'notes', 'DBMS', 'active')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO material_files (material_id, filename, content_type, size_bytes, "
                "data) VALUES (1, 'old.pdf', 'application/pdf', :n, :data)"
            ),
            {"n": len(PDF), "data": PDF},
        )
    command.upgrade(config, "head")
    with engine.connect() as conn:
        stored = conn.execute(text("SELECT sha256 FROM material_files")).scalar()
    engine.dispose()
    assert stored == hashlib.sha256(PDF).hexdigest()
    command.downgrade(config, "base")
