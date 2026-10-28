"""NFR-02 - protect credentials and enforce role-based authorization (issue #47).

Three acceptance criteria, each proved across the whole API rather than one feature:

1. Credentials are stored hashed (scrypt, salted), never in plain text, and no
   response ever carries a password or its hash.
2. Every unauthorized access attempt is denied. The matrix is built from the published
   API contract, so a route added later is covered without editing this file: every
   operation that declares bearer security is called anonymously and with every kind of
   bad token, and every role-guarded route is called by every role that lacks the
   capability it needs.
3. A user can modify only resources they have permission for: a second user tries to
   read or change each kind of resource another user owns, and nothing changes.

The Test Plan cases this NFR maps to (IT_28, UT_06, IT_20) run here too, as part of the
matrix, alongside their feature-level tests in ``test_admin.py``,
``test_official_content.py`` and ``test_channel_threads.py``.
"""

import base64
import json
import re
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select

import app.main as main_module
from app.core.config import Settings
from app.core.security import hash_password, verify_password
from app.modules.accounts import service as accounts
from app.modules.accounts.models import Role, User
from app.modules.accounts.permissions import Capability, capabilities_for
from app.modules.accounts.tokens import create_access_token
from app.modules.channels import service as channels
from app.modules.channels.models import ApprovalStatus, ChannelType
from app.modules.llm.provider import FakeProvider
from app.modules.llm.router import get_llm_provider
from app.modules.notifications import service as notifications
from app.modules.notifications.models import NotificationKind
from app.modules.questions import service as questions
from app.modules.questions.models import Difficulty, QuestionType

API = "/api/v1"
PASSWORD = "Pass@1234"

# Operations reachable without signing in, each for a stated reason. Anything else in
# the contract must declare bearer security, so a new route cannot ship unguarded by
# accident: it fails ``test_every_operation_outside_the_public_list_requires_a_token``.
PUBLIC_OPERATIONS = {
    ("GET", "/health"),  # liveness probe; returns no data
    ("POST", f"{API}/accounts/register"),  # creating an account
    ("POST", f"{API}/accounts/login"),  # obtaining a token
    ("GET", f"{API}/accounts/session-policy"),  # token lifetime, needed before login
    # Loaded by <img> tags, which cannot send a bearer header; avatars are public.
    ("GET", f"{API}/profiles/{{user_id}}/picture"),
}

# Routes only an admin may call. Pinned here as well as derived from the guards, so that
# weakening or dropping one of these guards fails a test instead of shrinking the matrix.
ADMIN_ONLY = {
    ("GET", f"{API}/accounts/admin/users"),
    ("GET", f"{API}/admin/dashboard"),
    ("GET", f"{API}/admin/users"),
    ("POST", f"{API}/admin/users/{{user_id}}/suspend"),
    ("POST", f"{API}/admin/users/{{user_id}}/reinstate"),
    ("GET", f"{API}/admin/channel-requests"),
    ("GET", f"{API}/admin/reports"),
    ("POST", f"{API}/admin/content/{{target_type}}/{{target_id}}/remove"),
    ("GET", f"{API}/admin/audit-log"),
    ("GET", f"{API}/channels/requests"),
    ("POST", f"{API}/channels/{{channel_id}}/approve"),
    ("POST", f"{API}/channels/{{channel_id}}/reject"),
}


def make_user(db, username: str, role: Role = Role.STUDENT) -> int:
    user = accounts.create_user(
        db,
        email=f"{username}@test.com",
        display_name=username.replace("_", " ").title(),
        password=PASSWORD,
        role=role,
        username=username,
    )
    db.commit()
    return user.id


def login(client: TestClient, username: str, password: str = PASSWORD):
    return client.post(f"{API}/accounts/login", json={"username": username, "password": password})


def auth(client: TestClient, username: str) -> dict[str, str]:
    response = login(client, username)
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def concrete(path: str) -> str:
    """Fill every path parameter so the request reaches the route's guards."""
    return re.sub(r"\{(\w+)\}", lambda m: "material" if m[1] == "target_type" else "1", path)


def call(client: TestClient, method: str, path: str, headers: dict | None = None):
    kwargs = {"headers": headers or {}}
    if method in {"POST", "PUT", "PATCH"}:
        kwargs["json"] = {}
    return client.request(method, concrete(path), **kwargs)


def contract_operations() -> list[tuple[str, str, dict]]:
    schema = main_module.create_app().openapi()
    return [
        (method.upper(), path, operation)
        for path, item in schema["paths"].items()
        for method, operation in item.items()
    ]


def protected_operations() -> list[tuple[str, str]]:
    return [(m, p) for m, p, op in contract_operations() if op.get("security")]


def role_guarded_routes() -> list[tuple[str, str, frozenset[Role]]]:
    """Each route guarded by role or capability, with the roles the guard admits.

    Read from the routers themselves: ``require_capability`` and ``require_roles``
    return closures over what they check, so the matrix follows the code rather than a
    hand-kept copy of it.
    """
    found: list[tuple[str, str, frozenset[Role]]] = []

    def admitted(dependant) -> frozenset[Role] | None:
        for sub in dependant.dependencies:
            call_ = sub.call
            cells = {
                name: cell.cell_contents
                for name, cell in zip(
                    getattr(getattr(call_, "__code__", None), "co_freevars", ()),
                    getattr(call_, "__closure__", None) or (),
                    strict=False,
                )
            }
            if isinstance(cells.get("capability"), Capability):
                capability = cells["capability"]
                return frozenset(r for r in Role if capability in capabilities_for(r))
            if isinstance(cells.get("allowed"), tuple):
                return frozenset(cells["allowed"])
            nested = admitted(sub)
            if nested is not None:
                return nested
        return None

    routers = [v for v in vars(main_module).values() if isinstance(v, APIRouter)]
    for router in routers:
        for route in router.routes:
            roles = admitted(route.dependant)
            if roles is not None and roles != frozenset(Role):
                for method in route.methods:
                    found.append((method, f"{API}{route.path}", roles))
    return found


@pytest.fixture()
def roles(client, db) -> dict[Role, dict[str, str]]:
    """Signed-in headers for one user of every role."""
    make_user(db, "sec_student", Role.STUDENT)
    make_user(db, "sec_faculty", Role.AUTHORIZED_USER)
    make_user(db, "sec_admin", Role.ADMIN)
    return {
        Role.STUDENT: auth(client, "sec_student"),
        Role.AUTHORIZED_USER: auth(client, "sec_faculty"),
        Role.ADMIN: auth(client, "sec_admin"),
    }


# ---------------------------------------------------------------------------
# AC1: credentials are stored hashed, never in plain text
# ---------------------------------------------------------------------------


def test_registered_password_is_stored_as_a_salted_scrypt_hash(client, db):
    response = client.post(
        f"{API}/accounts/register",
        json={
            "username": "hash_check",
            "email": "hash_check@test.com",
            "display_name": "Hash Check",
            "password": PASSWORD,
        },
    )
    assert response.status_code == 201, response.text

    stored = db.scalar(select(User.password_hash).where(User.username == "hash_check"))
    assert stored is not None
    assert PASSWORD not in stored
    scheme, n, r, p, salt_hex, hash_hex = stored.split("$")
    assert scheme == "scrypt"
    assert int(n) >= 2**14 and int(r) >= 8  # OWASP minimum work factor for scrypt
    assert len(bytes.fromhex(salt_hex)) >= 16
    assert len(bytes.fromhex(hash_hex)) >= 32
    assert verify_password(PASSWORD, stored)
    assert not verify_password("Pass@12345", stored)


def test_the_same_password_never_produces_the_same_stored_value():
    first, second = hash_password(PASSWORD), hash_password(PASSWORD)
    assert first != second  # per-password salt: equal passwords are not detectable
    assert verify_password(PASSWORD, first) and verify_password(PASSWORD, second)


def test_no_stored_credential_is_plain_text(client, db, roles):
    for username, stored in db.execute(select(User.username, User.password_hash)):
        assert stored.startswith("scrypt$"), username
        assert PASSWORD not in stored, username


def test_a_tampered_or_foreign_hash_never_verifies():
    stored = hash_password(PASSWORD)
    assert not verify_password(PASSWORD, stored.replace("scrypt$", "plain$", 1))
    assert not verify_password(PASSWORD, PASSWORD)  # a plain-text value is not accepted
    assert not verify_password(PASSWORD, "")


def test_no_response_schema_can_carry_a_password_or_its_hash():
    schema = main_module.create_app().openapi()
    response_refs: set[str] = set()
    for _, _, operation in contract_operations():
        for response in operation.get("responses", {}).values():
            for content in response.get("content", {}).values():
                response_refs |= set(re.findall(r"#/components/schemas/(\w+)", json.dumps(content)))

    components = schema["components"]["schemas"]
    seen: set[str] = set()
    pending = list(response_refs)
    while pending:
        name = pending.pop()
        if name in seen:
            continue
        seen.add(name)
        properties = components[name].get("properties", {})
        for field in properties:
            assert "password" not in field.lower(), f"{name}.{field} is returned to clients"
        pending += re.findall(r"#/components/schemas/(\w+)", json.dumps(components[name]))


def test_account_responses_never_include_the_password_or_hash(client, db, roles):
    bodies = [
        client.get(f"{API}/accounts/me", headers=roles[Role.STUDENT]).text,
        client.get(f"{API}/accounts/admin/users", headers=roles[Role.ADMIN]).text,
        client.get(f"{API}/admin/users", headers=roles[Role.ADMIN]).text,
        login(client, "sec_student").text,
    ]
    stored = db.scalar(select(User.password_hash).where(User.username == "sec_student"))
    for body in bodies:
        assert PASSWORD not in body
        assert stored not in body
        assert "password" not in body.lower()


def test_a_wrong_password_is_refused_without_saying_which_part_was_wrong(client, db):
    make_user(db, "sec_login")
    wrong_password = login(client, "sec_login", "Wrong@1234")
    unknown_user = login(client, "nobody_here", PASSWORD)
    assert wrong_password.status_code == unknown_user.status_code == 401
    assert wrong_password.json()["detail"] == unknown_user.json()["detail"]


# ---------------------------------------------------------------------------
# AC2: every unauthorized access attempt is denied
# ---------------------------------------------------------------------------


def _forged(claims: dict, header: dict | None = None, signature: str = "x") -> str:
    def segment(data: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()

    return f"{segment(header or {'alg': 'HS256', 'typ': 'JWT'})}.{segment(claims)}.{signature}"


def bad_credentials(db) -> dict[str, dict[str, str]]:
    """Every kind of credential that must not get a caller in."""
    student = make_user(db, "sec_victim")
    suspended = make_user(db, "sec_suspended")
    db.get(User, suspended).is_suspended = True
    db.commit()

    valid = create_access_token(student, Role.STUDENT.value)
    header, payload, signature = valid.split(".")
    claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    escalated = _forged({**claims, "role": Role.ADMIN.value}, signature=signature)
    other_key = Settings(auth_token_secret=SecretStr("not-the-server-secret"))
    expired = create_access_token(
        student, Role.STUDENT.value, now=datetime.now(UTC) - timedelta(days=30)
    )
    return {
        "no header": {},
        "empty bearer": {"Authorization": "Bearer "},
        "wrong scheme": {"Authorization": f"Basic {valid}"},
        "malformed token": bearer("not-a-token"),
        "signed with another key": bearer(
            create_access_token(student, Role.ADMIN.value, config=other_key)
        ),
        "role claim edited": bearer(escalated),
        "unsigned (alg none)": bearer(_forged({**claims, "role": "admin"}, {"alg": "none"}, "")),
        "expired": bearer(expired),
        "suspended account": bearer(create_access_token(suspended, Role.STUDENT.value)),
        "deleted account": bearer(create_access_token(999_999, Role.ADMIN.value)),
    }


def test_every_operation_outside_the_public_list_requires_a_token():
    operations = {(m, p): op for m, p, op in contract_operations()}
    for key, operation in operations.items():
        if key in PUBLIC_OPERATIONS:
            continue
        assert operation.get("security"), f"{key} is reachable without signing in"
    assert PUBLIC_OPERATIONS <= operations.keys(), "the public list names a missing route"


def test_every_protected_operation_refuses_every_bad_credential(client, db):
    operations = protected_operations()
    assert len(operations) > 90  # the whole API, not a sample
    failures = []
    for label, headers in bad_credentials(db).items():
        for method, path in operations:
            response = call(client, method, path, headers)
            if response.status_code != 401:
                failures.append(f"{label}: {method} {path} -> {response.status_code}")
            elif response.headers.get("www-authenticate") != "Bearer":
                failures.append(f"{label}: {method} {path} sent no Bearer challenge")
    assert not failures, "\n".join(failures)


def test_a_refused_request_does_not_reach_the_data(client, db):
    """An anonymous write is stopped before validation, so it reveals and changes nothing."""
    before = db.scalar(select(User.id).order_by(User.id.desc()))
    for method, path in protected_operations():
        if method == "GET":
            continue
        response = call(client, method, path)
        assert response.status_code == 401
        assert response.json() == {"detail": "Not authenticated"}
    assert db.scalar(select(User.id).order_by(User.id.desc())) == before


def test_every_role_guarded_route_refuses_each_role_without_the_capability(client, roles):
    guarded = role_guarded_routes()
    paths = {path for _, path, _ in guarded}
    # The admin surface and the authorized-user surface are both in the matrix.
    assert f"{API}/admin/dashboard" in paths
    assert f"{API}/channels/{{channel_id}}/approve" in paths

    failures, checked = [], 0
    for method, path, admitted in guarded:
        for role, headers in roles.items():
            if role in admitted:
                continue
            checked += 1
            response = call(client, method, path, headers)
            if response.status_code != 403 or response.json()["detail"] != "Unauthorized":
                failures.append(f"{role}: {method} {path} -> {response.status_code}")
    assert checked >= len(guarded)
    assert not failures, "\n".join(failures)


def test_roles_with_the_capability_pass_the_guard(client, roles):
    """The guard admits who it should: a refusal above is the guard, not a broken route."""
    for method, path, admitted in role_guarded_routes():
        for role in admitted:
            response = call(client, method, path, roles[role])
            assert response.status_code != 401, (role, method, path)
            assert not (
                response.status_code == 403 and response.json().get("detail") == "Unauthorized"
            ), (role, method, path)


def test_IT_28_non_admin_roles_are_denied_every_admin_route(client, roles):
    admin_paths = [(m, p) for m, p, admitted in role_guarded_routes() if admitted == {Role.ADMIN}]
    assert set(admin_paths) == ADMIN_ONLY
    for role in (Role.STUDENT, Role.AUTHORIZED_USER):
        for method, path in admin_paths:
            response = call(client, method, path, roles[role])
            assert response.status_code == 403, (role, method, path)
            assert response.json() == {"detail": "Unauthorized"}
    dashboard = client.get(f"{API}/admin/dashboard", headers=roles[Role.ADMIN])
    assert dashboard.status_code == 200


def test_a_role_change_takes_effect_on_the_next_request(client, db, roles):
    """Authorization reads the account on every request, not the role inside the token."""
    admin = db.scalar(select(User).where(User.username == "sec_admin"))
    assert client.get(f"{API}/admin/dashboard", headers=roles[Role.ADMIN]).status_code == 200
    admin.role = Role.STUDENT
    db.commit()
    demoted = client.get(f"{API}/admin/dashboard", headers=roles[Role.ADMIN])
    assert demoted.status_code == 403


# ---------------------------------------------------------------------------
# AC3: a user can modify only resources they have permission for
# ---------------------------------------------------------------------------

PDF = b"%PDF-1.4\n1 0 obj << /Type /Catalog >> endobj\ntrailer << >>\n%%EOF\n"


def refused(response) -> bool:
    """Refused without leaking: 403 for a visible resource, 404 for a private one."""
    return response.status_code in (403, 404)


@pytest.fixture()
def owners(client, db) -> dict:
    """``owner`` holds each kind of resource; ``intruder`` is another signed-in student."""
    client.app.dependency_overrides[get_llm_provider] = lambda: FakeProvider()
    ids = {name: make_user(db, f"sec_{name}") for name in ("owner", "intruder", "friend")}
    for i in range(6):
        questions.create_question(
            db,
            author_id=ids["friend"],
            subject="DBMS",
            topic="Normalization",
            difficulty=Difficulty.MEDIUM,
            question_type=QuestionType.TRUE_FALSE,
            text=f"DBMS statement {i}",
            options=["True", "False"],
            correct_answer="True",
        )
    db.commit()
    return {
        **ids,
        "owner_h": auth(client, "sec_owner"),
        "intruder_h": auth(client, "sec_intruder"),
        "friend_h": auth(client, "sec_friend"),
    }


def test_another_users_material_cannot_be_edited_or_removed(client, owners):
    created = client.post(
        f"{API}/materials",
        data={
            "title": "Owner Notes",
            "subject": "DBMS",
            "topic": "Normalization",
            "semester": "5",
            "material_type": "notes",
        },
        files={"file": ("notes.pdf", PDF, "application/octet-stream")},
        headers=owners["owner_h"],
    )
    assert created.status_code == 201, created.text
    material = created.json()["id"]
    path = f"{API}/materials/{material}"

    assert refused(client.patch(path, json={"title": "Hijacked"}, headers=owners["intruder_h"]))
    assert refused(client.delete(path, headers=owners["intruder_h"]))

    after = client.get(path, headers=owners["owner_h"])
    assert after.status_code == 200 and after.json()["title"] == "Owner Notes"
    edited = client.patch(path, json={"title": "Owner Notes v2"}, headers=owners["owner_h"])
    assert edited.status_code == 200  # the owner still can


def test_another_users_channel_posts_and_members_cannot_be_changed(client, owners):
    channel = client.post(
        f"{API}/channels",
        json={"name": "Owner Group", "subject": "DBMS"},
        headers=owners["owner_h"],
    ).json()["id"]
    for who in ("intruder_h", "friend_h"):
        joined = client.post(f"{API}/channels/{channel}/join", headers=owners[who])
        assert joined.status_code in (200, 201), joined.text
    post = client.post(
        f"{API}/channels/{channel}/posts", json={"body": "Owner post"}, headers=owners["owner_h"]
    )
    assert post.status_code == 201, post.text
    post_path = f"{API}/channels/{channel}/posts/{post.json()['id']}"

    assert refused(client.patch(post_path, json={"body": "x"}, headers=owners["intruder_h"]))
    assert refused(client.delete(post_path, headers=owners["intruder_h"]))
    kick = f"{API}/channels/{channel}/members/{owners['friend']}"
    assert refused(client.delete(kick, headers=owners["intruder_h"]))

    posts = client.get(f"{API}/channels/{channel}/posts", headers=owners["owner_h"]).json()
    assert "Owner post" in json.dumps(posts)
    members = client.get(f"{API}/channels/{channel}/members", headers=owners["owner_h"]).json()
    assert owners["friend"] in {m.get("user_id", m.get("id")) for m in members_list(members)}


def members_list(payload) -> list[dict]:
    return payload["items"] if isinstance(payload, dict) else payload


def test_another_users_quiz_draft_and_attempt_cannot_be_touched(client, owners):
    quiz = client.post(
        f"{API}/quizzes",
        json={
            "subject": "DBMS",
            "topic": "Normalization",
            "difficulty": "medium",
            "question_count": 3,
            "duration_minutes": 10,
            "title": "Owner Quiz",
        },
        headers=owners["owner_h"],
    )
    assert quiz.status_code == 201, quiz.text
    quiz_id = quiz.json()["id"]
    assert refused(
        client.post(f"{API}/quizzes/{quiz_id}/publish", json={}, headers=owners["intruder_h"])
    )

    published = client.post(f"{API}/quizzes/{quiz_id}/publish", json={}, headers=owners["owner_h"])
    assert published.status_code == 200, published.text
    attempt = client.post(f"{API}/quizzes/{quiz_id}/attempts", headers=owners["owner_h"])
    assert attempt.status_code == 201, attempt.text
    attempt_id = attempt.json()["id"]
    question_id = attempt.json()["questions"][0]["question_id"]
    path = f"{API}/attempts/{attempt_id}"

    intruder = owners["intruder_h"]
    assert refused(client.get(path, headers=intruder))
    assert refused(
        client.put(
            f"{path}/answers", json={"answers": {str(question_id): "False"}}, headers=intruder
        )
    )
    assert refused(client.post(f"{path}/submit", json={"confirm": True}, headers=intruder))
    assert refused(client.post(f"{path}/evaluate", json={}, headers=intruder))

    mine = client.get(path, headers=owners["owner_h"])
    assert mine.status_code == 200
    assert mine.json()["status"] == "in_progress"
    assert {q["response"] for q in mine.json()["questions"]} == {None}
    assert "Owner Quiz" not in client.get(f"{API}/attempts", headers=intruder).text


def test_another_users_ai_work_cannot_be_reviewed_or_continued(client, owners):
    intruder = owners["intruder_h"]
    generation = client.post(
        f"{API}/ai/generations",
        json={"subject": "DBMS", "topic": "Normalization", "difficulty": "medium", "count": 5},
        headers=owners["owner_h"],
    )
    assert generation.status_code == 201, generation.text
    generation_id = generation.json()["id"]
    question_id = generation.json()["questions"][0]["id"]
    gen_path = f"{API}/ai/generations/{generation_id}"
    q_path = f"{gen_path}/questions/{question_id}"

    assert refused(client.get(gen_path, headers=intruder))
    owners_draft = generation.json()["questions"][0]
    edit = {
        "text": "Hijacked",
        "options": owners_draft["options"],
        "correct_answer": owners_draft["correct_answer"],
        "explanation": owners_draft.get("explanation") or "x",
    }
    assert refused(client.put(q_path, json=edit, headers=intruder))
    assert refused(client.post(f"{q_path}/approve", headers=intruder))
    assert refused(client.post(f"{q_path}/reject", json={}, headers=intruder))
    assert refused(client.post(f"{gen_path}/quiz", json={}, headers=intruder))
    review = client.get(gen_path, headers=owners["owner_h"]).json()
    assert "Hijacked" not in json.dumps(review)
    assert {q["review_status"] for q in review["questions"]} == {"pending"}
    assert not any(q["edited"] for q in review["questions"])

    adaptive = client.post(
        f"{API}/ai/adaptive-quizzes",
        json={"subject": "DBMS", "topic": "Normalization", "question_count": 5},
        headers=owners["owner_h"],
    )
    assert adaptive.status_code == 201, adaptive.text
    session_path = f"{API}/ai/adaptive-quizzes/{adaptive.json()['id']}"
    assert refused(client.get(session_path, headers=intruder))
    assert refused(
        client.post(
            f"{session_path}/answers", json={"question_id": 1, "response": "True"}, headers=intruder
        )
    )

    ask = client.post(
        f"{API}/ai/ask", json={"query": "Explain normalization in DBMS"}, headers=owners["owner_h"]
    )
    assert ask.status_code in (200, 201), ask.text
    assert refused(client.post(f"{API}/ai/ask/{ask.json()['id']}/start", json={}, headers=intruder))


def test_another_users_conversation_and_notifications_stay_private(client, db, owners):
    intruder = owners["intruder_h"]
    conversation = client.post(
        f"{API}/conversations", json={"user_id": owners["friend"]}, headers=owners["owner_h"]
    )
    assert conversation.status_code in (200, 201), conversation.text
    path = f"{API}/conversations/{conversation.json()['id']}"
    assert refused(client.get(path, headers=intruder))
    assert refused(client.get(f"{path}/messages", headers=intruder))
    assert refused(client.post(f"{path}/messages", json={"body": "spoofed"}, headers=intruder))
    history = client.get(f"{path}/messages", headers=owners["owner_h"]).json()
    assert "spoofed" not in json.dumps(history)

    notifications.notify(
        db, user_id=owners["owner"], kind=NotificationKind.QUIZ_UPDATE, title="Your quiz is live"
    )
    db.commit()
    inbox = client.get(f"{API}/notifications", headers=owners["owner_h"]).json()
    item = members_list(inbox)[0]
    assert refused(client.post(f"{API}/notifications/{item['id']}/read", headers=intruder))
    assert (
        client.get(f"{API}/notifications/unread-count", headers=owners["owner_h"]).json()[
            "unread_count"
        ]
        == 1
    )
    assert "Your quiz is live" not in client.get(f"{API}/notifications", headers=intruder).text


def test_reports_and_the_moderation_queue_are_limited_to_who_may_see_them(client, owners):
    question = client.get(f"{API}/questions", headers=owners["owner_h"]).json()
    question_id = members_list(question)[0]["id"]
    report = client.post(
        f"{API}/questions/{question_id}/reports",
        json={"reason": "incorrect", "details": "Answer is wrong"},
        headers=owners["owner_h"],
    )
    assert report.status_code == 201, report.text
    report_path = f"{API}/moderation/reports/{report.json()['report_id']}"

    intruder = owners["intruder_h"]
    assert refused(client.get(f"{API}/moderation/queue", headers=intruder))
    assert refused(client.get(report_path, headers=intruder))
    assert refused(client.post(f"{report_path}/remove", json={"confirm": True}, headers=intruder))
    assert refused(client.post(f"{report_path}/dismiss", json={}, headers=intruder))
    still_there = client.get(f"{API}/questions/{question_id}", headers=owners["owner_h"])
    assert still_there.status_code == 200


def test_profile_changes_apply_only_to_the_caller(client, owners):
    before = client.get(f"{API}/profiles/{owners['owner']}", headers=owners["owner_h"]).json()
    changed = client.patch(
        f"{API}/profiles/me",
        json={"display_name": "Intruder Renamed"},
        headers=owners["intruder_h"],
    )
    assert changed.status_code == 200, changed.text
    after = client.get(f"{API}/profiles/{owners['owner']}", headers=owners["owner_h"]).json()
    assert after == before


def test_UT_06_a_regular_member_cannot_manage_official_content(client, db, owners):
    faculty = make_user(db, "sec_prof", Role.AUTHORIZED_USER)
    official = channels.create_channel(
        db,
        name="DBMS (official)",
        subject="DBMS",
        created_by=faculty,
        channel_type=ChannelType.AUTHORIZED,
    )
    channels.set_approval(db, official.id, ApprovalStatus.APPROVED)
    channels.add_member(db, official.id, owners["intruder"])
    db.commit()
    uploaded = client.post(
        f"{API}/materials",
        data={
            "title": "Unit1 Official",
            "subject": "DBMS",
            "topic": "Normalization",
            "semester": "5",
            "material_type": "notes",
            "channel_id": str(official.id),
            "is_official": "true",
        },
        files={"file": ("unit1.pdf", PDF, "application/octet-stream")},
        headers=auth(client, "sec_prof"),
    )
    assert uploaded.status_code == 201, uploaded.text
    path = f"{API}/materials/{uploaded.json()['id']}"
    member = owners["intruder_h"]

    edit = client.patch(path, json={"title": "Hacked"}, headers=member)
    remove = client.delete(path, headers=member)
    assert (edit.status_code, edit.json()["detail"]) == (403, "Insufficient permissions")
    assert (remove.status_code, remove.json()["detail"]) == (403, "Insufficient permissions")
    assert client.get(path, headers=member).json()["title"] == "Unit1 Official"


def test_IT_20_a_non_member_is_denied_the_channel_chat(client, owners):
    channel = client.post(
        f"{API}/channels",
        json={"name": "DBMS Study Group", "subject": "DBMS"},
        headers=owners["owner_h"],
    ).json()["id"]
    chat = f"{API}/channels/{channel}/messages"
    sent = client.post(chat, json={"body": "ER diagram notes"}, headers=owners["owner_h"])
    assert sent.status_code == 201, sent.text

    read = client.get(chat, headers=owners["intruder_h"])
    write = client.post(chat, json={"body": "let me in"}, headers=owners["intruder_h"])
    assert (read.status_code, read.json()["detail"]) == (403, "Join the channel to view this chat")
    assert (write.status_code, write.json()["detail"]) == (403, "Join the channel to send messages")
    assert "ER diagram notes" not in read.text
    history = client.get(chat, headers=owners["owner_h"]).text
    assert "let me in" not in history
