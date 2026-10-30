# Accounts module

Owns identity for the platform: registration, sign-in, and the authorization guards
every other module's routes use. Implements **FR-01** (story **S01.1**, issue #18)
under **NFR-02**. Module rules are in [`docs/architecture.md`](../../../docs/architecture.md).

## Data

Owns `users`. S01.1 adds a unique `username` (migration `0002`). It is nullable in the
database so accounts created before it (the seeded users) stay valid; registration
always sets it, and those older accounts sign in with their email.

Passwords are stored only as salted scrypt digests (`app.core.security`).

Lists elsewhere name people through `get_users` (one query for many ids), and the
admin dashboard counts accounts with `count_accounts` and `count_suspended_accounts`
rather than loading them (NFR-05).

S01.2 adds `profile_pictures` (migration `0003`): at most one picture per user, kept in
the database (≤ 2 MB) so it survives redeploys without a file volume. Profile reads and
edits go through `get_profile`, `update_profile`, `set_profile_picture`,
`remove_profile_picture` and `get_profile_picture`; `set_profile_picture` validates
size and type (detected from the bytes) and raises `InvalidPictureError` with the
user-facing message. The profile endpoints live in the
[profiles module](../profiles/README.md).

## Protecting a route in another module

Everything is imported from `app.modules.accounts.service`:

```python
from fastapi import APIRouter, Depends

from app.modules.accounts.service import Capability, UserSummary, require_capability

router = APIRouter()


@router.post("/materials")
def upload(user: UserSummary = Depends(require_capability(Capability.MATERIAL_UPLOAD))): ...
```

- `current_user` — the signed-in user as a `UserSummary`, else **401**.
- `require_capability(cap)` — **403 "Unauthorized"** unless the role grants `cap`.
  Prefer this: when a capability later moves to another role, the route is unchanged.
- `require_roles(*roles)` — for checks that really are about the role.

Guards return a `UserSummary` dataclass, never the ORM row. The account is re-read on
every request, so a suspension takes effect immediately, not when the token expires.

> **Before your first guarded route:** add `"accounts"` to your module's entry in
> `ALLOWED_DEPENDENCIES` (`app/modules/__init__.py`), otherwise
> `tests/test_module_boundaries.py` fails.

## Endpoints

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| `POST` | `/api/v1/accounts/register` | public | Create a Student. `201` + `"Registration successful"`. |
| `POST` | `/api/v1/accounts/login` | public | Username **or** email + password → bearer token. |
| `GET` | `/api/v1/accounts/me` | bearer | The caller's account — what the dashboard loads first. |
| `GET` | `/api/v1/accounts/me/permissions` | bearer | Capability list, so the UI renders the right controls. |
| `GET` | `/api/v1/accounts/admin/users` | `user:manage` | Admin-only roster. Full admin management is FR-14. |
| `GET` | `/api/v1/accounts/session-policy` | public | Token lifetime in minutes. |

Error wording is fixed by the Test Plan: `Enter a valid email address` (UT_02),
`Invalid username or password` (IT_02), `Unauthorized` (IT_28). An unknown account,
a wrong password and a suspended account all get the same 401 in the same time, so
sign-in cannot be used to discover which accounts exist. A suspended account's open
sessions stop working on their next request. Admins suspend and reinstate accounts
through `/api/v1/admin/users/{id}/suspend|reinstate` (S14.2, `app/modules/admin`).

## Roles and capabilities

| Capability | Student | Authorized user | Admin |
| --- | :-: | :-: | :-: |
| `material:upload`, `material:rate` | ✓ | ✓ | ✓ |
| `channel:create_normal` | ✓ | ✓ | ✓ |
| `question:contribute`, `quiz:attempt`, `quiz:publish` | ✓ | ✓ | ✓ |
| `report:submit` | ✓ | ✓ | ✓ |
| `channel:create_authorized`, `official_content:manage` | | ✓ | ✓ |
| `channel:approve`, `report:resolve`, `user:manage` | | | ✓ |
| `admin:dashboard` (admin dashboard and audit log, S14.2) | | | ✓ |

`official_content:manage` lets a member of an approved Authorized Channel add, edit and
remove its official materials, questions and quizzes (S03.1); outside one it grants
nothing extra.

Self-registration always produces a Student; a `role` in the request is ignored.

`/me/permissions` lets the UI hide controls a user cannot use. That is a convenience,
not a protection: every protected route re-checks on the server.

## Tokens

A JWT (HS256) carrying `sub`, `role`, `iat`, `exp`, signed with `AUTH_TOKEN_SECRET`
(`app/modules/accounts/tokens.py`). Lifetime is `ACCESS_TOKEN_EXPIRE_MINUTES`
(default 30). Signature and expiry are verified on every request; editing the `role`
claim breaks the signature.

## Tests

`tests/test_accounts.py` — Test Plan cases `UT_01`, `UT_02`, `IT_01`, `IT_02`, `IT_28`
under their case IDs, plus token, suspension and guard tests.
