# Notifications module

In-app notifications with per-user preferences (FR-14, story **S14.1**, issue #44). Owns
`notifications` and `notification_preferences` (both since migration `0001`); module
rules are in [`docs/architecture.md`](../../../docs/architecture.md).

## What creates a notification

Nothing imports notifications. The modules where things happen publish an event
through `app/core/events.py` with the people it concerns, and this module's service
subscribes. Handlers run in the publisher's transaction, so a message and its
notifications commit together.

| Event (publisher) | Category (`kind`) | Who | Title | Link |
| --- | --- | --- | --- | --- |
| `chat.channel_message_posted` (chat) — a reply | `reply` | author of the message replied to | `Meera K replied to your message in DBMS Study Group` | `/app/#/channels/{id}?message={id}` |
| `chat.channel_message_posted` (chat) — `@username` | `mention` | each named **member** of the channel, once | `Priya S mentioned you in DBMS Study Group` | same |
| `quizzes.quiz_published` (quizzes) | `quiz_update` | the channel's members except the author | `New quiz "Normalization Quiz" in DBMS Study Group` | `/app/#/channels/{id}?quiz={id}` |
| `channels.post_created` (channels) | `channel_activity` | the channel's members except the author | `Meera K posted in DBMS Study Group` | `/app/#/channels/{id}?post={id}` |

Rules: nobody is notified about their own action; a reply that also mentions the
person gives one `reply` notification (or the `mention`, if they switched replies
off); `@names` of non-members, unknown names and the inside of email addresses are
ignored. Mentions are found by `chat.service.mentioned_members`. The links are web UI
routes; UI-3 (#64) opens the chat at that message.

`direct_message` and `moderation` notifications are written by the stories that own
those features (S11.1, S14.2) through `service.notify`, which already honours the
preferences.

## Endpoints

All under `/api/v1/notifications`, signed in, always the caller's own.

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `?unread_only=&limit=` | Newest first, with `unread_count`. |
| `GET` | `/unread-count` | Just the bell's number. |
| `POST` | `/{id}/read` | Mark one read (**204**); someone else's is **404** `Notification not found`. |
| `POST` | `/read-all` | Mark all read. |
| `GET` | `/preferences` | Notification Settings: every category with `label` and `enabled`. |
| `PUT` | `/preferences` | `{"preferences": {"quiz_update": false}}`; only the named categories change. |

Preferences are rows in `notification_preferences`, so they persist across sessions
and devices. A category is on until switched off. Switching one off stops new
notifications of that kind being created at all (UT_28); others keep arriving, and
what was already received stays in the list.
