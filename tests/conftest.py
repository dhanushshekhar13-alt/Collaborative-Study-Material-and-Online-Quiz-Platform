import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture()
def client() -> TestClient:
    return TestClient(create_app())


@pytest.fixture()
def db():
    """A fresh in-memory database with every table, discarded after the test."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from sqlalchemy.pool import StaticPool

    import app.models  # noqa: F401
    from app.core.database import Base

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        yield session
    engine.dispose()
