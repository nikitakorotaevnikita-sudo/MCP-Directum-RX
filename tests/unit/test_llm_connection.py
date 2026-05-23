from src.models.schemas import LLMConnectionRequest


def test_llm_connection_request_strips_fields_and_masks_api_key():
    request = LLMConnectionRequest(
        provider="ollama",
        base_url=" http://localhost:11434/v1/ ",
        api_key="local-secret",
        model=" qwen3:8b ",
        tool_calling="auto",
    )

    assert request.base_url == "http://localhost:11434/v1"
    assert request.model == "qwen3:8b"
    assert "local-secret" not in repr(request)
    assert "local-secret" not in str(request.model_dump())
