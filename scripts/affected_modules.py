"""Which modules a change can affect, and which focused tests cover them (NFR-07).

A module can be affected by a change in two ways only:

* it calls the changed module's service (``ALLOWED_DEPENDENCIES``, enforced by
  ``tests/test_module_boundaries.py``), directly or through another module, so its
  code runs the changed code; or
* it subscribes to an event the changed module (or one of those callers) publishes
  (``app/core/events.py``), so its handler receives the changed module's payload.

Anything else cannot see the change, so it needs no edit and no redeploy of its own
code. Usage::

    python -m scripts.affected_modules app/modules/chat/service.py
    python -m scripts.affected_modules --base origin/main     # files changed on this branch

It prints the changed modules, every module the change can reach, the modules it
cannot reach, and the test files that exercise the affected modules directly. CI still
runs the whole suite; this is for reviewers and for a quick local check.
"""

import argparse
import ast
import subprocess
import sys
from collections.abc import Iterable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULES_DIR = ROOT / "app" / "modules"
TESTS_DIR = ROOT / "tests"
SHARED = "core"  # app/core: shared by every module, so a change there reaches all of them


def _dependencies() -> dict[str, set[str]]:
    sys.path.insert(0, str(ROOT))
    from app.modules import ALLOWED_DEPENDENCIES

    return {m: set(deps) for m, deps in ALLOWED_DEPENDENCIES.items()}


def module_of(path: str | Path) -> str | None:
    """The feature module a repository path belongs to, ``"core"`` for ``app/core``,
    or ``None`` for anything outside the modules (docs, tests, the web UI)."""
    parts = Path(path).as_posix().split("/")
    if parts[:2] == ["app", "modules"] and len(parts) > 3:
        return parts[2]
    if parts[:2] == ["app", "core"]:
        return SHARED
    return None


def _event_calls(module: str, kind: str) -> set[str]:
    """Event names a module publishes or subscribes to, read from its source."""
    found = set()
    for path in (MODULES_DIR / module).rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == kind
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "events"
                and node.args
            ):
                event = node.args[0]
                if isinstance(event, ast.Attribute):
                    found.add(event.attr)
                elif isinstance(event, ast.Name):
                    found.add(event.id)
    return found


def event_links(modules: Iterable[str]) -> dict[str, set[str]]:
    """For each module, the modules that subscribe to an event it publishes."""
    modules = list(modules)
    published = {m: _event_calls(m, "publish") for m in modules}
    subscribed = {m: _event_calls(m, "subscribe") for m in modules}
    return {m: {s for s in modules if s != m and published[m] & subscribed[s]} for m in modules}


def affected(changed: Iterable[str], deps: dict[str, set[str]] | None = None) -> set[str]:
    """Every module whose behaviour a change to ``changed`` modules can alter.

    That is the changed modules, every module that calls them directly or through
    another module (their code runs the changed code), and the subscribers of events
    those modules publish (their handlers receive its payload). A subscriber's own
    callers are not included: they never run the publisher's code. A change to
    ``app/core`` reaches every module.
    """
    deps = deps or _dependencies()
    changed = set(changed)
    if SHARED in changed:
        return set(deps)
    callers, todo = set(), [m for m in changed if m in deps]
    while todo:
        module = todo.pop()
        if module not in callers:
            callers.add(module)
            todo += [m for m, uses in deps.items() if module in uses]
    links = event_links(deps)
    return callers | {s for m in callers for s in links[m]}


def tests_for(modules: Iterable[str]) -> list[str]:
    """Test files that import one of ``modules`` directly."""
    wanted = set(modules)
    picked = []
    for path in sorted(TESTS_DIR.glob("test_*.py")):
        names = set()
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom) and node.module:
                names.add(node.module)
                names.update(f"{node.module}.{a.name}" for a in node.names)
            elif isinstance(node, ast.Import):
                names.update(a.name for a in node.names)
        used = {
            n.split(".")[2] for n in names if n.startswith("app.modules.") and n.count(".") >= 2
        }
        if used & wanted:
            picked.append(path.relative_to(ROOT).as_posix())
    return picked


def changed_files(base: str) -> list[str]:
    out = subprocess.run(
        ["git", "diff", "--name-only", f"{base}...HEAD"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return [line for line in out.stdout.splitlines() if line]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("paths", nargs="*", help="changed files")
    parser.add_argument("--base", help="compare HEAD with this git ref instead")
    args = parser.parse_args(argv)
    paths = changed_files(args.base) if args.base else args.paths
    deps = _dependencies()
    changed = {m for m in map(module_of, paths) if m}
    reached = affected(changed, deps)
    print("Changed modules:   ", ", ".join(sorted(changed)) or "none")
    print("Can be affected:   ", ", ".join(sorted(reached)) or "none")
    print("Cannot be affected:", ", ".join(sorted(set(deps) - reached)) or "none")
    for test in tests_for(reached):
        print("  test:", test)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
