"""NFR-01 load test: common operations stay inside their response-time targets.

The targets are the SRS's NFR-01 numbers, written down here (and in
``docs/performance.md``) before anything is measured:

* page loads ............................ p95 <= 2 s
* search and filter ..................... p95 <= 2 s
* quiz submission with result display ... p95 <= 3 s

measured at the 95th percentile under normal load: up to 50 concurrent users.
Operations that wait on the external LLM (question generation, descriptive evaluation,
Ask AI) are excluded, as the SRS says.

What one "operation" is
-----------------------
An operation is what a user waits for, so it is timed the way the web UI performs it:
a page load is every API call the page makes before it can render (calls the UI fires
together with ``Promise.all`` run concurrently here too), and a quiz submission is
``POST /attempts/{id}/submit`` followed by the result page's ``GET /attempts/{id}``.

How the load is applied
-----------------------
``CONCURRENT_USERS`` virtual students sign in and then all act at once. Each one works
through every operation in ``OPERATIONS`` (in its own shuffled order), pausing
``think`` seconds between operations, for ``rounds`` rounds. Requests go through the
real ASGI app with real per-request database sessions (``get_db``) on a file database,
and synchronous endpoints run on the same 40-thread pool uvicorn uses, so requests
genuinely queue behind each other. The client runs in the same process, so the
numbers include its overhead: they are an upper bound for one server worker.

Run it::

    python -m scripts.load_test                    # SQLite file in a temp dir
    python -m scripts.load_test --database-url postgresql+psycopg://...  # empty DB!
    python -m scripts.load_test --rounds 5 --think 0   # harsher: no pause at all

It prints a per-operation table and exits non-zero if any target is missed.
``tests/test_performance.py`` runs the same thing in CI.
"""

from __future__ import annotations

import argparse
import asyncio
import math
import random
import statistics
import sys
import tempfile
import time
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import httpx
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

API = "/api/v1"

# --------------------------------------------------------------------- targets ----

PAGE_LOAD = "page load"
SEARCH = "search/filter"
QUIZ_SUBMISSION = "quiz submission"

# NFR-01 (SRS): seconds, at the 95th percentile, under normal load.
TARGETS: dict[str, float] = {PAGE_LOAD: 2.0, SEARCH: 2.0, QUIZ_SUBMISSION: 3.0}
PERCENTILE = 95
CONCURRENT_USERS = 50  # "normal load: up to 50 concurrent users"

# ---------------------------------------------------------------- normal data ----

# What a class-sized deployment holds after a semester, on top of the 50 students.
SUBJECTS = ["DBMS", "Operating Systems", "Computer Networks", "Algorithms"]
TOPICS = ["Basics", "Normalization", "Scheduling", "Routing", "Sorting", "Graphs"]
CHANNELS = 8
MATERIALS = 400
QUESTIONS = 400
QUIZZES = 24
QUESTIONS_PER_QUIZ = 10
PAST_ATTEMPTS_PER_USER = 3
CHANNEL_MESSAGES = 400
OPTIONS = ["A", "B", "C", "D"]


@dataclass
class World:
    """The ids the operations need, created by ``seed_normal_load``."""

    tokens: list[str]
    channel_ids: list[int]
    quiz_ids: list[int]
    quiz_questions: dict[int, list[int]]
    material_ids: list[int]  # public ones, readable by every student


def seed_normal_load(db: Session, users: int = CONCURRENT_USERS) -> World:
    """Create the normal-load dataset through the module services and commit it."""
    import app.models  # noqa: F401  (register every table and event handler)
    from app.modules.accounts import service as accounts
    from app.modules.accounts.models import Role
    from app.modules.accounts.tokens import create_access_token
    from app.modules.channels import service as channels
    from app.modules.channels.models import ApprovalStatus, ChannelType
    from app.modules.chat import service as chat
    from app.modules.materials import service as materials
    from app.modules.materials.models import MaterialType
    from app.modules.questions import service as questions
    from app.modules.questions.models import Difficulty, QuestionType
    from app.modules.quizzes import service as quizzes
    from app.modules.reputation import service as reputation

    rng = random.Random(46)
    # One hashed password is reused: hashing is deliberately slow and is not what is
    # being measured (sign-in happens once per session, not per operation).
    faculty = accounts.create_user(
        db,
        email="load.faculty@example.com",
        display_name="Load Faculty",
        password="Load@12345",
        role=Role.AUTHORIZED_USER,
        username="load_faculty",
    )
    students = [
        accounts.create_user(
            db,
            email=f"load.student{i}@example.com",
            display_name=f"Load Student {i}",
            password="Load@12345",
            username=f"load_student{i}",
        )
        for i in range(users)
    ]

    channel_ids = []
    for i in range(CHANNELS):
        official = i % 2 == 0
        channel = channels.create_channel(
            db,
            name=f"{SUBJECTS[i % len(SUBJECTS)]} group {i}",
            created_by=faculty.id if official else students[i].id,
            channel_type=ChannelType.AUTHORIZED if official else ChannelType.NORMAL,
            subject=SUBJECTS[i % len(SUBJECTS)],
            topic=TOPICS[i % len(TOPICS)],
        )
        channels.set_approval(db, channel.id, ApprovalStatus.APPROVED)
        channel_ids.append(channel.id)
    for n, student in enumerate(students):
        for channel_id in {channel_ids[n % CHANNELS], channel_ids[(n * 3 + 1) % CHANNELS]}:
            if not channels.is_member(db, channel_id, student.id):
                channels.add_member(db, channel_id, student.id)

    material_ids = [  # every third one is in a channel; the rest are public
        materials.create_material(
            db,
            uploader_id=rng.choice(students).id,
            title=f"Study notes {i}: {TOPICS[i % len(TOPICS)]}",
            material_type=MaterialType.LINK,
            subject=SUBJECTS[i % len(SUBJECTS)],
            topic=TOPICS[i % len(TOPICS)],
            semester=str(1 + i % 8),
            channel_id=channel_ids[i % CHANNELS] if i % 3 == 0 else None,
            url=f"https://example.com/notes/{i}",
            description=f"Worked examples for {TOPICS[i % len(TOPICS)].lower()}.",
        ).id
        for i in range(MATERIALS)
    ]

    question_ids_by_subject: dict[str, list[int]] = defaultdict(list)
    for i in range(QUESTIONS):
        subject = SUBJECTS[i % len(SUBJECTS)]
        question_ids_by_subject[subject].append(
            questions.create_question(
                db,
                author_id=faculty.id if i % 2 else rng.choice(students).id,
                subject=subject,
                topic=TOPICS[i % len(TOPICS)],
                difficulty=list(Difficulty)[i % 3],
                question_type=QuestionType.MULTIPLE_CHOICE,
                text=f"{subject} question {i}: which option is correct?",
                options=OPTIONS,
                correct_answer="A",
                explanation=f"A is correct for question {i}.",
            ).id
        )

    quiz_ids, quiz_questions = [], {}
    for i in range(QUIZZES):
        subject = SUBJECTS[i % len(SUBJECTS)]
        picked = rng.sample(question_ids_by_subject[subject], QUESTIONS_PER_QUIZ)
        quiz_id = quizzes.create_quiz(
            db,
            creator_id=faculty.id,
            title=f"{subject} practice quiz {i}",
            subject=subject,
            topic=TOPICS[i % len(TOPICS)],
            difficulty="mixed",
            duration_minutes=30,
            question_ids=picked,
            publish=True,
        )
        quiz_ids.append(quiz_id)
        quiz_questions[quiz_id] = picked
    db.flush()

    for student in students:  # past attempts feed history, analytics, recommendations
        for quiz_id in rng.sample(quiz_ids, PAST_ATTEMPTS_PER_USER):
            attempt = quizzes.start_attempt(db, quiz_id, student.id)
            answers = {q: rng.choice(OPTIONS) for q in quiz_questions[quiz_id]}
            quizzes.submit_attempt(db, attempt, answers)

    for i in range(CHANNEL_MESSAGES):
        n = i % len(students)
        chat.post_channel_message(
            db,
            channel_id=channel_ids[n % CHANNELS],
            sender_id=students[n].id,
            body=f"Message {i} about {TOPICS[i % len(TOPICS)]}",
        )
    for student in students:
        reputation.award(
            db,
            user_id=student.id,
            points=rng.randint(1, 60),
            reason="material_shared",
            source_type="material",
            source_id=rng.choice(material_ids),
        )
    db.commit()

    tokens = [create_access_token(s.id, s.role.value) for s in students]
    public = [m for i, m in enumerate(material_ids) if i % 3]
    return World(tokens, channel_ids, quiz_ids, quiz_questions, public)


# ------------------------------------------------------------------ operations ----

Step = list[tuple[str, str, dict | None]]  # requests fired together: (method, path, json)


@dataclass
class Operation:
    name: str
    category: str
    # Builds the steps for one user; steps run one after another and may depend on
    # the previous step's responses (the quiz flow needs the attempt id).
    plan: Callable[[World, random.Random, int], list[Step]]


def _get(*paths: str) -> Step:
    return [("GET", f"{API}{p}", None) for p in paths]


SESSION = _get("/accounts/me", "/accounts/me/permissions", "/notifications/unread-count")


def _submit(world: World, attempt: dict) -> list[Step]:
    answers = {str(q["question_id"]): "A" for q in attempt["questions"]}
    return [
        [("POST", f"{API}/attempts/{attempt['id']}/submit", {"answers": answers, "confirm": True})],
        _get(f"/attempts/{attempt['id']}"),  # the result page
    ]


OPERATIONS: list[Operation] = [
    # Page loads: the calls each UI page makes before it renders (app/web/static).
    Operation(
        "Dashboard",
        PAGE_LOAD,
        lambda w, r, u: [SESSION, _get("/profiles/me", "/channels/mine", "/analytics/dashboard")],
    ),
    Operation(
        "My performance",
        PAGE_LOAD,
        lambda w, r, u: [_get("/analytics/dashboard", "/analytics/recommendations?limit=5")],
    ),
    Operation(
        "Recommended for you", PAGE_LOAD, lambda w, r, u: [_get("/analytics/recommendations")]
    ),
    Operation("Profile", PAGE_LOAD, lambda w, r, u: [_get("/profiles/me")]),
    Operation("Channel directory", PAGE_LOAD, lambda w, r, u: [_get("/channels")]),
    Operation(
        "Channel page",
        PAGE_LOAD,
        lambda w, r, u: [
            _get(f"/channels/{w.channel_ids[u % CHANNELS]}"),
            _get(
                f"/channels/{w.channel_ids[u % CHANNELS]}/members",
                f"/channels/{w.channel_ids[u % CHANNELS]}/messages?limit=50",
            ),
        ],
    ),
    Operation(
        "Materials",
        PAGE_LOAD,
        lambda w, r, u: [_get("/channels/mine"), _get("/materials?limit=50")],
    ),
    Operation(
        "Material detail",
        PAGE_LOAD,
        lambda w, r, u: [
            _get(f"/materials/{(m := r.choice(w.material_ids))}"),
            _get(f"/materials/{m}/reviews"),
        ],
    ),
    Operation(
        "Question bank",
        PAGE_LOAD,
        lambda w, r, u: [_get("/channels/mine"), _get("/questions?limit=50")],
    ),
    Operation(
        "Quizzes",
        PAGE_LOAD,
        lambda w, r, u: [_get("/channels/mine"), _get("/quizzes", "/quizzes?mine=true")],
    ),
    Operation("Quiz history", PAGE_LOAD, lambda w, r, u: [_get("/attempts")]),
    Operation("Leaderboard", PAGE_LOAD, lambda w, r, u: [_get("/leaderboard?limit=50")]),
    Operation(
        "Notifications",
        PAGE_LOAD,
        lambda w, r, u: [_get("/notifications?limit=50", "/notifications/preferences")],
    ),
    Operation(
        "Start a quiz",
        PAGE_LOAD,
        lambda w, r, u: [[("POST", f"{API}/quizzes/{r.choice(w.quiz_ids)}/attempts", None)]],
    ),
    # Search and filter.
    Operation(
        "Material search",
        SEARCH,
        lambda w, r, u: [_get(f"/materials?limit=50&q={r.choice(TOPICS).lower()}&subject=DBMS")],
    ),
    Operation(
        "Material filter",
        SEARCH,
        lambda w, r, u: [_get(f"/materials?limit=50&semester={r.randint(1, 8)}&type=link")],
    ),
    Operation(
        "Question search",
        SEARCH,
        lambda w, r, u: [
            _get(f"/questions?limit=50&q=question&subject={r.choice(SUBJECTS)}&difficulty=easy")
        ],
    ),
    Operation(
        "Channel search", SEARCH, lambda w, r, u: [_get(f"/channels?q={r.choice(SUBJECTS)}")]
    ),
    # Quiz submission: start (not timed), then submit + result page (timed).
    Operation(
        "Quiz submission",
        QUIZ_SUBMISSION,
        lambda w, r, u: [],  # see run_user: the attempt is started before the clock runs
    ),
]


# ---------------------------------------------------------------------- runner ----


@dataclass
class Report:
    samples: dict[str, list[float]] = field(default_factory=lambda: defaultdict(list))
    failures: list[str] = field(default_factory=list)

    def by_category(self) -> dict[str, list[float]]:
        out: dict[str, list[float]] = defaultdict(list)
        for op in OPERATIONS:
            out[op.category].extend(self.samples.get(op.name, []))
        return out

    def missed(self) -> list[str]:
        """Every target missed: by category, and by any single operation."""
        problems = []
        for category, values in self.by_category().items():
            if p95(values) > TARGETS[category]:
                problems.append(f"{category}: p95 {p95(values):.3f}s > {TARGETS[category]}s")
        for op in OPERATIONS:
            values = self.samples.get(op.name, [])
            if values and p95(values) > TARGETS[op.category]:
                problems.append(f"{op.name}: p95 {p95(values):.3f}s > {TARGETS[op.category]}s")
        return problems + self.failures

    def table(self) -> str:
        rows = [
            "| Operation | Category | Samples | Median (s) | p95 (s) | Max (s) "
            "| Target p95 (s) | Result |",
            "| --- | --- | ---: | ---: | ---: | ---: | ---: | --- |",
        ]

        def row(name: str, category: str, values: list[float]) -> str:
            ok = "pass" if p95(values) <= TARGETS[category] else "FAIL"
            return (
                f"| {name} | {category} | {len(values)} | {statistics.median(values):.3f} "
                f"| {p95(values):.3f} | {max(values):.3f} | {TARGETS[category]:.1f} | {ok} |"
            )

        for op in OPERATIONS:
            if self.samples.get(op.name):
                rows.append(row(op.name, op.category, self.samples[op.name]))
        for category, values in self.by_category().items():
            if values:
                rows.append(row(f"**All {category}**", category, values))
        return "\n".join(rows)


def p95(values: list[float], percentile: int = PERCENTILE) -> float:
    """Nearest-rank percentile: the smallest value at least ``percentile``% are <=."""
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[max(0, math.ceil(percentile / 100 * len(ordered)) - 1)]


async def _step(client: httpx.AsyncClient, step: Step, token: str, report: Report, op: str):
    headers = {"Authorization": f"Bearer {token}"}
    responses = await asyncio.gather(
        *(client.request(method, path, json=body, headers=headers) for method, path, body in step)
    )
    for (method, path, _), response in zip(step, responses, strict=True):
        if response.status_code >= 400:
            report.failures.append(f"{op}: {method} {path} -> {response.status_code}")
    return responses


async def run_user(
    client: httpx.AsyncClient,
    world: World,
    user: int,
    rounds: int,
    think: float,
    report: Report,
    start_gate: asyncio.Event,
) -> None:
    rng = random.Random(1000 + user)
    token = world.tokens[user]
    await start_gate.wait()
    for _ in range(rounds):
        ops = OPERATIONS[:]
        rng.shuffle(ops)
        for op in ops:
            if think:
                await asyncio.sleep(rng.uniform(0.5 * think, 1.5 * think))
            if op.category == QUIZ_SUBMISSION:
                quiz_id = rng.choice(world.quiz_ids)
                (started,) = await _step(
                    client,
                    [("POST", f"{API}/quizzes/{quiz_id}/attempts", None)],
                    token,
                    report,
                    op.name,
                )
                if started.status_code >= 400:
                    continue
                steps = _submit(world, started.json())
            else:
                steps = op.plan(world, rng, user)
            began = time.perf_counter()
            for step in steps:
                await _step(client, step, token, report, op.name)
            report.samples[op.name].append(time.perf_counter() - began)


async def run_load(
    app,
    world: World,
    *,
    users: int = CONCURRENT_USERS,
    rounds: int = 1,
    think: float = 1.0,
) -> Report:
    """Run ``users`` concurrent virtual users against ``app``; returns the timings."""
    report = Report()
    gate = asyncio.Event()
    transport = httpx.ASGITransport(app=app)
    limits = httpx.Limits(max_connections=None)
    async with httpx.AsyncClient(transport=transport, base_url="http://load", limits=limits) as c:
        tasks = [
            asyncio.create_task(run_user(c, world, u, rounds, think, report, gate))
            for u in range(users)
        ]
        gate.set()  # everyone starts together
        await asyncio.gather(*tasks)
    return report


def prepare(database_url: str):
    """Create the schema and data on ``database_url`` and point the app at it."""
    import app.models  # noqa: F401
    from app.core import database
    from app.main import create_app

    options = database.engine_options(database_url)  # the server's own pool sizing
    engine = create_engine(database_url, **options)
    database.Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    database.SessionLocal = factory  # get_db opens one session per request from here
    with factory() as db:
        world = seed_normal_load(db)
    return create_app(), world, engine


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--database-url", help="an EMPTY database; default: temp SQLite file")
    parser.add_argument("--users", type=int, default=CONCURRENT_USERS)
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--think", type=float, default=1.0, help="mean pause between ops (s)")
    args = parser.parse_args(argv)

    from pydantic import SecretStr

    from app.core.config import settings

    if settings.auth_token_secret is None:
        settings.auth_token_secret = SecretStr("load-test-only")

    with tempfile.TemporaryDirectory() as tmp:
        url = args.database_url or f"sqlite:///{Path(tmp) / 'load.db'}"
        app, world, engine = prepare(url)
        began = time.perf_counter()
        report = asyncio.run(
            run_load(app, world, users=args.users, rounds=args.rounds, think=args.think)
        )
        elapsed = time.perf_counter() - began
        engine.dispose()

    total = sum(len(v) for v in report.samples.values())
    print(f"{args.users} concurrent users, {args.rounds} rounds, think {args.think}s: ")
    print(f"{total} operations in {elapsed:.1f}s\n")
    print(report.table())
    problems = report.missed()
    for problem in problems:
        print(f"MISSED: {problem}", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
