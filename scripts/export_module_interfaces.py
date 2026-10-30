"""Write the module interface reference to docs/modules.md (NFR-07).

Run after changing a module's public service, routes, events or dependencies:
``python scripts/export_module_interfaces.py``. ``tests/test_maintainability.py``
fails if the committed reference and the code disagree, so an interface change is
always visible in review.

Everything except the routes is read from the source with ``ast``, without importing
the modules.
"""

import ast
import importlib
from pathlib import Path

from app.main import API_PREFIX, MODULE_ROUTERS
from app.modules import ALLOWED_DEPENDENCIES

ROOT = Path(__file__).resolve().parent.parent
MODULES_DIR = ROOT / "app" / "modules"
REFERENCE = ROOT / "docs" / "modules.md"


def _parse(path: Path) -> ast.Module | None:
    return ast.parse(path.read_text()) if path.exists() else None


def owned_tables(module: str) -> list[str]:
    """Table names declared in the module's ``models.py``."""
    tree = _parse(MODULES_DIR / module / "models.py")
    if tree is None:
        return []
    tables = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "__tablename__" for t in node.targets
        ):
            tables.append(ast.literal_eval(node.value))
    return tables


def _is_dataclass(node: ast.ClassDef) -> bool:
    for decorator in node.decorator_list:
        target = decorator.func if isinstance(decorator, ast.Call) else decorator
        if ast.unparse(target) in {"dataclass", "dataclasses.dataclass"}:
            return True
    return False


def _first_line(node: ast.AST) -> str:
    doc = ast.get_docstring(node) or ""
    return " ".join(doc.split("\n\n")[0].split())


def _signature(node: ast.FunctionDef) -> str:
    args = node.args
    names = [a.arg for a in args.posonlyargs + args.args]
    if args.vararg:
        names.append(f"*{args.vararg.arg}")
    elif args.kwonlyargs:
        names.append("*")
    names += [a.arg for a in args.kwonlyargs]
    if args.kwarg:
        names.append(f"**{args.kwarg.arg}")
    returns = f" -> {ast.unparse(node.returns)}" if node.returns else ""
    return f"{node.name}({', '.join(names)}){returns}"


def service_interface(module: str) -> tuple[list[str], list[str], list[str]]:
    """Public functions, dataclasses and re-exported names of ``service.py``."""
    tree = _parse(MODULES_DIR / module / "service.py")
    functions, types, reexports = [], [], []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and not node.name.startswith("_"):
            functions.append(f"`{_signature(node)}` — {_first_line(node)}")
        elif isinstance(node, ast.ClassDef) and not node.name.startswith("_"):
            if _is_dataclass(node):
                fields = [
                    s.target.id
                    for s in node.body
                    if isinstance(s, ast.AnnAssign) and isinstance(s.target, ast.Name)
                ]
                line = f"`{node.name}({', '.join(fields)})`"
            else:
                bases = ", ".join(ast.unparse(b) for b in node.bases)
                line = f"`{node.name}({bases})`" if bases else f"`{node.name}`"
            doc = _first_line(node)
            types.append(f"{line} — {doc}" if doc else line)
        elif (
            isinstance(node, ast.ImportFrom)
            and node.module
            and node.module.startswith(f"app.modules.{module}.")
            and not node.module.endswith(".models")
        ):
            reexports += [alias.asname or alias.name for alias in node.names]
    return functions, types, sorted(reexports)


def events(module: str) -> tuple[list[str], list[str]]:
    """Event names the module publishes and subscribes to (``app.core.events``)."""
    published, subscribed = set(), set()
    for path in sorted((MODULES_DIR / module).rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in {"publish", "subscribe"}
                and ast.unparse(node.func.value) == "events"
                and node.args
            ):
                name = ast.unparse(node.args[0]).split(".")[-1]
                (published if node.func.attr == "publish" else subscribed).add(name)
    return sorted(published), sorted(subscribed)


def routes(module: str) -> list[str]:
    found = []
    for owner, path in MODULE_ROUTERS:
        if owner != module:
            continue
        for route in importlib.import_module(path).router.routes:
            for method in sorted(getattr(route, "methods", ()) or ()):
                found.append(f"{method} {API_PREFIX}{route.path}")
    return found


def _names(values) -> str:
    return ", ".join(f"`{v}`" for v in values) if values else "–"


def render() -> str:
    callers = {
        m: sorted(o for o, deps in ALLOWED_DEPENDENCIES.items() if m in deps)
        for m in ALLOWED_DEPENDENCIES
    }
    out = [
        "# Module interfaces",
        "",
        "<!-- Generated by scripts/export_module_interfaces.py; do not edit by hand. -->",
        "",
        "Each feature module in `app/modules/` owns its tables and is used by other",
        "modules only through its `service.py`, whose functions return dataclasses. This",
        "reference lists, for every module, the tables it owns, the modules it may call",
        "and be called by (`ALLOWED_DEPENDENCIES`), the events it publishes or reacts to,",
        "its `/api/v1` routes and its public service interface. See",
        "`docs/architecture.md` for the rules and `ENABLED_MODULES` for serving a subset.",
        "",
        "| Module | Owns tables | May call | Called by | Routes |",
        "| --- | --- | --- | --- | --- |",
    ]
    modules = sorted(ALLOWED_DEPENDENCIES)
    for m in modules:
        out.append(
            f"| [{m}](#{m}) | {_names(owned_tables(m))} | "
            f"{_names(sorted(ALLOWED_DEPENDENCIES[m]))} | {_names(callers[m])} | {len(routes(m))} |"
        )
    for m in modules:
        package = MODULES_DIR / m
        summary = ast.get_docstring(_parse(package / "__init__.py")).split("\n")[0]
        functions, types, reexports = service_interface(m)
        published, subscribed = events(m)
        out += ["", f"## {m}", "", summary, ""]
        out.append(f"- **Owns tables:** {_names(owned_tables(m))}")
        out.append(f"- **May call:** {_names(sorted(ALLOWED_DEPENDENCIES[m]))}")
        out.append(f"- **Called by:** {_names(callers[m])}")
        out.append(f"- **Publishes events:** {_names(published)}")
        out.append(f"- **Subscribes to events:** {_names(subscribed)}")
        if (package / "README.md").exists():
            out.append(f"- **Notes:** [`app/modules/{m}/README.md`](../app/modules/{m}/README.md)")
        out += ["", "### Routes", ""]
        out += [f"- `{r}`" for r in routes(m)] or ["None."]
        out += ["", "### Service functions", ""]
        out += [f"- {f}" for f in functions] or ["None."]
        out += ["", "### Data types", ""]
        out += [f"- {t}" for t in types] or ["None."]
        if reexports:
            out += ["", f"Also re-exported from the module's internals: {_names(reexports)}."]
    return "\n".join(out) + "\n"


if __name__ == "__main__":
    REFERENCE.write_text(render())
    print(f"Wrote {REFERENCE}")
