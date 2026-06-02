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
        if name == "get_my_meetings":
            return [
                {
                    "id": 7,
                    "subject": "Еженедельная планёрка",
                    "start_date": "2026-05-27T10:00:00Z",
                    "end_date": "2026-05-27T11:00:00Z",
                    "place": "Конференц-зал А",
                    "agenda_summary": "Обсуждение итогов",
                    "client_card_url": "https://rx.example/Client/#/card/meeting-guid/7",
                }
            ]
        if name == "get_action_item_details":
            return {
                "id": int(arguments.get("action_item_id", 42)),
                "subject": "Подготовить записку",
                "text": "Подготовить аналитическую записку по итогам квартала",
                "performer": "Иванова М.П. (Главный специалист)",
                "author": "Петров А.С.",
                "deadline": "2026-05-30",
                "status": "InProcess",
                "created_date": "2026-05-20",
                "client_card_url": "https://rx.example/Client/#/card/x/42",
                "narrative": "",
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


class NarrativeCompletions:
    """Non-streaming completions for narrative generation."""
    def __init__(self, narrative="Поручение выполняется в срок."):
        self.narrative = narrative
        self.requests = []

    def create(self, **kwargs):
        self.requests.append(kwargs)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=self.narrative)
                )
            ]
        )


class MixedNarrativeClient:
    """Client where completions.create returns narrative for non-streaming calls."""
    def __init__(self):
        self.narrative_completions = NarrativeCompletions()
        self.chat = SimpleNamespace(completions=self.narrative_completions)


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


def test_llm_service_system_prompt_includes_todays_date():
    from datetime import datetime, timezone

    service = LLMService(
        provider="ollama",
        base_url="http://localhost:11434/v1",
        api_key="ollama",
        model="qwen3:8b",
        tool_calling="auto",
        tool_registry=FakeToolRegistry(),
    )

    prompt = service._system_prompt()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    assert today in prompt


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


def test_llm_service_defaults_to_verifying_tls():
    service = LLMService(
        provider="ario",
        base_url="https://llm.ario.directum360.ru/v1",
        api_key="test-key",
        model="Qwen/Qwen3-32B-AWQ",
        tool_calling="auto",
        tool_registry=FakeToolRegistry(),
    )

    assert service.verify_ssl is True


def test_llm_service_can_disable_tls_verification_for_self_signed_endpoint():
    service = LLMService(
        provider="ario",
        base_url="https://llm.ario.directum360.ru/v1",
        api_key="test-key",
        model="Qwen/Qwen3-32B-AWQ",
        tool_calling="auto",
        tool_registry=FakeToolRegistry(),
        verify_ssl=False,
    )

    assert service.verify_ssl is False


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

    text = "".join(service.stream_chat("Дай аналитику по исходящим поручениям", []))
    body, marker = _extract_analytics_marker(text)

    assert registry.calls == [("get_action_items_created_by_me", {})]
    # Заголовок-дубль убран (визуализация уже несёт title).
    assert "Аналитика по исходящим поручениям" not in body
    assert "Всего исходящих поручений: **3**." in body
    # Текстовые секции по категориям убраны — только сводка + визуализация.
    assert "analytics-heading" not in body
    assert "Поручения в работе" not in body

    # Элементы встроены в колонки маркера для drill-down модалки.
    bars = {b["label"]: b for b in marker["charts"][0]["bars"]}
    work_items = bars["В работе"]["items"]
    assert [i["subject"] for i in work_items] == ["Поручение в работе"]
    assert work_items[0]["url"] == (
        "https://rx.example/Client/#/card/83f2a537-0cf0-4429-ae76-e9a386ca53aa/101"
    )
    assert [i["subject"] for i in bars["Срок завтра"]["items"]] == ["Скоро срок"]
    assert [i["subject"] for i in bars["Просрочено"]["items"]] == ["Просроченное поручение"]


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


# \u2500\u2500 Task 7: meetings routing \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500


@pytest.mark.parametrize(
    "message",
    [
        "\u043f\u043e\u043a\u0430\u0436\u0438 \u043c\u043e\u0438 \u0441\u043e\u0432\u0435\u0449\u0430\u043d\u0438\u044f",
        "\u043a\u0430\u043a\u0438\u0435 \u0441\u043e\u0432\u0435\u0449\u0430\u043d\u0438\u044f \u043d\u0430 \u044d\u0442\u043e\u0439 \u043d\u0435\u0434\u0435\u043b\u0435",
        "\u043c\u043e\u0438 \u0432\u0441\u0442\u0440\u0435\u0447\u0438 \u043d\u0430 \u0441\u0435\u0433\u043e\u0434\u043d\u044f",
        "\u0431\u043b\u0438\u0436\u0430\u0439\u0448\u0438\u0435 \u0437\u0430\u0441\u0435\u0434\u0430\u043d\u0438\u044f",
    ],
)
def test_stream_chat_routes_meetings_intent_without_model_tool_call(message):
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
    response = "".join(chunks)

    assert ("get_my_meetings", {}) in registry.calls
    assert client.completions.requests == []  # LLM not called
    assert "\u0415\u0436\u0435\u043d\u0435\u0434\u0435\u043b\u044c\u043d\u0430\u044f \u043f\u043b\u0430\u043d\u0451\u0440\u043a\u0430" in response
    assert "\u041a\u043e\u043d\u0444\u0435\u0440\u0435\u043d\u0446-\u0437\u0430\u043b \u0410" in response


def test_stream_chat_meetings_empty_returns_friendly_message():
    class EmptyMeetingsRegistry(FakeToolRegistry):
        def call(self, name, arguments):
            if name == "get_my_meetings":
                return []
            return []

    service = LLMService(
        provider="openrouter",
        base_url="https://openrouter.ai/api/v1",
        api_key="test-key",
        model="openrouter/free",
        tool_calling="auto",
        tool_registry=EmptyMeetingsRegistry(),
    )
    service.client = ToolCallClient()

    response = "".join(service.stream_chat("\u0441\u043e\u0432\u0435\u0449\u0430\u043d\u0438\u044f", []))
    assert "\u043d\u0435 \u0437\u0430\u043f\u043b\u0430\u043d\u0438\u0440\u043e\u0432\u0430\u043d\u043e" in response.lower()


# \u2500\u2500 Task 8: action item report routing \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500


def test_stream_chat_action_item_report_extracts_id_from_hash_notation():
    registry = RecordingToolRegistry()
    service = LLMService(
        provider="openrouter",
        base_url="https://openrouter.ai/api/v1",
        api_key="test-key",
        model="openrouter/free",
        tool_calling="auto",
        tool_registry=registry,
    )
    service.client = MixedNarrativeClient()

    response = "".join(service.stream_chat("\u043e\u0442\u0447\u0451\u0442 \u043f\u043e\u0440\u0443\u0447\u0435\u043d\u0438\u0435 #42", []))

    assert ("get_action_item_details", {"action_item_id": 42}) in registry.calls
    assert "42" in response
    assert "\u041f\u043e\u0434\u0433\u043e\u0442\u043e\u0432\u0438\u0442\u044c \u0437\u0430\u043f\u0438\u0441\u043a\u0443" in response


def test_stream_chat_action_item_report_missing_id_asks_clarification():
    registry = RecordingToolRegistry()
    service = LLMService(
        provider="openrouter",
        base_url="https://openrouter.ai/api/v1",
        api_key="test-key",
        model="openrouter/free",
        tool_calling="auto",
        tool_registry=registry,
    )
    service.client = ToolCallClient()

    response = "".join(service.stream_chat("\u0434\u0430\u0439 \u043c\u043d\u0435 \u043e\u0442\u0447\u0451\u0442 \u043f\u043e \u043f\u043e\u0440\u0443\u0447\u0435\u043d\u0438\u044e", []))

    # Should ask for clarification, not call tool
    assert "get_action_item_details" not in [c[0] for c in registry.calls]
    assert "укажите номер" in response.lower() or "номер поручения" in response.lower()


def test_stream_chat_action_item_report_includes_narrative():
    registry = RecordingToolRegistry()
    service = LLMService(
        provider="openrouter",
        base_url="https://openrouter.ai/api/v1",
        api_key="test-key",
        model="openrouter/free",
        tool_calling="auto",
        tool_registry=registry,
    )
    service.client = MixedNarrativeClient()

    response = "".join(service.stream_chat("\u0440\u0430\u0441\u0441\u043a\u0430\u0436\u0438 \u043e \u043f\u043e\u0440\u0443\u0447\u0435\u043d\u0438\u0438 42", []))

    assert "\u041f\u043e\u0440\u0443\u0447\u0435\u043d\u0438\u0435 \u0432\u044b\u043f\u043e\u043b\u043d\u044f\u0435\u0442\u0441\u044f \u0432 \u0441\u0440\u043e\u043a." in response


# \u2500\u2500 Task 10: clickable report links in analytics \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500


def test_format_analytics_item_includes_report_link_when_id_present():
    service = LLMService(
        provider="openrouter",
        base_url="https://openrouter.ai/api/v1",
        api_key="test-key",
        model="openrouter/free",
        tool_calling="auto",
        tool_registry=RecordingToolRegistry(),
    )
    item = {
        "id": 42,
        "subject": "\u041f\u043e\u0434\u0433\u043e\u0442\u043e\u0432\u0438\u0442\u044c \u0437\u0430\u043f\u0438\u0441\u043a\u0443",
        "status": "InProcess",
        "deadline": None,
        "url": "https://rx.example/card/42",
    }
    result = service._format_analytics_item(item)
    assert "#action-item-42" in result


def test_format_analytics_item_no_report_link_when_id_absent():
    service = LLMService(
        provider="openrouter",
        base_url="https://openrouter.ai/api/v1",
        api_key="test-key",
        model="openrouter/free",
        tool_calling="auto",
        tool_registry=RecordingToolRegistry(),
    )
    item = {"subject": "Задача без id", "deadline": None}
    result = service._format_analytics_item(item)
    assert "#action-item-" not in result


# ── Analytics chart markers ───────────────────────────────────────────────


def _extract_analytics_marker(text):
    body, marker = text.split("[[DIRECTUM_ANALYTICS:", 1)
    payload = json.loads(marker.rsplit("]]", 1)[0])
    return body, payload


def test_stream_chat_outgoing_analytics_appends_chart_marker():
    now = datetime.now(timezone.utc)

    class AnalyticsRegistry(FakeToolRegistry):
        def __init__(self):
            self.calls = []

        def call(self, name, arguments):
            self.calls.append((name, arguments))
            return [
                {"subject": "A", "status": "InProcess", "deadline": (now + timedelta(days=3)).isoformat()},
                {"subject": "B", "status": "InProcess", "deadline": (now + timedelta(hours=12)).isoformat()},
                {"subject": "C", "status": "InProcess", "deadline": (now - timedelta(hours=1)).isoformat()},
            ]

    service = LLMService(
        provider="openrouter",
        base_url="https://openrouter.ai/api/v1",
        api_key="test-key",
        model="openrouter/free",
        tool_calling="auto",
        tool_registry=AnalyticsRegistry(),
    )

    text = "".join(service.stream_chat("Дай аналитику по исходящим поручениям", []))
    body, marker = _extract_analytics_marker(text)

    assert "Всего исходящих поручений: **3**." in body
    assert "Аналитика по исходящим поручениям" not in body
    assert marker["kind"] == "outgoing_action_items"
    bars = {b["label"]: b["value"] for b in marker["charts"][0]["bars"]}
    assert bars == {"В работе": 1, "Срок завтра": 1, "Просрочено": 1}


def test_outgoing_analytics_item_payload_carries_drilldown_fields():
    now = datetime.now(timezone.utc)
    deadline = (now - timedelta(hours=1)).isoformat()

    class AnalyticsRegistry(FakeToolRegistry):
        def call(self, name, arguments):
            return [
                {
                    "id": 77,
                    "subject": "Просроченное",
                    "status": "InProcess",
                    "deadline": deadline,
                    "url": "https://rx.example/card/77",
                    "performer": "Иванов Иван Иванович",
                    "entity_type": "action_item_task",
                }
            ]

    service = LLMService(
        provider="openrouter",
        base_url="https://openrouter.ai/api/v1",
        api_key="test-key",
        model="openrouter/free",
        tool_calling="auto",
        tool_registry=AnalyticsRegistry(),
    )

    text = "".join(service.stream_chat("Дай аналитику по исходящим поручениям", []))
    _, marker = _extract_analytics_marker(text)

    overdue = [b for b in marker["charts"][0]["bars"] if b["label"] == "Просрочено"][0]
    item = overdue["items"][0]
    assert item["id"] == 77
    assert item["subject"] == "Просроченное"
    assert item["url"] == "https://rx.example/card/77"
    assert item["status"] == "InProcess"
    assert item["deadline"] == deadline
    assert item["performer"] == "Иванов Иван Иванович"


class DisciplineToolCallCompletions:
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
                                            id="call_discipline",
                                            type="function",
                                            function=SimpleNamespace(
                                                name="get_discipline_analytics", arguments="{}"
                                            ),
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
                    choices=[SimpleNamespace(delta=SimpleNamespace(content="Дисциплина в норме."))]
                )
            ]
        )


class DisciplineToolCallClient:
    def __init__(self):
        self.completions = DisciplineToolCallCompletions()
        self.chat = SimpleNamespace(completions=self.completions)


class DisciplineRegistry(FakeToolRegistry):
    def openai_tools(self):
        return [
            {
                "type": "function",
                "function": {
                    "name": "get_discipline_analytics",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ]

    def call(self, name, arguments):
        return {
            "scope": "organization",
            "employee": None,
            "in_process": 66,
            "overdue": 27,
            "completed": 137,
            "completed_on_time": 110,
            "completed_late": 27,
            "on_time_rate": 80.3,
            "message": "",
        }


def test_stream_chat_discipline_appends_chart_marker_after_llm_answer():
    service = LLMService(
        provider="ollama",
        base_url="http://localhost:11434/v1",
        api_key="ollama",
        model="qwen",
        tool_calling="auto",
        tool_registry=DisciplineRegistry(),
    )
    service.client = DisciplineToolCallClient()

    text = "".join(service.stream_chat("Покажи исполнительскую дисциплину", []))
    body, marker = _extract_analytics_marker(text)

    assert "Дисциплина в норме." in body
    assert marker["kind"] == "discipline"
    bars = {b["label"]: b["value"] for b in marker["charts"][0]["bars"]}
    assert bars["В работе"] == 66
    assert bars["Просрочено"] == 27
    assert bars["В срок"] == 110
    assert bars["С опозданием"] == 27
    gauge = [c for c in marker["charts"] if c["type"] == "gauge"][0]
    assert gauge["value"] == 80.3


class EmptyArgsToolCallCompletions(DisciplineToolCallCompletions):
    """LLM эмитит tool call БЕЗ аргументов (arguments=\"\") — как реальный vLLM
    для инструмента со всеми опциональными параметрами."""

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
                                            id="call_discipline",
                                            type="function",
                                            function=SimpleNamespace(
                                                name="get_discipline_analytics", arguments=""
                                            ),
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
                    choices=[SimpleNamespace(delta=SimpleNamespace(content="Дисциплина в норме."))]
                )
            ]
        )


class EmptyArgsToolCallClient:
    def __init__(self):
        self.completions = EmptyArgsToolCallCompletions()
        self.chat = SimpleNamespace(completions=self.completions)


def test_tool_call_with_empty_arguments_is_normalized_in_followup_request():
    """Регресс: пустую строку arguments нельзя класть обратно в messages —
    vLLM делает json.loads(\"\") и падает с 400 BadRequestError."""
    service = LLMService(
        provider="ollama",
        base_url="http://localhost:11434/v1",
        api_key="ollama",
        model="qwen",
        tool_calling="auto",
        tool_registry=DisciplineRegistry(),
    )
    client = EmptyArgsToolCallClient()
    service.client = client

    text = "".join(service.stream_chat("Покажи исполнительскую дисциплину", []))

    assert "Дисциплина в норме." in text
    # Был сделан follow-up запрос
    assert len(client.completions.requests) == 2
    followup_messages = client.completions.requests[1]["messages"]
    assistant_with_tools = [
        m for m in followup_messages if m.get("role") == "assistant" and m.get("tool_calls")
    ]
    assert assistant_with_tools, "assistant-сообщение с tool_calls должно уйти в follow-up"
    for message in assistant_with_tools:
        for tool_call in message["tool_calls"]:
            arguments = tool_call["function"]["arguments"]
            assert arguments, "arguments не должны быть пустой строкой (vLLM 400)"
            # И должны быть валидным JSON-объектом
            assert json.loads(arguments) == {}


def test_discipline_keyword_routes_to_tool_path_not_overdue_shortcut():
    service = LLMService(
        provider="ollama",
        base_url="http://localhost:11434/v1",
        api_key="ollama",
        model="qwen",
        tool_calling="auto",
        tool_registry=DisciplineRegistry(),
    )

    # «дисциплина» не должна перехватываться прямым маршрутом (например, get_overdue_assignments).
    assert service._direct_rx_response("Аналитика по исполнительской дисциплине просрочки", []) is None


def test_action_item_preview_marker_includes_document_display():
    service = LLMService(
        provider="openrouter",
        base_url="https://openrouter.ai/api/v1",
        api_key="test-key",
        model="openrouter/free",
        tool_calling="auto",
        tool_registry=RecordingToolRegistry(),
    )
    marker = service._action_item_preview_marker(
        {"subject": "Тема", "action_text": "Сделать", "performer_id": 5, "document_id": 555},
        "Иванов И.И.",
        "action_item",
        correct_text=False,
        document={"name": "Письмо №7", "number": "7", "date": "30.05.2026",
                  "url": "https://rx.example/card/555"},
    )
    payload = json.loads(marker.split("[[DIRECTUM_ACTION_ITEM_PREVIEW:", 1)[1].rsplit("]]", 1)[0])
    assert payload["display"]["document"]["name"] == "Письмо №7"
    assert payload["display"]["document"]["url"] == "https://rx.example/card/555"


class DocCreateRegistry(FakeToolRegistry):
    def __init__(self):
        self.calls = []

    def call(self, name, arguments):
        self.calls.append((name, arguments))
        if name == "search_employee":
            return [{"id": 5, "name": "Иванов И.И."}]
        if name == "get_document":
            return {"id": 555, "name": "Письмо №7", "registration_number": "7",
                    "registration_date": "2026-05-30T00:00:00Z",
                    "url": "https://rx.example/card/555"}
        if name == "create_action_item":
            return {"mode": "preview", "confirmation_payload": {**arguments, "confirm": False}}
        return None


def test_create_route_links_document_from_hash_token():
    service = LLMService(
        provider="openrouter", base_url="https://openrouter.ai/api/v1",
        api_key="test-key", model="openrouter/free", tool_calling="auto",
        tool_registry=DocCreateRegistry(),
    )
    text = service._direct_rx_response(
        "Выдай поручение по документу #555: подготовить ответ, исполнитель Иванов", []
    )
    assert text is not None
    payload = json.loads(text.split("[[DIRECTUM_ACTION_ITEM_PREVIEW:", 1)[1].rsplit("]]", 1)[0])
    assert payload["payload"]["document_id"] == 555
    assert payload["display"]["document"]["name"] == "Письмо №7"


def test_performer_phrasing_routes_task_without_porucheniye_keyword():
    service = LLMService(
        provider="openrouter", base_url="https://openrouter.ai/api/v1",
        api_key="test-key", model="openrouter/free", tool_calling="auto",
        tool_registry=RecordingToolRegistry(),
    )
    draft = service._parse_action_item_draft("Вынести мусор, исполнитель Ардо")
    assert draft is not None
    assert draft["type"] == "task"


def test_performer_phrasing_routes_action_item_with_porucheniye_keyword():
    service = LLMService(
        provider="openrouter", base_url="https://openrouter.ai/api/v1",
        api_key="test-key", model="openrouter/free", tool_calling="auto",
        tool_registry=RecordingToolRegistry(),
    )
    draft = service._parse_action_item_draft("Поручение: подготовить ответ, исполнитель Ардо")
    assert draft is not None
    assert draft["type"] == "action_item"


def test_resolve_document_display_through_real_tool_registry():
    from src.services.tool_registry import ToolRegistry
    from src.models.schemas import DocumentSummary
    from datetime import datetime, timezone

    class _FakeAISvc:
        def get_document(self, document_id):
            return DocumentSummary(
                id=document_id, name="Письмо №7", subject="О поставке",
                registration_number="7",
                registration_date=datetime(2026, 5, 30, tzinfo=timezone.utc),
                url="https://rx.example/card/555",
            )

    registry = ToolRegistry(
        current_user_service=None,
        assignments_service=None,
        action_item_service=_FakeAISvc(),
        meetings_service=None,
    )
    service = LLMService(
        provider="openrouter", base_url="https://openrouter.ai/api/v1",
        api_key="test-key", model="openrouter/free", tool_calling="auto",
        tool_registry=registry,
    )
    display = service._resolve_document_display(555)
    assert display is not None
    assert display["name"] == "Письмо №7"
    assert display["number"] == "7"
    assert display["url"] == "https://rx.example/card/555"


def test_format_tool_result_adds_issue_action_item_link_for_documents():
    service = LLMService(
        provider="openrouter", base_url="https://openrouter.ai/api/v1",
        api_key="test-key", model="openrouter/free", tool_calling="auto",
        tool_registry=RecordingToolRegistry(),
    )
    docs = [
        {"id": 555, "name": "Письмо №7", "registration_number": "7",
         "registration_date": "2026-05-30T00:00:00Z", "url": "https://rx.example/card/555"},
        {"id": 556, "name": "Акт №9", "registration_number": "9",
         "registration_date": "2026-05-28T00:00:00Z", "url": "https://rx.example/card/556"},
    ]
    text = service._format_tool_result(docs)
    assert "#document-555" in text
    assert "#document-556" in text
    assert "Выдать поручение" in text
