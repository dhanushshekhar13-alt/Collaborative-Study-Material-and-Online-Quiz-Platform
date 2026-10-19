"""S00.2: seed data per role, and the published API contract matches the app."""

from sqlalchemy import func, select

from app.modules.accounts.models import Role, User
from app.modules.questions.models import Question
from app.modules.quizzes.models import QuizAttempt
from app.seed import seed
from scripts.export_openapi import CONTRACT, render


def test_seed_creates_a_user_per_role_and_sample_content(db):
    created = seed(db, password="seed-test-password")
    roles = set(db.scalars(select(User.role)))
    assert roles == set(Role)
    assert created["authorized_channel"] != created["peer_channel"]
    assert db.scalar(select(func.count(Question.id))) == 3
    assert db.scalar(select(QuizAttempt.score)) == 1.0
    assert seed(db, password="again") == {}  # idempotent


def test_published_api_contract_is_current():
    assert CONTRACT.exists(), "run python scripts/export_openapi.py"
    assert CONTRACT.read_text() == render(), "API changed: run python scripts/export_openapi.py"


def test_contract_is_versioned():
    import json

    spec = json.loads(CONTRACT.read_text())
    assert spec["info"]["version"].split(".")[0] == "1"
