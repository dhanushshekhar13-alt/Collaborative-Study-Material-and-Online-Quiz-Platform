"""S00.2: migrations run forward and backward cleanly and match the models."""

import os

import pytest
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect

import app.models  # noqa: F401
from alembic import command
from app.core.database import Base

DATABASE_URLS = ["sqlite_tmp"]
if os.environ.get("TEST_POSTGRES_URL"):
    DATABASE_URLS.append(os.environ["TEST_POSTGRES_URL"])


@pytest.fixture(params=DATABASE_URLS)
def database_url(request, tmp_path, monkeypatch):
    url = request.param
    if url == "sqlite_tmp":
        url = f"sqlite:///{tmp_path / 'migrations.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    return url


def _alembic() -> Config:
    return Config("alembic.ini")


def _tables(url: str) -> set[str]:
    engine = create_engine(url)
    try:
        return set(inspect(engine).get_table_names()) - {"alembic_version"}
    finally:
        engine.dispose()


def test_upgrade_then_downgrade_on_empty_database(database_url):
    command.upgrade(_alembic(), "head")
    assert _tables(database_url) == set(Base.metadata.tables)

    command.downgrade(_alembic(), "base")
    assert _tables(database_url) == set()

    command.upgrade(_alembic(), "head")
    assert _tables(database_url) == set(Base.metadata.tables)
    command.downgrade(_alembic(), "base")


def test_migrations_match_the_models(database_url):
    command.upgrade(_alembic(), "head")
    engine = create_engine(database_url)
    try:
        with engine.connect() as conn:
            diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    finally:
        engine.dispose()
    command.downgrade(_alembic(), "base")
    assert diff == []


def test_migrated_schema_enforces_enum_and_check_constraints(database_url):
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError

    command.upgrade(_alembic(), "head")
    engine = create_engine(database_url)
    insert_user = text(
        "INSERT INTO users (email, display_name, password_hash, role) "
        "VALUES (:email, 'x', 'x', :role)"
    )
    try:
        with engine.begin() as conn:
            conn.execute(insert_user, {"email": "ok@example.com", "role": "student"})
        with pytest.raises(IntegrityError), engine.begin() as conn:
            conn.execute(insert_user, {"email": "bad@example.com", "role": "superuser"})
    finally:
        engine.dispose()
        command.downgrade(_alembic(), "base")
