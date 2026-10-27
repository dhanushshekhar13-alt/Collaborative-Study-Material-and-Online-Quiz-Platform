# Admin module

Admin management (FR-14, story **S14.2**, issue #45): suspend users, decide Authorized
Channel requests, and remove rule-violating content — with the poster notified and every
action in an audit log. The module owns no tables: it is assembled from the public
services of accounts, channels, materials, questions, moderation and notifications. The
audit log is moderation's `moderation_actions` table; module rules are in
[`docs/architecture.md`](../../../docs/architecture.md).

## Access (IT_28)

Every route is guarded on the server by a capability only admins hold
(`admin:dashboard`, `user:manage`, `channel:approve`, `report:resolve`). A student or
authorized user who calls an admin URL directly gets **403** `Unauthorized` and none of
the admin data; without a token it is **401**. Hiding the admin menu in the UI is a
convenience, not the protection.

## Endpoints

All under `/api/v1/admin`.

| Method | Path | Capability | Purpose |
| --- | --- | --- | --- |
| `GET` | `/dashboard` | `admin:dashboard` | Counts: users, suspended users, pending channel requests, open reports, flagged items. |
| `GET` | `/users?q=&status=active\|suspended` | `user:manage` | Admin > User Management, each with `status` `Active` / `Suspended`. |
| `POST` | `/users/{id}/suspend` | `user:manage` | Suspend; body `{"confirm": true, "reason": "..."}` (IT_27). |
| `POST` | `/users/{id}/reinstate` | `user:manage` | Lift a suspension; optional `reason`. |
| `GET` | `/channel-requests` | `channel:approve` | Pending Authorized Channels with their requester (ST_27). |
| `GET` | `/reports?target_type=` | `report:resolve` | Flagged content: open reports grouped per item, most-reported first. |
| `POST` | `/content/{material\|question}/{id}/remove` | `report:resolve` | Remove Content — Rule Violation; body `{"reason": "..."}` (ST_28). |
| `GET` | `/audit-log?limit=&before=&target_type=&target_id=` | `admin:dashboard` | Every admin action, newest first. |

Channel decisions themselves stay at `POST /api/v1/channels/{id}/approve|reject`, which
now log the decision and notify the requester (see the channels README).

## Rules

- **Suspension** sets the account's status to Suspended: login is refused with the same
  401 as a wrong password, and tokens issued earlier stop working on the next request.
  It needs `confirm: true` (**400** `Confirm suspending this account`). An admin cannot
  suspend themselves (`You cannot suspend your own account`) or another admin
  (`An admin account cannot be suspended`). Suspending twice is **409**
  `This account is already suspended`; reinstating an active account is **409**
  `This account is not suspended`.
- **Content removal** (materials and questions) needs a reason (**422**
  `Give a reason for removing this content`). The item's status becomes removed, so it
  disappears from search, channels and direct links for everyone; its open reports are
  resolved with the reason; the poster receives a `moderation` notification titled
  `Your material "<title>" was removed` explaining why; and the removal is logged. A
  missing or already removed item is **404** `Content not found`.
- **Audit log**: every suspension, reinstatement, channel decision and removal is one
  `moderation_actions` row with actor, action, target type and id, the related report,
  the note and a timestamp, written in the same transaction as the change.

## Notifications inbox

The poster reads the explanation at `GET /api/v1/notifications` (their own, newest
first, with `unread_count`; `?unread_only=true`) and marks one read with
`POST /api/v1/notifications/{id}/read` (someone else's is **404**). These routes live in
the notifications module (S14.1).

## Service interface

`app.modules.admin.service`: `suspend_user`, `reinstate_user` (→ `AccountDetails`),
`remove_content` (→ `RemovedContent`), `dashboard` (→ `DashboardSummary`). Refusals raise
`NotFound`, `NotAllowed` or `Conflict` (all `AdminActionError`), whose message is the
wording above. No other module calls admin.

New service functions elsewhere: `accounts.AccountDetails.is_suspended`;
`moderation.flagged_targets`, `count_open_reports`, `audit_log`. Removal closes the
open reports with S13.2's `moderation.close_reports_on`.

## Tests

`tests/test_admin.py` — Test Plan cases `IT_27`, `IT_28`, `ST_27`, `ST_28` under their
IDs, plus a test per S14.2 acceptance criterion.
