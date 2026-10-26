# Reputation module

Peer reputation, badges and the contributor leaderboard: **FR-13**, story **S13.1**
(issue #42), plus the reputation and badges shown on profiles (S01.2). Owns
`reputation_events`, `badges` and `user_badges`; may call `accounts`. Module rules are
in [`docs/architecture.md`](../../../docs/architecture.md).

## How reputation is earned

- A user's reputation is **the sum of their `reputation_events`**. Every change is one
  event with `points`, a `reason` and its source (`source_type`, `source_id`), so a
  total can always be traced back to the ratings that produced it.
  `users.reputation` is only a cache, kept in step by `award`.
- `materials.rate_material` and `questions.rate_question` call
  `record_peer_rating` after saving a rating. The source is the rating row
  (`material_review` / `question_rating` + its id), which is unique per rater and item,
  so a rating counts once however often it is changed.
- A **positive** rating (at least `REPUTATION_POSITIVE_RATING` stars, default 4) is
  worth `REPUTATION_POINTS_PER_POSITIVE_RATING` points (default 5). Lowering it below
  the threshold withdraws the points with a compensating `peer_rating_withdrawn`
  event; raising it again re-awards them. Ratings of 1–3 earn nothing.
- **A self-rating never changes reputation.** Materials already refuse it (403);
  questions accept it for the average, and `record_peer_rating` ignores it.

## Badges

`BADGE_LEVELS` in `service.py`, lowest first. `award` grants every level whose
threshold the new total has reached; a jump across several thresholds grants each
once. Badges are **kept** if reputation later drops. Badge rows are created on first
award (`ensure_badge`), so no migration or seed is needed.

| Code | Name | Threshold |
| --- | --- | --- |
| `rising-contributor` | Rising Contributor | 75 |
| `top-contributor` | Top Contributor | 100 |
| `community-champion` | Community Champion | 250 |

Earned badges appear on `GET /api/v1/profiles/me` and `/profiles/{user_id}`.

## Endpoints

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| `GET` | `/api/v1/leaderboard?limit=&offset=` | bearer | Users with positive reputation, highest first. Each row: `rank`, `user_id`, `display_name`, `username`, `reputation`, `badges` (codes). Equal totals share a rank (1, 2, 2, 4) and are listed by user id. `me` is the caller's `rank` (`null` until they have reputation) and `reputation`. |
| `GET` | `/api/v1/reputation/me?limit=&offset=` | bearer | The caller's total, rank, earned badges and reputation events (newest first) with `event_count`. |
| `GET` | `/api/v1/badges` | bearer | Every badge level with its threshold and whether the caller has earned it. |

## Public interface

`award`, `total`, `record_peer_rating`, `award_threshold_badges`, `ensure_badge`,
`grant_badge`, `badges_for`, `badge_codes_for`, `events_for`, `leaderboard`,
`rank_of`, `BADGE_LEVELS`. All return dataclasses or plain values.

Tests: `tests/test_reputation.py` (UT_25, UT_26, ST_26 and one test per acceptance
criterion); the leaderboard screen is covered in `tests/test_web_ui.py`.
