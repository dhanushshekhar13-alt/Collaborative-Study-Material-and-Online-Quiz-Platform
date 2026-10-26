# Web UI

The browser front end, started by card **UI-1** (issue #62, NFR-03): register, log
in, profile, and finding, creating, joining and leaving channels. UI-2 (#63) and UI-3
(#64) add their screens here.

## How it is built

Plain HTML, CSS and JavaScript in `static/`. There is no framework, no npm and no
build step. FastAPI serves the files at **`/app/`** (`/` redirects there), on the same
origin as the API, so no CORS setup is needed.

| File | Role |
| --- | --- |
| `static/index.html` | The shell: top bar, nav, and the `<main>` the pages render into. |
| `static/api.js` | `API.get/post/patch/del` for `/api/v1`. It adds the bearer token, turns error responses into `ApiError(status, detail)` and sends the user back to Log in when a token expires. |
| `static/app.js` | Hash routes (`#/login`, `#/register`, `#/channels`, `#/channels/<id>`, `#/my-channels`, `#/profile`, `#/performance`), one function per page. The performance page (S12.1) draws its charts as inline SVG with `createElementNS`. |
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
- The token is kept in `sessionStorage`, so it is per tab and gone when the tab closes.
  It expires with the API's token lifetime.

## Tests

`tests/test_web_ui.py` covers serving, CSP, the endpoint contract, and a Playwright
walk-through of every UI-1 acceptance criterion against a live, seeded server. The
browser test is skipped where Playwright or Chromium is not installed (CI today). Run
it locally with `pip install playwright && playwright install chromium`, then
`python -m pytest tests/test_web_ui.py`.
