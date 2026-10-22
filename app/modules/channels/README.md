# Channels module

Group spaces where collaborative activity happens. Implements **FR-02** (stories
**S02.1**, issue #20, and **S02.2**, issue #21) and the FR-03 discussion board (**S03.2**, issue #27). Owns
`channels`, `channel_memberships` and `channel_posts`; module rules are in
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

## Official content (S03.1)

Channel responses carry `can_manage_official_content`: true only in an approved
Authorized Channel, for its members with `official_content:manage` and for admins. The
UI shows "Manage Official Content" only when it is true. The rule lives in
`service.manages_official_content`, which materials (and the question bank) also use,
so the option and the API's `Insufficient permissions` never disagree. Normal Channels
hold no official content; members contribute there as peers.

## Discussion posts (S03.2)

Every channel has a discussion board (`channel_posts`, migration `0008`): standing,
editable topics such as "Share your DBMS normalization notes here". It is separate from
chat (FR-10, S10.1 #36), which is the live message stream.

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| `POST` | `/api/v1/channels/{id}/posts` | member | Post `body` (1–5000 characters); any member, not only the creator (IT_05). |
| `GET` | `/api/v1/channels/{id}/posts` | member or admin | The board, newest first; `limit`, `offset`. |
| `PATCH` | `/api/v1/channels/{id}/posts/{post_id}` | author | Replace the text; the post then has `is_edited: true` and `edited_at`. |
| `DELETE` | `/api/v1/channels/{id}/posts/{post_id}?confirm=true` | author | Delete after confirming. |

Rules:

- Each post carries `author_id` and `author_name`, and `can_edit` (true for its
  author only) so the UI shows Edit and Delete to the author alone.
- Only the author edits or deletes a post, not even the channel creator: **403**
  `Only the author can edit or delete this post`.
- Deleting without `confirm=true` is **400** `Confirm deletion of this post`, so the API
  cannot skip the UI's confirm step.
- Posting needs membership (**403** `Join this channel to post`); reading the board is
  channel-restricted like other content (`Join this channel to view its content`),
  with admins allowed to read for moderation. A blank post is **422**
  `Write something to post`.
- Members contribute materials, bank questions and quizzes through those modules with
  `channel_id`; every one is attributed (`uploader_name`, `author_name`,
  `creator_name`) and can be rated and reported by other members.

## Service interface for other modules

`app.modules.channels.service`: `create_channel`, `get_channel`, `describe_channel`,
`list_channels`, `pending_requests`, `set_approval`, `is_visible_to`, `add_member`,
`member_role`, `is_member`, and from S02.2 `remove_member`, `list_members`
(`MemberSummary`), `channels_for_member`, `can_manage_members`, `can_remove`, and from S03.1
`manages_official_content`, and from S03.2 `create_post`, `get_post`, `list_posts`
(`PostSummary`), `edit_post`, `delete_post`. All
return dataclasses or plain values. Use `is_member` to gate channel-scoped content
(materials, quizzes, chat).

## Tests

`tests/test_channels.py` — Test Plan cases `UT_03`, `UT_04`, `ST_04`, `ST_27` under
their IDs, plus visibility, search and validation tests.

`tests/test_channel_membership.py` — `IT_03`, `IT_04`, `ST_03` under their IDs, plus a
test per S02.2 acceptance criterion and the role rules above.

`tests/test_peer_contribution.py` — `IT_05`, `IT_06`, `ST_05` under their IDs, plus a
test per S03.2 acceptance criterion.
