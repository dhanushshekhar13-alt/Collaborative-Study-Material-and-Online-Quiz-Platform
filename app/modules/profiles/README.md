# Profiles module

The profile page's backend. Implements the profile part of **FR-01** (story **S01.2**,
issue #19). It owns no tables: a profile is assembled on every read from the public
services of the modules that own the data, so what the page shows always matches the
backend records (ST_02). Module rules are in
[`docs/architecture.md`](../../../docs/architecture.md).

| Section | Comes from |
| --- | --- |
| Display name, bio, institution, picture | `accounts` (`users`, `profile_pictures`) |
| Contributions | `materials.count_uploaded_by`, `questions.count_authored_by`, `quizzes.count_created_by` (+ `recent_*`) |
| Reputation | `reputation.total` — the sum of `reputation_events`, not the cached `users.reputation` |
| Badges | `reputation.badges_for` |

A contribution counts while it is live: active materials, **published** questions and
quizzes. Removed items, LLM drafts awaiting review and unpublished quizzes are not
counted. `recent` lists the newest ten across all three kinds.

## Endpoints

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| `GET` | `/api/v1/profiles/me` | bearer | The caller's profile. |
| `PATCH` | `/api/v1/profiles/me` | bearer | Change `display_name`, `bio` (≤ 500 chars), `institution`. Omitted fields are unchanged; blank bio/institution clears it. |
| `PUT` | `/api/v1/profiles/me/picture` | bearer | Multipart field `file`. Sets or replaces the picture. |
| `DELETE` | `/api/v1/profiles/me/picture` | bearer | Removes the picture. |
| `GET` | `/api/v1/profiles/{user_id}` | bearer | Anyone's profile. Email is never included. |
| `GET` | `/api/v1/profiles/{user_id}/picture` | public | The image bytes, so an `<img>` tag can load it without a token. |

A brand-new user gets `null` bio/institution/picture, `reputation: 0`, zero counts and
empty `recent` and `badges` lists; the UI renders those as empty states.

`picture_url` carries a `?v=` version that changes whenever the picture does, so
browsers never show a stale picture after an update.

## Picture rules

| Upload | Response |
| --- | --- |
| JPEG, PNG, GIF or WebP up to 2 MB | `200`, the updated profile |
| Larger than 2 MB | `413` `Profile picture must be 2 MB or smaller` |
| Anything else (including SVG, or a renamed non-image) | `422` `Profile picture must be a JPEG, PNG, GIF or WebP image` |
| Empty file | `422` `Choose an image to upload` |

The type is detected from the file's bytes; the filename and the client's
Content-Type are ignored. SVG is refused because it can carry script. Pictures are
served with the detected type, `X-Content-Type-Options: nosniff` and a restrictive CSP.
A refused upload leaves the previous picture in place.

## Tests

`tests/test_profiles.py` — Test Plan cases `ST_01` and `ST_02` under their case IDs,
plus upload validation, empty state, counting rules and access tests.
