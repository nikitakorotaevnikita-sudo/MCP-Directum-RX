from types import SimpleNamespace

from src.mcp_server.app import build_server
from src.models.schemas import DepartmentDiscipline, EmployeeSummary
from tests.unit.mcp_fakes import FakeProvider, call_tool, error_text, payload

TOOL = "get_department_discipline"
DEPARTMENTS = {445: "Отдел документационного обеспечения", 500: "Сектор регистрации", 600: "Правовой отдел"}


def make(is_admin=False, managed=(445, 500), found=None):
    seen = {}

    def compute(employees, departments=None, date_from=None, date_to=None, sort_by="overdue", top=50):
        seen.update(employees=[p.id for p in employees], departments=departments, date_from=date_from, date_to=date_to, sort_by=sort_by, top=top)
        return DepartmentDiscipline(departments=departments or [], employees_total=len(employees))

    def tree(roots):
        seen["tree_roots"] = list(roots)
        return list(roots) + ([500] if 445 in roots else [])

    def employees_of(ids):
        seen["employee_departments"] = list(ids)
        return [EmployeeSummary(id=dep, name=f"Сотрудник {dep}", department=DEPARTMENTS[dep]) for dep in ids]

    subordination = SimpleNamespace(
        managed_department_ids=lambda: list(managed),
        department_tree=tree,
        employees_of=employees_of,
        find_departments=lambda query: found if found is not None else [{"id": 445, "name": DEPARTMENTS[445]}],
    )
    services = SimpleNamespace(
        admin_access=SimpleNamespace(is_admin=lambda: is_admin),
        subordination=subordination,
        department_discipline=SimpleNamespace(compute=compute),
    )
    return build_server(FakeProvider(services)), seen


def test_manager_defaults_to_own_departments():
    server, seen = make()

    data = payload(call_tool(server, TOOL, {}))

    assert seen["employee_departments"] == [445, 500]
    assert seen["employees"] == [445, 500]
    assert seen["departments"] == ["Отдел документационного обеспечения", "Сектор регистрации"]
    assert seen["sort_by"] == "overdue"
    assert data["employees_total"] == 2


def test_non_manager_without_department_gets_clear_error():
    server, _ = make(managed=())

    assert "не руководитель" in error_text(call_tool(server, TOOL, {}))


def test_admin_must_name_department():
    server, _ = make(is_admin=True)

    assert "get_discipline_analytics" in error_text(call_tool(server, TOOL, {}))


def test_named_department_includes_subdepartments():
    server, seen = make(is_admin=True)

    payload(call_tool(server, TOOL, {"department": "документационного", "date_from": "2026-01-01", "date_to": "2026-03-31", "sort_by": "on_time_rate", "top": 5}))

    assert seen["tree_roots"] == [445]
    assert seen["employee_departments"] == [445, 500]
    assert (seen["date_from"], seen["date_to"], seen["sort_by"], seen["top"]) == ("2026-01-01", "2026-03-31", "on_time_rate", 5)


def test_named_department_without_subdepartments():
    server, seen = make(is_admin=True)

    payload(call_tool(server, TOOL, {"department": "445", "include_subdepartments": False}))

    assert seen["employee_departments"] == [445]


def test_manager_cannot_open_foreign_department():
    server, seen = make(found=[{"id": 600, "name": DEPARTMENTS[600]}])

    assert "Нет доступа" in error_text(call_tool(server, TOOL, {"department": "Правовой"}))
    assert "employees" not in seen


def test_ambiguous_department_lists_candidates():
    server, _ = make(is_admin=True, found=[{"id": 445, "name": "Отдел А"}, {"id": 600, "name": "Отдел Б"}])

    text = error_text(call_tool(server, TOOL, {"department": "Отдел"}))

    assert "445 — Отдел А" in text and "600 — Отдел Б" in text


def test_unknown_department():
    server, _ = make(is_admin=True, found=[])

    assert "не найдено" in error_text(call_tool(server, TOOL, {"department": "Космос"}))


def test_bad_period_rejected():
    server, _ = make()

    assert "date_from позже date_to" in error_text(call_tool(server, TOOL, {"date_from": "2026-05-01", "date_to": "2026-01-01"}))
