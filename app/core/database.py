from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import settings


class Base(DeclarativeBase):
    """Base class for all persisted domain entities."""


def _connect_args(url: str) -> dict[str, bool]:
    return {"check_same_thread": False} if url.startswith("sqlite") else {}


engine = create_engine(
    settings.database_url, connect_args=_connect_args(settings.database_url), pool_pre_ping=True
)
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
