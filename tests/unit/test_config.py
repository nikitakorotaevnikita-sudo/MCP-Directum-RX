import pytest

from src.config import Settings


@pytest.fixture(autouse=True)
def isolate_settings_env(monkeypatch):
    for key in Settings.model_fields:
        monkeypatch.delenv(key, raising=False)


def test_ollama_profile_uses_openai_compatible_defaults():
    settings = Settings(
        DIRECTUM_BASE_URL="https://rx.example/Integration/odata",
        DIRECTUM_AUTH_TOKEN="Basic secret-token",
        _env_file=None,
    )

    assert settings.llm_provider == "ollama"
    assert settings.openai_base_url == "http://localhost:11434/v1"
    assert settings.openai_api_key == "ollama"
    assert settings.openai_model == "qwen3:8b"


def test_openrouter_profile_uses_gemma_defaults_when_selected():
    settings = Settings(
        LLM_PROVIDER="openrouter",
        DIRECTUM_BASE_URL="https://rx.example/Integration/odata",
        DIRECTUM_AUTH_TOKEN="Basic secret-token",
        _env_file=None,
    )

    assert settings.llm_provider == "openrouter"
    assert settings.openai_base_url == "https://openrouter.ai/api/v1"
    assert settings.openai_model == "google/gemma-4-26b-a4b-it:free"


def test_public_config_masks_secrets():
    settings = Settings(
        OPENAI_API_KEY="very-secret",
        DIRECTUM_AUTH_TOKEN="Basic directum-secret",
        DIRECTUM_BASE_URL="https://rx.example/Integration/odata",
        _env_file=None,
    )

    public = settings.public_config()

    assert public["openai_api_key_set"] is True
    assert public["directum_auth_token_set"] is True
    assert "very-secret" not in str(public)
    assert "directum-secret" not in str(public)


def test_directum_headers_use_raw_authorization_token():
    settings = Settings(
        DIRECTUM_AUTH_TOKEN="Basic directum-secret",
        DIRECTUM_BASE_URL="https://rx.example/Integration/odata",
        _env_file=None,
    )

    assert settings.directum_headers()["Authorization"] == "Basic directum-secret"


def test_settings_repr_masks_secret_values():
    settings = Settings(
        OPENAI_API_KEY="very-secret",
        DIRECTUM_AUTH_TOKEN="Basic directum-secret",
        BACKOFFICE_PASSWORD="backoffice-secret",
        DIRECTUM_BASE_URL="https://rx.example/Integration/odata",
        _env_file=None,
    )

    settings_repr = repr(settings)

    assert "very-secret" not in settings_repr
    assert "directum-secret" not in settings_repr
    assert "backoffice-secret" not in settings_repr


def test_settings_json_dump_masks_secret_values():
    settings = Settings(
        OPENAI_API_KEY="very-secret",
        DIRECTUM_AUTH_TOKEN="Basic directum-secret",
        BACKOFFICE_PASSWORD="backoffice-secret",
        DIRECTUM_BASE_URL="https://rx.example/Integration/odata",
        _env_file=None,
    )

    settings_dump = settings.model_dump(mode="json")

    assert "very-secret" not in str(settings_dump)
    assert "directum-secret" not in str(settings_dump)
    assert "backoffice-secret" not in str(settings_dump)
