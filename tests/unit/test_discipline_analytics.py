from datetime import datetime, timedelta, timezone

from src.services.action_items import ActionItemService
from src.services.discipline_analytics import DisciplineAnalyticsService

TZ = timezone(timedelta(hours=4))
FIXED_NOW = datetime(2026, 6, 2, 10, 0, 0, tzinfo=TZ)


class CountingClient:
    """Фейковый клиент: count() отдаёт число по содержимому фильтра."""

    def __init__(self, resolver, employees=None):
        self._resolver = resolver
        self._employees = [] if employees is None else employees
        self.count_calls = []
        self.query_calls = []

    def count(self, entity_set, filter_=None):
        self.count_calls.append((entity_set, filter_))
        return self._resolver(filter_ or "")

    def query(self, entity_set, **kwargs):
        self.query_calls.append((entity_set, kwargs))
        return self._employees


def _org_resolver(filter_: str) -> int:
    if "Status eq 'Completed'" in filter_ and "Modified gt Deadline" in filter_:
        return 27
    if "Status eq 'Completed'" in filter_:
        return 137
    if "Status eq 'InProcess'" in filter_ and "Deadline lt" in filter_:
        return 27
    if "Status eq 'InProcess'" in filter_:
        return 66
    return 0


def _service(client) -> DisciplineAnalyticsService:
    return DisciplineAnalyticsService(
        client,
        ActionItemService(client),
        now=lambda: FIXED_NOW,
    )


def test_org_wide_discipline_metrics_and_on_time_rate():
    client = CountingClient(_org_resolver)
    service = _service(client)

    summary = service.get_discipline_analytics()

    assert summary.scope == "organization"
    assert summary.employee is None
    assert summary.in_process == 66
    assert summary.overdue == 27
    assert summary.completed == 137
    assert summary.completed_late == 27
    assert summary.completed_on_time == 110
    assert summary.on_time_rate == 80.3


def test_overdue_filter_uses_now_with_timezone_offset():
    client = CountingClient(_org_resolver)
    service = _service(client)

    service.get_discipline_analytics()

    overdue_calls = [
        f for _, f in client.count_calls
        if "Status eq 'InProcess'" in f and "Deadline lt" in f
    ]
    assert overdue_calls, "overdue count query was not issued"
    assert "Deadline lt 2026-06-02T10:00:00+04:00" in overdue_calls[0]


def test_on_time_rate_is_none_when_no_completed():
    client = CountingClient(lambda f: 0)
    service = _service(client)

    summary = service.get_discipline_analytics()

    assert summary.completed == 0
    assert summary.completed_on_time == 0
    assert summary.on_time_rate is None


def test_period_adds_modified_bounds_on_completed_queries():
    client = CountingClient(_org_resolver)
    service = _service(client)

    service.get_discipline_analytics(date_from="2026-05-01", date_to="2026-05-31")

    completed_calls = [
        f for _, f in client.count_calls if "Status eq 'Completed'" in f
    ]
    assert completed_calls
    for f in completed_calls:
        assert "Modified ge 2026-05-01T00:00:00+04:00" in f
        assert "Modified le 2026-05-31T23:59:59+04:00" in f


def test_employee_filter_resolves_name_and_scopes_by_performer():
    employees = [{"Id": 42, "Name": "Иванов Иван", "Status": "Active"}]
    client = CountingClient(_org_resolver, employees=employees)
    service = _service(client)

    summary = service.get_discipline_analytics(employee="Иванов")

    assert summary.scope == "employee"
    assert summary.employee is not None
    assert summary.employee.id == 42
    for _, f in client.count_calls:
        assert "Performer/Id eq 42" in f


def test_unknown_employee_returns_message_and_zeros():
    client = CountingClient(_org_resolver, employees=[])
    service = _service(client)

    summary = service.get_discipline_analytics(employee="Несуществующий")

    assert summary.employee is None
    assert summary.in_process == 0
    assert summary.completed == 0
    assert summary.on_time_rate is None
    assert "не найден" in summary.message.lower()
    assert client.count_calls == []
