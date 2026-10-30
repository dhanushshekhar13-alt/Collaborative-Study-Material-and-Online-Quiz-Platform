from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import Settings, settings


class Base(DeclarativeBase):
    """Base class for all persisted domain entities."""


def _connect_args(url: str) -> dict[str, bool]:
    return {"check_same_thread": False} if url.startswith("sqlite") else {}


def _in_memory(url: str) -> bool:
    return url.rstrip("/") in ("sqlite:", "sqlite://") or ":memory:" in url


def engine_options(url: str, config: Settings = settings) -> dict:
    """``create_engine`` options: a pool sized from the settings (NFR-01).

    An in-memory SQLite database has a single connection, so it keeps SQLAlchemy's
    own pool for that case.
    """
    options: dict = {"connect_args": _connect_args(url), "pool_pre_ping": True}
    if not _in_memory(url):
        options["pool_size"] = config.database_pool_size
        options["max_overflow"] = config.database_max_overflow
    return options


def max_connections(config: Settings = settings) -> int:
    """How many requests one process may serve at once: one connection each."""
    return config.database_pool_size + config.database_max_overflow


engine = create_engine(settings.database_url, **engine_options(settings.database_url))
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    """FastAPI dependency that yields a database session and always closes it.

    If the request fails, its uncommitted work is rolled back explicitly, so a
    transient failure part-way through (NFR-04) never leaves half-written rows behind;
    everything committed earlier is untouched.
    """
    db = SessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
