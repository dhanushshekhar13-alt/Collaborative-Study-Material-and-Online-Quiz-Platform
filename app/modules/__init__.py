"""Feature modules.

Each module is a package that owns its tables (``models.py``) and exposes a public
interface (``service.py``). A module may only import ``app.core``, its own package and
the ``service`` of the modules listed for it in ``ALLOWED_DEPENDENCIES``; it never
imports another module's models. ``tests/test_module_boundaries.py`` enforces this.
Tables still reference each other through foreign keys, which needs no Python import.
"""

ALLOWED_DEPENDENCIES: dict[str, set[str]] = {
    "accounts": set(),
    "channels": {"accounts"},
    "materials": {"accounts", "channels", "moderation"},
    "questions": {"accounts", "channels", "moderation"},
    "quizzes": {"questions"},
    "llm": {"questions", "quizzes"},
    "chat": set(),
    "analytics": {"quizzes"},
    "moderation": set(),
    "reputation": {"accounts"},
    "notifications": set(),
    "profiles": {"accounts", "materials", "questions", "quizzes", "reputation"},
}
