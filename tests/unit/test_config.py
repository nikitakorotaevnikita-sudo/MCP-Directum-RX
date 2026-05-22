from src.config import Settings


def test_ollama_profile_uses_openai_compatible_defaults():
    settings = Settings(
        LLM_PROVIDER="ollama",
        OPENAI_BASE_URL="http://localhost:11434/v1",
        OPENAI_API_KEY="ollama",
        OPENAI_MODEL="qwen3:8b",
        DIRECTUM_BASE_URL="https://rx.example/Integration/odata",
        DIRECTUM_AUTH_TOKEN="Basic secret-token",
    )

    assert settings.llm_provider == "ollama"
    assert settings.openai_base_url == "http://localhost:11434/v1"
    assert settings.openai_api_key == "ollama"
    assert settings.openai_model == "qwen3:8b"


def test_public_config_masks_secrets():
    settings = Settings(
        OPENAI_API_KEY="very-secret",
        DIRECTUM_AUTH_TOKEN="Basic directum-secret",
        DIRECTUM_BASE_URL="https://rx.example/Integration/odata",
    )

    public = settings.public_config()

    assert public["openai_api_key_set"] is True
    assert public["directum_auth_token_set"] is True
    assert "very-secret" not in str(public)
    assert "directum-secret" not in str(public)
