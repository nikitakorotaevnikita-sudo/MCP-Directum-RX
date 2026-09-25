from datetime import date
from types import SimpleNamespace

from src.mcp_server.app import build_server
from src.models.schemas import AssignmentSummary, EmployeeSummary
from src.services.assignments import ActionItemFilters
from tests.unit.mcp_fakes import FakeProvider, call_tool, error_text, list_tools, payload

TOOL = "admin_list_employee_action_items"
NADYA = EmployeeSummary(id=63, name="Концева Надежда Ивановна", status="Active")


class FakeAssignments:
    def __init__(self):
        self.listed = []
        self.counted = []

    def list_employee_action_items(self, direction, employee_id, filters=None, top=None):
        self.listed.append((direction, employee_id, filters, top))
        return [AssignmentSummary(id=1, subject="Поручение", status="InProcess", entity_type="action_item_assignment")]

    def count_employee_action_items(self, direction, employee_id, filters=None):
        self.counted.append((direction, employee_id, filters))
        return 3


def make_services(is_admin=True, search=None, by_id=None):
    return SimpleNamespace(
        admin_access=SimpleNamespace(is_admin=lambda: is_admin),
        action_items=SimpleNamespace(
            search_employee=lambda query: search if search is not None else [NADYA],
            get_employee=lambda employee_id: by_id,
        ),
        assignments=FakeAssignments(),
    )


def call(services, **arguments):
    return call_tool(build_server(FakeProvider(services)), TOOL, arguments)


def test_admin_tool_listed_for_admin():
    assert TOOL in [tool.name for tool in list_tools(build_server(FakeProvider(make_services())))]


def test_returns_envelope_with_employee_and_filters():
    services = make_services()

    data = payload(call(services, employee="Концева", direction="incoming", limit=2))

    assert data["employee"] == {"id": 63, "name": "Концева Надежда Ивановна"}
    assert data["total"] == 3
    assert data["returned"] == 1
    assert data["truncated"] is True
    assert data["filters"] == {
        "direction": "incoming",
        "status": "in_process",
        "only_overdue": False,
        "date_field": "deadline",
        "date_from": None,
        "date_to": None,
    }
    assert services.assignments.listed == [("incoming", 63, ActionItemFilters(), 2)]


def test_passes_filters_and_dates():
    services = make_services(by_id=NADYA)

    payload(
        call(
            services,
            employee="63",
            direction="outgoing",
            status="all",
            only_overdue=True,
            date_field="created",
            date_from="2026-01-01",
            date_to="2026-01-31",
        )
    )

    _, _, filters, _ = services.assignments.listed[0]
    assert filters == ActionItemFilters(
        status="all",
        only_overdue=True,
        date_field="created",
        date_from=date(2026, 1, 1),
        date_to=date(2026, 1, 31),
    )


def test_numeric_employee_uses_get_employee():
    services = make_services(by_id=EmployeeSummary(id=99, name="Петров П.П."))

    data = payload(call(services, employee="99", direction="incoming"))

    assert data["employee"] == {"id": 99, "name": "Петров П.П."}


def test_numeric_employee_not_found():
    assert "id 99 не найден" in error_text(call(make_services(by_id=None), employee="99", direction="incoming"))


def test_employee_not_found_by_name():
    assert "не найден" in error_text(call(make_services(search=[]), employee="Никто", direction="incoming"))


def test_exact_name_wins_among_several():
    other = EmployeeSummary(id=70, name="Концева Надежда Петровна")
    services = make_services(search=[other, NADYA])

    data = payload(call(services, employee="концева надежда ивановна", direction="incoming"))

    assert data["employee"]["id"] == 63


def test_ambiguous_employee_lists_candidates():
    other = EmployeeSummary(id=70, name="Концева Надежда Петровна")

    text = error_text(call(make_services(search=[NADYA, other]), employee="Концева", direction="incoming"))

    assert "63 — Концева Надежда Ивановна" in text
    assert "70 — Концева Надежда Петровна" in text


def test_overdue_with_completed_rejected():
    text = error_text(
        call(make_services(), employee="63", direction="incoming", status="completed", only_overdue=True)
    )

    assert "только поручения в работе" in text


def test_bad_date_rejected():
    text = error_text(call(make_services(), employee="63", direction="incoming", date_from="01.02.2026"))

    assert "YYYY-MM-DD" in text


def test_reversed_period_rejected():
    text = error_text(
        call(make_services(), employee="63", direction="incoming", date_from="2026-02-01", date_to="2026-01-01")
    )

    assert "date_from позже date_to" in text


def test_non_admin_rejected_inside_tool():
    services = make_services(is_admin=False)

    text = error_text(call(services, employee="63", direction="incoming"))

    assert "только администраторам" in text
    assert services.assignments.listed == []
