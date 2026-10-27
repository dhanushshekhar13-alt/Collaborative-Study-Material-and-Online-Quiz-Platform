# Moderation queue module

The report queue and moderator actions (FR-13, story **S13.2**, issue #43). Reports
filed with `POST /api/v1/materials/{id}/reports` and `POST /api/v1/questions/{id}/reports`
land here; a moderator reviews them and either removes the content or clears the
report. The module owns no tables: reports and the action log are the moderation
module's `reports` and `moderation_actions`, and the content is hidden through the
materials and questions services. Module rules are in
[`docs/architecture.md`](../../../docs/architecture.md).

It is a separate module because moderation is a leaf that materials and questions
already call to file reports; acting on content from inside moderation would make
those modules depend on each other in a cycle.

## Who may moderate (ST_25)

Decided in one place, `service.can_moderate_channel`:

| Report scoped to | Who may see and act on it |
| --- | --- |
| any channel, or none | admins (`report:resolve`) |
| an approved Authorized Channel | its members who manage official content (authorized users) |
| a Normal Channel | its creator (owner) and moderators |

Regular members never moderate. Calling the queue without moderating anything is
**403** `Only moderators can review reports`; acting on a report in a channel you do not
moderate is **403** `Only a moderator of this channel can act on its reports`.

## Endpoints

All under `/api/v1/moderation`, signed in.

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/queue?channel_id=` | Open reports, one entry per reported item (duplicates grouped), oldest first: content title, link and channel, `report_count`, `reasons`, and every report with its reporter, reason, details and time (IT_25). |
| `GET` | `/reports/{id}` | One report with the other reports on the same item and the item's moderation `history`. |
| `POST` | `/reports/{id}/remove` | Remove Content: body `{"confirm": true, "note": "..."}` (IT_26). |
| `POST` | `/reports/{id}/dismiss` | Clear the report; the content stays. Optional `note`. |
| `GET` | `/reports/mine` | The caller's own reports with their current status. |

## Rules

- **Grouping.** Reports on the same item show as one queue entry. The same reporter
  reporting an item again while their report is open gets their existing report back
  (materials and questions routers), so it is never duplicated.
- **Confirmation.** Filing a report answers **201** with a confirmation `message` and
  status `open`; `/reports/mine` shows the reporter what happened to it later.
- **Remove Content** needs `confirm: true` (**400** `Confirm removing this content`).
  The material or question's status becomes removed, so it disappears from search,
  channel lists and direct links (404), and **every** open report on it becomes
  `resolved`. Only materials and questions can be removed here (**422** otherwise).
- **Dismiss** sets every open report on the item to `dismissed` and leaves the content.
- A report that is already closed cannot be decided again (**409**
  `This report has already been closed`). An unknown report is **404** `Report not found`.
- **Audit.** Each decision writes one `moderation_actions` row (`remove_content` or
  `dismiss_report`) with the moderator, the report it came from, the note and the
  time, in the same transaction as the change; the closed reports also record
  `resolved_by` and `resolved_at`. Migration `0010` adds `dismiss_report` to the
  action type's CHECK constraint.
