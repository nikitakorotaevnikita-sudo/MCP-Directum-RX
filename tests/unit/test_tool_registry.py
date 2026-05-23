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
    def search_employee(self, query):
        return []

    def create_action_item(self, request: ActionItemCreateRequest):
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
