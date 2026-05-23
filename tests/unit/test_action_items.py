from datetime import datetime, timezone

from src.models.schemas import ActionItemCreateRequest, ToolCallRecord
from src.services.action_items import ActionItemService
from src.services.directum_client import DirectumError


class FakeClient:
    def __init__(self, query_rows=None, post_response=None):
        self.query_rows = [] if query_rows is None else query_rows
        self.post_response = {"Id": 123} if post_response is None else post_response
        self.query_calls = []
        self.post_calls = []

    def query(self, entity_set, **kwargs):
        self.query_calls.append((entity_set, kwargs))
        return self.query_rows

    def post(self, entity_set, payload):
        self.post_calls.append((entity_set, payload))
        return self.post_response


def test_action_item_create_defaults_to_preview_mode():
    request = ActionItemCreateRequest(
        subject="Prepare response",
        performer_id=42,
        action_text="Prepare a short response for the incoming letter",
    )

    assert request.confirm is False
    assert request.deadline is None


def test_action_item_create_rejects_empty_subject():
    try:
        ActionItemCreateRequest(
            subject=" ",
            performer_id=42,
            action_text="Prepare a short response for the incoming letter",
        )
    except ValueError as exc:
        assert "subject" in str(exc)
    else:
        raise AssertionError("request with empty subject was accepted")


def test_action_item_create_rejects_zero_performer_id():
    try:
        ActionItemCreateRequest(
            subject="Prepare response",
            performer_id=0,
            action_text="Prepare a short response for the incoming letter",
        )
    except ValueError as exc:
        assert "performer_id" in str(exc)
    else:
        raise AssertionError("request with zero performer_id was accepted")


def test_action_item_create_rejects_empty_action_text():
    try:
        ActionItemCreateRequest(
            subject="Prepare response",
            performer_id=42,
            action_text=" ",
        )
    except ValueError as exc:
        assert "action_text" in str(exc)
    else:
        raise AssertionError("request with empty action_text was accepted")


def test_tool_call_record_accepts_any_list_result():
    record = ToolCallRecord(
        name="search",
        arguments={"query": "response"},
        result=["match", 1, {"id": 2}],
    )

    assert record.result == ["match", 1, {"id": 2}]


def test_create_action_item_preview_never_posts():
    client = FakeClient()
    service = ActionItemService(client)
    request = ActionItemCreateRequest(
        subject="Prepare response",
        performer_id=42,
        action_text="Prepare a short response for the incoming letter",
        deadline=datetime(2026, 6, 1, 12, 30, tzinfo=timezone.utc),
    )

    result = service.create_action_item(request)

    assert result.mode == "preview"
    assert result.success is True
    assert result.directum_id is None
    assert result.payload == {
        "Subject": "Prepare response",
        "PerformersGD": "42",
        "ActionItem": "Prepare a short response for the incoming letter",
        "ExecutionState": "OnExecution",
        "Deadline": "2026-06-01T12:30:00+00:00",
    }
    assert client.post_calls == []


def test_create_action_item_confirm_posts_to_directum():
    client = FakeClient(post_response={"Id": 987})
    service = ActionItemService(client)
    request = ActionItemCreateRequest(
        subject="Prepare response",
        performer_id=42,
        action_text="Prepare a short response for the incoming letter",
        confirm=True,
    )

    result = service.create_action_item(request)

    assert client.post_calls == [
        (
            "IActionItemExecutionTasks",
            {
                "Subject": "Prepare response",
                "PerformersGD": "42",
                "ActionItem": "Prepare a short response for the incoming letter",
                "ExecutionState": "OnExecution",
            },
        )
    ]
    assert result.mode == "created"
    assert result.success is True
    assert result.directum_id == 987


def test_create_action_item_confirm_accepts_numeric_string_id():
    client = FakeClient(post_response={"Id": "987"})
    service = ActionItemService(client)
    request = ActionItemCreateRequest(
        subject="Prepare response",
        performer_id=42,
        action_text="Prepare a short response for the incoming letter",
        confirm=True,
    )

    result = service.create_action_item(request)

    assert result.directum_id == 987


def test_create_action_item_confirm_rejects_missing_id():
    client = FakeClient(post_response={})
    service = ActionItemService(client)
    request = ActionItemCreateRequest(
        subject="Prepare response",
        performer_id=42,
        action_text="Prepare a short response for the incoming letter",
        confirm=True,
    )

    try:
        service.create_action_item(request)
    except DirectumError as exc:
        assert exc.safe_message == "Directum returned an invalid action item id"
    else:
        raise AssertionError("missing action item id was accepted")


def test_create_action_item_confirm_rejects_invalid_id():
    client = FakeClient(post_response={"Id": "abc"})
    service = ActionItemService(client)
    request = ActionItemCreateRequest(
        subject="Prepare response",
        performer_id=42,
        action_text="Prepare a short response for the incoming letter",
        confirm=True,
    )

    try:
        service.create_action_item(request)
    except DirectumError as exc:
        assert exc.safe_message == "Directum returned an invalid action item id"
    else:
        raise AssertionError("invalid action item id was accepted")


def test_create_action_item_includes_aware_utc_deadline_in_payload():
    client = FakeClient()
    service = ActionItemService(client)
    request = ActionItemCreateRequest(
        subject="Prepare response",
        performer_id=42,
        action_text="Prepare a short response for the incoming letter",
        deadline=datetime(2026, 6, 1, 12, 30, tzinfo=timezone.utc),
        confirm=True,
    )

    service.create_action_item(request)

    assert client.post_calls[0][1]["Deadline"] == "2026-06-01T12:30:00+00:00"


def test_create_action_item_rejects_naive_deadline_before_post():
    client = FakeClient()
    service = ActionItemService(client)
    request = ActionItemCreateRequest(
        subject="Prepare response",
        performer_id=42,
        action_text="Prepare a short response for the incoming letter",
        deadline=datetime(2026, 6, 1, 12, 30),
        confirm=True,
    )

    try:
        service.create_action_item(request)
    except DirectumError as exc:
        assert exc.safe_message == "Action item deadline must include timezone"
    else:
        raise AssertionError("naive deadline was accepted")

    assert client.post_calls == []


def test_search_employee_uses_contains_name_filter():
    client = FakeClient(
        query_rows=[
            {"Id": 42, "Name": "O'Connor Alice", "Status": "Active"},
            {"Id": 43, "Name": "Connor Bob", "Status": "Active"},
        ]
    )
    service = ActionItemService(client)

    result = service.search_employee("  O'Connor  ", top=5)

    assert client.query_calls == [
        (
            "IEmployees",
            {
                "filter_": "contains(Name,'O''Connor') and Status eq 'Active'",
                "select": "Id,Name,Status",
                "top": 5,
            },
        )
    ]
    assert [employee.id for employee in result] == [42, 43]
    assert [employee.name for employee in result] == ["O'Connor Alice", "Connor Bob"]


def test_search_employee_returns_empty_without_query_for_whitespace():
    client = FakeClient(query_rows=[{"Id": 42, "Name": "Alice", "Status": "Active"}])
    service = ActionItemService(client)

    result = service.search_employee("   ")

    assert result == []
    assert client.query_calls == []
