from app.core.config import Settings


def test_settings_are_read_from_environment(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@db:5432/x")
    monkeypatch.setenv("ACCESS_TOKEN_EXPIRE_MINUTES", "45")

    settings = Settings(_env_file=None)

    assert settings.database_url == "postgresql+psycopg://u:p@db:5432/x"
    assert settings.access_token_expire_minutes == 45


def test_llm_key_comes_from_configuration_and_is_masked(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_API_KEY", "test-key-from-env")

    settings = Settings(_env_file=None)

    assert settings.llm_enabled
    assert settings.llm_api_key.get_secret_value() == "test-key-from-env"
    assert "test-key-from-env" not in repr(settings)


def test_secrets_have_no_defaults(monkeypatch):
    for name in ("AUTH_TOKEN_SECRET", "LLM_API_KEY", "LLM_PROVIDER"):
        monkeypatch.delenv(name, raising=False)

    settings = Settings(_env_file=None)

    assert settings.auth_token_secret is None
    assert settings.llm_api_key is None
    assert not settings.llm_enabled


def test_cors_origins_are_parsed(monkeypatch):
    monkeypatch.setenv("CORS_ORIGINS", "http://localhost:5173, https://demo.example")

    assert Settings(_env_file=None).cors_origin_list == [
        "http://localhost:5173",
        "https://demo.example",
    ]
