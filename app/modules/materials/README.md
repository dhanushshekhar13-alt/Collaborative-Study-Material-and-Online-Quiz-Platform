# Materials module

Shared study resources. Implements the upload, search and download part of **FR-04**
(story **S04.1**, issue #22). Rating, review and reporting is S04.2 (#23); official
content in Authorized Channels is S03.1 (#26). Owns `study_materials`, `material_files`
and `material_reviews`; module rules are in
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
  (`Only authorized users can add content to Authorized Channels`); S03.1 builds the
  official-content flow on top.
- Removed materials (`status = removed`) are never listed or served.

## Endpoints

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| `POST` | `/api/v1/materials` | `material:upload` | Multipart upload: `file`, `subject`, `topic`, `semester`, `material_type`, optional `title` (defaults to the file name), `description`, `channel_id`, `url` (links). |
| `GET` | `/api/v1/materials` | signed in | Search: `q` keyword (title, description, subject, topic), exact `subject` / `topic` / `semester` (case-insensitive), `type`, `channel_id`, `limit` (≤100), `offset`. Returns `items`, `total`, and `empty_state` when nothing matches. |
| `GET` | `/api/v1/materials/{id}` | signed in | One material with tags, uploader, file details, `preview_url`, `download_url`. |
| `GET` | `/api/v1/materials/{id}/preview` | signed in | The file inline (PDF, images, text); **415** for other types. |
| `GET` | `/api/v1/materials/{id}/download` | signed in | The file as an attachment. |

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
`get_material_file`. `MaterialSummary` gained optional fields (semester, uploader,
file, ...) with defaults, so existing callers are unchanged.

## Dependencies

`accounts` (route guards, uploader names) and `channels` (visibility and membership).

## Tests

`tests/test_materials.py` — Test Plan cases `UT_07`, `UT_08`, `IT_07`, `IT_08` under
their IDs, plus file type and size, empty state, response time, and channel access
tests.
