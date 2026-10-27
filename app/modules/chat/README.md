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

Shared resources are in the table already but not exposed by these endpoints yet;
S11.2 adds them to the request and response.

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

## Service interface for other modules

`app.modules.chat.service`: `post_channel_message`, `channel_history`,
`get_channel_message`, `channel_messages_page` (all `MessageView`; pass `thread_of`
for a thread's replies), `thread_root`, `thread_summaries` (`ThreadSummary`), `is_blocked`,
`open_conversation`, `send_direct_message`. All return dataclasses or plain values.
No other module may depend on chat (`test_no_module_imports_chat`); chat itself calls
accounts (route guards, sender names) and channels (visibility, membership).

## Tests

`tests/test_channel_chat.py` — Test Plan cases `UT_19`, `UT_20`, `ST_19`, `ST_20`
under their IDs, plus a test per S10.1 acceptance criterion and the access rules above.
`tests/test_channel_threads.py` — S10.2 cases `IT_19`, `IT_20` and a test per
acceptance criterion and thread rule.
