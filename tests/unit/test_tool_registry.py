from src.models.schemas import ActionItemCreateRequest, AssignmentSummary, DirectumUser
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

    def search_employee(self, query):
        return []

    def create_action_item(self, request: ActionItemCreateRequest):
        self.created_requests.append(request)
        return {"mode": "preview", "payload": request.model_dump(), "success": True}


def test_tool_registry_lists_core_tools():
    registry = ToolRegistry(FakeCurrentUser(), FakeAssignments(), FakeActionItems())

    names = [tool["function"]["name"] for tool in registry.openai_tools()]

    assert "get_current_user" in names
    assert "get_my_assignments" in names
    assert "create_action_item" in names


def test_tool_registry_dispatches_assignment_tool():
    registry = ToolRegistry(FakeCurrentUser(), FakeAssignments(), FakeActionItems())

    result = registry.call("get_my_assignments", {})

    assert result[0]["subject"] == "Task"


def test_tool_registry_create_action_item_schema_does_not_expose_confirm():
    registry = ToolRegistry(FakeCurrentUser(), FakeAssignments(), FakeActionItems())

    create_tool = next(tool for tool in registry.openai_tools() if tool["function"]["name"] == "create_action_item")

    assert "confirm" not in create_tool["function"]["parameters"]["properties"]


def test_tool_registry_create_action_item_forces_preview_mode():
    action_items = FakeActionItems()
    registry = ToolRegistry(FakeCurrentUser(), FakeAssignments(), action_items)

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


def test_tool_registry_normalizes_short_deadline_before_validation():
    action_items = FakeActionItems()
    registry = ToolRegistry(FakeCurrentUser(), FakeAssignments(), action_items)

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


def test_tool_registry_rejects_direct_create_confirmation():
    registry = ToolRegistry(FakeCurrentUser(), FakeAssignments(), FakeActionItems())

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
    registry = ToolRegistry(FakeCurrentUser(), FakeAssignments(), FakeActionItems())

    try:
        registry.call("missing_tool", {})
    except ValueError as exc:
        assert str(exc) == "Unknown tool: missing_tool"
    else:
        raise AssertionError("unknown tool was accepted")


def test_tool_registry_rejects_missing_required_argument():
    registry = ToolRegistry(FakeCurrentUser(), FakeAssignments(), FakeActionItems())

    try:
        registry.call("search_employee", {})
    except ValueError as exc:
        assert str(exc) == "Tool 'search_employee' missing required argument: query"
    else:
        raise AssertionError("missing search query was accepted")


def test_tool_registry_wraps_invalid_create_action_item_arguments():
    registry = ToolRegistry(FakeCurrentUser(), FakeAssignments(), FakeActionItems())

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
    registry = ToolRegistry(FakeCurrentUser(), FakeAssignments(), FakeActionItems())

    try:
        registry.call("get_current_user", [])
    except ValueError as exc:
        assert str(exc) == "Tool 'get_current_user' arguments must be an object"
    else:
        raise AssertionError("non-object arguments were accepted")
