import pytest

from app.config import Settings, get_settings


def test_local_profile_skips_lz_validation(monkeypatch):
    monkeypatch.setenv("APP_PROFILE", "local")
    monkeypatch.setenv("MOCK_LLM", "true")
    monkeypatch.setenv("LOCAL_EMBEDDINGS", "true")
    monkeypatch.setenv("DATABASE_URL", "sqlite+pysqlite:///:memory:")
    get_settings.cache_clear()
    s = Settings(
        app_profile="local",
        mock_llm=True,
        local_embeddings=True,
        database_url="sqlite+pysqlite:///:memory:",
    )
    s.validate_profile()  # must not raise


def test_lz_profile_requires_azure_and_postgres():
    s = Settings(
        app_profile="lz",
        mock_llm=True,
        local_embeddings=True,
        database_url="sqlite+pysqlite:///:memory:",
        azure_openai_endpoint="",
        azure_search_endpoint="",
        rag_enabled=True,
        guardrails_enabled=True,
    )
    with pytest.raises(RuntimeError, match="APP_PROFILE=lz validation failed"):
        s.validate_profile()


def test_lz_profile_passes_when_configured():
    s = Settings(
        app_profile="lz",
        mock_llm=False,
        local_embeddings=False,
        use_managed_identity=True,
        database_url="postgresql+psycopg://u:p@host:5432/cmaa",
        azure_openai_endpoint="https://example.openai.azure.com/",
        azure_search_endpoint="https://example.search.windows.net",
        rag_enabled=True,
        guardrails_enabled=True,
        apim_base_url="https://example.azure-api.net/cmaa",
        content_safety_fail_open=False,
        pipeline_lock_backend="db",
    )
    s.validate_profile()
    assert s.use_mock_llm is False
    assert s.use_azure_embeddings is True


def test_lz_profile_requires_fail_closed_content_safety_and_db_lock():
    s = Settings(
        app_profile="lz",
        mock_llm=False,
        local_embeddings=False,
        use_managed_identity=True,
        database_url="postgresql+psycopg://u:p@host:5432/cmaa",
        azure_openai_endpoint="https://example.openai.azure.com/",
        azure_search_endpoint="https://example.search.windows.net",
        rag_enabled=True,
        guardrails_enabled=True,
        apim_base_url="https://example.azure-api.net/cmaa",
    )
    with pytest.raises(RuntimeError, match="CONTENT_SAFETY_FAIL_OPEN"):
        s.validate_profile()


def test_lz_profile_requires_auth_or_apim():
    s = Settings(
        app_profile="lz",
        mock_llm=False,
        local_embeddings=False,
        use_managed_identity=True,
        database_url="postgresql+psycopg://u:p@host:5432/cmaa",
        azure_openai_endpoint="https://example.openai.azure.com/",
        azure_search_endpoint="https://example.search.windows.net",
        rag_enabled=True,
        guardrails_enabled=True,
        api_auth_key="",
        apim_base_url="",
    )
    with pytest.raises(RuntimeError, match="API_AUTH_KEY or APIM_BASE_URL"):
        s.validate_profile()


def test_invalid_profile_rejected():
    with pytest.raises(Exception):
        Settings(app_profile="prod")
