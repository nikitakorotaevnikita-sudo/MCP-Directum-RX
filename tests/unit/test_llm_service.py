from types import SimpleNamespace

import pytest

from src.services.llm_service import LLMService


class FakeToolRegistry:
    def openai_tools(self):
        return [{"type": "function", "function": {"name": "get_my_assignments", "parameters": {"type": "object", "properties": {}}}}]


class FakeCompletions:
    def create(self, **kwargs):
        return iter(
            [
                SimpleNamespace(choices=[]),
                SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=None))]),
                SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=""))]),
                SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(tool_calls=[]))]),
                SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="Hello"))]),
                SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=" world"))]),
            ]
        )


class FakeClient:
    def __init__(self):
        self.chat = SimpleNamespace(completions=FakeCompletions())


def test_llm_service_reports_provider_status_without_secret():
    service = LLMService(
        provider="ollama",
        base_url="http://localhost:11434/v1",
        api_key="ollama",
        model="qwen3:8b",
        tool_calling="auto",
        tool_registry=FakeToolRegistry(),
    )

    status = service.status()

    assert status["provider"] == "ollama"
    assert status["base_url"] == "http://localhost:11434/v1"
    assert status["model"] == "qwen3:8b"
    assert "api_key" not in status


@pytest.mark.parametrize("tool_calling", ["auto", "enabled"])
def test_llm_service_builds_tools_when_enabled_or_auto(tool_calling):
    service = LLMService(
        provider="ollama",
        base_url="http://localhost:11434/v1",
        api_key="ollama",
        model="qwen3:8b",
        tool_calling=tool_calling,
        tool_registry=FakeToolRegistry(),
    )

    assert service.tools_for_request()[0]["function"]["name"] == "get_my_assignments"


def test_stream_chat_skips_empty_or_tool_only_chunks():
    service = LLMService(
        provider="ollama",
        base_url="http://localhost:11434/v1",
        api_key="ollama",
        model="qwen3:8b",
        tool_calling="auto",
        tool_registry=FakeToolRegistry(),
    )
    service.client = FakeClient()

    assert list(service.stream_chat("Hi", [])) == ["Hello", " world"]
