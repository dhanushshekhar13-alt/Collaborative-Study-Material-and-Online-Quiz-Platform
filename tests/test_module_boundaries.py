"""S00.2: each module owns its data and only uses other modules' public services."""

import ast
from pathlib import Path

import pytest

from app.modules import ALLOWED_DEPENDENCIES

MODULES_DIR = Path(__file__).resolve().parent.parent / "app" / "modules"


def _imports(module: str) -> set[str]:
    found: set[str] = set()
    for path in (MODULES_DIR / module).rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import):
                found.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                found.update(f"{node.module}.{alias.name}" for alias in node.names)
    return {name for name in found if name.startswith("app.modules.")}


def test_every_module_is_registered():
    packages = {p.name for p in MODULES_DIR.iterdir() if (p / "__init__.py").exists()}
    assert packages == set(ALLOWED_DEPENDENCIES)


@pytest.mark.parametrize("module", sorted(ALLOWED_DEPENDENCIES))
def test_module_has_documented_interface(module):
    package = MODULES_DIR / module
    assert (package / "service.py").exists()
    doc = ast.get_docstring(ast.parse((package / "__init__.py").read_text()))
    assert doc and "Owns tables" in doc and "Public interface" in doc


@pytest.mark.parametrize("module", sorted(ALLOWED_DEPENDENCIES))
def test_module_imports_only_allowed_services(module):
    for name in _imports(module):
        other = name.split(".")[2]
        if other == module:
            continue
        assert other in ALLOWED_DEPENDENCIES[module], f"{module} imports {name}"
        assert name.startswith(f"app.modules.{other}.service"), f"{module} imports {name}"


def _reachable(module: str) -> set[str]:
    seen, stack = set(), [module]
    while stack:
        for dep in ALLOWED_DEPENDENCIES[stack.pop()]:
            if dep not in seen:
                seen.add(dep)
                stack.append(dep)
    return seen


def test_quiz_scoring_does_not_depend_on_chat():
    """A change to the chat module can never require a change to quiz scoring."""
    assert "chat" not in _reachable("quizzes")
    assert "chat" not in _reachable("questions")


def test_no_module_imports_chat():
    assert not [m for m, deps in ALLOWED_DEPENDENCIES.items() if "chat" in _reachable(m)]
