# Moderation module

Reports and the moderation audit log: **FR-13** and **FR-14**. Owns `reports` and
`moderation_actions`; may call no other module. Module rules are in
[`docs/architecture.md`](../../../docs/architecture.md), and every public function is
listed in [`docs/module-interfaces.md`](../../../docs/module-interfaces.md).

## Why it is a leaf

Materials, questions, chat, channels and llm all file reports, and admin and the
moderation queue act on them. If moderation called any of those modules back, they
would depend on each other in a cycle. So moderation stores reports and log entries
only, and any content is referred to as a **type plus an id** (`ReportTarget`:
`material`, `question`, `quiz`, `channel_message`, `direct_message`, `user`,
`channel`, `ai_response`) rather than a foreign key. Showing or removing the content
is done by the modules that own it: `moderation_queue` (S13.2) and `admin` (S14.2).

## Interface

| Function | Used for |
| --- | --- |
| `file_report`, `find_open_report` | Filing a report, and returning a reporter's open report instead of a duplicate |
| `get_report`, `reports_on`, `report_queue`, `open_reports`, `count_open_reports` | Reading reports: one, all on an item, the open queue grouped by item |
| `resolve_report`, `close_reports_on` | Closing one report, or every open report on an item, as resolved or dismissed |
| `reports_filed_by` | A reporter's own reports with their status |
| `flagged_targets` | Items with open reports, for the admin dashboard |
| `log_action`, `actions_on`, `audit_log` | The audit log: who did what to which item, and when |

Callers commit. Report and action enums (`ReportTarget`, `ReportReason`,
`ReportStatus`, `ModerationActionType`) are re-exported from `service.py`. Adding a new
kind of reportable content means a new `ReportTarget` value and a migration widening
the `report_target` and `moderation_action_target` CHECK constraints (see `0012`).
