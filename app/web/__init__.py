"""The web UI (UI-1, issue #62): static pages that call the ``/api/v1`` endpoints.

There is no build step. ``static/`` holds plain HTML, CSS and JavaScript, served by
the same FastAPI process at ``/app/``, so the UI and the API share one origin and need
no CORS setup. ``/`` redirects there. The UI holds no rules of its own: every check
(validation messages, permissions, channel visibility) comes from the API response.
"""

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

STATIC_DIR = Path(__file__).resolve().parent / "static"
UI_PATH = "/app"

# The pages load only their own files; the API is called on the same origin.
_CSP = (
    "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
    "connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
)


class _UIFiles(StaticFiles):
    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers.setdefault("Content-Security-Policy", _CSP)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Cache-Control", "no-cache")
        return response


def mount_web(app: FastAPI) -> None:
    """Serve the UI at ``/app/`` and send ``/`` there."""

    @app.get("/", include_in_schema=False)
    def root() -> RedirectResponse:
        return RedirectResponse(url=f"{UI_PATH}/")

    app.mount(UI_PATH, _UIFiles(directory=STATIC_DIR, html=True), name="web")
