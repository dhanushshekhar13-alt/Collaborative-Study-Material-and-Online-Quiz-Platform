# Channels module

Group spaces where collaborative activity happens. Implements **FR-02** (story
**S02.1**, issue #20). Owns `channels` and `channel_memberships`; module rules are in
[`docs/architecture.md`](../../../docs/architecture.md).

## Channel types

| Type | Who can create | Starts as | Label / style key |
| --- | --- | --- | --- |
| Normal | any signed-in user (`channel:create_normal`) | approved | `Normal` / `channel-normal` |
| Authorized | authorized users and admins (`channel:create_authorized`) | pending; an admin's own is approved at once | `Authorized` / `channel-authorized` |

A Student asking for an Authorized Channel gets **403** with the Test Plan wording
`Only authorized users/admins can create Authorized Channels` (UT_04). The check runs
on the server, whatever the UI shows.

Every channel response carries `type_label`, `type_style` and `is_verified` computed in
one place, so lists, search results and channel pages always show the type the same
way (ST_04). The UI maps `type_style` to its styling.

## Visibility

Approved channels are visible to everyone signed in. A pending or rejected Authorized
Channel is visible only to the person who requested it and to admins; others get
**404**, so its existence isn't revealed. Approval makes it visible platform-wide (ST_27).

## Endpoints

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| `POST` | `/api/v1/channels` | signed in | Create; name plus subject or topic required. Creator becomes owner. |
| `GET` | `/api/v1/channels?q=&type=` | signed in | List / search by name, subject or topic; filter by `normal` or `authorized`. |
| `GET` | `/api/v1/channels/{id}` | signed in | Channel page, with the caller's `my_role`. |
| `GET` | `/api/v1/channels/requests` | `channel:approve` | Admin > Channel Requests (pending Authorized Channels). |
| `POST` | `/api/v1/channels/{id}/approve` | `channel:approve` | Approve a pending request. |
| `POST` | `/api/v1/channels/{id}/reject` | `channel:approve` | Reject a pending request. |

Deciding a request that's already decided returns **409**.

## Service interface for other modules

`app.modules.channels.service`: `create_channel`, `get_channel`, `describe_channel`,
`list_channels`, `pending_requests`, `set_approval`, `is_visible_to`, `add_member`,
`member_role`, `is_member`. All return `ChannelSummary` dataclasses or plain values.
Use `is_member` to gate channel-scoped content (materials, quizzes, chat).

Membership management (join, leave, remove members) is S02.2, issue #21.

## Tests

`tests/test_channels.py` — Test Plan cases `UT_03`, `UT_04`, `ST_04`, `ST_27` under
their IDs, plus visibility, search and validation tests.
