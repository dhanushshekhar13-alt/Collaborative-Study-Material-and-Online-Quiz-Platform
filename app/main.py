from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.core.config import settings

API_PREFIX = "/api/v1"


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

    @app.get("/health", tags=["operations"])
    def health() -> dict[str, str]:
        """Return process health without exposing environment or database details."""
        return {"status": "ok", "version": __version__}

    return app


app = create_app()
