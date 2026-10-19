import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.core.config import settings
from app.core.database import get_db
from app.main import create_app


@pytest.fixture(autouse=True)
def signing_secret(monkeypatch) -> None:
    """Give every test a token signing key without using a real secret."""
    monkeypatch.setattr(settings, "auth_token_secret", SecretStr("test-signing-secret"))


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


@pytest.fixture()
def client(db) -> TestClient:
    """The API, wired to the same in-memory database as the ``db`` fixture."""
    application = create_app()
    application.dependency_overrides[get_db] = lambda: db
    with TestClient(application) as test_client:
        yield test_client
