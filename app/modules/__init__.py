"""Feature modules.

Each module is a package that owns its tables (``models.py``) and exposes a public
interface (``service.py``). A module may only import ``app.core``, its own package and
the ``service`` of the modules listed for it in ``ALLOWED_DEPENDENCIES``; it never
imports another module's models. ``tests/test_module_boundaries.py`` enforces this.
Tables still reference each other through foreign keys, which needs no Python import.

Because the graph is explicit, any set of modules can be served on its own together
with the modules it may call (``required_modules``): ``ENABLED_MODULES=quizzes`` runs
quiz building and scoring without chat, analytics or the LLM code ever being imported
(NFR-07). ``docs/modules.md`` documents each module's interface and is generated from
the code by ``scripts/export_module_interfaces.py``.
"""

from collections.abc import Iterable

ALLOWED_DEPENDENCIES: dict[str, set[str]] = {
    "accounts": set(),
    "channels": {"accounts", "moderation", "notifications"},
    "materials": {"accounts", "channels", "moderation", "reputation"},
    "questions": {"accounts", "channels", "moderation", "reputation"},
    "quizzes": {"accounts", "channels", "questions"},
    "llm": {"accounts", "channels", "moderation", "questions", "quizzes"},
    "chat": {"accounts", "channels", "materials", "moderation", "questions", "quizzes"},
    "analytics": {"accounts", "channels", "materials", "questions", "quizzes"},
    "moderation": set(),
    "moderation_queue": {"accounts", "channels", "llm", "materials", "moderation", "questions"},
    "reputation": {"accounts"},
    "notifications": {"accounts"},
    "profiles": {"accounts", "materials", "questions", "quizzes", "reputation"},
    "admin": {"accounts", "channels", "materials", "moderation", "notifications", "questions"},
}


def required_modules(selected: Iterable[str]) -> set[str]:
    """Return ``selected`` plus every module they may call, directly or indirectly.

    Raises ``ValueError`` for a name that is not a module.
    """
    wanted = set(selected)
    unknown = wanted - ALLOWED_DEPENDENCIES.keys()
    if unknown:
        raise ValueError(f"Unknown modules: {', '.join(sorted(unknown))}")
    found: set[str] = set()
    stack = list(wanted)
    while stack:
        name = stack.pop()
        if name not in found:
            found.add(name)
            stack.extend(ALLOWED_DEPENDENCIES[name])
    return found
