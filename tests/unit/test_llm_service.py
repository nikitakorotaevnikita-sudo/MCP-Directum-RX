from types import SimpleNamespace

import pytest

from src.services.llm_service import LLMService


class FakeToolRegistry:
    def openai_tools(self):
        return [{"type": "function", "function": {"name": "get_my_assignments", "parameters": {"type": "object", "properties": {}}}}]


class RecordingToolRegistry(FakeToolRegistry):
    def __init__(self):
        self.calls = []

    def call(self, name, arguments):
        self.calls.append((name, arguments))
        return [{"id": 1, "subject": "Task", "status": "InProcess", "entity_type": "assignment"}]


class FailingToolRegistry(FakeToolRegistry):
    def call(self, name, arguments):
        raise RuntimeError("DIRECTUM_AUTH_TOKEN must be a valid Basic token")


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


class FailingCompletions:
    def create(self, **kwargs):
        raise RuntimeError("Provider returned error 429 for sk-or-v1-secret")


class ToolCallCompletions:
    def __init__(self):
        self.requests = []

    def create(self, **kwargs):
        self.requests.append(kwargs)
        if len(self.requests) == 1:
            return iter(
                [
                    SimpleNamespace(
                        choices=[
                            SimpleNamespace(
                                delta=SimpleNamespace(
                                    tool_calls=[
                                        SimpleNamespace(
                                            index=0,
                                            id="call_1",
                                            type="function",
                                            function=SimpleNamespace(name="get_my_assignments", arguments="{}"),
                                        )
                                    ]
                                )
                            )
                        ]
                    )
                ]
            )
        return iter(
            [
                SimpleNamespace(
                    choices=[
                        SimpleNamespace(delta=SimpleNamespace(content="Found 1 assignment: Task."))
                    ]
                )
            ]
        )


class FakeClient:
    def __init__(self):
        self.chat = SimpleNamespace(completions=FakeCompletions())


class FailingClient:
    def __init__(self):
        self.chat = SimpleNamespace(completions=FailingCompletions())


class ToolCallClient:
    def __init__(self):
        self.completions = ToolCallCompletions()
        self.chat = SimpleNamespace(completions=self.completions)


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


def test_stream_chat_returns_safe_error_when_provider_fails():
    service = LLMService(
        provider="openrouter",
        base_url="https://openrouter.ai/api/v1",
        api_key="sk-or-v1-secret",
        model="google/gemma-4-26b-a4b-it:free",
        tool_calling="auto",
        tool_registry=FakeToolRegistry(),
    )
    service.client = FailingClient()

    chunks = list(service.stream_chat("Hi", []))

    assert len(chunks) == 1
    assert "LLM request failed" in chunks[0]
    assert "429" in chunks[0]
    assert "sk-or-v1-secret" not in chunks[0]


def test_stream_chat_executes_tool_calls_and_streams_final_answer():
    registry = RecordingToolRegistry()
    service = LLMService(
        provider="openrouter",
        base_url="https://openrouter.ai/api/v1",
        api_key="test-key",
        model="openrouter/free",
        tool_calling="auto",
        tool_registry=registry,
    )
    client = ToolCallClient()
    service.client = client

    chunks = list(service.stream_chat("Use the available tool", []))

    assert chunks == ["Found 1 assignment: Task."]
    assert registry.calls == [("get_my_assignments", {})]
    second_messages = client.completions.requests[1]["messages"]
    assert second_messages[-2]["tool_calls"][0]["function"]["name"] == "get_my_assignments"
    assert second_messages[-1]["role"] == "tool"
    assert "Task" in second_messages[-1]["content"]


def test_stream_chat_routes_my_assignments_intent_without_model_tool_call():
    registry = RecordingToolRegistry()
    service = LLMService(
        provider="openrouter",
        base_url="https://openrouter.ai/api/v1",
        api_key="test-key",
        model="openrouter/free",
        tool_calling="auto",
        tool_registry=registry,
    )
    client = ToolCallClient()
    service.client = client

    chunks = list(service.stream_chat("Посмотри мои задания", []))

    assert registry.calls == [("get_my_assignments", {})]
    assert chunks == ["Найдено 1: Task (InProcess)."]
    assert client.completions.requests == []


def test_stream_chat_reports_directum_error_for_direct_rx_intent():
    service = LLMService(
        provider="openrouter",
        base_url="https://openrouter.ai/api/v1",
        api_key="test-key",
        model="openrouter/free",
        tool_calling="auto",
        tool_registry=FailingToolRegistry(),
    )

    chunks = list(service.stream_chat("Посмотри мои задания", []))

    assert chunks == ["Directum RX request failed: DIRECTUM_AUTH_TOKEN must be a valid Basic token"]
