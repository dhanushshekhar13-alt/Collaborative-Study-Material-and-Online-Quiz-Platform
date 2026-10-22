from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import __version__
from app.core.config import settings
from app.modules.accounts.router import router as accounts_router
from app.modules.channels.router import router as channels_router
from app.modules.llm.evaluation_router import router as evaluation_router
from app.modules.llm.router import router as llm_router
from app.modules.materials.router import router as materials_router
from app.modules.profiles.router import router as profiles_router
from app.modules.questions.router import router as questions_router
from app.modules.quizzes.attempts_router import router as quiz_attempts_router
from app.modules.quizzes.router import router as quizzes_router
from app.web import mount_web

API_PREFIX = "/api/v1"

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


def create_app() -> FastAPI:
    app = FastAPI(title=settings.app_name, version=__version__)

    if settings.cors_origin_list:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origin_list,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    app.add_exception_handler(RequestValidationError, _validation_error)

    @app.get("/health", tags=["operations"])
    def health() -> dict[str, str]:
        """Return process health without exposing environment or database details."""
        return {"status": "ok", "version": __version__}

    app.include_router(accounts_router, prefix=API_PREFIX)
    app.include_router(channels_router, prefix=API_PREFIX)
    app.include_router(materials_router, prefix=API_PREFIX)
    app.include_router(profiles_router, prefix=API_PREFIX)
    app.include_router(questions_router, prefix=API_PREFIX)
    app.include_router(quizzes_router, prefix=API_PREFIX)
    app.include_router(quiz_attempts_router, prefix=API_PREFIX)
    app.include_router(llm_router, prefix=API_PREFIX)
    app.include_router(evaluation_router, prefix=API_PREFIX)

    mount_web(app)

    return app


app = create_app()
