from datetime import date

import pytest

from src.models.schemas import DirectumUser
from src.services.assignments import ActionItemFilters, AssignmentsService


class FakeClient:
    def __init__(self):
        self.calls = []
        self.counts = []

    def query(self, entity_set, **kwargs):
        self.calls.append((entity_set, kwargs))
        return [{"Id": 7, "Subject": "Поручение", "Status": "InProcess", "Assignee": {"Name": "Иванов И.И."}}]

    def count(self, entity_set, filter_=None):
        self.counts.append((entity_set, filter_))
        return 42

    def build_client_card_url(self, entity_path):
        return f"https://rx.example/card/{entity_path}"


class FakeCurrentUser:
    def get_current_user(self):
        return DirectumUser(id=1165, name="Test User", login="user")


def service():
    return AssignmentsService(client=FakeClient(), current_user_service=FakeCurrentUser())


def test_incoming_default_is_in_process_for_employee():
    entity_set, filter_ = service().action_items_filter("incoming", 63)

    assert entity_set == "IActionItemExecutionAssignments"
    assert filter_ == "Performer/Id eq 63 and Status eq 'InProcess'"


def test_outgoing_uses_tasks_and_author():
    entity_set, filter_ = service().action_items_filter("outgoing", 63)

    assert entity_set == "IActionItemExecutionTasks"
    assert filter_ == "Author/Id eq 63 and Status eq 'InProcess'"


@pytest.mark.parametrize(
    ("status", "expected"),
    [("completed", " and Status eq 'Completed'"), ("aborted", " and Status eq 'Aborted'")],
)
def test_status_filter(status, expected):
    _, filter_ = service().action_items_filter("incoming", 63, ActionItemFilters(status=status))

    assert filter_ == "Performer/Id eq 63" + expected


def test_status_all_has_no_status_condition():
    _, filter_ = service().action_items_filter("incoming", 63, ActionItemFilters(status="all"))

    assert filter_ == "Performer/Id eq 63"


def test_only_overdue_adds_deadline_and_in_process():
    _, filter_ = service().action_items_filter("incoming", 63, ActionItemFilters(status="all", only_overdue=True))

    assert filter_.startswith("Performer/Id eq 63 and Status eq 'InProcess' and Deadline lt ")
    assert filter_.endswith("Z")


def test_only_overdue_with_completed_is_rejected():
    with pytest.raises(ValueError):
        service().action_items_filter("incoming", 63, ActionItemFilters(status="completed", only_overdue=True))


def test_period_by_deadline_is_inclusive():
    filters = ActionItemFilters(status="all", date_from=date(2026, 1, 1), date_to=date(2026, 1, 31))

    _, filter_ = service().action_items_filter("outgoing", 63, filters)

    assert filter_ == "Author/Id eq 63 and Deadline ge 2026-01-01T00:00:00Z and Deadline lt 2026-02-01T00:00:00Z"


def test_period_by_created():
    filters = ActionItemFilters(status="all", date_field="created", date_from=date(2026, 3, 5))

    _, filter_ = service().action_items_filter("incoming", 63, filters)

    assert filter_ == "Performer/Id eq 63 and Created ge 2026-03-05T00:00:00Z"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"direction": "sideways"},
        {"filters": ActionItemFilters(status="lost")},
        {"filters": ActionItemFilters(date_field="modified")},
    ],
)
def test_unknown_values_are_rejected(kwargs):
    args = {"direction": "incoming", "employee_id": 63, **kwargs}
    with pytest.raises(ValueError):
        service().action_items_filter(**args)


def test_list_employee_outgoing_expands_assignee_and_sorts():
    svc = service()

    items = svc.list_employee_action_items("outgoing", 63, top=5)

    entity_set, kwargs = svc.client.calls[0]
    assert entity_set == "IActionItemExecutionTasks"
    assert kwargs["filter_"] == "Author/Id eq 63 and Status eq 'InProcess'"
    assert kwargs["expand"] == "Assignee($select=Name)"
    assert kwargs["orderby"] == "Deadline asc"
    assert kwargs["top"] == 5
    assert items[0].entity_type == "action_item_task"
    assert items[0].performer == "Иванов И.И."


def test_list_employee_incoming_has_no_expand():
    svc = service()

    items = svc.list_employee_action_items("incoming", 63)

    _, kwargs = svc.client.calls[0]
    assert kwargs["expand"] is None
    assert items[0].entity_type == "action_item_assignment"


def test_count_employee_uses_same_filter():
    svc = service()

    total = svc.count_employee_action_items("incoming", 63, ActionItemFilters(status="completed"))

    assert total == 42
    assert svc.client.counts == [("IActionItemExecutionAssignments", "Performer/Id eq 63 and Status eq 'Completed'")]


def test_my_action_items_filter_unchanged():
    svc = service()

    svc.count_action_items("outgoing")

    assert svc.client.counts == [("IActionItemExecutionTasks", "Author/Id eq 1165 and Status eq 'InProcess'")]
