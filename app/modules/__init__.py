"""Feature modules.

Each module is a package that owns its tables (``models.py``) and exposes a public
interface (``service.py``). A module may only import ``app.core``, its own package and
the ``service`` of the modules listed for it in ``ALLOWED_DEPENDENCIES``; it never
imports another module's models. ``tests/test_module_boundaries.py`` enforces this.
Tables still reference each other through foreign keys, which needs no Python import.
"""

ALLOWED_DEPENDENCIES: dict[str, set[str]] = {
    "accounts": set(),
    "channels": {"accounts", "moderation", "notifications"},
    "materials": {"accounts", "channels", "moderation", "reputation"},
    "questions": {"accounts", "channels", "moderation", "reputation"},
    "quizzes": {"accounts", "channels", "questions"},
    "llm": {"accounts", "channels", "questions", "quizzes"},
    "chat": {"accounts", "channels", "materials", "moderation", "questions", "quizzes"},
    "analytics": {"accounts", "channels", "materials", "questions", "quizzes"},
    "moderation": set(),
    "moderation_queue": {"accounts", "channels", "materials", "moderation", "questions"},
    "reputation": {"accounts"},
    "notifications": {"accounts"},
    "profiles": {"accounts", "materials", "questions", "quizzes", "reputation"},
    "admin": {"accounts", "channels", "materials", "moderation", "notifications", "questions"},
}
