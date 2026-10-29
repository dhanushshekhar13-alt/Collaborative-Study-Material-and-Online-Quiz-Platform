# Web UI

The browser front end, started by card **UI-1** (issue #62, NFR-03): register, log
in, profile, and finding, creating, joining and leaving channels. UI-2 (#63) adds the
study screens and UI-3 (#64) the chat, messages, Ask AI, notification, moderation and
admin screens.

## How it is built

Plain HTML, CSS and JavaScript in `static/`. There is no framework, no npm and no
build step. FastAPI serves the files at **`/app/`** (`/` redirects there), on the same
origin as the API, so no CORS setup is needed.

| File | Role |
| --- | --- |
| `static/index.html` | The shell: top bar, nav, and the `<main>` the pages render into. |
| `static/api.js` | `API.get/post/put/patch/del` for `/api/v1`, plus `API.upload` (multipart) and `API.blob` (an authenticated file fetch for preview and download). It adds the bearer token, turns error responses into `ApiError(status, detail)` and sends the user back to Log in when a token expires. |
| `static/study.js` | UI-2 (#63): `#/materials` (search, Upload Material), `#/materials/<id>` (preview, download, rate and review, helpful, report), `#/questions` (search, rate, report, Add Question), `#/quizzes` (lists by channel, My quizzes with Publish, Create Quiz), `#/quizzes/<id>/attempt` (countdown timer, autosave, unanswered warning, auto-submit), `#/attempts/<id>` (result) and `#/history` (My Quiz History). Pages register in `window.StudyHubPages`; `app.js` routes to them with its shared helpers, so the file loads before `app.js`. |
| `static/community.js` | UI-3 (#64): the channel chat (mounted on `#/channels/<id>` through `window.StudyHubChat`: messages with sender and time, Send disabled while blank, threads opened inline, "Load older messages", polling for new ones every 5 s, and `?message=<id>` from a notification opens that thread), `#/users/<id>` (a member's profile with **Message**), `#/messages` and `#/messages/<id>` (direct conversations, Block/Unblock User with the blocked indicator, Report a message with a reason), `#/ask` (Ask AI: explanations, topic quizzes with Start quiz, Generate practice questions) and `#/ask/review/<id>` (approve, edit or reject each generated question, then add the approved ones to a quiz), `#/notifications` (the list behind the 🔔 bell, mark read, Notification settings), `#/moderation` (pending reports with content, reporters and reasons; Remove Content or Dismiss) and `#/admin` (counts, user management with Suspend/Reinstate, Authorized Channel requests with Approve/Reject, flagged content removal with a reason, and the admin action log). Loads before `app.js`. |
| `static/app.js` | Hash routes (`#/login`, `#/register`, `#/channels`, `#/channels/<id>`, `#/my-channels`, `#/profile`, `#/leaderboard` (S13.1), `#/performance` (S12.1), `#/recommended` (S12.2)), one function per page. The performance page draws its charts as inline SVG with `createElementNS`. |
| `static/styles.css` | Design tokens on `:root` with a dark-mode set, and the `channel-normal` / `channel-authorized` badge styles named by the API's `type_style`. |

## Rules for adding screens

- **The API is the source of truth.** Messages come from the API's `detail`, so the UI
  shows the Test Plan wording exactly. Permissions come from
  `/accounts/me/permissions`; hiding or disabling a control is a convenience, and the
  server still checks every request.
- **No HTML from data.** Build elements with the `h(tag, attrs, ...children)` helper in
  `app.js`. Text goes in with `textContent`. `innerHTML` and similar calls fail
  `tests/test_web_ui.py`.
- **Same-origin files only.** No CDN scripts, inline scripts or inline styles. The
  pages are served with `script-src 'self'`, so anything else is blocked.
- **Every endpoint the UI calls must exist.** `test_every_endpoint_the_ui_calls_exists`
  matches each `API.*("/path")` call against the OpenAPI routes.
- **The server owns the quiz clock.** The attempt screen counts down from the API's
  `remaining_seconds`, saves answers as they change, and when it reaches zero reads the
  attempt until the server has auto-submitted it, so only answers saved in time count.
- **Files open as blobs.** Preview and download need the bearer token, so they are
  fetched with `API.blob`. A preview is shown only for PDFs (a frame), PNG/JPEG
  (an image) and text (`textContent`); the CSP allows `blob:` for images and frames only.
- **Role links follow capabilities.** A nav link with `data-cap="a b"` shows only when
  the user has one of those capabilities (Moderation: `report:resolve` or
  `official_content:manage`; Admin: `admin:dashboard`). Opening the URL anyway shows
  the API's refusal, e.g. `Unauthorized` on `#/admin`, and nothing else.
- **Links from the server are followed only inside the UI.** Notification links and
  shared-resource cards are used as `href` only when they point into `/app/#/...` (or
  an `/api/v1/...` path mapped to its page); anything else stays plain text.
- The token is kept in `sessionStorage`, so it is per tab and gone when the tab closes.
  It expires with the API's token lifetime.

## Tests

`tests/test_web_ui.py` covers serving, CSP, the endpoint contract, and Playwright
walk-throughs of every UI-1 and UI-2 acceptance criterion against a live, seeded server;
`tests/test_web_ui_community.py` does the same for UI-3 (with the offline `fake` LLM
provider). The
browser test is skipped where Playwright or Chromium is not installed (CI today). Run
it locally with `pip install playwright && playwright install chromium`, then
`python -m pytest tests/test_web_ui.py`.
