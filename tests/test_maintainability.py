"""NFR-07 - maintainability: modular components with independent deployability (#52).

Verified as the issue asks: change the chat module and confirm quiz scoring needs no
edit and its tests still pass (``test_NFR_07_a_chat_change_needs_no_quiz_edit_and_
quiz_tests_still_pass`` does exactly that on a copy of the repository). Around it:

* the six components the SRS names are separate modules that own their tables;
* what a change to a module can reach is computed from the dependency map and the
  events (``scripts/affected_modules.py``), and quiz scoring is out of chat's reach;
* every module's interface is documented: module docstrings, the architecture table
  and READMEs agree with the code, every public service function has a docstring, and
  ``docs/module-interfaces.md`` is generated from the code and kept current.

``tests/test_module_boundaries.py`` (S00.2) enforces the import rules themselves.
The Test Plan has no NFR-07 case, so tests are named ``test_NFR_07_*``.
"""

import ast
import enum
import inspect
import os
import re
import shutil
import subprocess
import sys
import textwrap
from dataclasses import is_dataclass
from pathlib import Path

import pytest

import app.models  # noqa: F401
from app.core.database import Base
from app.modules import ALLOWED_DEPENDENCIES
from scripts import affected_modules, export_interfaces

ROOT = Path(__file__).resolve().parents[1]
MODULES_DIR = ROOT / "app" / "modules"
# The components NFR-07 names: channels, chat, quizzes, question banks, analytics, LLM.
NAMED_COMPONENTS = ["channels", "chat", "quizzes", "questions", "analytics", "llm"]
QUIZ_SCORING = {"quizzes", "questions"}  # scoring.py and the answer keys it reads


def owned_tables(module: str) -> set[str]:
    models = MODULES_DIR / module / "models.py"
    if not models.exists():
        return set()
    return set(re.findall(r'__tablename__\s*=\s*"(\w+)"', models.read_text()))


def _names(text: str, known: set[str]) -> set[str]:
    """Names from ``known`` in a prose list, ignoring parenthetical remarks."""
    text = re.sub(r"\([^)]*\)", "", text)
    return {w for w in re.findall(r"[a-z_]+", text) if w in known}


def module_doc(module: str) -> str:
    return ast.get_docstring(ast.parse((MODULES_DIR / module / "__init__.py").read_text()))


def doc_field(doc: str, key: str) -> str:
    """The first sentence after ``key:`` in a module docstring."""
    match = re.search(
        rf"{key}:\s*(.*?)(?=\n(?:Owns tables|Public interface|May call|HTTP routes)|\n\n|\Z)",
        doc,
        re.S,
    )
    assert match, f"no '{key}:' line"
    text = re.sub(r"\([^)]*\)", "", " ".join(match.group(1).split()))
    return re.split(r"(?<=\.)\s", text)[0]


# --------------------------------------------------------------------------- #
# Separate modules
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("module", NAMED_COMPONENTS)
def test_NFR_07_each_named_component_is_a_separate_module(module):
    package = MODULES_DIR / module
    assert module in ALLOWED_DEPENDENCIES
    for part in ("__init__.py", "models.py", "service.py", "README.md"):
        assert (package / part).exists(), f"{module} has no {part}"
    assert list(package.glob("*router.py")), f"{module} has no routes of its own"
    assert owned_tables(module), f"{module} owns no tables"


def test_NFR_07_every_table_is_owned_by_exactly_one_module():
    owners: dict[str, list[str]] = {}
    for module in ALLOWED_DEPENDENCIES:
        for table in owned_tables(module):
            owners.setdefault(table, []).append(module)
    assert {t: m for t, m in owners.items() if len(m) > 1} == {}
    assert set(owners) == set(Base.metadata.tables)


# --------------------------------------------------------------------------- #
# A change to one module needs no change to unrelated modules
# --------------------------------------------------------------------------- #


def test_NFR_07_quiz_scoring_is_out_of_reach_of_a_chat_change():
    reached = affected_modules.affected({"chat"})
    assert reached == {"chat", "notifications"}  # notifications reacts to chat's event
    assert not reached & QUIZ_SCORING


def test_NFR_07_quiz_scoring_runs_with_the_chat_module_absent():
    """Quizzes and questions load and score in a process where chat cannot be imported,
    so nothing on the scoring path can depend on chat's code."""
    script = textwrap.dedent(
        """
        import importlib.abc, sys

        class NoChat(importlib.abc.MetaPathFinder):
            def find_spec(self, name, path=None, target=None):
                if name == "app.modules.chat" or name.startswith("app.modules.chat."):
                    raise ImportError(f"chat is not deployed: {name}")
                return None

        sys.meta_path.insert(0, NoChat())
        from app.modules.questions.service import Difficulty, QuestionType, QuestionView
        from app.modules.quizzes import scoring, service
        mcq = QuestionView(1, "DBMS", "Keys", Difficulty.EASY, QuestionType.MULTIPLE_CHOICE,
                           "Which key?", ("A", "B"), "A", None, ())
        assert scoring.score_objective(mcq, " a ", 2.0) == 2.0
        assert scoring.score_objective(mcq, "B", 2.0) == 0.0
        assert not any(m.startswith("app.modules.chat") for m in sys.modules)
        print("scored without chat")
        """
    )
    done = subprocess.run(
        [sys.executable, "-c", script], cwd=ROOT, capture_output=True, text=True, timeout=60
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "scored without chat"


CHAT_CHANGE = '''

def recent_channel_messages(db: Session, channel_id: int, limit: int = 20) -> list[MessageView]:
    """The channel's newest ``limit`` messages, newest first (NFR-07 change drill)."""
    rows = db.scalars(
        select(ChannelMessage)
        .where(ChannelMessage.channel_id == channel_id, ChannelMessage.is_deleted.is_(False))
        .order_by(ChannelMessage.created_at.desc(), ChannelMessage.id.desc())
        .limit(limit)
    )
    return [_view(m) for m in rows]
'''
QUIZ_TESTS = [
    "tests/test_quiz_attempts.py",
    "tests/test_quiz_history.py",
    "tests/test_descriptive_results.py",
    "tests/test_services.py::test_chat_activity_does_not_change_quiz_scores",
]


def _copy_repository(target: Path) -> None:
    for name in ("app", "tests", "scripts", "alembic", "docs"):
        shutil.copytree(
            ROOT / name, target / name, ignore=shutil.ignore_patterns("__pycache__", "*.db")
        )
    for name in ("alembic.ini", "pyproject.toml"):
        shutil.copy2(ROOT / name, target / name)


def _tree(root: Path) -> dict[str, bytes]:
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in (root / "app").rglob("*.py")
        if "__pycache__" not in p.parts
    }


def test_NFR_07_a_chat_change_needs_no_quiz_edit_and_quiz_tests_still_pass(tmp_path):
    """The issue's verification: change the chat module (a new query and a changed
    default in its service), confirm nothing outside chat was edited, and run the quiz
    scoring tests against the changed code."""
    copy = tmp_path / "repo"
    _copy_repository(copy)
    service = copy / "app" / "modules" / "chat" / "service.py"
    source = service.read_text()
    changed = source.replace(
        "def channel_history(db: Session, channel_id: int, limit: int = 50)",
        "def channel_history(db: Session, channel_id: int, limit: int = 100)",
    )
    assert changed != source, "the chat change no longer applies; update this test"
    service.write_text(changed + CHAT_CHANGE)

    before, after = _tree(ROOT), _tree(copy)
    edited = {p for p in after if before.get(p) != after[p]}
    assert edited == {"app/modules/chat/service.py"}
    modules = {affected_modules.module_of(p) for p in edited}
    assert not affected_modules.affected(modules) & QUIZ_SCORING

    env = {**os.environ, "PYTHONPATH": str(copy), "PYTHONDONTWRITEBYTECODE": "1"}
    probe = subprocess.run(
        [
            sys.executable,
            "-c",
            "import app, app.modules.chat.service as c; "
            "print(app.__file__); print(hasattr(c, 'recent_channel_messages'))",
        ],
        cwd=copy,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert probe.stdout.split() == [str(copy / "app" / "__init__.py"), "True"], probe.stderr
    run = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "-W",
            "ignore",
            "tests/test_channel_chat.py",
            *QUIZ_TESTS,
        ],
        cwd=copy,
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert run.returncode == 0, run.stdout[-4000:] + run.stderr[-2000:]


def test_affected_modules_follow_callers_and_event_subscribers():
    assert affected_modules.affected({"moderation"}) >= {"materials", "questions", "chat"}
    reached = affected_modules.affected({"quizzes"})
    assert {"analytics", "llm", "chat", "profiles"} <= reached  # callers and subscribers
    assert "accounts" not in reached and "materials" not in reached
    assert affected_modules.affected({"core"}) == set(ALLOWED_DEPENDENCIES)
    assert affected_modules.module_of("app/modules/chat/router.py") == "chat"
    assert affected_modules.module_of("app/core/events.py") == "core"
    assert affected_modules.module_of("docs/architecture.md") is None


def test_the_event_map_is_read_from_the_code():
    links = affected_modules.event_links(ALLOWED_DEPENDENCIES)
    assert links["chat"] == {"notifications"}
    assert links["quizzes"] == {"analytics", "notifications"}
    assert links["channels"] == {"notifications"}


# --------------------------------------------------------------------------- #
# Module interfaces are documented
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("module", sorted(ALLOWED_DEPENDENCIES))
def test_NFR_07_module_docstring_matches_its_tables_and_dependencies(module):
    doc = module_doc(module)
    tables = doc_field(doc, "Owns tables")
    listed = (
        set()
        if tables.startswith("none")
        else {t.strip(" .`") for t in tables.split(",") if t.strip(" .`")}
    )
    assert listed == owned_tables(module)
    calls = doc_field(doc, "May call the services of")
    assert _names(calls, set(ALLOWED_DEPENDENCIES)) == ALLOWED_DEPENDENCIES[module]


def test_NFR_07_the_architecture_table_matches_the_code():
    text = (ROOT / "docs" / "architecture.md").read_text()
    rows = {}
    for line in text.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) == 4 and cells[0] in ALLOWED_DEPENDENCIES:
            rows[cells[0]] = cells
    assert set(rows) == set(ALLOWED_DEPENDENCIES)
    for module, (_, _, tables, calls) in rows.items():
        documented = (
            set()
            if tables.startswith("none")
            else {t.strip(" `") for t in re.sub(r"\([^)]*\)", "", tables).split(",")}
        )
        assert documented == owned_tables(module), module
        assert _names(calls, set(ALLOWED_DEPENDENCIES)) == ALLOWED_DEPENDENCIES[module], module


@pytest.mark.parametrize("module", sorted(ALLOWED_DEPENDENCIES))
def test_NFR_07_every_module_has_a_readme(module):
    readme = MODULES_DIR / module / "README.md"
    assert readme.exists()
    assert "docs/architecture.md" in readme.read_text()


def _interface(module: str):
    import importlib

    service = importlib.import_module(f"app.modules.{module}.service")
    for name in export_interfaces._public_names(service):
        obj = getattr(service, name)
        if export_interfaces._owned_by(module, obj) or name in export_interfaces._reexported(
            module
        ):
            yield name, obj


@pytest.mark.parametrize("module", sorted(ALLOWED_DEPENDENCIES))
def test_NFR_07_every_public_service_name_is_documented(module):
    missing = []
    for name, obj in _interface(module):
        if inspect.isclass(obj) and issubclass(obj, enum.Enum):
            continue  # an enum documents itself through its values
        if inspect.isfunction(obj) or is_dataclass(obj) or inspect.isclass(obj):
            if not export_interfaces._first_line(obj):
                missing.append(name)
    assert missing == []


def test_NFR_07_orm_models_are_never_part_of_an_interface():
    for module in ALLOWED_DEPENDENCIES:
        for name, obj in _interface(module):
            assert not (inspect.isclass(obj) and issubclass(obj, Base)), f"{module}.{name}"


def test_NFR_07_the_module_interface_reference_is_current():
    committed = (ROOT / "docs" / "module-interfaces.md").read_text()
    assert committed == export_interfaces.render(), (
        "docs/module-interfaces.md is out of date: run python scripts/export_interfaces.py"
    )
    for module in ALLOWED_DEPENDENCIES:
        assert f"\n## {module}\n" in committed
