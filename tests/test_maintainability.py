"""NFR-07 (#52): modular components that change and deploy independently.

The Test Plan has no case of its own for NFR-07; the issue's "How it is verified"
(change the chat module and confirm quiz scoring needs no edit and its tests still
pass) is automated here, together with checks that the named components are separate
modules and that their interfaces are documented. The import rules themselves are in
``tests/test_module_boundaries.py``.
"""

import ast
import importlib
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from app.core.database import Base
from app.main import API_PREFIX, MODULE_ROUTERS, create_app, served_modules
from app.models import load_models
from app.modules import ALLOWED_DEPENDENCIES, required_modules
from scripts.export_module_interfaces import REFERENCE, owned_tables, render

ROOT = Path(__file__).resolve().parent.parent
MODULES_DIR = ROOT / "app" / "modules"

# The components NFR-07 names: channels, chat, quizzes, question bank, analytics, LLM.
NAMED_COMPONENTS = ["channels", "chat", "quizzes", "questions", "analytics", "llm"]

# Quiz scoring's own tests: building quizzes and taking and scoring attempts.
QUIZ_SCORING_TESTS = ["tests/test_quiz_attempts.py", "tests/test_quiz_building.py"]


def _table_owners() -> dict[str, str]:
    load_models()
    return {table: module for module in ALLOWED_DEPENDENCIES for table in owned_tables(module)}


# --- AC1: the named components are separate modules ---------------------------------


@pytest.mark.parametrize("module", NAMED_COMPONENTS)
def test_NFR_07_named_component_is_a_separate_module(module):
    package = MODULES_DIR / module
    for part in ("__init__.py", "models.py", "service.py"):
        assert (package / part).exists(), f"{module} has no {part}"
    assert owned_tables(module), f"{module} owns no tables"
    assert any(owner == module for owner, _ in MODULE_ROUTERS), f"{module} mounts no routes"


def test_NFR_07_every_table_belongs_to_exactly_one_module():
    declared = [table for module in ALLOWED_DEPENDENCIES for table in owned_tables(module)]
    assert len(declared) == len(set(declared)), "a table is declared by two modules"
    owners = _table_owners()
    assert set(Base.metadata.tables) == set(owners)


def test_NFR_07_each_module_documents_the_tables_it_owns():
    for module in ALLOWED_DEPENDENCIES:
        doc = ast.get_docstring(ast.parse((MODULES_DIR / module / "__init__.py").read_text()))
        owns = doc.split("Owns tables:")[1].split("Public interface")[0]
        owns = owns.split("\n\n")[0].replace("\n", " ")
        tables = owned_tables(module)
        if not tables:
            assert owns.strip().startswith("none"), module
        for table in tables:
            assert table in owns.replace(",", " ").replace(".", " ").split(), (module, table)


def test_NFR_07_every_router_is_mounted_by_the_module_that_owns_it():
    files = {
        f"app.modules.{path.parent.name}.{path.stem}" for path in MODULES_DIR.glob("*/*router.py")
    }
    assert files == {path for _, path in MODULE_ROUTERS}
    for module, path in MODULE_ROUTERS:
        assert path.startswith(f"app.modules.{module}."), path


def test_NFR_07_each_deployable_module_set_holds_the_tables_it_references():
    """Serving a module with the modules it may call gives it every table it points at."""
    owners = _table_owners()
    for module in {owner for owner, _ in MODULE_ROUTERS}:
        served = required_modules({module})
        for name, table in Base.metadata.tables.items():
            if owners[name] not in served:
                continue
            for fk in table.foreign_keys:
                target = owners[fk.column.table.name]
                assert target in served, f"serving {module}: {name} references {target}"


# --- AC2: a change to one module deploys without changes to unrelated modules -------


def _route_paths(application) -> set[str]:
    return {path for path in application.openapi()["paths"] if path.startswith(API_PREFIX)}


def _module_paths(module: str) -> set[str]:
    return {
        API_PREFIX + route.path
        for owner, path in MODULE_ROUTERS
        if owner == module
        for route in importlib.import_module(path).router.routes
        if getattr(route, "include_in_schema", True)
    }


@pytest.mark.parametrize("module", sorted(ALLOWED_DEPENDENCIES))
def test_NFR_07_each_module_can_be_served_with_only_its_dependencies(module):
    served = served_modules([module])
    assert served == required_modules({module})
    application = create_app([module])
    assert application.state.modules == served
    expected = set().union(*(_module_paths(m) for m in served))
    assert _route_paths(application) == expected


def test_NFR_07_serving_quizzes_leaves_out_chat_analytics_and_llm():
    served = served_modules(["quizzes"])
    assert {"quizzes", "questions", "channels", "accounts"} <= served
    assert not served & {"chat", "analytics", "llm", "materials", "admin", "profiles"}


def test_NFR_07_enabled_modules_setting_selects_what_is_served(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "enabled_modules", " quizzes , ")
    assert create_app().state.modules == required_modules({"quizzes"})
    monkeypatch.setattr(settings, "enabled_modules", "")
    assert create_app().state.modules == set(ALLOWED_DEPENDENCIES)
    with pytest.raises(ValueError, match="Unknown modules: chatt"):
        create_app(["chatt"])


def _run_pytest(cwd: Path, tests: list[str], env_extra: dict[str, str]):
    env = dict(os.environ)
    env.pop("ENABLED_MODULES", None)
    env.update(env_extra)
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *tests],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
    )


def test_NFR_07_quiz_scoring_tests_pass_with_chat_unavailable(tmp_path):
    """Serve quizzes alone, make every module it does not need unimportable, and run
    quiz scoring's own tests unchanged: chat's code is never needed."""
    blocked = sorted(set(ALLOWED_DEPENDENCIES) - required_modules({"quizzes"}))
    assert "chat" in blocked
    (tmp_path / "block_unrelated_modules.py").write_text(
        textwrap.dedent(
            f"""
            import sys

            BLOCKED = {tuple(f"app.modules.{m}" for m in blocked)!r}

            class Blocker:
                def find_spec(self, name, path=None, target=None):
                    if name.startswith(BLOCKED):
                        raise ImportError(f"{{name}} is not deployed")
                    return None

            sys.meta_path.insert(0, Blocker())

            def pytest_sessionfinish(session):
                loaded = [m for m in sys.modules if m.startswith(BLOCKED)]
                assert not loaded, loaded
            """
        )
    )
    result = _run_pytest(
        ROOT,
        ["-p", "block_unrelated_modules", *QUIZ_SCORING_TESTS],
        {
            "ENABLED_MODULES": "quizzes",
            "PYTHONPATH": os.pathsep.join([str(tmp_path), str(ROOT)]),
        },
    )
    assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-3000:]


CHAT_CHANGE = """

# --- NFR-07 check: a new chat release that changes chat's behaviour and schema. ---
from sqlalchemy import String  # noqa: E402
from sqlalchemy.orm import mapped_column  # noqa: E402

ChannelMessage.edited_note = mapped_column(String(80), nullable=True)
"""

CHAT_SERVICE_CHANGE = """

# --- NFR-07 check: chat now refuses every message (a deliberately visible change). ---
def post_channel_message(db, **kwargs):  # noqa: F811
    raise RuntimeError("chat is being redeployed")
"""


def test_NFR_07_changing_chat_needs_no_change_to_quiz_scoring(tmp_path):
    """The issue's check: change chat in a copy of the project, confirm nothing outside
    chat was edited, that the change is real (chat's own tests notice it) and that quiz
    scoring's tests pass unchanged with every module served."""
    copy = tmp_path / "project"
    ignore = shutil.ignore_patterns(".git", "__pycache__", "*.db", ".pytest_cache", ".ruff_cache")
    for part in ("app", "tests", "scripts", "alembic", "docs"):
        shutil.copytree(ROOT / part, copy / part, ignore=ignore)
    for part in ("pyproject.toml", "alembic.ini"):
        shutil.copy2(ROOT / part, copy / part)

    with (copy / "app/modules/chat/models.py").open("a") as handle:
        handle.write(CHAT_CHANGE)
    with (copy / "app/modules/chat/service.py").open("a") as handle:
        handle.write(CHAT_SERVICE_CHANGE)

    changed = sorted(
        str(path.relative_to(copy))
        for path in copy.rglob("*.py")
        if path.read_bytes() != (ROOT / path.relative_to(copy)).read_bytes()
    )
    assert changed == ["app/modules/chat/models.py", "app/modules/chat/service.py"]

    env = {"PYTHONPATH": str(copy)}
    chat = _run_pytest(copy, ["-x", "tests/test_channel_chat.py"], env)
    assert chat.returncode != 0, "the chat change should be visible to chat's own tests"

    scoring = _run_pytest(copy, QUIZ_SCORING_TESTS, env)
    assert scoring.returncode == 0, scoring.stdout[-3000:] + scoring.stderr[-3000:]


# --- AC3: module interfaces are documented in the repo ------------------------------


def test_NFR_07_interface_reference_matches_the_code():
    assert REFERENCE.exists(), "run python scripts/export_module_interfaces.py"
    assert REFERENCE.read_text() == render(), (
        "a module interface changed: run python scripts/export_module_interfaces.py"
    )


@pytest.mark.parametrize("module", sorted(ALLOWED_DEPENDENCIES))
def test_NFR_07_every_public_service_function_is_documented(module):
    tree = ast.parse((MODULES_DIR / module / "service.py").read_text())
    undocumented = [
        node.name
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and not node.name.startswith("_")
        and not ast.get_docstring(node)
    ]
    assert not undocumented, f"{module}.service: {undocumented}"


def test_NFR_07_reference_covers_every_module():
    text = REFERENCE.read_text()
    for module in ALLOWED_DEPENDENCIES:
        assert f"\n## {module}\n" in text
    assert "docs/modules.md" in (ROOT / "docs" / "architecture.md").read_text()
