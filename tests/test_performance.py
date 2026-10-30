"""NFR-01 - performance: common operations stay inside response-time targets (issue #46).

The targets are written down as numbers (``scripts/load_test.py`` and
``docs/performance.md``, checked to agree) and then verified by load-testing the app:
50 concurrent students load every common page, search and filter, and submit quizzes,
and the 95th-percentile time of each operation must be inside its target. The run uses
a file SQLite database and, when ``TEST_POSTGRES_URL`` is set (CI), PostgreSQL too.

The Test Plan has no NFR-01 case, so tests are named after the NFR.
"""

import asyncio
import os
import re
import uuid
from pathlib import Path

import httpx
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

import app.main
from app.core import database
from app.core.config import settings
from app.core.database import engine_options, max_connections
from scripts import load_test
from scripts.load_test import PAGE_LOAD, QUIZ_SUBMISSION, SEARCH, TARGETS

ROOT = Path(__file__).resolve().parents[1]

BACKENDS = ["sqlite"]
if os.environ.get("TEST_POSTGRES_URL"):
    BACKENDS.append("postgresql")


@pytest.fixture()
def database_url(request, tmp_path):
    """An empty database of the requested kind, removed afterwards."""
    if request.param == "sqlite":
        yield f"sqlite:///{tmp_path / 'nfr01.db'}"
        return
    base = make_url(os.environ["TEST_POSTGRES_URL"])
    name = f"nfr01_{uuid.uuid4().hex[:8]}"
    admin = create_engine(base, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    try:
        yield base.set(database=name).render_as_string(hide_password=False)
    finally:
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


@pytest.fixture()
def restore_sessions(monkeypatch):
    """``load_test.prepare`` repoints the app's session factory; undo it afterwards."""
    monkeypatch.setattr(database, "SessionLocal", database.SessionLocal)


# ------------------------------------------------------- targets are numbers ----


def test_NFR_01_targets_are_written_down_as_explicit_numbers():
    assert TARGETS == {PAGE_LOAD: 2.0, SEARCH: 2.0, QUIZ_SUBMISSION: 3.0}
    assert load_test.PERCENTILE == 95
    assert load_test.CONCURRENT_USERS == 50


def test_NFR_01_the_documented_targets_match_the_ones_tested():
    doc = (ROOT / "docs" / "performance.md").read_text()
    documented = dict(
        re.findall(r"^\| (page load|search/filter|quiz submission) \| ([\d.]+) s", doc, re.M)
    )
    assert {k: float(v) for k, v in documented.items()} == TARGETS
    assert "95th percentile" in doc and "50 concurrent users" in doc


def test_NFR_01_the_load_covers_dashboard_quiz_submission_and_material_search():
    names = {op.name: op.category for op in load_test.OPERATIONS}
    assert names["Dashboard"] == PAGE_LOAD
    assert names["Quiz submission"] == QUIZ_SUBMISSION
    assert names["Material search"] == SEARCH
    assert set(names.values()) == set(TARGETS)


def test_NFR_01_p95_is_the_nearest_rank_percentile():
    assert load_test.p95([float(i) for i in range(1, 101)]) == 95.0
    assert load_test.p95([float(i) for i in range(1, 21)]) == 19.0
    assert load_test.p95([0.5]) == 0.5


# ------------------------------------------------------------ the load test ----


@pytest.mark.parametrize("database_url", BACKENDS, indirect=True)
def test_NFR_01_common_operations_stay_inside_their_targets_under_normal_load(
    database_url, restore_sessions
):
    application, world, engine = load_test.prepare(database_url)
    try:
        report = asyncio.run(load_test.run_load(application, world, rounds=1, think=1.0))
    finally:
        engine.dispose()
    print(f"\n{database_url.split(':')[0]}\n{report.table()}")

    for op in load_test.OPERATIONS:  # every user completed every operation
        assert len(report.samples[op.name]) == load_test.CONCURRENT_USERS, op.name
    assert report.failures == []
    assert report.missed() == []


# ------------------------------------------- bursts never deadlock the pool ----


def _burst(monkeypatch, tmp_path, requests: int = 60) -> list[int]:
    """Fire ``requests`` signed-in requests at once at an app with a 2-connection pool."""
    monkeypatch.setattr(settings, "database_pool_size", 1)
    monkeypatch.setattr(settings, "database_max_overflow", 1)
    url = f"sqlite:///{tmp_path / 'burst.db'}"
    engine = create_engine(url, **engine_options(url), pool_timeout=3)
    database.Base.metadata.create_all(engine)
    monkeypatch.setattr(
        database, "SessionLocal", sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    )
    with database.SessionLocal() as db:
        from app.modules.accounts import service as accounts
        from app.modules.accounts.tokens import create_access_token

        user = accounts.create_user(db, email="b@test.com", display_name="B", password="Pass@1234")
        db.commit()
        token = create_access_token(user.id, user.role.value)

    async def fire() -> list[int]:
        transport = httpx.ASGITransport(app=app.main.create_app())
        async with httpx.AsyncClient(transport=transport, base_url="http://burst") as client:
            headers = {"Authorization": f"Bearer {token}"}
            calls = [client.get("/api/v1/profiles/me", headers=headers) for _ in range(requests)]
            return [r.status_code for r in await asyncio.gather(*calls, return_exceptions=False)]

    try:
        return asyncio.run(fire())
    except Exception:  # the pool timeout surfaces through the transport
        return [500]
    finally:
        engine.dispose()


def test_NFR_01_the_server_admits_one_request_per_database_connection():
    assert max_connections() == settings.database_pool_size + settings.database_max_overflow
    assert max_connections() < 40  # fewer than the thread pool, so no request starves


def test_NFR_01_a_burst_larger_than_the_pool_is_served_without_errors(monkeypatch, tmp_path):
    statuses = _burst(monkeypatch, tmp_path)
    assert statuses == [200] * 60


def test_NFR_01_static_pages_are_not_held_back_by_the_api_limit():
    from app.core.concurrency import RequestLimit

    seen = []

    async def inner(scope, receive, send):
        seen.append(scope["path"])

    limit = RequestLimit(inner, limit=1, prefix="/api/")

    async def run() -> None:
        async with limit._semaphore():  # the one API slot is taken
            await limit({"type": "http", "path": "/app/index.html"}, None, None)

    asyncio.run(run())
    assert seen == ["/app/index.html"]
    with pytest.raises(ValueError):
        RequestLimit(inner, limit=0)
