from fastapi import FastAPI

from app.core.config import settings

app = FastAPI(title=settings.app_name, version="0.1.0")


@app.get("/health", tags=["operations"])
def health() -> dict[str, str]:
    """Return process health without exposing environment or database details."""
    return {"status": "ok"}
