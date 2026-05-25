from types import SimpleNamespace
from datetime import datetime, timedelta, timezone
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
                "confirmation_payload": {**arguments, "confirm": False},
            }
        if name == "create_task":
            return {
                "mode": "preview",
                "payload": {
                    "subject": arguments["subject"],
                    "performer_id": arguments["performer_id"],
                    "action_text": arguments["action_text"],
                },
                "success": True,
                "message": "Preview generated; confirm to create the task.",
                "confirmation_payload": {**arguments, "confirm": False},
            }
        return [{"id": 1, "subject": "Task", "status": "InProcess", "entity_type": "assignment"}]


class FailingToolRegistry(FakeToolRegistry):
    def call(self, name, arguments):
        raise RuntimeError("DIRECTUM_AUTH_TOKEN must be a valid Basic token")


class RejectingCreateConfirmationRegistry(FakeToolRegistry):
    def openai_tools(self):
        return [
            {
                "type": "function",
                "function": {
                    "name": "create_action_item",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ]

    def call(self, name, arguments):
        raise ValueError("Tool 'create_action_item' cannot confirm creation directly; use preview mode first")


class RejectingVagueCreateRegistry(FakeToolRegistry):
    def openai_tools(self):
        return [
            {
                "type": "function",
                "function": {
                    "name": "create_action_item",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ]

    def call(self, name, arguments):
        raise ValueError(
            "Tool 'create_action_item' needs concrete user-provided subject and action_text; "
            "ask the user for the task text"
        )


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


class ConfirmingCreateCompletions:
    def __init__(self):
        self.requests = []

    def create(self, **kwargs):
        self.requests.append(kwargs)
        return iter(
            [
                SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            delta=SimpleNamespace(
                                tool_calls=[
                                    SimpleNamespace(
                                        index=0,
                                        id="call_confirm",
                                        type="function",
                                        function=SimpleNamespace(
                                            name="create_action_item",
                                            arguments=(
                                                '{"subject":"Проверить документы",'
                                                '"performer_id":42,'
                                                '"action_text":"Проверить документы",'
                                                '"confirm":true}'
                                            ),
                                        ),
                                    )
                                ]
                            )
                        )
                    ]
                )
            ]
        )


class ExtractDraftCompletions:
    def __init__(self, draft):
        self.draft = draft
        self.requests = []

    def create(self, **kwargs):
        self.requests.append(kwargs)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=json.dumps(self.draft, ensure_ascii=False))
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


class ConfirmingCreateClient:
    def __init__(self):
        self.completions = ConfirmingCreateCompletions()
        self.chat = SimpleNamespace(completions=self.completions)


class ExtractDraftClient:
    def __init__(self, draft):
        self.completions = ExtractDraftCompletions(draft)
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


@pytest.mark.parametrize(
    "message",
    [
        "Дай сводку по моим исходящим поручениям",
        "Поручения от меня",
    ],
)
def test_stream_chat_routes_created_action_items_intent_without_model_tool_call(message):
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

    chunks = list(service.stream_chat(message, []))

    assert registry.calls == [("get_action_items_created_by_me", {})]
    assert chunks == ["Найдено 1: Task (InProcess)."]
    assert client.completions.requests == []


def test_stream_chat_formats_multiple_directum_items_as_markdown_list():
    class MultipleItemsRegistry(FakeToolRegistry):
        def __init__(self):
            self.calls = []

        def call(self, name, arguments):
            self.calls.append((name, arguments))
            return [
                {
                    "subject": "Поручение: Подготовить ответ",
                    "status": "InProcess",
                    "deadline": "2026-06-27T23:59:00+00:00",
                    "entity_type": "action_item_task",
                    "url": "https://rx.example/Client/#/card/83f2a537-0cf0-4429-ae76-e9a386ca53aa/987",
                },
                {
                    "subject": "Поручение: Проверить документы",
                    "status": "InProcess",
                    "deadline": None,
                    "entity_type": "action_item_task",
                },
            ]

    registry = MultipleItemsRegistry()
    service = LLMService(
        provider="openrouter",
        base_url="https://openrouter.ai/api/v1",
        api_key="test-key",
        model="openrouter/free",
        tool_calling="auto",
        tool_registry=registry,
    )

    chunks = list(service.stream_chat("Дай сводку по моим исходящим поручениям", []))

    assert registry.calls == [("get_action_items_created_by_me", {})]
    assert chunks == [
        (
            "Найдено 2:\n\n"
            "1. [**Поручение: Подготовить ответ**]"
            "(<https://rx.example/Client/#/card/83f2a537-0cf0-4429-ae76-e9a386ca53aa/987>)"
            " — статус: InProcess, срок: 27.06.2026\n"
            "2. **Поручение: Проверить документы** — статус: InProcess"
        )
    ]


def test_stream_chat_formats_outgoing_action_item_analytics_by_deadline():
    now = datetime.now(timezone.utc)
    work_deadline = now + timedelta(days=3)
    due_soon_deadline = now + timedelta(hours=12)
    overdue_deadline = now - timedelta(hours=1)

    class AnalyticsRegistry(FakeToolRegistry):
        def __init__(self):
            self.calls = []

        def call(self, name, arguments):
            self.calls.append((name, arguments))
            return [
                {
                    "subject": "Поручение в работе",
                    "status": "InProcess",
                    "deadline": work_deadline.isoformat(),
                    "entity_type": "action_item_task",
                    "url": "https://rx.example/Client/#/card/83f2a537-0cf0-4429-ae76-e9a386ca53aa/101",
                },
                {
                    "subject": "Скоро срок",
                    "status": "InProcess",
                    "deadline": due_soon_deadline.isoformat(),
                    "entity_type": "action_item_task",
                },
                {
                    "subject": "Просроченное поручение",
                    "status": "InProcess",
                    "deadline": overdue_deadline.isoformat(),
                    "entity_type": "action_item_task",
                },
            ]

    registry = AnalyticsRegistry()
    service = LLMService(
        provider="openrouter",
        base_url="https://openrouter.ai/api/v1",
        api_key="test-key",
        model="openrouter/free",
        tool_calling="auto",
        tool_registry=registry,
    )

    chunks = list(service.stream_chat("Дай аналитику по исходящим поручениям", []))

    assert registry.calls == [("get_action_items_created_by_me", {})]
    assert len(chunks) == 1
    assert "## Аналитика по исходящим поручениям" in chunks[0]
    assert "Всего исходящих поручений: **3**." in chunks[0]
    assert (
        '### <span class="analytics-heading analytics-heading-work">Поручения в работе</span>'
        "\n1. [**Поручение в работе**](<https://rx.example/Client/#/card/83f2a537-0cf0-4429-ae76-e9a386ca53aa/101>)"
    ) in chunks[0]
    assert (
        '### <span class="analytics-heading analytics-heading-due-soon">Срок подходит к концу (остался один день)</span>'
        "\n1. **Скоро срок**"
    ) in chunks[0]
    assert (
        '### <span class="analytics-heading analytics-heading-overdue">Просроченные поручения</span>'
        "\n1. **Просроченное поручение**"
    ) in chunks[0]


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


def test_stream_chat_routes_performer_only_action_item_request_without_model():
    registry = RecordingToolRegistry()
    service = LLMService(
        provider="ollama",
        base_url="http://localhost:11434/v1",
        api_key="ollama",
        model="qwen",
        tool_calling="auto",
        tool_registry=registry,
    )
    client = ToolCallClient()
    service.client = client

    chunks = list(service.stream_chat("Создай поручение на Ардо Наталью", []))

    assert chunks == ["Для подготовки поручения нужен конкретный текст: что именно должен сделать исполнитель?"]
    assert registry.calls == []
    assert len(client.completions.requests) == 1


def test_stream_chat_routes_plural_performer_only_action_item_request_without_model():
    registry = RecordingToolRegistry()
    service = LLMService(
        provider="ollama",
        base_url="http://localhost:11434/v1",
        api_key="ollama",
        model="qwen",
        tool_calling="auto",
        tool_registry=registry,
    )
    client = ToolCallClient()
    service.client = client

    chunks = list(service.stream_chat("Подготовь поручения для Ардо", []))

    assert chunks == ["Для подготовки поручения нужен конкретный текст: что именно должен сделать исполнитель?"]
    assert registry.calls == []
    assert len(client.completions.requests) == 1


def test_stream_chat_rejects_text_confirmation_after_preview_without_model():
    registry = RecordingToolRegistry()
    service = LLMService(
        provider="ollama",
        base_url="http://localhost:11434/v1",
        api_key="ollama",
        model="qwen",
        tool_calling="auto",
        tool_registry=registry,
    )
    client = ToolCallClient()
    service.client = client

    chunks = list(
        service.stream_chat(
            "Да",
            [
                {
                    "role": "assistant",
                    "content": (
                        "Подготовлен preview поручения для Ардо Наталья: Проверить документы. "
                        "Для фактического создания нажмите кнопку подтверждения."
                    ),
                }
            ],
        )
    )

    assert chunks == [service._confirmation_requires_button_message()]
    assert registry.calls == []
    assert client.completions.requests == []


def test_stream_chat_turns_model_direct_confirmation_tool_call_into_button_instruction():
    service = LLMService(
        provider="ollama",
        base_url="http://localhost:11434/v1",
        api_key="ollama",
        model="qwen",
        tool_calling="auto",
        tool_registry=RejectingCreateConfirmationRegistry(),
    )
    service.client = ConfirmingCreateClient()

    chunks = list(service.stream_chat("Да", []))

    assert chunks == [service._confirmation_requires_button_message()]


def test_stream_chat_turns_model_vague_create_tool_call_into_text_request():
    service = LLMService(
        provider="ollama",
        base_url="http://localhost:11434/v1",
        api_key="ollama",
        model="qwen",
        tool_calling="auto",
        tool_registry=RejectingVagueCreateRegistry(),
    )
    service.client = ConfirmingCreateClient()

    chunks = list(service.stream_chat("Use the create tool with vague generated text", []))

    assert chunks == ["Для подготовки поручения нужен конкретный текст: что именно должен сделать исполнитель?"]


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

    assert chunks[0] == "I found Ардо Наталья Алексеевна. "
    visible, preview = _split_action_item_preview_marker(chunks[1])
    assert visible == (
        "Подготовлен preview поручения для Ардо Наталья Алексеевна: "
        "Проверить документы по Минцифре. "
        "Для фактического создания нажмите кнопку подтверждения."
    )
    assert preview["type"] == "action_item"
    assert preview["payload"] == {
        "subject": "Проверить документы по Минцифре",
        "performer_id": 42,
        "action_text": "Проверить документы по Минцифре",
        "confirm": False,
    }
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
    assert len(client.completions.requests) == 2


def test_stream_chat_routes_explicit_assignment_word_to_task_without_model():
    class CreateCompletions:
        def __init__(self):
            self.call_count = 0

        def create(self, **kwargs):
            self.call_count += 1
            if self.call_count == 1:
                return SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            message=SimpleNamespace(
                                content='{"subject": "Проверка документов по Минцифре", '
                                '"action_text": "Проверьте документы по Минцифре."}'
                            )
                        )
                    ]
                )
            return iter([])

    class CreateClient:
        def __init__(self):
            self.completions = CreateCompletions()
            self.chat = SimpleNamespace(completions=self.completions)

    registry = RecordingToolRegistry()
    service = LLMService(
        provider="openrouter",
        base_url="https://openrouter.ai/api/v1",
        api_key="test-key",
        model="openrouter/free",
        tool_calling="auto",
        tool_registry=registry,
    )
    service.client = CreateClient()

    chunks = list(
        service.stream_chat("Создай задание для Ардо, Проверить документы по Минцифре, срок - завтра", [])
    )

    assert registry.calls[0] == ("search_employee", {"query": "Ардо"})
    assert registry.calls[1][0] == "create_task"
    assert registry.calls[1][1]["performer_id"] == 42
    assert "deadline" in registry.calls[1][1]
    visible, preview = _split_action_item_preview_marker(chunks[0])
    assert visible.startswith("Подготовлен preview")
    assert preview["type"] == "task"
    assert preview["payload"]["subject"] == "Проверить документы по Минцифре"
    assert preview["payload"]["performer_id"] == 42
    assert preview["payload"]["action_text"] == "Проверить документы по Минцифре"
    assert preview["payload"]["deadline"].endswith("+00:00")


def test_stream_chat_routes_user_assignment_example_to_task_without_model():
    registry = RecordingToolRegistry()
    service = LLMService(
        provider="ollama",
        base_url="http://localhost:11434/v1",
        api_key="ollama",
        model="qwen",
        tool_calling="auto",
        tool_registry=registry,
    )
    service.client = MultiStepCreateClient()

    chunks = list(service.stream_chat("Подготовь задание для Ардо, Ей нужно вынести мусор, срок 25.05.2026", []))

    assert registry.calls[0] == ("search_employee", {"query": "Ардо"})
    assert registry.calls[1][0] == "create_task"
    assert registry.calls[1][1]["subject"] == "Ей нужно вынести мусор"
    assert registry.calls[1][1]["action_text"] == "Ей нужно вынести мусор"
    assert registry.calls[1][1]["deadline"].startswith("2026-05-25T23:59:00")
    visible, preview = _split_action_item_preview_marker(chunks[0])
    assert visible.startswith("Подготовлен preview задачи")
    assert preview["type"] == "task"


def test_stream_chat_uses_llm_draft_for_task_with_dot_after_employee():
    registry = RecordingToolRegistry()
    service = LLMService(
        provider="ollama",
        base_url="http://localhost:11434/v1",
        api_key="ollama",
        model="qwen",
        tool_calling="auto",
        tool_registry=registry,
    )
    service.client = ExtractDraftClient(
        {
            "entity_type": "task",
            "employee_query": "Ардо Н",
            "subject": "Вынос мусора из коридора",
            "action_text": "Вынесите мусор из коридора",
            "deadline": "2026-06-27T23:59:00+00:00",
            "missing_fields": [],
        }
    )

    chunks = list(service.stream_chat("Создай задачу для Ардо Н. пусть вынесет мусор из корридора срок 27.06.26", []))

    assert registry.calls[0] == ("search_employee", {"query": "Ардо Н"})
    assert registry.calls[1] == (
        "create_task",
        {
            "subject": "Вынос мусора из коридора",
            "performer_id": 42,
                "action_text": "Вынесите мусор из коридора",
            "deadline": "2026-06-27T23:59:00+00:00",
        },
    )
    visible, preview = _split_action_item_preview_marker(chunks[0])
    assert visible.startswith("Подготовлен preview задачи")
    assert preview["type"] == "task"
    assert preview["payload"]["deadline"] == "2026-06-27T23:59:00+00:00"


def test_stream_chat_uses_llm_draft_for_assignment_with_sentence_after_employee():
    registry = RecordingToolRegistry()
    service = LLMService(
        provider="ollama",
        base_url="http://localhost:11434/v1",
        api_key="ollama",
        model="qwen",
        tool_calling="auto",
        tool_registry=registry,
    )
    service.client = ExtractDraftClient(
        {
            "entity_type": "task",
            "employee_query": "Ардо Н",
            "subject": "Вынос мусора из кабинета",
            "action_text": "Вынесите мусор из кабинета.",
            "deadline": "2027-06-26T23:59:00+00:00",
            "missing_fields": [],
        }
    )

    chunks = list(service.stream_chat("Создай задание для Ардо Н. Вынести мусор из кабинета. Срок 26.06.27", []))

    assert registry.calls[0] == ("search_employee", {"query": "Ардо Н"})
    assert registry.calls[1][0] == "create_task"
    assert registry.calls[1][1]["deadline"] == "2027-06-26T23:59:00+00:00"
    visible, preview = _split_action_item_preview_marker(chunks[0])
    assert visible.startswith("Подготовлен preview задачи")
    assert preview["type"] == "task"
    assert preview["payload"]["action_text"] == "Вынесите мусор из кабинета"


def test_stream_chat_reuses_previous_preview_for_same_action_item_request():
    registry = RecordingToolRegistry()
    service = LLMService(
        provider="ollama",
        base_url="http://localhost:11434/v1",
        api_key="ollama",
        model="qwen",
        tool_calling="auto",
        tool_registry=registry,
    )
    previous_marker = service._action_item_preview_marker(
        {
            "subject": "Вынести мусор",
            "performer_id": 42,
            "action_text": "Вынести мусор",
            "deadline": "2026-06-26T23:59:00+00:00",
        },
        "Ардо Наталья Алексеевна",
        "task",
        correct_text=False,
    )
    service.client = ExtractDraftClient(
        {
            "entity_type": "action_item",
            "employee_query": None,
            "subject": None,
            "action_text": None,
            "deadline": None,
            "missing_fields": ["employee_query", "action_text"],
        }
    )

    chunks = list(
        service.stream_chat(
            "Создай такое же поручение для нее",
            [{"role": "assistant", "content": f"Подготовлен preview задачи для Ардо Наталья Алексеевна: Вынести мусор.\n{previous_marker}"}],
        )
    )

    assert registry.calls[0] == ("search_employee", {"query": "Ардо Наталья Алексеевна"})
    assert registry.calls[1][0] == "create_action_item"
    assert registry.calls[1][1]["subject"] == "Вынести мусор"
    assert registry.calls[1][1]["action_text"] == "Вынести мусор"
    assert registry.calls[1][1]["deadline"] == "2026-06-26T23:59:00+00:00"
    visible, preview = _split_action_item_preview_marker(chunks[0])
    assert visible.startswith("Подготовлен preview поручения")
    assert preview["type"] == "action_item"


def test_stream_chat_completes_pending_action_item_from_executor_followup():
    registry = RecordingToolRegistry()
    service = LLMService(
        provider="ollama",
        base_url="http://localhost:11434/v1",
        api_key="ollama",
        model="qwen",
        tool_calling="auto",
        tool_registry=registry,
    )
    service.client = ToolCallClient()

    chunks = list(
        service.stream_chat(
            "Вынести мусор, исполнитель Ардо срок 26.06.2026",
            [{"role": "assistant", "content": "Для подготовки поручения нужен конкретный текст: что именно должен сделать исполнитель?"}],
        )
    )

    assert registry.calls[0] == ("search_employee", {"query": "Ардо"})
    assert registry.calls[1][0] == "create_action_item"
    assert registry.calls[1][1]["subject"] == "Вынести мусор"
    assert registry.calls[1][1]["deadline"].startswith("2026-06-26T23:59:00")
    visible, preview = _split_action_item_preview_marker(chunks[0])
    assert visible.startswith("Подготовлен preview поручения")
    assert preview["type"] == "action_item"


def test_stream_chat_completes_pending_action_item_from_topic_followup_with_typo():
    registry = RecordingToolRegistry()
    service = LLMService(
        provider="ollama",
        base_url="http://localhost:11434/v1",
        api_key="ollama",
        model="qwen",
        tool_calling="auto",
        tool_registry=registry,
    )
    service.client = ToolCallClient()

    chunks = list(
        service.stream_chat(
            "Тема - документы от МЦ РФ, испонитель - Ардо, срок - 28.06.2026",
            [{"role": "assistant", "content": "Укажите тему, исполнителя и текст поручения в чате. Сначала будет подготовлен preview."}],
        )
    )

    assert registry.calls[0] == ("search_employee", {"query": "Ардо"})
    assert registry.calls[1][0] == "create_action_item"
    assert registry.calls[1][1]["subject"] == "документы от МЦ РФ"
    assert registry.calls[1][1]["deadline"].startswith("2026-06-28T23:59:00")
    visible, preview = _split_action_item_preview_marker(chunks[0])
    assert visible.startswith("Подготовлен preview поручения")
    assert preview["type"] == "action_item"


def test_stream_chat_uses_llm_draft_for_give_action_item_request():
    registry = RecordingToolRegistry()
    service = LLMService(
        provider="ollama",
        base_url="http://localhost:11434/v1",
        api_key="ollama",
        model="qwen",
        tool_calling="auto",
        tool_registry=registry,
    )
    service.client = ExtractDraftClient(
        {
            "entity_type": "action_item",
            "employee_query": "Ардо Н",
            "subject": "Подготовить документы для МЦ РФ",
            "action_text": "Подготовить документы для МЦ РФ",
            "deadline": "2027-06-27T23:59:00+00:00",
            "missing_fields": [],
        }
    )

    chunks = list(service.stream_chat("Выдай поручение для Ардо Н. Подготовить документы для МЦ РФ. Срок 27.06.2027", []))

    assert registry.calls[0] == ("search_employee", {"query": "Ардо Н"})
    assert registry.calls[1][0] == "create_action_item"
    assert registry.calls[1][1]["deadline"] == "2027-06-27T23:59:00+00:00"
    visible, preview = _split_action_item_preview_marker(chunks[0])
    assert visible.startswith("Подготовлен preview поручения")
    assert preview["type"] == "action_item"


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


def test_stream_chat_routes_natural_task_request_with_short_date_without_model():
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
    assert registry.calls[1][0] == "create_task"
    assert registry.calls[1][1]["subject"] == (
        "\u043f\u0440\u043e\u0432\u0435\u0440\u0438\u043b\u0430 "
        "\u0434\u043e\u043a\u0443\u043c\u0435\u043d\u0442\u044b "
        "\u043e\u0442 \u041c\u0438\u043d\u0446\u0438\u0444\u0440\u044b"
    )
    assert registry.calls[1][1]["deadline"].startswith("2026-05-26T23:59:00")
    visible, preview = _split_action_item_preview_marker(chunks[0])
    assert visible.startswith("\u041f\u043e\u0434\u0433\u043e\u0442\u043e\u0432\u043b\u0435\u043d preview \u0437\u0430\u0434\u0430\u0447\u0438")
    assert preview["type"] == "task"


def test_stream_chat_routes_comma_task_request_without_model():
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
            "\u041f\u0420\u0438\u0432\u0435\u0442, "
            "\u0441\u043e\u0437\u0434\u0430\u0439 "
            "\u0437\u0430\u0434\u0430\u0447\u0443 "
            "\u0434\u043b\u044f \u041d\u0430\u0442\u0430\u043b\u044c\u0438 "
            "\u0410\u0440\u0434\u043e, "
            "\u043f\u043e\u043c\u044b\u0442\u044c "
            "\u043f\u043e\u043b\u044b, "
            "\u0441\u0440\u043e\u043a 27.06.2026",
            [],
        )
    )

    assert registry.calls[0] == (
        "search_employee",
        {"query": "\u041d\u0430\u0442\u0430\u043b\u044c\u0438 \u0410\u0440\u0434\u043e"},
    )
    assert registry.calls[1][0] == "create_task"
    assert registry.calls[1][1]["subject"] == "\u043f\u043e\u043c\u044b\u0442\u044c \u043f\u043e\u043b\u044b"
    assert registry.calls[1][1]["action_text"] == "\u043f\u043e\u043c\u044b\u0442\u044c \u043f\u043e\u043b\u044b"
    assert registry.calls[1][1]["deadline"].startswith("2026-06-27T23:59:00")
    visible, preview = _split_action_item_preview_marker(chunks[0])
    assert visible.startswith("\u041f\u043e\u0434\u0433\u043e\u0442\u043e\u0432\u043b\u0435\u043d preview \u0437\u0430\u0434\u0430\u0447\u0438")
    assert preview["type"] == "task"


def test_stream_chat_routes_natural_task_with_comma_and_due_by_date_without_model():
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
            "\u041d\u0430\u0442\u0430\u0448\u0438, "
            "\u0447\u0442\u043e\u0431\u044b \u043e\u043d\u0430 "
            "\u043f\u043e\u0434\u0433\u043e\u0442\u043e\u0432\u0438\u043b\u0430 "
            "\u0434\u043e\u043a\u0443\u043c\u0435\u043d\u0442\u044b "
            "\u0434\u043b\u044f \u041c\u0426 \u0420\u0424 "
            "\u043a 27.06.2026",
            [],
        )
    )

    assert registry.calls[0] == (
        "search_employee",
        {"query": "\u0410\u0440\u0434\u043e \u041d\u0430\u0442\u0430\u0448\u0438"},
    )
    assert registry.calls[1][0] == "create_task"
    assert registry.calls[1][1]["subject"] == (
        "\u043f\u043e\u0434\u0433\u043e\u0442\u043e\u0432\u0438\u043b\u0430 "
        "\u0434\u043e\u043a\u0443\u043c\u0435\u043d\u0442\u044b "
        "\u0434\u043b\u044f \u041c\u0426 \u0420\u0424"
    )
    assert registry.calls[1][1]["deadline"].startswith("2026-06-27T23:59:00")
    visible, preview = _split_action_item_preview_marker(chunks[0])
    assert visible.startswith("\u041f\u043e\u0434\u0433\u043e\u0442\u043e\u0432\u043b\u0435\u043d preview \u0437\u0430\u0434\u0430\u0447\u0438")
    assert preview["type"] == "task"


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
