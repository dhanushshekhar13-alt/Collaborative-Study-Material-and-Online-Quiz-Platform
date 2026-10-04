from fastapi import FastAPI

from app.core.config import settings
from app.modules.accounts.router import router as accounts_router
from app.modules.channels.router import router as channels_router

app = FastAPI(title=settings.app_name, version="0.1.0")
app.include_router(accounts_router)
app.include_router(channels_router)


@app.get("/health", tags=["operations"])
def health() -> dict[str, str]:
    """Return process health without exposing environment or database details."""
    return {"status": "ok"}
