"""Runtime configuration.

Every setting is read from environment variables (or a local, git-ignored ``.env``
file). Nothing secret has a default value, so a fresh checkout contains no
credentials and a missing secret is reported instead of silently replaced.
"""

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

    # Study material uploads (materials module, S04.1).
    material_max_upload_mb: int = 20

    # Comma-separated list of browser origins allowed to call the API.
    cors_origins: str = ""

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def llm_enabled(self) -> bool:
        return self.llm_provider != "none" and self.llm_api_key is not None


def get_settings() -> Settings:
    """Build settings from the current environment (useful in tests)."""
    return Settings()


settings = get_settings()
