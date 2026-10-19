"""S01.1 - registration, login and role-based access (issue #18, FR-01, NFR-02).

Test Plan cases are named after their IDs so the execution log can be read off the
test names: UT_01, UT_02, IT_01, IT_02, IT_28.
"""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.core.config import settings
from app.core.database import get_db
from app.modules.accounts import service as accounts
from app.modules.accounts.models import Role, User
from app.modules.accounts.service import (
    Capability,
    UserSummary,
    capabilities_for,
    require_capability,
    require_roles,
)
from app.modules.accounts.tokens import (
    InvalidTokenError,
    create_access_token,
    decode_access_token,
)

ACCOUNTS = "/api/v1/accounts"

# The Test Plan's data for UT_01 / IT_01 / IT_02. "Pass@123" in the plan is seven
# characters; the API requires eight, so one character is appended.
ADITI = {
    "display_name": "Aditi Rao",
    "email": "aditi.rao@test.com",
    "username": "aditi_r",
    "password": "Pass@1234",
}


def register(client: TestClient, **overrides):
    return client.post(f"{ACCOUNTS}/register", json={**ADITI, **overrides})


def login(client: TestClient, username: str, password: str):
    return client.post(f"{ACCOUNTS}/login", json={"username": username, "password": password})


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def make_user(db, *, role: Role, username: str, password: str = "Pass@1234") -> UserSummary:
    user = accounts.create_user(
        db,
        email=f"{username}@test.com",
        display_name=username.title(),
        password=password,
        role=role,
        username=username,
    )
    db.commit()
    return user


def token_for(client: TestClient, username: str, password: str = "Pass@1234") -> str:
    return login(client, username, password).json()["access_token"]


# --------------------------------------------------------------------------- #
# UT_01 / UT_02 - registration
# --------------------------------------------------------------------------- #


def test_UT_01_valid_submission_creates_the_account(client):
    response = register(client)

    assert response.status_code == 201
    body = response.json()
    assert body["message"] == "Registration successful"
    assert body["account"]["username"] == "aditi_r"
    assert body["account"]["email"] == "aditi.rao@test.com"
    assert body["account"]["display_name"] == "Aditi Rao"
    assert body["account"]["role"] == "student"
    assert not {"password", "password_hash"} & set(body["account"])


def test_UT_02_invalid_email_is_rejected_with_the_specified_message(client):
    response = register(client, email="aditi.rao@@test")

    assert response.status_code == 422
    assert response.json()["detail"] == "Enter a valid email address"
    assert response.json()["errors"][0]["field"] == "email"


def test_duplicate_username_is_rejected_with_a_clear_message(client):
    register(client)

    response = register(client, email="someone.else@test.com")

    assert response.status_code == 409
    assert response.json()["detail"] == "This username is already registered"


def test_duplicate_email_is_rejected_with_a_clear_message(client):
    register(client)

    response = register(client, username="aditi_rao2")

    assert response.status_code == 409
    assert response.json()["detail"] == "This email is already registered"


def test_duplicates_are_caught_regardless_of_case(client):
    register(client)

    assert register(client, username="ADITI_R", email="x@test.com").status_code == 409
    assert register(client, username="other", email="ADITI.RAO@test.com").status_code == 409


def test_registration_never_stores_the_plain_password(client, db):
    register(client)

    stored = db.query(User).filter_by(username="aditi_r").one()
    assert ADITI["password"] not in stored.password_hash
    assert stored.password_hash.startswith("scrypt$")


def test_self_registration_cannot_claim_an_elevated_role(client):
    response = register(client, role="admin")

    assert response.status_code == 201
    assert response.json()["account"]["role"] == "student"


def test_a_short_password_is_rejected(client):
    assert register(client, password="short").status_code == 422


# --------------------------------------------------------------------------- #
# IT_01 / IT_02 - authentication
# --------------------------------------------------------------------------- #


def test_IT_01_valid_credentials_log_in_and_reach_student_features(client):
    register(client)

    response = login(client, ADITI["username"], ADITI["password"])

    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["account"]["role"] == "student"
    headers = bearer(body["access_token"])

    # The dashboard's first call: who am I.
    dashboard = client.get(f"{ACCOUNTS}/me", headers=headers)
    assert dashboard.status_code == 200
    assert dashboard.json()["username"] == "aditi_r"

    # A Student-permitted feature, such as uploading material, is available.
    permissions = client.get(f"{ACCOUNTS}/me/permissions", headers=headers).json()
    assert "material:upload" in permissions["capabilities"]


def test_IT_02_wrong_password_is_rejected_and_the_dashboard_stays_closed(client):
    register(client)

    response = login(client, ADITI["username"], "WrongPass1")

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid username or password"
    assert "access_token" not in response.json()
    assert client.get(f"{ACCOUNTS}/me").status_code == 401


def test_an_unknown_username_gets_the_same_message_as_a_wrong_password(client):
    response = login(client, "no_such_user", "Pass@1234")

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid username or password"


def test_login_accepts_the_email_as_the_identifier(client):
    register(client)

    assert login(client, ADITI["email"], ADITI["password"]).status_code == 200


def test_an_account_created_before_usernames_existed_can_sign_in_by_email(client, db):
    """Seeded users (S00.2) have no username; they must still be able to log in."""
    accounts.create_user(db, email="faculty@example.com", display_name="F", password="Pass@1234")
    db.commit()

    assert login(client, "faculty@example.com", "Pass@1234").status_code == 200


def test_a_suspended_account_cannot_log_in(client, db):
    user = make_user(db, role=Role.STUDENT, username="suspended_s")
    accounts.set_suspended(db, user.id, True)
    db.commit()

    response = login(client, "suspended_s", "Pass@1234")

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid username or password"


def test_suspension_takes_effect_on_a_token_already_issued(client, db):
    user = make_user(db, role=Role.STUDENT, username="soon_suspended")
    headers = bearer(token_for(client, "soon_suspended"))
    assert client.get(f"{ACCOUNTS}/me", headers=headers).status_code == 200

    accounts.set_suspended(db, user.id, True)
    db.commit()

    assert client.get(f"{ACCOUNTS}/me", headers=headers).status_code == 401


# --------------------------------------------------------------------------- #
# IT_28 and the rest of the authorization surface
# --------------------------------------------------------------------------- #


def test_IT_28_a_student_cannot_reach_admin_only_endpoints(client, db):
    make_user(db, role=Role.STUDENT, username="student_a")

    response = client.get(f"{ACCOUNTS}/admin/users", headers=bearer(token_for(client, "student_a")))

    assert response.status_code == 403
    assert response.json()["detail"] == "Unauthorized"


def test_an_admin_can_reach_admin_only_endpoints(client, db):
    make_user(db, role=Role.ADMIN, username="admin_a")

    response = client.get(f"{ACCOUNTS}/admin/users", headers=bearer(token_for(client, "admin_a")))

    assert response.status_code == 200
    assert [account["username"] for account in response.json()] == ["admin_a"]


def test_an_authorized_user_is_not_an_admin(client, db):
    make_user(db, role=Role.AUTHORIZED_USER, username="faculty_a")
    headers = bearer(token_for(client, "faculty_a"))

    assert client.get(f"{ACCOUNTS}/admin/users", headers=headers).status_code == 403
    capabilities = client.get(f"{ACCOUNTS}/me/permissions", headers=headers).json()
    assert "channel:create_authorized" in capabilities["capabilities"]


@pytest.mark.parametrize("path", ["/me", "/me/permissions", "/admin/users"])
def test_protected_endpoints_reject_unauthenticated_requests(client, path):
    response = client.get(f"{ACCOUNTS}{path}")

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_a_tampered_token_is_rejected(client, db):
    make_user(db, role=Role.STUDENT, username="student_b")
    header, payload, signature = token_for(client, "student_b").split(".")
    forged = f"{header}.{payload}.{signature[:-2]}xx"

    assert client.get(f"{ACCOUNTS}/me", headers=bearer(forged)).status_code == 401


def test_a_token_cannot_be_promoted_by_editing_its_role_claim(client, db):
    """Rewriting ``role`` in the payload breaks the signature."""
    import base64
    import json

    user = make_user(db, role=Role.STUDENT, username="student_f")
    header, payload, signature = create_access_token(user.id, "student").split(".")
    claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    claims["role"] = "admin"
    promoted = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()

    response = client.get(
        f"{ACCOUNTS}/admin/users", headers=bearer(f"{header}.{promoted}.{signature}")
    )

    assert response.status_code == 401


def test_authorization_is_enforced_server_side_not_only_hidden_in_the_ui(client, db):
    """A Student who ignores the capability list still cannot act as an admin."""
    make_user(db, role=Role.STUDENT, username="student_c")
    headers = bearer(token_for(client, "student_c"))

    advertised = client.get(f"{ACCOUNTS}/me/permissions", headers=headers).json()
    assert "user:manage" not in advertised["capabilities"]

    # Calling the endpoint directly, as a client that ignored the list would.
    assert client.get(f"{ACCOUNTS}/admin/users", headers=headers).status_code == 403


def test_service_guards_protect_another_modules_route(db):
    """The guards other modules import from ``accounts.service`` behave as needed."""
    router = APIRouter()

    @router.post("/materials")
    def upload(user: UserSummary = Depends(require_capability(Capability.MATERIAL_UPLOAD))):
        return {"by": user.username}

    @router.post("/channels/approve")
    def approve(user: UserSummary = Depends(require_roles(Role.ADMIN))):
        return {"by": user.username}

    application = FastAPI()
    application.include_router(router)
    application.dependency_overrides[get_db] = lambda: db
    student = make_user(db, role=Role.STUDENT, username="student_d")
    headers = bearer(create_access_token(student.id, student.role.value))

    with TestClient(application) as test_client:
        assert test_client.post("/materials", headers=headers).json() == {"by": "student_d"}
        assert test_client.post("/channels/approve", headers=headers).status_code == 403
        assert test_client.post("/materials").status_code == 401


def test_capability_sets_grow_with_privilege():
    assert capabilities_for(Role.STUDENT) < capabilities_for(Role.AUTHORIZED_USER)
    assert capabilities_for(Role.AUTHORIZED_USER) < capabilities_for(Role.ADMIN)
    assert set(Role) == {Role.STUDENT, Role.AUTHORIZED_USER, Role.ADMIN}


# --------------------------------------------------------------------------- #
# Token handling
# --------------------------------------------------------------------------- #


def test_a_token_round_trips_its_claims():
    claims = decode_access_token(create_access_token(42, "student"))

    assert (claims.user_id, claims.role) == (42, "student")


def test_token_expiry_is_enforced_server_side():
    token = create_access_token(42, "student", now=datetime.now(UTC) - timedelta(hours=2))

    with pytest.raises(InvalidTokenError, match="expired"):
        decode_access_token(token)


def test_an_expired_token_is_refused_by_a_protected_endpoint(client, db):
    user = make_user(db, role=Role.STUDENT, username="student_e")
    stale = create_access_token(
        user.id, user.role.value, now=datetime.now(UTC) - timedelta(hours=2)
    )

    assert client.get(f"{ACCOUNTS}/me", headers=bearer(stale)).status_code == 401


def test_token_lifetime_follows_configuration(monkeypatch):
    monkeypatch.setattr(settings, "access_token_expire_minutes", 5)
    issued = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)

    claims = decode_access_token(create_access_token(1, "student", now=issued), now=issued)

    assert claims.expires_at == issued + timedelta(minutes=5)


def test_a_token_signed_with_another_key_is_rejected(monkeypatch):
    token = create_access_token(42, "student")
    monkeypatch.setattr(settings, "auth_token_secret", SecretStr("a-different-secret"))

    with pytest.raises(InvalidTokenError, match="signature"):
        decode_access_token(token)


def test_signing_without_a_secret_is_reported_not_silently_allowed(monkeypatch):
    monkeypatch.setattr(settings, "auth_token_secret", None)

    with pytest.raises(RuntimeError, match="AUTH_TOKEN_SECRET"):
        create_access_token(1, "student")


def test_session_policy_reports_the_token_lifetime(client):
    response = client.get(f"{ACCOUNTS}/session-policy")

    assert response.status_code == 200
    assert response.json()["access_token_expire_minutes"] == settings.access_token_expire_minutes
