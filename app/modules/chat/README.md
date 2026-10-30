# Chat module

Live conversation. Implements the FR-10 channel chat (story **S10.1**, issue #36);
threads and reply counts (**S10.2**, #37), direct conversations (**S11.1**, #38) and
in-chat sharing (**S11.2**, #39) build on it. Owns `channel_messages`,
`conversations`, `conversation_participants`, `direct_messages` and `user_blocks`;
module rules are in [`docs/architecture.md`](../../../docs/architecture.md).

Chat is separate from the channel discussion board (`channel_posts`, S03.2): a post is
a standing, editable topic; a chat message is part of the live stream.

## Channel chat (S10.1)

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| `POST` | `/api/v1/channels/{id}/messages` | member | Send `body` (1–2000 characters after trimming). |
| `GET` | `/api/v1/channels/{id}/messages?limit=&before=&after=` | member | Read the chat, a page at a time. |

Each message carries `id`, `channel_id`, `sender_id`, `sender_name`, `body` and
`created_at`. The sender is always the signed-in user and the timestamp is the
server's, so every member sees the same sender and time (UT_19).

Rules:

- A blank or whitespace-only message (or no `body` at all) is **422** with the Test
  Plan wording `Message cannot be empty`, and nothing is stored (UT_20). The UI should
  also disable Send while the box is blank; the server check holds either way.
- Only members read or send. A visible channel you haven't joined is **403**
  `Join the channel to view this chat` (reading, IT_20's wording) or
  `Join the channel to send messages` (sending). A channel you can't see is **404**
  `Channel not found`, so a pending Authorized Channel's existence isn't revealed.
  Membership is read on every request, so leaving or being removed closes the chat at
  once. Admins are not let in: private group chat stays private, and moderation goes
  through reports (S11.1, S14.2).
- Order is the message `id`, which the database assigns as messages are accepted, so
  two messages sent in the same second never swap places (ST_19). Messages are stored,
  so they survive logout and reload unchanged.
- Paging (ST_20). Every page is oldest first, ready to render top to bottom:
  - no cursor: the newest `limit` messages (default 50, max 100) — opening the chat;
  - `before=<oldest id shown>`: the page just older — scrolling up; repeat until
    `has_more` is false;
  - `after=<newest id shown>`: the page just newer — polling for new messages;
    `has_more` then means more new messages are waiting.
  Passing both cursors is **422** `Use either before or after, not both`.
- Deleted messages (`is_deleted`) are never returned.

Shared resources are covered under [In-chat sharing (S11.2)](#in-chat-sharing-s112).

## Threads (S10.2)

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| `POST` | `/api/v1/channels/{id}/messages` with `parent_id` | member | Reply in a thread. |
| `GET` | `/api/v1/channels/{id}/messages/{message_id}/replies?limit=&before=&after=` | member | Open a thread: `parent` plus a page of its replies. |

Every message now carries `parent_id` (null for a top-level message), `reply_count`
and `last_reply_at` (0 and null when nobody has replied).

Rules:

- A reply is stored with `parent_id` pointing at the message it answers (IT_19). The
  parent must be a visible message in the same channel; otherwise **404**
  `Message not found`. Blank replies get UT_20's `Message cannot be empty`.
- Threads are **one level deep**, like Slack: replying to a reply joins the thread of
  its parent (the stored `parent_id` is the top-level message), and a reply's id opens
  that same thread. Nested sub-threads would be hard to follow in a study-group chat.
- The main stream (`GET .../messages`) holds top-level messages only; replies are
  displayed nested under their parent, which shows `reply_count` and
  `last_reply_at`. Deleted replies are neither shown nor counted.
- A thread page is oldest first and pages exactly like the main stream (`before`
  scrolls up, `after` polls, `has_more`). Polling the main stream with `after` only
  returns new top-level messages, so the UI refreshes reply counts by re-reading the
  visible page (or the open thread with `after`).
- Membership is checked on every request, for threads too (IT_20): a non-member is
  **403** `Join the channel to view this chat` (reading) or
  `Join the channel to send messages` (replying), straight from the API, whatever the
  UI shows (NFR-02).

## Direct conversations (S11.1, issue #38)

A one-to-one conversation can be started with any registered user (the profile's
**Message** button). The same two users always share one conversation, whoever starts
it: `POST /conversations` returns **201** when it is new and **200** with the existing
one otherwise. A conversation is private to its two participants; anyone else gets
**404** `Conversation not found`, so its existence is not revealed.

Messages are 1–2000 characters after trimming; a blank one is refused with
`Message cannot be empty` and nothing is stored. The sender and time are always the
server's. History is ordered by message id (the order the database accepted the
messages, which never ties) and paged with cursors, oldest first within each page,
so it reloads identically on return or after re-login.

### Blocking

`PUT /blocks/{user_id}` with `{"confirm": true}` blocks a user (without it: **400**
`Confirm blocking this user`). The block is a row in `user_blocks`, checked on every
send, so it takes effect on the very next message and persists across sessions until
`DELETE /blocks/{user_id}` lifts it. Either side's block stops messages **both ways**:

| Sender | Refusal (403) |
| --- | --- |
| the user who was blocked | `You can no longer send messages to this user` |
| the user who blocked | `You have blocked this user. Unblock them to send messages` |

Conversation responses carry the "blocked" indicator: `blocked` (either side),
`blocked_by_me` (so the UI can offer Unblock) and `can_send`. History stays readable.

### Reporting a message

`POST /conversations/{id}/messages/{message_id}/reports` with a `reason` (the Test
Plan's "Inappropriate content" is `inappropriate`) files an open report in the
moderation queue with `target_type = direct_message` and the message id. Only a
participant can report, never their own message; a missing reason is **422**
`Select a reason for reporting this message`. Reporting the same message again while
its report is open returns that report. Moderators read the reported message with
`chat.service.get_direct_message` (the admin queue screen is S14.2).

### Endpoints

All under `/api/v1`, signed in.

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/conversations` | Start (or reopen) a conversation: `{"user_id": n}`. |
| `GET` | `/conversations` | My conversations, most recent activity first, with the last message. |
| `GET` | `/conversations/{id}` | One conversation with its blocked indicator. |
| `POST` | `/conversations/{id}/messages` | Send a message. |
| `GET` | `/conversations/{id}/messages?limit=&before=&after=` | History page (newest `limit` without a cursor; `before` scrolls up, `after` polls). |
| `POST` | `/conversations/{id}/messages/{message_id}/reports` | Report a received message (`report:submit`). |
| `GET` | `/blocks` | Users I have blocked. |
| `PUT` | `/blocks/{user_id}` | Block (needs `confirm: true`; repeating changes nothing). |
| `DELETE` | `/blocks/{user_id}` | Unblock. |

### Direct-conversation service functions

`find_conversation`, `open_conversation` (refuses a user with themselves),
`get_conversation`, `conversations_for`, `conversation_participants`,
`send_direct_message`, `get_direct_message`, `direct_messages_page`, `has_blocked`
(one direction), `is_blocked` (either direction), `block_user`, `unblock_user`,
`blocked_by`. All return ids or dataclasses, never ORM objects.

## In-chat sharing (S11.2, issue #39)

Any message, in a channel chat, a thread or a direct conversation, can carry a
material, question or quiz as a **live link**. Send it with the message:

```json
{"body": "Read this before Friday", "shared_resource": {"type": "material", "id": 12}}
```

`body` may be blank when a resource is shared (the card is the message); a blank
message with nothing shared still gets `Message cannot be empty`. Every message in a
response carries `shared_resource`: `null`, or the preview card:

| Field | Meaning |
| --- | --- |
| `type`, `id` | What was shared. |
| `available` | False when the item was removed or this reader may not open it. |
| `title`, `subtitle` | Live from the original (material title / question text / quiz title; type, subject, topic, size). Null title and `This item is no longer available` when unavailable. |
| `web_path` | Web UI route that opens the original: `#/materials/<id>`, `#/questions/<id>`, `#/quizzes/<id>/attempt` (the quiz start screen, ST_22). |
| `api_path` | The original's API resource (`/api/v1/materials/<id>`, ...). |
| `channel_id` | The channel the original belongs to, if any. |

Rules:

- **A reference, not a copy.** The message stores only `shared_resource_type` and
  `shared_resource_id` (columns since migration `0001`). The card is rebuilt from the
  original on every read, so edits show in old messages and the link keeps working.
- **The sender must be able to open it**, otherwise **404** `Shared item not found`
  and nothing is stored (so a share can't probe for hidden content). Adaptive quiz runs
  are one person's session and can't be shared.
- **The card follows the reader's access**, using the same rules as the owning
  module's `GET` endpoint (`app/modules/chat/sharing.py`): a channel-only material
  shared with a non-member shows as unavailable with no title, and opening it is still
  refused by `/materials/{id}`. A question card never includes the answer key.
- Blocking still stops shares like any other direct message.

The chat screens that render the cards are UI-3 (#64); the question detail route
`#/questions/<id>` arrives with it.

## Service interface for other modules

`app.modules.chat.service`: `post_channel_message`, `channel_history`,
`get_channel_message`, `channel_messages_page` (all `MessageView`; pass `thread_of`
for a thread's replies), `thread_root`, `thread_summaries` (`ThreadSummary`), `is_blocked`,
`open_conversation`, `send_direct_message`, and from NFR-05 `blocks_involving` (both
directions of a user's blocks in one query). All return dataclasses or plain values.
The conversation list (`conversations_for`) and a page of channel messages cost a
fixed number of queries however many conversations or senders there are (NFR-05).
No other module may depend on chat (`test_no_module_imports_chat`); chat itself calls
accounts (route guards, sender names), channels (visibility, membership),
moderation (reporting a direct message) and materials, questions and quizzes (preview
cards for shared resources, S11.2).

## Tests

`tests/test_channel_chat.py` — Test Plan cases `UT_19`, `UT_20`, `ST_19`, `ST_20`
under their IDs, plus a test per S10.1 acceptance criterion and the access rules above.

`tests/test_direct_chat.py` — S11.1 Test Plan cases and acceptance criteria for
direct conversations, blocking and reporting.

`tests/test_in_chat_sharing.py` — S11.2 cases `ST_21`, `ST_22`, a test per acceptance
criterion and the sharing rules above.

`tests/test_channel_threads.py` — S10.2 cases `IT_19`, `IT_20` and a test per
acceptance criterion and thread rule.
