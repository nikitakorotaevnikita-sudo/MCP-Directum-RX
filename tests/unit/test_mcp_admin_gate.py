from types import SimpleNamespace

from src.mcp_server.app import build_server
from src.models.schemas import AssignmentSummary, EmployeeSummary
from tests.unit.mcp_fakes import FakeProvider, call_tool, error_text, list_tools

TOOL = "admin_list_employee_action_items"


class Counter:
    def __init__(self, answer=True, error=None):
        self.answer = answer
        self.error = error
        self.calls = 0

    def is_admin(self):
        self.calls += 1
        if self.error:
            raise self.error
        return self.answer


def make_services(admin):
    listed = []

    def list_items(direction, employee_id, filters=None, top=None):
        listed.append(employee_id)
        return [AssignmentSummary(id=1, subject="П", entity_type="action_item_assignment")]

    return SimpleNamespace(
        admin_access=admin,
        action_items=SimpleNamespace(
            search_employee=lambda query: [EmployeeSummary(id=63, name="Концева")],
            get_employee=lambda employee_id: EmployeeSummary(id=employee_id, name="Концева"),
        ),
        assignments=SimpleNamespace(
            list_employee_action_items=list_items,
            count_employee_action_items=lambda direction, employee_id, filters=None: 1,
            listed=listed,
        ),
    )


def names(server):
    return [tool.name for tool in list_tools(server)]


def test_admin_sees_admin_tool():
    assert TOOL in names(build_server(FakeProvider(make_services(Counter(True)))))


def test_non_admin_does_not_see_admin_tool():
    tool_names = names(build_server(FakeProvider(make_services(Counter(False)))))

    assert TOOL not in tool_names
    assert "get_current_user" in tool_names


def test_check_error_hides_admin_tool():
    assert TOOL not in names(build_server(FakeProvider(make_services(Counter(error=RuntimeError("boom"))))))


def test_missing_admin_service_hides_admin_tool():
    assert TOOL not in names(build_server(FakeProvider(SimpleNamespace())))


def test_non_admin_call_blocked_before_tool():
    admin = Counter(False)
    services = make_services(admin)

    text = error_text(
        call_tool(build_server(FakeProvider(services)), TOOL, {"employee": "63", "direction": "incoming"})
    )

    assert "только администраторам" in text
    assert services.assignments.listed == []
    # Отказал middleware: до проверки внутри тула дело не дошло.
    assert admin.calls == 1


def test_admin_check_is_cached_for_tools_list():
    admin = Counter(True)
    server = build_server(FakeProvider(make_services(admin)))

    names(server)
    names(server)

    assert admin.calls == 1
