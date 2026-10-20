# Channels module

Group spaces where collaborative activity happens. Implements **FR-02** (stories
**S02.1**, issue #20, and **S02.2**, issue #21). Owns `channels` and `channel_memberships`; module rules are in
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

### Membership (S02.2)

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| `GET` | `/api/v1/channels/mine` | signed in | My Channels, each with the caller's `my_role`. |
| `POST` | `/api/v1/channels/{id}/join` | signed in | Join a visible channel; effective at once (IT_03). |
| `POST` | `/api/v1/channels/{id}/leave` | member | Leave; the channel leaves My Channels and its content closes (IT_04). |
| `GET` | `/api/v1/channels/{id}/members` | member or admin | Member list (owner, moderators, members). Channel-restricted. |
| `DELETE` | `/api/v1/channels/{id}/members/{user_id}?confirm=true` | creator or moderator | Remove a member after confirming (ST_03). |

Rules:

- Joining twice, or leaving a channel you're not in, returns **409**. The creator
  cannot leave their own channel (**409**), so a channel always has an owner.
- Only channels you can see are joinable; a pending Authorized Channel is **404**.
- Who may remove whom: the creator (owner) removes moderators and members; a moderator
  removes members only; a regular member or non-member gets **403**
  `Only the channel creator or a moderator can remove members`. Removing someone above
  your role is **403**; removing yourself is **400** (use Leave).
- Removal without `confirm=true` is refused with **400** `Confirm removal of this member`,
  so the API cannot skip the UI's confirm step.
- Access to channel-restricted content is `is_member`, read on every request, so it
  ends the moment a member leaves or is removed. Admins may read member lists for
  moderation; admin member management is S14.2 (#45).
- There is no endpoint yet to promote a moderator; moderators are assigned with
  `add_member(..., MemberRole.MODERATOR)`.

## Service interface for other modules

`app.modules.channels.service`: `create_channel`, `get_channel`, `describe_channel`,
`list_channels`, `pending_requests`, `set_approval`, `is_visible_to`, `add_member`,
`member_role`, `is_member`, and from S02.2 `remove_member`, `list_members`
(`MemberSummary`), `channels_for_member`, `can_manage_members`, `can_remove`. All
return dataclasses or plain values. Use `is_member` to gate channel-scoped content
(materials, quizzes, chat).

## Tests

`tests/test_channels.py` — Test Plan cases `UT_03`, `UT_04`, `ST_04`, `ST_27` under
their IDs, plus visibility, search and validation tests.

`tests/test_channel_membership.py` — `IT_03`, `IT_04`, `ST_03` under their IDs, plus a
test per S02.2 acceptance criterion and the role rules above.
