import importlib
from collections.abc import Iterable

from fastapi import APIRouter, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import OperationalError

from app import __version__
from app.core.config import settings
from app.modules import ALLOWED_DEPENDENCIES, required_modules
from app.web import mount_web

API_PREFIX = "/api/v1"

# The routers each feature module mounts under /api/v1, in contract order. They are
# imported only when their module is served, so a process started with
# ENABLED_MODULES=quizzes never loads chat's code (NFR-07). moderation has no routes of
# its own; its queue and actions are served by moderation_queue.
MODULE_ROUTERS: tuple[tuple[str, str], ...] = (
    ("accounts", "app.modules.accounts.router"),
    ("admin", "app.modules.admin.router"),
    ("channels", "app.modules.channels.router"),
    ("chat", "app.modules.chat.router"),
    ("chat", "app.modules.chat.direct_router"),
    ("materials", "app.modules.materials.router"),
    ("moderation_queue", "app.modules.moderation_queue.router"),
    ("notifications", "app.modules.notifications.router"),
    ("profiles", "app.modules.profiles.router"),
    ("questions", "app.modules.questions.router"),
    ("quizzes", "app.modules.quizzes.router"),
    ("quizzes", "app.modules.quizzes.attempts_router"),
    ("llm", "app.modules.llm.router"),
    ("llm", "app.modules.llm.evaluation_router"),
    ("llm", "app.modules.llm.assistant_router"),
    ("llm", "app.modules.llm.report_router"),
    ("reputation", "app.modules.reputation.router"),
    ("analytics", "app.modules.analytics.router"),
)

_PYDANTIC_PREFIX = "Value error, "


def _message(error: dict) -> str:
    return str(error.get("msg", "Invalid request")).removeprefix(_PYDANTIC_PREFIX)


async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Return the first problem as one readable message, plus a per-field list.

    Validators raise the Test Plan's exact wording (for example "Enter a valid email
    address"); this strips pydantic's framing so the client sees that wording alone.
    Only strings are copied out, because pydantic's ``ctx`` holds exception objects.
    """
    errors = exc.errors()
    return JSONResponse(
        status_code=422,
        content={
            "detail": _message(errors[0]) if errors else "Invalid request",
            "errors": [
                {
                    "field": ".".join(str(p) for p in error.get("loc", ()) if p != "body"),
                    "message": _message(error),
                }
                for error in errors
            ],
        },
    )


TEMPORARY_FAILURE = (
    "Temporary problem saving your data. Nothing from this request was saved "
    "and your earlier work is safe; please try again."
)


async def _temporary_failure(request: Request, exc: OperationalError) -> JSONResponse:
    """A transient database failure (connection lost, lock timeout) is a 503 (NFR-04).

    The request's transaction has already been rolled back by ``get_db``, so the client
    can simply retry; attempts and answers committed before the failure are kept.
    """
    return JSONResponse(status_code=503, content={"detail": TEMPORARY_FAILURE})


def served_modules(modules: Iterable[str] | None = None) -> set[str]:
    """The modules a process serves: ``modules`` (or ``ENABLED_MODULES``) and their
    dependencies, or every module when neither names any."""
    wanted = list(modules or ()) or settings.enabled_module_list
    return required_modules(wanted) if wanted else set(ALLOWED_DEPENDENCIES)


def create_app(modules: Iterable[str] | None = None) -> FastAPI:
    """Build the API serving ``modules`` (default ``ENABLED_MODULES``, blank for all)."""
    served = served_modules(modules)
    app = FastAPI(title=settings.app_name, version=__version__)
    app.state.modules = frozenset(served)

    if settings.cors_origin_list:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origin_list,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(OperationalError, _temporary_failure)

    @app.get("/health", tags=["operations"])
    def health() -> dict[str, str]:
        """Return process health without exposing environment or database details."""
        return {"status": "ok", "version": __version__}

    for module, path in MODULE_ROUTERS:
        if module in served:
            router: APIRouter = importlib.import_module(path).router
            app.include_router(router, prefix=API_PREFIX)

    mount_web(app)

    return app


app = create_app()
