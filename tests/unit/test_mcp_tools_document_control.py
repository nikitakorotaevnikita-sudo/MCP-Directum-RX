from datetime import date
from types import SimpleNamespace

from src.mcp_server.app import build_server
from src.models.schemas import EmployeeSummary
from tests.unit.mcp_fakes import FakeProvider, call_tool, error_text, list_tools, payload


def make(employees=None):
    seen = {}

    def list_controlled(filters, limit=30):
        seen["filters"], seen["limit"] = filters, limit
        return {"items": [], "total": 0}

    def dossier(action_item_id, kind=None):
        seen["dossier"] = (action_item_id, kind)
        return {"action_item": {"id": action_item_id}}

    people = employees if employees is not None else [EmployeeSummary(id=77, name="Иванов Иван Иванович")]
    services = SimpleNamespace(
        document_control=SimpleNamespace(list_controlled=list_controlled, dossier=dossier),
        action_items=SimpleNamespace(search_employee=lambda query: people, get_employee=lambda employee_id: EmployeeSummary(id=employee_id, name="X")),
        current_user=SimpleNamespace(get_current_user=lambda: SimpleNamespace(id=5)),
    )
    return build_server(FakeProvider(services)), seen


def test_tools_registered():
    server, _ = make()
    names = {tool.name for tool in list_tools(server)}
    assert {"list_controlled_action_items", "get_control_dossier"} <= names


def test_list_defaults():
    server, seen = make()
    payload(call_tool(server, "list_controlled_action_items", {}))
    f = seen["filters"]
    assert f.state == "in_work" and f.supervisor_id is None and f.due is None and seen["limit"] == 30


def test_list_resolves_people_and_me():
    server, seen = make()
    payload(call_tool(server, "list_controlled_action_items", {
        "supervisor": "me", "assigned_by": "12", "assignee": "Иванов", "co_assignee": "Иванов",
        "state": "overdue", "date_from": "2026-09-01", "date_to": "2026-09-30", "limit": 5}))
    f = seen["filters"]
    assert (f.supervisor_id, f.assigned_by_id, f.assignee_id, f.co_assignee_id) == (5, 12, 77, 77)
    assert f.state == "overdue" and f.date_from == date(2026, 9, 1) and f.date_to == date(2026, 9, 30) and seen["limit"] == 5


def test_list_due_conflicts_with_period():
    server, _ = make()
    assert "либо окно срока" in error_text(call_tool(server, "list_controlled_action_items", {"due": "today", "date_from": "2026-09-01"}))


def test_list_ambiguous_employee():
    server, _ = make(employees=[EmployeeSummary(id=1, name="Иванов А"), EmployeeSummary(id=2, name="Иванов Б")])
    assert "несколько сотрудников" in error_text(call_tool(server, "list_controlled_action_items", {"assignee": "Иванов"}))


def test_dossier_kinds():
    server, seen = make()
    assert payload(call_tool(server, "get_control_dossier", {"action_item_id": 100}))["action_item"]["id"] == 100
    assert seen["dossier"] == (100, "task")
    payload(call_tool(server, "get_control_dossier", {"action_item_id": 3609, "entity_type": "action_item_assignment"}))
    assert seen["dossier"] == (3609, "assignment")
    payload(call_tool(server, "get_control_dossier", {"action_item_id": 3609, "entity_type": None}))
    assert seen["dossier"] == (3609, None)


def make_history():
    seen = {}

    def history(author_id, date_from=None, date_to=None, limit=1000):
        seen["history"] = (author_id, date_from, date_to, limit)
        return {"items": [], "total": 0}

    services = SimpleNamespace(
        resolution_history=SimpleNamespace(history=history),
        action_items=SimpleNamespace(search_employee=lambda query: [EmployeeSummary(id=53, name="Босов")], get_employee=lambda i: EmployeeSummary(id=i, name="X")),
        current_user=SimpleNamespace(get_current_user=lambda: SimpleNamespace(id=5)),
    )
    return build_server(FakeProvider(services)), seen


def test_resolution_history_tool():
    server, seen = make_history()
    payload(call_tool(server, "get_resolution_history", {"author": "Босов"}))
    assert seen["history"] == (53, None, None, 1000)
    payload(call_tool(server, "get_resolution_history", {"author": "me", "date_from": "2025-01-01", "limit": 50}))
    assert seen["history"] == (5, date(2025, 1, 1), None, 50)
    assert "Укажите автора" in error_text(call_tool(server, "get_resolution_history", {"author": "  "}))


def test_me_under_service_account_is_refused():
    # Служебная учётка: «me» не подставляется молча.
    svc = SimpleNamespace(
        service_account=True,
        document_control=SimpleNamespace(list_controlled=lambda f, limit=30: {"items": []}),
        resolution_history=SimpleNamespace(history=lambda *a, **k: {"items": []}),
        action_items=SimpleNamespace(search_employee=lambda q: [EmployeeSummary(id=77, name="Иванов")], get_employee=lambda i: None),
        current_user=SimpleNamespace(get_current_user=lambda: SimpleNamespace(id=1)),
    )
    srv = build_server(FakeProvider(svc))
    assert "служебной учётной записью" in error_text(call_tool(srv, "list_controlled_action_items", {"supervisor": "me"}))
    assert "служебной учётной записью" in error_text(call_tool(srv, "get_resolution_history", {"author": "мне"}))
    payload(call_tool(srv, "list_controlled_action_items", {"supervisor": "Иванов"}))
