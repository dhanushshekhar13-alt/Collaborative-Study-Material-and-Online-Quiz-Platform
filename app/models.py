"""Import the feature modules' ORM models so ``Base.metadata`` knows their tables.

Alembic and the seed script need every table and call ``load_models()``. Importing
this module loads the tables of the modules this process serves (``ENABLED_MODULES``,
blank for all), so a process serving quizzes alone never imports chat (NFR-07).
"""

import importlib
from collections.abc import Iterable
from pathlib import Path

from app.core.config import settings
from app.modules import ALLOWED_DEPENDENCIES, required_modules

_MODULES_DIR = Path(__file__).resolve().parent / "modules"


def load_models(modules: Iterable[str] | None = None) -> set[str]:
    """Import the models of ``modules`` and the modules they may call (default: all).

    Returns the modules whose tables are now registered on ``Base.metadata``.
    """
    selected = required_modules(modules) if modules else set(ALLOWED_DEPENDENCIES)
    for name in sorted(selected):
        if (_MODULES_DIR / name / "models.py").exists():
            importlib.import_module(f"app.modules.{name}.models")
    return selected


load_models(settings.enabled_module_list)
