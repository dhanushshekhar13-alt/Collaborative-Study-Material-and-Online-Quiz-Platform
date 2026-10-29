# Materials module

Shared study resources. Implements **FR-04**: upload, search and download (story
**S04.1**, issue #22) and rating, review, helpful marks and reporting (**S04.2**,
issue #23), and official content in Authorized Channels (**S03.1**, issue #26,
FR-03). Owns
`study_materials`, `material_files` and `material_reviews`; module rules are in
[`docs/architecture.md`](../../../docs/architecture.md).

## Tags

Every upload carries **subject**, **topic**, **semester** and **material type**
(`notes`, `pdf`, `presentation`, `link`, `other`). A blank one is refused with **422**
and its own message; a blank subject gives the Test Plan wording
**`Subject is required`** (UT_08), and the others follow it (`Topic is required`,
`Semester is required`, `Material type is required`). Responses carry `tags`, every tag
in display order (`["Operating Systems", "CPU Scheduling", "Semester 5", "Notes"]`), so
listings show them the same way everywhere (UT_07).

## Files

| Extension | Served as | Preview |
| --- | --- | --- |
| `.pdf` | `application/pdf` | yes |
| `.png`, `.jpg`, `.jpeg` | `image/png`, `image/jpeg` | yes |
| `.txt`, `.md` | `text/plain`, `text/markdown` (UTF-8) | yes |
| `.doc`, `.docx`, `.ppt`, `.pptx` | Word / PowerPoint types | download only |

- The type comes from the extension and is checked against the file's first bytes, so
  a renamed `.exe` or HTML page is refused (`The file's contents do not match its type`).
  The client's `Content-Type` is never trusted.
- Anything else: **422** `This file type is not supported. Upload a PDF, Word,
  PowerPoint, text, PNG or JPEG file`.
- Size limit `MATERIAL_MAX_UPLOAD_MB` (default 20): **413** `File must be 20 MB or smaller`.
- Files are stored in the database (`material_files`, one per material), like profile
  pictures, so every instance serves the same file and redeploys lose nothing. The
  `data` column is deferred: listings read only name, type and size.
- **Reliability (NFR-04).** An upload is one transaction: the material and its file
  commit together, so a crash or dropped connection never leaves a material without its
  file (a transient database failure answers **503**; retry the upload). The SHA-256 of
  the bytes is stored with the file and returned as `file.sha256`; previews and
  downloads check it and send it as `X-Content-SHA256`, and a file that no longer
  matches is refused with **500** `This file is damaged and cannot be downloaded…`
  instead of being served damaged.
- Previews are served `inline`, downloads as `attachment` with the original name
  (`filename*` for non-ASCII). Both send `nosniff`, `no-store`, and a `default-src
  'none'` CSP on images and text, so an uploaded file can never run script.
- A `link` material carries a URL (`http://` or `https://`) instead of a file.

## Visibility

- A material with no channel is visible to everyone signed in.
- A channel-scoped material is visible only to the channel's members (and admins, for
  moderation); others get **403** `Join this channel to view its content` on the
  material, its preview and its download, and never see it in search. Access is checked
  with `channels.service.is_member` on every request, so it follows joins, leaves and
  removals at once. A hidden (pending) channel answers **404**.
- Uploading into a channel needs membership (`Join this channel to add materials`).
  Authorized Channels take uploads only from users with `official_content:manage`
  (`Only authorized users can add content to Authorized Channels`); see below.
- Removed materials (`status = removed`) are never listed or served.

## Official content (S03.1)

- Everything published in an approved **Authorized Channel** is official: only users
  with `official_content:manage` (authorized users) and admins can add it, and it is
  stored with `is_official = true`. Sending `is_official=true` anywhere else is
  **422** `Official content can only be published in an Authorized Channel`.
- Responses carry `content_label` (`Official/Verified` or `Peer`) and `content_style`
  (`content-official` / `content-peer`), and an official material's `tags` start with
  `Official/Verified` (UT_05), so it stands out the same way on every screen. Search
  takes `official=true|false`.
- `can_manage` tells the caller whether to show edit and remove. Official content is
  managed by the channel's authorized members and admins
  (`channels.service.manages_official_content`, the same rule behind the channel's
  `can_manage_official_content`). Peer content is managed by its uploader and admins;
  S03.2 (#27) covers peer management in Normal Channels.
- Anyone else gets **403** `Insufficient permissions` (UT_06). Editing never changes
  the type, file, channel or official flag, and cannot blank a required tag.
- Removing sets `status = removed`: the material leaves listings, its page, preview
  and download (404) at once.

## Ratings, reviews and helpful marks (S04.2)

- `material_reviews` holds **one row per (material, user)**: that user's 1–5 rating,
  optional review text and helpful mark. Rating again replaces the earlier rating and
  text, and marking helpful again changes nothing, so no user can count twice towards
  the average or the helpful count.
- Marking helpful does not need a rating: the row's `rating` is then NULL (migration
  `0005`), which `AVG` and `COUNT(rating)` skip.
- Every material response carries `rating_average` (unrounded; the UI shows one
  decimal, e.g. 4.3), `rating_count`, `review_count`, `helpful_count`, and the caller's
  own `my_rating`, `my_review`, `marked_helpful`. Listings compute these in one grouped
  query per page.
- A missing or out-of-range rating: **422** `Select a rating from 1 to 5 stars`.
- Uploaders cannot rate or mark their own material (**403** `You can't rate or mark
  your own material as helpful`), so nobody can lift their own work.
- A blank review is stored as a rating only and not listed as a review.
- A rating of 4 or 5 stars earns the uploader reputation (S13.1), through
  `reputation.record_peer_rating` with source `material_review` / the review row's id.
  See [`../reputation/README.md`](../reputation/README.md).

## Reporting (S04.2)

- `POST /materials/{id}/reports` with a `reason`: `incorrect`, `inappropriate`,
  `duplicate` ("Duplicate content"), `misleading`, `spam`, `other`, plus optional
  `details`. No reason: **422** `Select a reason for reporting this material`.
- The report is filed through `moderation.service.file_report` as an **open** report
  on target `material`, scoped to the material's channel, so it is in the moderation
  queue (`moderation.service.open_reports`, worked by S13.2 #43). The response carries
  `report_id`, `reporter_id` and `reported_at`.
- Reporting the same material again while the first report is open returns that
  report instead of filing a duplicate (same as questions).

## Endpoints

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| `POST` | `/api/v1/materials` | `material:upload` | Multipart upload: `file`, `subject`, `topic`, `semester`, `material_type`, optional `title` (defaults to the file name), `description`, `channel_id`, `url` (links). |
| `GET` | `/api/v1/materials` | signed in | Search: `q` keyword (title, description, subject, topic), exact `subject` / `topic` / `semester` (case-insensitive), `type`, `channel_id`, `limit` (≤100), `offset`. Returns `items`, `total`, and `empty_state` when nothing matches. |
| `GET` | `/api/v1/materials/{id}` | signed in | One material with tags, uploader, file details, `preview_url`, `download_url`. |
| `GET` | `/api/v1/materials/{id}/preview` | signed in | The file inline (PDF, images, text); **415** for other types. |
| `GET` | `/api/v1/materials/{id}/download` | signed in | The file as an attachment. |
| `PUT` | `/api/v1/materials/{id}/review` | `material:rate` | Save the caller's `rating` (1–5) and optional `review`; returns the material with updated ratings. |
| `GET` | `/api/v1/materials/{id}/reviews` | signed in | Written reviews, newest first, each with `author_name`; `limit`, `offset`. |
| `PUT` | `/api/v1/materials/{id}/helpful` | `material:rate` | Mark helpful (idempotent); returns `helpful_count`. |
| `DELETE` | `/api/v1/materials/{id}/helpful` | `material:rate` | Take the helpful mark back. |
| `POST` | `/api/v1/materials/{id}/reports` | `report:submit` | Report to moderation with a `reason`. |
| `PATCH` | `/api/v1/materials/{id}` | `can_manage` (S03.1) | Edit `title`, `description`, `subject`, `topic`, `semester`, `url` (links); only the fields sent change. |
| `DELETE` | `/api/v1/materials/{id}` | `can_manage` (S03.1) | Remove the material. |

Without `channel_id`, search covers general materials plus those in the caller's
channels; with it, that channel only.

## Response time (NFR-01)

Target for search and filter: **within 1 second with 1,000 materials**. Subject and
topic are indexed, results are paginated, and file bytes are never loaded by a
listing. `test_search_and_filter_stay_inside_the_response_time_target` checks it.
Load testing under concurrent users is NFR-01 (#46).

## Service interface for other modules

`app.modules.materials.service`: `create_material`, `get_material`, `set_removed`,
`count_uploaded_by`, `recent_uploaded_by`, and from S04.1 `describe_material`,
`search_materials` (`MaterialPage`), `validate_file`, `attach_file`,
`get_material_file`, and from S04.2 `feedback_for` (`MaterialFeedback`),
`rate_material`, `set_helpful`, `list_reviews` (`MaterialReviewEntry`), and from
S03.1 `update_material` and the `is_official` filter on `search_materials`.
`MaterialSummary` gained optional fields (semester, uploader,
file, ...) with defaults, so existing callers are unchanged.

## Dependencies

`accounts` (route guards, uploader and review author names), `channels` (visibility
and membership) and `moderation` (filing reports).

## Tests

`tests/test_materials.py` — Test Plan cases `UT_07`, `UT_08`, `IT_07`, `IT_08` under
their IDs, plus file type and size, empty state, response time, and channel access
tests. `tests/test_material_feedback.py` — `ST_07`, `ST_08`, plus one test per S04.2
acceptance criterion (average, helpful once per user, review author, reason required,
moderation queue). `tests/test_official_content.py` — `UT_05`, `UT_06`, `ST_06`, plus
one test per S03.1 acceptance criterion.
