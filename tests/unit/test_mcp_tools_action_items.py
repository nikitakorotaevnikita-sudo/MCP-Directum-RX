from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

from src.mcp_server.app import build_server
from src.models.schemas import ActionItemDetail, AssignmentSummary, DisciplineSummary
from tests.unit.mcp_fakes import FakeProvider, call_tool, error_text, payload


def make_server(services):
    return build_server(FakeProvider(services))


def summaries(count, deadline=None):
    return [
        AssignmentSummary(id=i, subject=f"Задание {i}", status="InProcess", entity_type="assignment", deadline=deadline)
        for i in range(count)
    ]


def test_list_my_assignments_returns_total_from_count():
    calls = {}

    def get_my(top):
        calls["top"] = top
        return summaries(2)

    services = SimpleNamespace(
        assignments=SimpleNamespace(
            get_my_assignments=get_my,
            get_overdue_assignments=lambda top: [],
            count_my_assignments=lambda only_overdue=False: 1191,
        )
    )

    data = payload(call_tool(make_server(services), "list_my_assignments", {"limit": 2}))

    assert calls["top"] == 2
    assert data["total"] == 1191
    assert data["returned"] == 2
    assert data["truncated"] is True


def test_list_my_assignments_only_overdue():
    seen = {}

    def count(only_overdue=False):
        seen["only_overdue"] = only_overdue
        return 1

    services = SimpleNamespace(
        assignments=SimpleNamespace(
            get_my_assignments=lambda top: [],
            get_overdue_assignments=lambda top: summaries(1),
            count_my_assignments=count,
        )
    )

    data = payload(call_tool(make_server(services), "list_my_assignments", {"only_overdue": True}))

    assert seen["only_overdue"] is True
    assert data["total"] == 1
    assert data["truncated"] is False


def test_list_action_items_outgoing_uses_created_by_me():
    seen = {}

    def created(top):
        seen["top"] = top
        return summaries(1)

    def count(direction):
        seen["direction"] = direction
        return 5

    services = SimpleNamespace(
        assignments=SimpleNamespace(
            get_action_items_assigned_to_me=lambda top: [],
            get_action_items_created_by_me=created,
            count_action_items=count,
        )
    )

    data = payload(call_tool(make_server(services), "list_action_items", {"direction": "outgoing", "limit": 3}))

    assert seen == {"top": 3, "direction": "outgoing"}
    assert data["total"] == 5


def test_list_action_items_rejects_unknown_direction():
    result = call_tool(make_server(SimpleNamespace()), "list_action_items", {"direction": "sideways"})

    assert result.is_error


def test_get_action_item_returns_details():
    detail = ActionItemDetail(
        id=42,
        subject="Подготовить записку",
        performer="Иванова М.П.",
        author="Петров А.С.",
        status="InProcess",
        created_date=date(2026, 9, 20),
        client_card_url="https://rx.example/Client/#/card/x/42",
    )
    services = SimpleNamespace(meetings=SimpleNamespace(get_action_item_details=lambda action_item_id: detail))

    data = payload(call_tool(make_server(services), "get_action_item", {"action_item_id": 42}))

    assert data["id"] == 42
    assert data["performer"] == "Иванова М.П."


def test_get_discipline_analytics_passes_filters():
    seen = {}

    def analytics(employee=None, date_from=None, date_to=None):
        seen.update(employee=employee, date_from=date_from, date_to=date_to)
        return DisciplineSummary(in_process=66, overdue=27, completed=137, completed_on_time=110, completed_late=27, on_time_rate=80.3)

    services = SimpleNamespace(discipline=SimpleNamespace(get_discipline_analytics=analytics))

    data = payload(
        call_tool(
            make_server(services),
            "get_discipline_analytics",
            {"employee": "Ардо", "date_from": "2026-09-01", "date_to": "2026-09-25"},
        )
    )

    assert seen == {"employee": "Ардо", "date_from": "2026-09-01", "date_to": "2026-09-25"}
    assert data["on_time_rate"] == 80.3


def test_outgoing_analytics_groups_by_deadline():
    now = datetime.now(timezone.utc)
    items = [
        AssignmentSummary(id=1, subject="В работе", entity_type="action_item_task", deadline=now + timedelta(days=5)),
        AssignmentSummary(id=2, subject="Завтра", entity_type="action_item_task", deadline=now + timedelta(hours=6)),
        AssignmentSummary(id=3, subject="Просрочено", entity_type="action_item_task", deadline=now - timedelta(hours=2)),
    ]
    services = SimpleNamespace(assignments=SimpleNamespace(get_action_items_created_by_me=lambda top=None: items))

    data = payload(call_tool(make_server(services), "get_outgoing_action_items_analytics"))

    assert data["total"] == 3
    assert [item["id"] for item in data["categories"]["work"]["items"]] == [1]
    assert [item["id"] for item in data["categories"]["due_soon"]["items"]] == [2]
    assert [item["id"] for item in data["categories"]["overdue"]["items"]] == [3]


def test_directum_errors_become_tool_errors():
    def boom(top):
        from src.services.directum_client import DirectumError

        raise DirectumError("status 400: Используйте фильтрацию.", 400)

    services = SimpleNamespace(
        assignments=SimpleNamespace(get_my_assignments=boom, count_my_assignments=lambda only_overdue=False: 0)
    )

    text = error_text(call_tool(make_server(services), "list_my_assignments"))

    assert "Слишком широкий запрос" in text
