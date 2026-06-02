import pytest
from datetime import datetime, date, timezone

from src.models.schemas import (
    ActionItemCreateRequest,
    ActionItemCreateResult,
    ActionItemDetail,
    AssignmentSummary,
    DirectumUser,
    DisciplineSummary,
    DocumentSummary,
    EmployeeSummary,
    MeetingSummary,
    TaskCreateRequest,
)
from src.services.tool_registry import ToolRegistry


class FakeCurrentUser:
    def get_current_user(self):
        return DirectumUser(id=1165, name="Test User", login="nt_work\\\\user")


class FakeAssignments:
    def get_my_assignments(self):
        return [AssignmentSummary(id=1, subject="Task", status="InProcess", entity_type="assignment")]

    def get_overdue_assignments(self):
        return []

    def get_action_items_assigned_to_me(self):
        return []

    def get_action_items_created_by_me(self):
        return []


class FakeActionItems:
    def __init__(self):
        self.created_requests = []
        self.employee_queries = []

    def search_employee(self, query):
        self.employee_queries.append(query)
        if query == "Ардо Наташи":
            return [EmployeeSummary(id=75, name="Ардо Наталья Алексеевна", status="Active")]
        return []

    def search_documents(self, query):
        return []

    def search_documents_by_counterparty(self, query):
        self.counterparty_query = query
        return {
            "counterparty": {"id": 100, "name": "ООО Ромашка", "tin": "7700000000"},
            "documents": [{"id": 1, "name": "Договор", "url": "https://rx.example/IContracts(1)"}],
            "message": "",
        }

    def list_letters(self, direction, date_from=None, date_to=None):
        self.list_letters_call = {"direction": direction, "date_from": date_from, "date_to": date_to}
        return [DocumentSummary(id=585, name="Вх. письмо", url="https://rx.example/IIncomingLetters(585)")]

    def create_action_item(self, request: ActionItemCreateRequest):
        self.created_requests.append(request)
        return {"mode": "preview", "payload": request.model_dump(), "success": True}

    def create_task(self, request: TaskCreateRequest):
        self.created_requests.append(request)
        return {"mode": "preview", "payload": request.model_dump(), "success": True}


class FakeMeetingsService:
    def get_my_meetings(self, days=7):
        return [
            MeetingSummary(
                id=5,
                subject="Планёрка",
                start_date=datetime(2026, 5, 27, 10, 0, tzinfo=timezone.utc),
                client_card_url="https://rx.example/Client/#/card/x/5",
            )
        ]

    def get_action_item_details(self, action_item_id):
        return ActionItemDetail(
            id=action_item_id,
            subject="Подготовить записку",
            performer="Иванова М.П.",
            author="Петров А.С.",
            status="InProcess",
            created_date=date(2026, 5, 20),
            client_card_url="https://rx.example/Client/#/card/x/42",
        )


def make_registry_with_meetings():
    return ToolRegistry(
        FakeCurrentUser(),
        FakeAssignments(),
        FakeActionItems(),
        FakeMeetingsService(),
    )


def test_tool_registry_lists_core_tools():
    registry = ToolRegistry(FakeCurrentUser(), FakeAssignments(), FakeActionItems(), FakeMeetingsService())

    names = [tool["function"]["name"] for tool in registry.openai_tools()]

    assert "get_current_user" in names
    assert "get_my_assignments" in names
    assert "create_action_item" in names
    assert "create_task" in names


def test_tool_registry_lists_documents_by_counterparty_tool():
    registry = ToolRegistry(FakeCurrentUser(), FakeAssignments(), FakeActionItems(), FakeMeetingsService())

    tools = {t["function"]["name"]: t["function"] for t in registry.openai_tools()}

    assert "search_documents_by_counterparty" in tools
    assert tools["search_documents_by_counterparty"]["parameters"]["required"] == ["query"]


def test_tool_registry_dispatches_documents_by_counterparty():
    action_items = FakeActionItems()
    registry = ToolRegistry(FakeCurrentUser(), FakeAssignments(), action_items, FakeMeetingsService())

    result = registry.call("search_documents_by_counterparty", {"query": "  Ромашка "})

    assert action_items.counterparty_query == "Ромашка"
    assert result["counterparty"]["id"] == 100
    assert result["documents"][0]["url"] == "https://rx.example/IContracts(1)"


def test_tool_registry_lists_letters_tool():
    registry = ToolRegistry(FakeCurrentUser(), FakeAssignments(), FakeActionItems(), FakeMeetingsService())

    tools = {t["function"]["name"]: t["function"] for t in registry.openai_tools()}

    assert "list_letters" in tools
    assert tools["list_letters"]["parameters"]["required"] == ["direction"]
    assert tools["list_letters"]["parameters"]["properties"]["direction"]["enum"] == [
        "incoming",
        "outgoing",
    ]


def test_tool_registry_dispatches_list_letters_with_period():
    action_items = FakeActionItems()
    registry = ToolRegistry(FakeCurrentUser(), FakeAssignments(), action_items, FakeMeetingsService())

    result = registry.call(
        "list_letters",
        {"direction": "incoming", "date_from": "2026-05-01", "date_to": "2026-05-31"},
    )

    assert action_items.list_letters_call == {
        "direction": "incoming",
        "date_from": "2026-05-01",
        "date_to": "2026-05-31",
    }
    assert result[0]["id"] == 585
    assert result[0]["url"] == "https://rx.example/IIncomingLetters(585)"


def test_tool_registry_list_letters_requires_direction():
    registry = ToolRegistry(FakeCurrentUser(), FakeAssignments(), FakeActionItems(), FakeMeetingsService())

    with pytest.raises(ValueError):
        registry.call("list_letters", {"date_from": "2026-05-01"})


class FakeDiscipline:
    def __init__(self):
        self.calls = []

    def get_discipline_analytics(self, employee=None, date_from=None, date_to=None):
        self.calls.append({"employee": employee, "date_from": date_from, "date_to": date_to})
        return DisciplineSummary(
            scope="organization",
            in_process=66,
            overdue=27,
            completed=137,
            completed_on_time=110,
            completed_late=27,
            on_time_rate=80.3,
        )


def test_tool_registry_lists_discipline_tool_when_service_present():
    registry = ToolRegistry(
        FakeCurrentUser(), FakeAssignments(), FakeActionItems(), FakeMeetingsService(), FakeDiscipline()
    )

    names = [tool["function"]["name"] for tool in registry.openai_tools()]

    assert "get_discipline_analytics" in names


def test_tool_registry_omits_discipline_tool_without_service():
    registry = ToolRegistry(FakeCurrentUser(), FakeAssignments(), FakeActionItems(), FakeMeetingsService())

    names = [tool["function"]["name"] for tool in registry.openai_tools()]

    assert "get_discipline_analytics" not in names


def test_tool_registry_dispatches_discipline_with_employee_and_period():
    discipline = FakeDiscipline()
    registry = ToolRegistry(
        FakeCurrentUser(), FakeAssignments(), FakeActionItems(), FakeMeetingsService(), discipline
    )

    result = registry.call(
        "get_discipline_analytics",
        {"employee": "Иванов", "date_from": "2026-05-01", "date_to": "2026-05-31"},
    )

    assert discipline.calls == [
        {"employee": "Иванов", "date_from": "2026-05-01", "date_to": "2026-05-31"}
    ]
    assert result["on_time_rate"] == 80.3
    assert result["overdue"] == 27


def test_tool_registry_dispatches_assignment_tool():
    registry = ToolRegistry(FakeCurrentUser(), FakeAssignments(), FakeActionItems(), FakeMeetingsService())

    result = registry.call("get_my_assignments", {})

    assert result[0]["subject"] == "Task"


def test_tool_registry_create_action_item_schema_does_not_expose_confirm():
    registry = ToolRegistry(FakeCurrentUser(), FakeAssignments(), FakeActionItems(), FakeMeetingsService())

    create_tool = next(tool for tool in registry.openai_tools() if tool["function"]["name"] == "create_action_item")

    assert "confirm" not in create_tool["function"]["parameters"]["properties"]


def test_tool_registry_describes_russian_create_tool_split():
    registry = ToolRegistry(FakeCurrentUser(), FakeAssignments(), FakeActionItems(), FakeMeetingsService())

    tools = {tool["function"]["name"]: tool["function"]["description"] for tool in registry.openai_tools()}

    assert "'поручение'" in tools["create_action_item"]
    assert "Do not use it for 'задача' or 'задание'" in tools["create_action_item"]
    assert "'задача' or 'задание'" in tools["create_task"]
    assert "Do not use create_action_item" in tools["create_task"]


def test_tool_registry_create_action_item_forces_preview_mode():
    action_items = FakeActionItems()
    registry = ToolRegistry(FakeCurrentUser(), FakeAssignments(), action_items, FakeMeetingsService())

    result = registry.call(
        "create_action_item",
        {
            "subject": "Prepare response",
            "performer_id": 42,
            "action_text": "Prepare a short response",
        },
    )

    assert result["mode"] == "preview"
    assert result["payload"]["confirm"] is False
    assert action_items.created_requests[0].confirm is False


def test_tool_registry_rejects_vague_model_generated_create_payload():
    registry = ToolRegistry(FakeCurrentUser(), FakeAssignments(), FakeActionItems(), FakeMeetingsService())

    try:
        registry.call(
            "create_action_item",
            {
                "subject": "Подготовить поручение",
                "performer_id": 42,
                "action_text": "Необходимо выполнить задачу согласно заданию.",
            },
        )
    except ValueError as exc:
        assert "needs concrete user-provided subject and action_text" in str(exc)
    else:
        raise AssertionError("vague generated create payload was accepted")


def test_tool_registry_adds_confirmation_payload_to_pydantic_preview_result():
    class PydanticPreviewActionItems(FakeActionItems):
        def create_action_item(self, request: ActionItemCreateRequest):
            self.created_requests.append(request)
            return ActionItemCreateResult(
                mode="preview",
                payload={"assigneeId": request.performer_id},
                success=True,
                message="Preview generated.",
            )

    action_items = PydanticPreviewActionItems()
    registry = ToolRegistry(FakeCurrentUser(), FakeAssignments(), action_items, FakeMeetingsService())

    result = registry.call(
        "create_action_item",
        {
            "subject": "Prepare response",
            "performer_id": 42,
            "action_text": "Prepare a short response",
        },
    )

    assert result["mode"] == "preview"
    assert result["confirmation_payload"] == {
        "subject": "Prepare response",
        "performer_id": 42,
        "action_text": "Prepare a short response",
        "deadline": None,
        "document_id": None,
        "confirm": False,
    }


def test_tool_registry_normalizes_short_deadline_before_validation():
    action_items = FakeActionItems()
    registry = ToolRegistry(FakeCurrentUser(), FakeAssignments(), action_items, FakeMeetingsService())

    registry.call(
        "create_action_item",
        {
            "subject": "Prepare response",
            "performer_id": 42,
            "action_text": "Prepare a short response",
            "deadline": "26.05.26",
        },
    )

    assert action_items.created_requests[0].deadline.isoformat().startswith("2026-05-26T23:59:00")


def test_tool_registry_adds_timezone_to_naive_iso_deadline_before_payload():
    action_items = FakeActionItems()
    registry = ToolRegistry(FakeCurrentUser(), FakeAssignments(), action_items, FakeMeetingsService())

    registry.call(
        "create_action_item",
        {
            "subject": "Prepare response",
            "performer_id": 42,
            "action_text": "Prepare a short response",
            "deadline": "2026-05-26T23:59:00",
        },
    )

    assert action_items.created_requests[0].deadline.isoformat() == "2026-05-26T23:59:00+00:00"


def test_tool_registry_normalizes_model_style_create_action_item_arguments():
    action_items = FakeActionItems()
    registry = ToolRegistry(FakeCurrentUser(), FakeAssignments(), action_items, FakeMeetingsService())

    registry.call(
        "create_action_item",
        {
            "parameters": {
                "confirm": False,
                "document_id": "<Integer>",
                "performer_id": "Ардо Наташи",
                "subject": "подготовка документов для МЦ РФ",
                "deadline": "27.06.2026",
            }
        },
    )

    request = action_items.created_requests[0]
    assert action_items.employee_queries == ["Ардо Наташи"]
    assert request.confirm is False
    assert request.document_id is None
    assert request.performer_id == 75
    assert request.subject == "подготовка документов для МЦ РФ"
    assert request.action_text == "подготовка документов для МЦ РФ"
    assert request.deadline.isoformat().startswith("2026-06-27T23:59:00")


def test_tool_registry_rejects_direct_create_confirmation():
    registry = ToolRegistry(FakeCurrentUser(), FakeAssignments(), FakeActionItems(), FakeMeetingsService())

    try:
        registry.call(
            "create_action_item",
            {
                "subject": "Prepare response",
                "performer_id": 42,
                "action_text": "Prepare a short response",
                "confirm": True,
            },
        )
    except ValueError as exc:
        assert str(exc) == "Tool 'create_action_item' cannot confirm creation directly; use preview mode first"
    else:
        raise AssertionError("direct create confirmation was accepted")


def test_tool_registry_rejects_unknown_tool_with_value_error():
    registry = ToolRegistry(FakeCurrentUser(), FakeAssignments(), FakeActionItems(), FakeMeetingsService())

    try:
        registry.call("missing_tool", {})
    except ValueError as exc:
        assert str(exc) == "Unknown tool: missing_tool"
    else:
        raise AssertionError("unknown tool was accepted")


def test_tool_registry_rejects_missing_required_argument():
    registry = ToolRegistry(FakeCurrentUser(), FakeAssignments(), FakeActionItems(), FakeMeetingsService())

    try:
        registry.call("search_employee", {})
    except ValueError as exc:
        assert str(exc) == "Tool 'search_employee' missing required argument: query"
    else:
        raise AssertionError("missing search query was accepted")


def test_tool_registry_wraps_invalid_create_action_item_arguments():
    registry = ToolRegistry(FakeCurrentUser(), FakeAssignments(), FakeActionItems(), FakeMeetingsService())

    try:
        registry.call(
            "create_action_item",
            {
                "subject": "Prepare response",
                "performer_id": 0,
                "action_text": "Prepare a short response",
            },
        )
    except ValueError as exc:
        message = str(exc)
        assert message.startswith("Tool 'create_action_item' invalid arguments:")
        assert "performer_id" in message
    else:
        raise AssertionError("invalid performer_id was accepted")


def test_tool_registry_rejects_non_object_arguments():
    registry = ToolRegistry(FakeCurrentUser(), FakeAssignments(), FakeActionItems(), FakeMeetingsService())

    try:
        registry.call("get_current_user", [])
    except ValueError as exc:
        assert str(exc) == "Tool 'get_current_user' arguments must be an object"
    else:
        raise AssertionError("non-object arguments were accepted")


def test_registry_has_get_my_meetings_tool():
    registry = make_registry_with_meetings()
    names = [t["function"]["name"] for t in registry.openai_tools()]
    assert "get_my_meetings" in names


def test_registry_has_get_action_item_details_tool():
    registry = make_registry_with_meetings()
    names = [t["function"]["name"] for t in registry.openai_tools()]
    assert "get_action_item_details" in names


def test_get_my_meetings_tool_call_returns_list():
    registry = make_registry_with_meetings()
    result = registry.call("get_my_meetings", {})
    assert isinstance(result, list)
    assert result[0]["id"] == 5


def test_get_action_item_details_tool_call_returns_detail():
    registry = make_registry_with_meetings()
    result = registry.call("get_action_item_details", {"action_item_id": 42})
    assert result["id"] == 42
    assert result["subject"] == "Подготовить записку"


def test_get_action_item_details_missing_id_raises():
    registry = make_registry_with_meetings()
    with pytest.raises(ValueError, match="missing required argument"):
        registry.call("get_action_item_details", {})
