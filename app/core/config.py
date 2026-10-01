from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Collaborative Study Platform API"
    database_url: str = "sqlite:///./study_platform.db"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
