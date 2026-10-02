from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Collaborative Study Platform API"
    database_url: str = "sqlite:///./study_platform.db"
    auth_token_secret: str | None = None
    access_token_expire_minutes: int = 30

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
