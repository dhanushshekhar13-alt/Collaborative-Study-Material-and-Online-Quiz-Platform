"""NFR-05 - scalability: hold performance as users and content grow (issue #51).

The Test Plan has no case of its own for NFR-05; the issue's "How it is verified" is
"seed a large dataset and re-run the performance suite; review schema indexes". These
tests do exactly that, against the same in-memory SQLite the rest of the suite uses:

* ``scripts/seed_scale.py`` seeds the tested range, factor 1 (50 users, 500 materials,
  1,100 channel messages, ...) to factor 10 (500 users, 5,000 materials, 11,000 channel
  messages, 10,000 direct messages, 10,000 notifications, 2,000 quiz attempts, ...);
* every hot path answers inside the 1-second target at both ends of the range;
* the number of database queries a hot path makes does not depend on the amount of
  data (a list that looked each row's details up separately grew with the platform);
* every query those paths run is answered from an index, never by scanning a growing
  table, and every foreign key column is indexed (PostgreSQL does not do that itself).

"A new domain can be added without redesigning the core architecture" is covered by the
module rules (``tests/test_module_boundaries.py``) plus the acyclic-dependency check
below; ``docs/architecture.md`` ("Scalability (NFR-05)") has the checklist.
"""

import re
import statistics
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import PrimaryKeyConstraint, UniqueConstraint, create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401  (register every table)
from app.core.database import Base, get_db
from app.main import create_app
from app.modules import ALLOWED_DEPENDENCIES
from scripts.seed_scale import ADMIN, PROBE, ScaleDataset, seed_scale

SMALL, LARGE = 1, 10  # the tested range, in units of scripts.seed_scale.PER_FACTOR
TARGET_SECONDS = 1.0  # NFR-01's search target, applied to every hot path
PASSWORD = "Scale@1234"


@dataclass(frozen=True)
class HotPath:
    name: str
    method: str
    path: str
    as_admin: bool = False
    params: dict | None = None
    json: dict | None = None


def hot_paths(d: ScaleDataset) -> list[HotPath]:
    """The requests a busy platform serves most, against the dataset's busiest rows."""
    channel = f"/api/v1/channels/{d.busy_channel_id}"
    return [
        HotPath("material search", "get", "/api/v1/materials", params={"subject": "DBMS"}),
        HotPath("material text search", "get", "/api/v1/materials", params={"q": "notes 99"}),
        HotPath("material listing", "get", "/api/v1/materials"),
        HotPath("material page", "get", f"/api/v1/materials/{d.material_id}"),
        HotPath("material reviews", "get", f"/api/v1/materials/{d.material_id}/reviews"),
        HotPath("channel directory", "get", "/api/v1/channels"),
        HotPath("my channels", "get", "/api/v1/channels/mine"),
        HotPath("channel page", "get", channel),
        HotPath("channel members", "get", f"{channel}/members"),
        HotPath("channel chat history", "get", f"{channel}/messages"),
        HotPath("thread replies", "get", f"{channel}/messages/{d.busy_thread_id}/replies"),
        HotPath("send channel message", "post", f"{channel}/messages", json={"body": "hi"}),
        HotPath("conversation list", "get", "/api/v1/conversations"),
        HotPath(
            "direct message history",
            "get",
            f"/api/v1/conversations/{d.conversation_id}/messages",
        ),
        HotPath("notifications", "get", "/api/v1/notifications"),
        HotPath("unread count", "get", "/api/v1/notifications/unread-count"),
        HotPath("question bank", "get", "/api/v1/questions", params={"subject": "DBMS"}),
        HotPath(
            "channel question bank",
            "get",
            "/api/v1/questions",
            params={"channel_id": d.busy_channel_id},
        ),
        HotPath("quiz list", "get", "/api/v1/quizzes"),
        HotPath(
            "channel quizzes", "get", "/api/v1/quizzes", params={"channel_id": d.busy_channel_id}
        ),
        HotPath("quiz history", "get", "/api/v1/attempts"),
        HotPath("performance dashboard", "get", "/api/v1/analytics/dashboard"),
        HotPath("recommendations", "get", "/api/v1/analytics/recommendations"),
        HotPath("leaderboard", "get", "/api/v1/leaderboard"),
        HotPath("my profile", "get", "/api/v1/profiles/me"),
        HotPath("my reputation", "get", "/api/v1/reputation/me"),
        HotPath("moderation queue", "get", "/api/v1/moderation/queue", as_admin=True),
        HotPath("admin dashboard", "get", "/api/v1/admin/dashboard", as_admin=True),
    ]


# Paths whose query count is bounded by something other than the amount of data:
# recommendations look candidates up per weak topic, so they follow how many topics
# the user has attempted (at most the catalogue's subjects x topics), not volume.
BOUNDED_BY_TOPICS = {"recommendations"}

# Full scans that are the point of the query, with the reason.
INTENDED_SCANS = {
    # The leaderboard ranks everyone by summed reputation: the aggregate reads every
    # event (through ix_reputation_events_user_id), then counts the grouped rows.
    ("leaderboard", "anon_1"),
    # The admin dashboard's headline figures count every account (and the suspended
    # ones); a count over all rows reads them all, and users is a narrow table.
    ("admin dashboard", "users"),
}


class World:
    """A seeded in-memory database with the API on top, recording every query."""

    def __init__(self, factor: int) -> None:
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine, expire_on_commit=False)
        self.data = seed_scale(self.db, factor, PASSWORD)
        application = create_app()
        application.dependency_overrides[get_db] = lambda: self.db
        self.client = TestClient(application)
        self.probe = self._login(PROBE)
        self.admin = self._login(ADMIN)
        self.statements: list[tuple[str, tuple]] = []
        event.listen(self.engine, "before_cursor_execute", self._record)

    def _login(self, username: str) -> dict[str, str]:
        response = self.client.post(
            "/api/v1/accounts/login", json={"username": username, "password": PASSWORD}
        )
        assert response.status_code == 200, response.text
        return {"Authorization": f"Bearer {response.json()['access_token']}"}

    def _record(self, conn, cursor, statement, parameters, context, executemany) -> None:
        self.statements.append((statement, parameters))

    def call(self, hot: HotPath):
        headers = self.admin if hot.as_admin else self.probe
        kwargs = {"params": hot.params} if hot.params else {}
        if hot.json is not None:
            kwargs["json"] = hot.json
        response = getattr(self.client, hot.method)(hot.path, headers=headers, **kwargs)
        assert response.status_code < 300, (hot.name, response.status_code, response.text)
        return response

    def queries(self, hot: HotPath) -> list[tuple[str, tuple]]:
        self.statements.clear()
        self.call(hot)
        return list(self.statements)

    def timed(self, action: Callable[[], object], runs: int = 3) -> float:
        """Median wall time of ``runs`` calls, so one slow tick on CI does not decide."""
        samples = []
        for _ in range(runs):
            started = time.perf_counter()
            action()
            samples.append(time.perf_counter() - started)
        return statistics.median(samples)

    def close(self) -> None:
        self.client.close()
        self.db.close()
        self.engine.dispose()


@pytest.fixture()
def world_factory():
    worlds: list[World] = []

    def build(factor: int) -> World:
        worlds.append(World(factor))
        return worlds[-1]

    yield build
    for w in worlds:
        w.close()


# --------------------------------------------------------------------------- #
# Performance stays acceptable as users and content volume increase
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("factor", [SMALL, LARGE])
def test_NFR_05_hot_paths_stay_inside_the_target_across_the_tested_range(world_factory, factor):
    world = world_factory(factor)
    slow = {}
    for hot in hot_paths(world.data):
        took = world.timed(lambda hot=hot: world.call(hot))
        if took >= TARGET_SECONDS:
            slow[hot.name] = round(took, 3)
    assert slow == {}, f"over {TARGET_SECONDS}s at factor {factor}: {slow}"


@pytest.mark.parametrize("factor", [SMALL, LARGE])
def test_NFR_05_starting_and_submitting_a_quiz_stays_inside_the_target(world_factory, factor):
    world = world_factory(factor)
    quiz = f"/api/v1/quizzes/{world.data.quiz_id}"
    started = time.perf_counter()
    attempt = world.client.post(f"{quiz}/attempts", headers=world.probe)
    assert attempt.status_code == 201, attempt.text
    body = attempt.json()
    answers = {str(q["question_id"]): "A" for q in body["questions"]}
    submitted = world.client.post(
        f"/api/v1/attempts/{body['id']}/submit",
        json={"answers": answers},
        headers=world.probe,
    )
    took = time.perf_counter() - started
    assert submitted.status_code == 200, submitted.text
    assert took < TARGET_SECONDS


def test_NFR_05_queries_per_request_do_not_grow_with_the_data(world_factory):
    small, large = world_factory(SMALL), world_factory(LARGE)
    grew = {}
    for hot_small, hot_large in zip(hot_paths(small.data), hot_paths(large.data), strict=True):
        if hot_small.name in BOUNDED_BY_TOPICS:
            continue
        before, after = len(small.queries(hot_small)), len(large.queries(hot_large))
        if after > before:
            grew[hot_small.name] = (before, after)
    assert grew == {}, f"queries per request grew with 10x the data: {grew}"


def test_NFR_05_lists_cost_a_fixed_number_of_queries():
    """The lists that used to look each row up separately, each now a handful of
    queries at the large end of the range (where they listed 100-500 rows)."""
    world = World(LARGE)
    try:
        budget = {
            "channel directory": 5,
            "channel members": 7,
            "conversation list": 6,
            "moderation queue": 7,
            "performance dashboard": 10,
            "admin dashboard": 6,
        }
        counts = {
            hot.name: len(world.queries(hot)) for hot in hot_paths(world.data) if hot.name in budget
        }
        over = {name: n for name, n in counts.items() if n > budget[name]}
        assert over == {}, over
    finally:
        world.close()


# --------------------------------------------------------------------------- #
# Hot query paths are indexed
# --------------------------------------------------------------------------- #


def _full_scans(world: World, statement: str, parameters) -> set[str]:
    """Tables the query reads end to end without an index (SQLite's query plan)."""
    raw = world.engine.raw_connection()
    try:
        plan = raw.cursor().execute(f"EXPLAIN QUERY PLAN {statement}", parameters).fetchall()
    finally:
        raw.close()
    scans = set()
    for row in plan:
        match = re.fullmatch(r"SCAN (\w+)", row[-1])
        if match:
            scans.add(match.group(1))
    return scans


def test_NFR_05_hot_paths_never_scan_a_growing_table(world_factory):
    world = world_factory(LARGE)
    found = Counter()
    for hot in hot_paths(world.data):
        for statement, parameters in world.queries(hot):
            if not statement.lstrip().upper().startswith("SELECT"):
                continue
            for table in _full_scans(world, statement, parameters):
                if (hot.name, table) not in INTENDED_SCANS:
                    found[(hot.name, table)] += 1
    assert dict(found) == {}, f"full table scans on hot paths: {dict(found)}"


def test_NFR_05_every_foreign_key_is_indexed():
    """Joins and cascading deletes follow foreign keys; unindexed, they scan the table."""
    missing = []
    for table in Base.metadata.sorted_tables:
        leading = {next(iter(i.columns)).name for i in table.indexes}
        leading |= {
            next(iter(c.columns)).name
            for c in table.constraints
            if isinstance(c, UniqueConstraint | PrimaryKeyConstraint) and len(c.columns)
        }
        missing += [
            f"{table.name}.{fk.parent.name}"
            for fk in table.foreign_keys
            if fk.parent.name not in leading
        ]
    assert missing == []


def test_NFR_05_the_moderation_queue_and_report_lookups_have_their_indexes():
    reports = Base.metadata.tables["reports"]
    actions = Base.metadata.tables["moderation_actions"]
    shapes = {
        tuple(c.name for c in index.columns) for t in (reports, actions) for index in t.indexes
    }
    assert ("status", "created_at") in shapes  # the open queue, oldest first
    assert ("target_type", "target_id") in shapes  # every report / action on one item
    channels = Base.metadata.tables["channels"]
    assert any(  # the admin's pending channel requests
        [c.name for c in index.columns] == ["approval_status"] for index in channels.indexes
    )


# --------------------------------------------------------------------------- #
# A new domain can be added without redesigning the core architecture
# --------------------------------------------------------------------------- #


def test_NFR_05_module_dependencies_form_layers_a_new_module_can_sit_on():
    """No dependency cycles: a new feature module can depend on any existing service
    without an existing module having to change (see docs/architecture.md)."""
    visiting, done = set(), set()

    def visit(module: str, path: tuple[str, ...]) -> None:
        assert module not in visiting, f"dependency cycle: {' -> '.join(path + (module,))}"
        if module in done:
            return
        visiting.add(module)
        for dependency in ALLOWED_DEPENDENCIES[module]:
            visit(dependency, path + (module,))
        visiting.discard(module)
        done.add(module)

    for module in ALLOWED_DEPENDENCIES:
        visit(module, ())


def test_the_scale_seed_covers_the_tested_range(world_factory):
    small = world_factory(SMALL).data.counts
    large = world_factory(LARGE).data.counts
    for table in ("users", "materials", "channel_messages", "direct_messages", "attempts"):
        assert large[table] == small[table] * LARGE
