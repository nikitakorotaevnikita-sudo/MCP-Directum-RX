from types import SimpleNamespace
import json

import pytest

import src.services.llm_service as llm_service_module
from src.services.llm_service import LLMService


PREVIEW_MARKER = "[[DIRECTUM_ACTION_ITEM_PREVIEW:"


def _split_action_item_preview_marker(text):
    visible, marker = text.split(PREVIEW_MARKER, 1)
    return visible.rstrip(), json.loads(marker.removesuffix("]]"))


class FakeToolRegistry:
    def openai_tools(self):
        return [{"type": "function", "function": {"name": "get_my_assignments", "parameters": {"type": "object", "properties": {}}}}]


class RecordingToolRegistry(FakeToolRegistry):
    def __init__(self):
        self.calls = []

    def call(self, name, arguments):
        self.calls.append((name, arguments))
        if name == "search_employee":
            return [{"id": 42, "name": "Ардо Наталья Алексеевна", "status": "Active"}]
        if name == "create_action_item":
            return {
                "mode": "preview",
                "payload": {
                    "Subject": arguments["subject"],
                    "PerformersGD": str(arguments["performer_id"]),
                    "ActionItem": arguments["action_text"],
                },
                "success": True,
                "message": "Preview generated; confirm to create the action item.",
            }
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


class MultiStepCreateCompletions:
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
                                            id="call_search",
                                            type="function",
                                            function=SimpleNamespace(name="search_employee", arguments='{"query":"Ардо"}'),
                                        )
                                    ]
                                )
                            )
                        ]
                    )
                ]
            )
        if len(self.requests) == 2:
            return iter(
                [
                    SimpleNamespace(
                        choices=[
                            SimpleNamespace(
                                delta=SimpleNamespace(
                                    content="I found Ардо Наталья Алексеевна. ",
                                    tool_calls=[
                                        SimpleNamespace(
                                            index=0,
                                            id="call_create",
                                            type="function",
                                            function=SimpleNamespace(
                                                name="create_action_item",
                                                arguments=(
                                                    '{"subject":"Проверить документы по Минцифре",'
                                                    '"performer_id":42,'
                                                    '"action_text":"Проверить документы по Минцифре"}'
                                                ),
                                            ),
                                        )
                                    ],
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
                        SimpleNamespace(
                            delta=SimpleNamespace(
                                content="Preview generated for Ардо Наталья Алексеевна; confirmation is required."
                            )
                        )
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


class MultiStepCreateClient:
    def __init__(self):
        self.completions = MultiStepCreateCompletions()
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


def test_llm_service_disables_env_proxy_for_local_ollama(monkeypatch):
    created_clients = []

    class CapturingOpenAI:
        def __init__(self, **kwargs):
            created_clients.append(kwargs)

    monkeypatch.setattr(llm_service_module, "OpenAI", CapturingOpenAI)

    LLMService(
        provider="ollama",
        base_url="http://localhost:11434/v1",
        api_key="ollama",
        model="gemma4",
        tool_calling="auto",
        tool_registry=FakeToolRegistry(),
    )

    assert created_clients[0]["http_client"].trust_env is False


def test_llm_service_keeps_env_proxy_for_remote_provider(monkeypatch):
    created_clients = []

    class CapturingOpenAI:
        def __init__(self, **kwargs):
            created_clients.append(kwargs)

    monkeypatch.setattr(llm_service_module, "OpenAI", CapturingOpenAI)

    LLMService(
        provider="openrouter",
        base_url="https://openrouter.ai/api/v1",
        api_key="test-key",
        model="openrouter/free",
        tool_calling="auto",
        tool_registry=FakeToolRegistry(),
    )

    assert created_clients[0]["http_client"].trust_env is True


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


def test_stream_chat_routes_in_progress_tasks_question_without_model_tool_call():
    registry = RecordingToolRegistry()
    service = LLMService(
        provider="ollama",
        base_url="http://localhost:11434/v1",
        api_key="ollama",
        model="gemma4",
        tool_calling="auto",
        tool_registry=registry,
    )
    client = ToolCallClient()
    service.client = client

    chunks = list(
        service.stream_chat(
            "\u041a\u0430\u043a\u0438\u0435 \u0443 \u043c\u0435\u043d\u044f \u0435\u0441\u0442\u044c "
            "\u0437\u0430\u0434\u0430\u0447\u0438 \u0432 \u0440\u0430\u0431\u043e\u0442\u0435?",
            [],
        )
    )

    assert registry.calls == [("get_my_assignments", {})]
    assert chunks == ["\u041d\u0430\u0439\u0434\u0435\u043d\u043e 1: Task (InProcess)."]
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


def test_stream_chat_handles_search_then_create_tool_calls():
    registry = RecordingToolRegistry()
    service = LLMService(
        provider="openrouter",
        base_url="https://openrouter.ai/api/v1",
        api_key="test-key",
        model="openrouter/free",
        tool_calling="auto",
        tool_registry=registry,
    )
    client = MultiStepCreateClient()
    service.client = client

    chunks = list(service.stream_chat("Use tools to prepare an action item", []))

    assert chunks == [
        "I found Ардо Наталья Алексеевна. ",
        "Preview generated for Ардо Наталья Алексеевна; confirmation is required.",
    ]
    assert registry.calls == [
        ("search_employee", {"query": "Ардо"}),
        (
            "create_action_item",
            {
                "subject": "Проверить документы по Минцифре",
                "performer_id": 42,
                "action_text": "Проверить документы по Минцифре",
            },
        ),
    ]
    assert len(client.completions.requests) == 3


def test_stream_chat_routes_explicit_create_action_item_intent_without_model():
    registry = RecordingToolRegistry()
    service = LLMService(
        provider="openrouter",
        base_url="https://openrouter.ai/api/v1",
        api_key="test-key",
        model="openrouter/free",
        tool_calling="auto",
        tool_registry=registry,
    )
    client = MultiStepCreateClient()
    service.client = client

    chunks = list(
        service.stream_chat("Создай задание для Ардо, Проверить документы по Минцифре, срок - завтра", [])
    )

    assert registry.calls[0] == ("search_employee", {"query": "Ардо"})
    assert registry.calls[1][0] == "create_action_item"
    assert registry.calls[1][1]["subject"] == "Проверить документы по Минцифре"
    assert registry.calls[1][1]["performer_id"] == 42
    assert registry.calls[1][1]["action_text"] == "Проверить документы по Минцифре"
    assert "deadline" in registry.calls[1][1]
    visible, preview = _split_action_item_preview_marker(chunks[0])
    assert visible.startswith("Подготовлен preview")
    assert preview["type"] == "action_item"
    assert preview["payload"]["subject"] == registry.calls[1][1]["subject"]
    assert preview["payload"]["performer_id"] == 42
    assert preview["payload"]["action_text"] == registry.calls[1][1]["action_text"]
    assert preview["payload"]["deadline"].endswith("+00:00")
    assert client.completions.requests == []


def test_stream_chat_completes_action_item_draft_from_history_without_model():
    registry = RecordingToolRegistry()
    service = LLMService(
        provider="ollama",
        base_url="http://localhost:11434/v1",
        api_key="ollama",
        model="gemma4",
        tool_calling="auto",
        tool_registry=registry,
    )
    client = MultiStepCreateClient()
    service.client = client

    chunks = list(
        service.stream_chat(
            "\u0422\u0435\u043a\u0441\u0442 \u043f\u043e\u0440\u0443\u0447\u0435\u043d\u0438\u044f - "
            "\u041f\u0440\u043e\u0432\u0435\u0440\u044c \u0434\u043e\u043a\u0443\u043c\u0435\u043d\u0442\u044b "
            "\u043e\u0442 \u041c\u0426.",
            [
                {
                    "role": "user",
                    "content": (
                        "\u041f\u043e\u0434\u0433\u043e\u0442\u043e\u0432\u044c "
                        "\u043f\u043e\u0440\u0443\u0447\u0435\u043d\u0438\u0435 "
                        "\u0434\u043b\u044f \u0410\u0440\u0434\u043e \u041d, "
                        "\u0442\u0435\u043c\u0430 - \u0414\u043e\u043a\u0443\u043c\u0435\u043d\u0442\u044b "
                        "\u041c\u0426, \u0441\u0440\u043e\u043a - 25.05"
                    ),
                },
                {
                    "role": "assistant",
                    "content": (
                        "\u0414\u043b\u044f \u0441\u043e\u0437\u0434\u0430\u043d\u0438\u044f "
                        "\u043f\u043e\u0440\u0443\u0447\u0435\u043d\u0438\u044f \u043c\u043d\u0435 "
                        "\u043d\u0443\u0436\u0435\u043d \u0442\u0435\u043a\u0441\u0442."
                    ),
                },
            ],
        )
    )

    assert registry.calls[0] == ("search_employee", {"query": "\u0410\u0440\u0434\u043e \u041d"})
    assert registry.calls[1][0] == "create_action_item"
    assert registry.calls[1][1]["subject"] == "\u0414\u043e\u043a\u0443\u043c\u0435\u043d\u0442\u044b \u041c\u0426"
    assert registry.calls[1][1]["action_text"] == (
        "\u041f\u0440\u043e\u0432\u0435\u0440\u044c \u0434\u043e\u043a\u0443\u043c\u0435\u043d\u0442\u044b "
        "\u043e\u0442 \u041c\u0426."
    )
    assert registry.calls[1][1]["performer_id"] == 42
    assert "deadline" in registry.calls[1][1]
    visible, preview = _split_action_item_preview_marker(chunks[0])
    assert visible == (
        "\u041f\u043e\u0434\u0433\u043e\u0442\u043e\u0432\u043b\u0435\u043d preview "
        "\u043f\u043e\u0440\u0443\u0447\u0435\u043d\u0438\u044f \u0434\u043b\u044f "
        "\u0410\u0440\u0434\u043e \u041d\u0430\u0442\u0430\u043b\u044c\u044f "
        "\u0410\u043b\u0435\u043a\u0441\u0435\u0435\u0432\u043d\u0430: "
        "\u0414\u043e\u043a\u0443\u043c\u0435\u043d\u0442\u044b \u041c\u0426. "
        "\u0414\u043b\u044f \u0444\u0430\u043a\u0442\u0438\u0447\u0435\u0441\u043a\u043e\u0433\u043e "
        "\u0441\u043e\u0437\u0434\u0430\u043d\u0438\u044f \u043d\u0443\u0436\u043d\u043e "
        "\u043f\u043e\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0435\u043d\u0438\u0435."
    )
    assert preview["type"] == "action_item"
    assert preview["payload"]["subject"] == "\u0414\u043e\u043a\u0443\u043c\u0435\u043d\u0442\u044b \u041c\u0426"
    assert preview["payload"]["performer_id"] == 42
    assert preview["payload"]["action_text"] == (
        "\u041f\u0440\u043e\u0432\u0435\u0440\u044c \u0434\u043e\u043a\u0443\u043c\u0435\u043d\u0442\u044b "
        "\u043e\u0442 \u041c\u0426."
    )
    assert client.completions.requests == []


def test_stream_chat_routes_quoted_action_item_with_short_date_without_model():
    registry = RecordingToolRegistry()
    service = LLMService(
        provider="ollama",
        base_url="http://localhost:11434/v1",
        api_key="ollama",
        model="gemma4",
        tool_calling="auto",
        tool_registry=registry,
    )
    client = MultiStepCreateClient()
    service.client = client

    chunks = list(
        service.stream_chat(
            "\u0421\u043e\u0437\u0434\u0430\u0439 "
            "\u043f\u043e\u0440\u0443\u0447\u0435\u043d\u0438\u0435 "
            "\u0434\u043b\u044f \u0410\u0440\u0434\u043e "
            '"\u041f\u0440\u043e\u0432\u0435\u0440\u044c '
            "\u0434\u043e\u043a\u0443\u043c\u0435\u043d\u0442\u044b "
            '\u043e\u0442 \u041c\u0438\u043d\u0446\u0438\u0444\u0440\u044b" '
            "\u0441\u0440\u043e\u043a 26.05.26",
            [],
        )
    )

    assert registry.calls[0] == ("search_employee", {"query": "\u0410\u0440\u0434\u043e"})
    assert registry.calls[1][0] == "create_action_item"
    assert registry.calls[1][1]["subject"] == (
        "\u041f\u0440\u043e\u0432\u0435\u0440\u044c "
        "\u0434\u043e\u043a\u0443\u043c\u0435\u043d\u0442\u044b "
        "\u043e\u0442 \u041c\u0438\u043d\u0446\u0438\u0444\u0440\u044b"
    )
    assert registry.calls[1][1]["action_text"] == registry.calls[1][1]["subject"]
    assert registry.calls[1][1]["deadline"].startswith("2026-05-26T23:59:00")
    visible, preview = _split_action_item_preview_marker(chunks[0])
    assert visible == (
        "\u041f\u043e\u0434\u0433\u043e\u0442\u043e\u0432\u043b\u0435\u043d preview "
        "\u043f\u043e\u0440\u0443\u0447\u0435\u043d\u0438\u044f \u0434\u043b\u044f "
        "\u0410\u0440\u0434\u043e \u041d\u0430\u0442\u0430\u043b\u044c\u044f "
        "\u0410\u043b\u0435\u043a\u0441\u0435\u0435\u0432\u043d\u0430: "
        "\u041f\u0440\u043e\u0432\u0435\u0440\u044c "
        "\u0434\u043e\u043a\u0443\u043c\u0435\u043d\u0442\u044b "
        "\u043e\u0442 \u041c\u0438\u043d\u0446\u0438\u0444\u0440\u044b. "
        "\u0414\u043b\u044f \u0444\u0430\u043a\u0442\u0438\u0447\u0435\u0441\u043a\u043e\u0433\u043e "
        "\u0441\u043e\u0437\u0434\u0430\u043d\u0438\u044f \u043d\u0443\u0436\u043d\u043e "
        "\u043f\u043e\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0435\u043d\u0438\u0435."
    )
    assert preview["type"] == "action_item"
    assert preview["payload"]["deadline"].startswith("2026-05-26T23:59:00")
    assert client.completions.requests == []


def test_stream_chat_routes_natural_action_item_request_with_short_date_without_model():
    registry = RecordingToolRegistry()
    service = LLMService(
        provider="ollama",
        base_url="http://localhost:11434/v1",
        api_key="ollama",
        model="gemma4",
        tool_calling="auto",
        tool_registry=registry,
    )
    client = MultiStepCreateClient()
    service.client = client

    chunks = list(
        service.stream_chat(
            "\u0421\u043e\u0437\u0434\u0430\u0439 "
            "\u0437\u0430\u0434\u0430\u0447\u0443 "
            "\u0434\u043b\u044f \u0410\u0440\u0434\u043e "
            "\u0447\u0442\u043e\u0431\u044b \u043e\u043d\u0430 "
            "\u043f\u0440\u043e\u0432\u0435\u0440\u0438\u043b\u0430 "
            "\u0434\u043e\u043a\u0443\u043c\u0435\u043d\u0442\u044b "
            "\u043e\u0442 \u041c\u0438\u043d\u0446\u0438\u0444\u0440\u044b "
            "\u0441\u043e \u0441\u0440\u043e\u043a\u043e\u043c 26.05.26",
            [],
        )
    )

    assert registry.calls[0] == ("search_employee", {"query": "\u0410\u0440\u0434\u043e"})
    assert registry.calls[1][0] == "create_action_item"
    assert registry.calls[1][1]["subject"] == (
        "\u043f\u0440\u043e\u0432\u0435\u0440\u0438\u043b\u0430 "
        "\u0434\u043e\u043a\u0443\u043c\u0435\u043d\u0442\u044b "
        "\u043e\u0442 \u041c\u0438\u043d\u0446\u0438\u0444\u0440\u044b"
    )
    assert registry.calls[1][1]["deadline"].startswith("2026-05-26T23:59:00")
    assert chunks[0].startswith("\u041f\u043e\u0434\u0433\u043e\u0442\u043e\u0432\u043b\u0435\u043d preview")
    assert client.completions.requests == []


def test_stream_chat_strips_sentence_punctuation_from_employee_query_without_model():
    registry = RecordingToolRegistry()
    service = LLMService(
        provider="ollama",
        base_url="http://localhost:11434/v1",
        api_key="ollama",
        model="gemma4",
        tool_calling="auto",
        tool_registry=registry,
    )
    client = MultiStepCreateClient()
    service.client = client

    chunks = list(
        service.stream_chat(
            "\u0421\u043e\u0437\u0434\u0430\u0439 "
            "\u043f\u043e\u0440\u0443\u0447\u0435\u043d\u0438\u0435 "
            "\u0434\u043b\u044f \u041d\u0430\u0442\u0430\u043b\u044c\u044f "
            "\u0410\u0440\u0434\u043e. "
            "\u0427\u0442\u043e\u0431\u044b \u043e\u043d\u0430 "
            "\u043f\u0440\u043e\u0432\u0435\u0440\u0438\u043b\u0430 "
            "\u0434\u043e\u043a\u0443\u043c\u0435\u043d\u0442\u044b "
            "\u043e\u0442 \u041c\u0438\u043d\u0446\u0438\u0444\u0440\u044b. "
            "\u0421\u0440\u043e\u043a 25.05.2026",
            [],
        )
    )

    assert registry.calls[0] == ("search_employee", {"query": "\u041d\u0430\u0442\u0430\u043b\u044c\u044f \u0410\u0440\u0434\u043e"})
    assert registry.calls[1][0] == "create_action_item"
    assert registry.calls[1][1]["subject"] == (
        "\u043f\u0440\u043e\u0432\u0435\u0440\u0438\u043b\u0430 "
        "\u0434\u043e\u043a\u0443\u043c\u0435\u043d\u0442\u044b "
        "\u043e\u0442 \u041c\u0438\u043d\u0446\u0438\u0444\u0440\u044b"
    )
    assert registry.calls[1][1]["deadline"].startswith("2026-05-25T23:59:00")
    assert chunks[0].startswith("\u041f\u043e\u0434\u0433\u043e\u0442\u043e\u0432\u043b\u0435\u043d preview")


def test_stream_chat_extracts_sentence_deadline_from_action_item_text_without_model():
    registry = RecordingToolRegistry()
    service = LLMService(
        provider="ollama",
        base_url="http://localhost:11434/v1",
        api_key="ollama",
        model="gemma4",
        tool_calling="auto",
        tool_registry=registry,
    )
    client = MultiStepCreateClient()
    service.client = client

    chunks = list(
        service.stream_chat(
            "\u0421\u043e\u0437\u0434\u0430\u0439 "
            "\u043f\u043e\u0440\u0443\u0447\u0435\u043d\u0438\u0435 "
            "\u0434\u043b\u044f \u0410\u0440\u0434\u043e, "
            "\u041f\u043e\u0434\u0433\u043e\u0442\u043e\u0432\u044c "
            "\u0434\u043e\u043a\u0443\u043c\u0435\u043d\u0442\u044b "
            "\u0434\u043b\u044f \u0410\u043f\u043f\u0430\u0440\u0430\u0442\u0430 "
            "\u043f\u0440\u0430\u0432\u0438\u0442\u0435\u043b\u044c\u0441\u0442\u0432\u0430. "
            "\u0421\u0440\u043e\u043a 25.06.2026",
            [],
        )
    )

    assert registry.calls[0] == ("search_employee", {"query": "\u0410\u0440\u0434\u043e"})
    assert registry.calls[1][0] == "create_action_item"
    assert registry.calls[1][1]["subject"] == (
        "\u041f\u043e\u0434\u0433\u043e\u0442\u043e\u0432\u044c "
        "\u0434\u043e\u043a\u0443\u043c\u0435\u043d\u0442\u044b "
        "\u0434\u043b\u044f \u0410\u043f\u043f\u0430\u0440\u0430\u0442\u0430 "
        "\u043f\u0440\u0430\u0432\u0438\u0442\u0435\u043b\u044c\u0441\u0442\u0432\u0430"
    )
    assert registry.calls[1][1]["action_text"] == registry.calls[1][1]["subject"]
    assert registry.calls[1][1]["deadline"].startswith("2026-06-25T23:59:00")
    visible, preview = _split_action_item_preview_marker(chunks[0])
    assert "\u0421\u0440\u043e\u043a 25.06.2026" not in visible
    assert preview["payload"]["deadline"].startswith("2026-06-25T23:59:00")
