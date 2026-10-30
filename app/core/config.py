"""Runtime configuration.

Every setting is read from environment variables (or a local, git-ignored ``.env``
file). Nothing secret has a default value, so a fresh checkout contains no
credentials and a missing secret is reported instead of silently replaced.
"""

from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "Collaborative Study Platform API"
    environment: str = "development"
    database_url: str = "sqlite:///./study_platform.db"

    # Authentication (used by the accounts module).
    auth_token_secret: SecretStr | None = None
    access_token_expire_minutes: int = 30

    # LLM provider (used by the LLM modules). Keys come only from the environment.
    llm_provider: str = "none"
    llm_model: str = ""
    llm_api_key: SecretStr | None = None
    # Optional base URL for an OpenAI-compatible endpoint (blank = the provider default).
    llm_base_url: str = ""
    # Seconds to wait for the provider before reporting a timeout (S07.1, NFR-06).
    llm_timeout_seconds: float = 30.0

    # Study material uploads (materials module, S04.1).
    material_max_upload_mb: int = 20

    # Quiz attempts (S06.1): what happens when someone submits with unanswered
    # questions. "warn" asks them to confirm first; "block" refuses until all are answered.
    quiz_unanswered_rule: Literal["warn", "block"] = "warn"

    # Peer reputation (S13.1): points a contributor earns when a peer rates their
    # material or question at least ``reputation_positive_rating`` stars.
    reputation_points_per_positive_rating: int = 5
    reputation_positive_rating: int = 4

    # Comma-separated list of browser origins allowed to call the API.
    cors_origins: str = ""

    # Feature modules this process serves (NFR-07), comma-separated, for example
    # "quizzes,analytics". Each brings the modules it may call (ALLOWED_DEPENDENCIES in
    # app/modules/__init__.py). Blank serves every module.
    enabled_modules: str = ""

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def enabled_module_list(self) -> list[str]:
        return [name.strip() for name in self.enabled_modules.split(",") if name.strip()]

    @property
    def llm_enabled(self) -> bool:
        # "fake" is the built-in deterministic generator for demos and tests; it needs
        # no key and never calls the network.
        if self.llm_provider == "fake":
            return True
        return self.llm_provider != "none" and self.llm_api_key is not None


def get_settings() -> Settings:
    """Build settings from the current environment (useful in tests)."""
    return Settings()


settings = get_settings()
