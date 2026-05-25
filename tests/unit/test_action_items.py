from datetime import datetime, timezone

from src.models.schemas import ActionItemCreateRequest, TaskCreateRequest, ToolCallRecord
from src.services.action_items import ActionItemService
from src.services.directum_client import DirectumError


class FakeClient:
    def __init__(self, query_rows=None, post_response=None, get_one_response=None, get_one_error=None):
        self.query_rows = [] if query_rows is None else query_rows
        self.post_response = {"Id": 123} if post_response is None else post_response
        self.get_one_response = {} if get_one_response is None else get_one_response
        self.get_one_error = get_one_error
        self.query_calls = []
        self.post_calls = []
        self.get_one_calls = []

    def query(self, entity_set, **kwargs):
        self.query_calls.append((entity_set, kwargs))
        return self.query_rows

    def post(self, entity_set, payload):
        self.post_calls.append((entity_set, payload))
        return self.post_response

    def get_one(self, entity_path):
        self.get_one_calls.append(entity_path)
        if self.get_one_error is not None:
            raise self.get_one_error
        return self.get_one_response

    def build_url(self, entity_path):
        return f"https://rx.example/Integration/odata/{entity_path}"

    def build_client_card_url(self, entity_path):
        directum_id = entity_path.split("(", 1)[1].rstrip(")")
        return f"https://rx.example/Client/#/card/83f2a537-0cf0-4429-ae76-e9a386ca53aa/{directum_id}"


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
        "documentId": None,
        "assigneeId": 42,
        "isUnderControl": False,
        "supervisorId": None,
        "coassigneeId": None,
        "deadline": "2026-06-01T12:30:00+00:00",
        "activeText": "Prepare a short response for the incoming letter",
    }
    assert client.post_calls == []


def test_create_action_item_confirm_posts_to_directum():
    client = FakeClient(post_response={"value": 987})
    service = ActionItemService(client)
    request = ActionItemCreateRequest(
        subject="Prepare response",
        performer_id=42,
        action_text="Prepare a short response for the incoming letter",
        document_id=555,
        confirm=True,
    )

    result = service.create_action_item(request)

    assert client.post_calls == [
        (
            "RecordManagement/CreateActionItemExecution",
            {
                "documentId": 555,
                "assigneeId": 42,
                "isUnderControl": False,
                "supervisorId": None,
                "coassigneeId": None,
                "deadline": None,
                "activeText": "Prepare a short response for the incoming letter",
            },
        ),
        (
            "Docflow/StartTask",
            {"taskId": 987},
        ),
    ]
    assert result.mode == "created"
    assert result.success is True
    assert result.directum_id == 987


def test_create_action_item_confirm_returns_client_card_url_without_hyperlink_lookup():
    client = FakeClient(
        post_response={"value": 987},
        get_one_response={"Id": 987, "ClientHyperlink": "https://rx.example/action-item/987"},
    )
    service = ActionItemService(client)
    request = ActionItemCreateRequest(
        subject="Prepare response",
        performer_id=42,
        action_text="Prepare a short response for the incoming letter",
        document_id=555,
        confirm=True,
    )

    result = service.create_action_item(request)

    assert client.get_one_calls == []
    assert result.url == "https://rx.example/Client/#/card/83f2a537-0cf0-4429-ae76-e9a386ca53aa/987"


def test_create_action_item_confirm_falls_back_to_client_card_url_when_hyperlink_lookup_fails():
    client = FakeClient(
        post_response={"value": 987},
        get_one_error=DirectumError("Directum OData request failed with status 404", 404),
    )
    service = ActionItemService(client)
    request = ActionItemCreateRequest(
        subject="Prepare response",
        performer_id=42,
        action_text="Prepare a short response for the incoming letter",
        document_id=555,
        confirm=True,
    )

    result = service.create_action_item(request)

    assert result.mode == "created"
    assert result.directum_id == 987
    assert result.url == "https://rx.example/Client/#/card/83f2a537-0cf0-4429-ae76-e9a386ca53aa/987"


def test_create_action_item_confirm_accepts_numeric_string_id():
    client = FakeClient(post_response={"value": "987"})
    service = ActionItemService(client)
    request = ActionItemCreateRequest(
        subject="Prepare response",
        performer_id=42,
        action_text="Prepare a short response for the incoming letter",
        document_id=555,
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
        document_id=555,
        confirm=True,
    )

    try:
        service.create_action_item(request)
    except DirectumError as exc:
        assert exc.safe_message == "Directum returned an invalid action item id"
    else:
        raise AssertionError("missing action item id was accepted")


def test_create_action_item_confirm_rejects_invalid_id():
    client = FakeClient(post_response={"value": "abc"})
    service = ActionItemService(client)
    request = ActionItemCreateRequest(
        subject="Prepare response",
        performer_id=42,
        action_text="Prepare a short response for the incoming letter",
        document_id=555,
        confirm=True,
    )

    try:
        service.create_action_item(request)
    except DirectumError as exc:
        assert exc.safe_message == "Directum returned an invalid action item id"
    else:
        raise AssertionError("invalid action item id was accepted")


def test_create_action_item_includes_aware_utc_deadline_in_payload():
    client = FakeClient(post_response={"value": 987})
    service = ActionItemService(client)
    request = ActionItemCreateRequest(
        subject="Prepare response",
        performer_id=42,
        action_text="Prepare a short response for the incoming letter",
        deadline=datetime(2026, 6, 1, 12, 30, tzinfo=timezone.utc),
        document_id=555,
        confirm=True,
    )

    service.create_action_item(request)

    assert client.post_calls[0][1]["deadline"] == "2026-06-01T12:30:00+00:00"


def test_create_action_item_confirm_requires_document_id():
    client = FakeClient()
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
        assert "document_id" in exc.safe_message
    else:
        raise AssertionError("action item without document_id was accepted")

    assert client.post_calls == []


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


def test_search_employee_falls_back_to_name_tokens_when_full_query_has_no_matches():
    class TokenFallbackClient(FakeClient):
        def query(self, entity_set, **kwargs):
            self.query_calls.append((entity_set, kwargs))
            if kwargs["filter_"] == "contains(Name,'Ардо') and Status eq 'Active'":
                return [{"Id": 42, "Name": "Ардо Наталья Алексеевна", "Status": "Active"}]
            return []

    client = TokenFallbackClient()
    service = ActionItemService(client)

    result = service.search_employee("Натальи Ардо.", top=5)

    assert [call[1]["filter_"] for call in client.query_calls] == [
        "contains(Name,'Натальи Ардо') and Status eq 'Active'",
        "contains(Name,'Ардо') and Status eq 'Active'",
    ]
    assert [employee.name for employee in result] == ["Ардо Наталья Алексеевна"]


def test_search_employee_returns_empty_without_query_for_whitespace():
    client = FakeClient(query_rows=[{"Id": 42, "Name": "Alice", "Status": "Active"}])
    service = ActionItemService(client)

    result = service.search_employee("   ")

    assert result == []
    assert client.query_calls == []


def test_search_documents_falls_back_to_keyword_stem_for_inflected_query():
    class DocumentFallbackClient(FakeClient):
        def query(self, entity_set, **kwargs):
            self.query_calls.append((entity_set, kwargs))
            if kwargs["filter_"] == "(contains(Name,'Минцифр') or contains(Subject,'Минцифр')) and RegistrationDate ne null":
                return [
                    {
                        "Id": 576,
                        "Name": "Вх. письмо от Минцифры Алтайского Края",
                        "Subject": "Договор",
                        "RegistrationNumber": "1-0008/26",
                        "RegistrationDate": "2026-05-21T00:00:00+04:00",
                    }
                ]
            return []

    client = DocumentFallbackClient()
    service = ActionItemService(client)

    result = service.search_documents("Проверь документы по Минцифре", top=5)

    assert [call[1]["filter_"] for call in client.query_calls] == [
        "(contains(Name,'Проверь документы по Минцифре') or contains(Subject,'Проверь документы по Минцифре')) and RegistrationDate ne null",
        "(contains(Name,'Минцифре') or contains(Subject,'Минцифре')) and RegistrationDate ne null",
        "(contains(Name,'Минцифр') or contains(Subject,'Минцифр')) and RegistrationDate ne null",
    ]
    assert result[0].id == 576


def test_search_documents_expands_mc_abbreviation_to_mincifry_stem():
    class DocumentFallbackClient(FakeClient):
        def query(self, entity_set, **kwargs):
            self.query_calls.append((entity_set, kwargs))
            if kwargs["filter_"] == "(contains(Name,'Минцифр') or contains(Subject,'Минцифр')) and RegistrationDate ne null":
                return [{"Id": 576, "Name": "Вх. письмо от Минцифры", "Subject": "Договор"}]
            return []

    client = DocumentFallbackClient()
    service = ActionItemService(client)

    result = service.search_documents("Подготовьте документы для МЦ РФ", top=5)

    assert [call[1]["filter_"] for call in client.query_calls] == [
        "(contains(Name,'Подготовьте документы для МЦ РФ') or contains(Subject,'Подготовьте документы для МЦ РФ')) and RegistrationDate ne null",
        "(contains(Name,'Минцифр') or contains(Subject,'Минцифр')) and RegistrationDate ne null",
    ]
    assert result[0].id == 576


def test_create_action_item_confirm_auto_resolves_document_from_text():
    class DocumentResolvingClient(FakeClient):
        def query(self, entity_set, **kwargs):
            self.query_calls.append((entity_set, kwargs))
            return [{"Id": 576, "Name": "Вх. письмо от Минцифры", "Subject": "Договор"}]

    client = DocumentResolvingClient(post_response={"value": 987})
    service = ActionItemService(client)
    request = ActionItemCreateRequest(
        subject="Проверь документы по Минцифре",
        performer_id=42,
        action_text="Проверь документы по Минцифре",
        confirm=True,
    )

    service.create_action_item(request)

    assert client.post_calls[0] == (
        "RecordManagement/CreateActionItemExecution",
        {
            "documentId": 576,
            "assigneeId": 42,
            "isUnderControl": False,
            "supervisorId": None,
            "coassigneeId": None,
            "deadline": None,
            "activeText": "Проверь документы по Минцифре",
        },
    )


def test_create_task_preview_never_posts():
    client = FakeClient()
    service = ActionItemService(client)
    request = TaskCreateRequest(
        subject="Помыть полы",
        performer_id=75,
        action_text="Помыть полы",
        deadline=datetime(2026, 6, 27, 23, 59, tzinfo=timezone.utc),
    )

    result = service.create_task(request)

    assert result.mode == "preview"
    assert result.payload == {
        "assignmentType": "Assignment",
        "subject": "Помыть полы",
        "deadline": "2026-06-27T23:59:00+00:00",
        "importance": "Normal",
        "text": "Помыть полы",
        "performerIds": [75],
        "observerIds": [],
        "documentIds": [],
    }
    assert client.post_calls == []


def test_create_task_confirm_uses_simple_task_actions():
    client = FakeClient(post_response={"value": 901})
    service = ActionItemService(client)
    request = TaskCreateRequest(
        subject="Помыть полы",
        performer_id=75,
        action_text="Помыть полы",
        confirm=True,
    )

    result = service.create_task(request)

    assert client.post_calls == [
        (
            "Docflow/CreateSimpleTask",
            {
                "assignmentType": "Assignment",
                "subject": "Помыть полы",
                "deadline": None,
                "importance": "Normal",
                "text": "Помыть полы",
                "performerIds": [75],
                "observerIds": [],
                "documentIds": [],
            },
        ),
        ("Docflow/StartTask", {"taskId": 901}),
    ]
    assert result.mode == "created"
    assert result.directum_id == 901
    assert result.url == "https://rx.example/Client/#/card/83f2a537-0cf0-4429-ae76-e9a386ca53aa/901"
