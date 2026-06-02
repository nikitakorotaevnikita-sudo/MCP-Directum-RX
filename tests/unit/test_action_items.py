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
        assert "документ" in exc.safe_message.lower()
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


class CounterpartyDocsClient(FakeClient):
    """Fuzzy counterparty lookup (ICompanies) + typed document sets by Counterparty nav."""

    def __init__(self, counterparties_by_filter=None, docs_by_set=None, error_sets=None):
        super().__init__()
        self.counterparties_by_filter = counterparties_by_filter or {}
        self.docs_by_set = docs_by_set or {}
        self.error_sets = error_sets or {}

    def query(self, entity_set, **kwargs):
        self.query_calls.append((entity_set, kwargs))
        if entity_set in self.error_sets:
            raise self.error_sets[entity_set]
        if entity_set == "ICompanies":
            return self.counterparties_by_filter.get(kwargs.get("filter_"), [])
        return self.docs_by_set.get(entity_set, [])


def test_search_documents_by_counterparty_takes_first_match_and_aggregates_typed_sets():
    client = CounterpartyDocsClient(
        counterparties_by_filter={
            "contains(Name,'Ромашка')": [
                {"Id": 100, "Name": "ООО Ромашка", "TIN": "7700000000"},
                {"Id": 101, "Name": "Ромашка-Сервис", "TIN": "7700000001"},
            ]
        },
        docs_by_set={
            "IContractualDocuments": [
                {
                    "Id": 1,
                    "Name": "Договор поставки",
                    "Subject": "Поставка",
                    "RegistrationNumber": "Д-1",
                    "RegistrationDate": "2026-05-01T00:00:00+04:00",
                }
            ],
            "IUniversalTransferDocuments": [
                {
                    "Id": 2,
                    "Name": "УПД №5",
                    "Subject": None,
                    "RegistrationNumber": "УПД-5",
                    "RegistrationDate": "2026-05-10T00:00:00+04:00",
                }
            ],
        },
    )
    service = ActionItemService(client)

    result = service.search_documents_by_counterparty("Ромашка", top=20)

    # First counterparty wins; Directum permissions limit what is visible.
    assert result.counterparty.id == 100
    assert result.counterparty.name == "ООО Ромашка"
    assert result.counterparty.tin == "7700000000"

    # Counterparty searched in ICompanies by fuzzy contains(Name,...).
    counterparty_calls = [c for c in client.query_calls if c[0] == "ICompanies"]
    assert counterparty_calls[0][1]["filter_"] == "contains(Name,'Ромашка')"

    # Documents aggregated across typed sets and sorted by date desc.
    assert [d.id for d in result.documents] == [2, 1]
    # Each typed set filtered by the Counterparty navigation on counterparty Id.
    filters = {c[0]: c[1]["filter_"] for c in client.query_calls if c[0] != "ICompanies"}
    assert filters["IContractualDocuments"] == "Counterparty/Id eq 100"
    assert filters["IUniversalTransferDocuments"] == "Counterparty/Id eq 100"
    # Documents carry a link.
    contract = next(d for d in result.documents if d.id == 1)
    assert contract.url == "https://rx.example/Integration/odata/IContractualDocuments(1)"


def test_search_documents_by_counterparty_includes_letters_via_correspondent_nav():
    # Письма (вх./исх.) ссылаются на контрагента через навигацию Correspondent,
    # а не Counterparty (проверено вживую на стенде ogvsale253).
    client = CounterpartyDocsClient(
        counterparties_by_filter={
            "contains(Name,'Минцифры России')": [
                {"Id": 2, "Name": "Минцифры России", "TIN": None}
            ]
        },
        docs_by_set={
            "IIncomingLetters": [
                {
                    "Id": 587,
                    "Name": "Вх. письмо от Минцифры России",
                    "Subject": "Тестовое входящее от Минцифры РФ",
                    "RegistrationNumber": None,
                    "RegistrationDate": None,
                }
            ]
        },
    )
    service = ActionItemService(client)

    result = service.search_documents_by_counterparty("Минцифры России")

    assert result.counterparty.id == 2
    assert [d.id for d in result.documents] == [587]
    filters = {c[0]: c[1]["filter_"] for c in client.query_calls if c[0] != "ICompanies"}
    assert filters["IIncomingLetters"] == "Correspondent/Id eq 2"
    assert filters["IOutgoingLetters"] == "Correspondent/Id eq 2"
    letter = result.documents[0]
    assert letter.url == "https://rx.example/Integration/odata/IIncomingLetters(587)"


def test_search_documents_by_counterparty_skips_sets_that_reject_filter():
    client = CounterpartyDocsClient(
        counterparties_by_filter={
            "contains(Name,'Ромашка')": [{"Id": 100, "Name": "ООО Ромашка", "TIN": None}]
        },
        docs_by_set={
            "IContractualDocuments": [
                {"Id": 1, "Name": "Договор", "RegistrationDate": "2026-05-01T00:00:00+04:00"}
            ]
        },
        error_sets={"IIncomingInvoices": DirectumError("bad filter", status_code=400)},
    )
    service = ActionItemService(client)

    result = service.search_documents_by_counterparty("Ромашка")

    # Rejected set is skipped, the rest still aggregated.
    assert [d.id for d in result.documents] == [1]


def test_search_documents_by_counterparty_deduplicates_by_document_id():
    # IContractualDocuments is a base type; a contract may also surface in another set.
    client = CounterpartyDocsClient(
        counterparties_by_filter={
            "contains(Name,'Ромашка')": [{"Id": 100, "Name": "ООО Ромашка", "TIN": None}]
        },
        docs_by_set={
            "IContractualDocuments": [
                {"Id": 33, "Name": "Договор", "RegistrationDate": "2024-03-30T00:00:00+04:00"}
            ],
            "IIncomingInvoices": [
                {"Id": 33, "Name": "Договор (дубль)", "RegistrationDate": "2024-03-30T00:00:00+04:00"}
            ],
        },
    )
    service = ActionItemService(client)

    result = service.search_documents_by_counterparty("Ромашка")

    assert [d.id for d in result.documents] == [33]


def test_search_documents_by_counterparty_uses_token_fallback_for_loose_query():
    client = CounterpartyDocsClient(
        counterparties_by_filter={
            "contains(Name,'Минцифры Алтайского края')": [],
            "contains(Name,'края')": [],
            "contains(Name,'Алтайского')": [{"Id": 200, "Name": "Минцифры Алтайского края", "TIN": None}],
        },
        docs_by_set={
            "IContractualDocuments": [
                {"Id": 7, "Name": "Договор", "RegistrationDate": "2026-04-01T00:00:00+04:00"}
            ]
        },
    )
    service = ActionItemService(client)

    result = service.search_documents_by_counterparty("Минцифры Алтайского края")

    assert result.counterparty.id == 200
    assert [d.id for d in result.documents] == [7]


def test_search_counterparty_prefers_longest_token_over_short_noise():
    # «Минцифры РФ»: короткое «РФ» не должно матчить чужую организацию раньше,
    # чем специфичное «Минцифры». Сперва пробуем самый длинный токен.
    client = CounterpartyDocsClient(
        counterparties_by_filter={
            "contains(Name,'Минцифры РФ')": [],
            "contains(Name,'Минцифры')": [{"Id": 2, "Name": "Минцифры России", "TIN": None}],
            "contains(Name,'РФ')": [
                {"Id": 9, "Name": "Администрация Президента РФ (УРОГ)", "TIN": None}
            ],
        },
    )
    service = ActionItemService(client)

    result = service.search_counterparty("Минцифры РФ")

    assert result[0].id == 2
    queried = [c[1]["filter_"] for c in client.query_calls]
    # Короткий «РФ» не должен запрашиваться, раз «Минцифры» уже совпал.
    assert "contains(Name,'РФ')" not in queried


def test_search_counterparty_expands_mc_abbreviation_to_mincifry():
    # «МЦ РФ» — обиходное сокращение «Минцифры РФ». Раскрываем «МЦ» → «Минцифр»
    # и пробуем его раньше шумного «РФ».
    client = CounterpartyDocsClient(
        counterparties_by_filter={
            "contains(Name,'МЦ РФ')": [],
            "contains(Name,'Минцифр')": [{"Id": 2, "Name": "Минцифры России", "TIN": None}],
            "contains(Name,'РФ')": [
                {"Id": 9, "Name": "Администрация Президента РФ (УРОГ)", "TIN": None}
            ],
        },
    )
    service = ActionItemService(client)

    result = service.search_counterparty("МЦ РФ")

    assert result[0].id == 2
    queried = [c[1]["filter_"] for c in client.query_calls]
    assert "contains(Name,'РФ')" not in queried


def test_list_letters_incoming_filters_registered_and_period():
    client = FakeClient(
        query_rows=[
            {
                "Id": 585,
                "Name": "Вх. письмо №1-0010/26",
                "Subject": "Тест",
                "RegistrationNumber": "1-0010/26",
                "RegistrationDate": "2026-05-21T00:00:00+04:00",
            }
        ]
    )
    service = ActionItemService(client)

    result = service.list_letters(
        direction="incoming",
        date_from="2026-05-01",
        date_to="2026-05-31",
    )

    entity_set, kwargs = client.query_calls[0]
    assert entity_set == "IIncomingLetters"
    flt = kwargs["filter_"]
    assert "RegistrationState eq 'Registered'" in flt
    assert "RegistrationDate ge 2026-05-01T00:00:00" in flt
    # date_to (date-only) расширяется до конца дня — иначе письма за 31-е выпадут.
    assert "RegistrationDate le 2026-05-31T23:59:59" in flt
    assert kwargs["orderby"] == "RegistrationDate desc"
    assert result[0].id == 585
    assert result[0].url == "https://rx.example/Integration/odata/IIncomingLetters(585)"


def test_list_letters_outgoing_without_dates_only_registered():
    client = FakeClient(query_rows=[])
    service = ActionItemService(client)

    service.list_letters(direction="outgoing")

    entity_set, kwargs = client.query_calls[0]
    assert entity_set == "IOutgoingLetters"
    assert kwargs["filter_"] == "RegistrationState eq 'Registered'"


def test_list_letters_rejects_unknown_direction():
    client = FakeClient(query_rows=[])
    service = ActionItemService(client)

    try:
        service.list_letters(direction="sideways")
    except DirectumError as exc:
        assert "direction" in str(exc)
    else:
        raise AssertionError("expected DirectumError for unknown direction")


def test_list_letters_ignores_non_iso_dates_to_prevent_injection():
    client = FakeClient(query_rows=[])
    service = ActionItemService(client)

    service.list_letters(direction="incoming", date_from="2026 or 1 eq 1")

    flt = client.query_calls[0][1]["filter_"]
    # Мусорная дата отбрасывается, остаётся только фильтр по статусу.
    assert flt == "RegistrationState eq 'Registered'"


def test_search_documents_by_counterparty_returns_empty_when_no_counterparty():
    client = CounterpartyDocsClient(counterparties_by_filter={})
    service = ActionItemService(client)

    result = service.search_documents_by_counterparty("НесуществующийКонтрагент")

    assert result.counterparty is None
    assert result.documents == []
    # No document sets queried if counterparty not found.
    assert all(c[0] == "ICompanies" for c in client.query_calls)


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


class _DocClient:
    def __init__(self, rows):
        self._rows = rows
        self.calls = []

    def query(self, entity_set, **kwargs):
        self.calls.append((entity_set, kwargs))
        return self._rows

    def build_client_card_url(self, entity_path):
        return f"https://rx.example/Client/#/card/guid/{entity_path.split('(')[1].rstrip(')')}"


def test_get_document_returns_summary_by_id():
    client = _DocClient([
        {"Id": 555, "Name": "Письмо №7", "Subject": "О поставке",
         "RegistrationNumber": "7", "RegistrationDate": "2026-05-30T00:00:00Z"}
    ])
    service = ActionItemService(client)

    doc = service.get_document(555)

    assert doc is not None
    assert doc.id == 555
    assert doc.name == "Письмо №7"
    assert doc.registration_number == "7"
    entity_set, kwargs = client.calls[0]
    assert entity_set == "IOfficialDocuments"
    assert "Id eq 555" in kwargs["filter_"]


def test_get_document_returns_none_when_absent():
    service = ActionItemService(_DocClient([]))
    assert service.get_document(999) is None


class _EmpClient:
    def __init__(self, rows):
        self._rows = rows
        self.calls = []

    def query(self, entity_set, **kwargs):
        self.calls.append((entity_set, kwargs))
        return self._rows


def test_get_employee_returns_summary_by_id():
    client = _EmpClient([{"Id": 75, "Name": "Ардо Иван Иванович", "Status": "Active"}])
    service = ActionItemService(client)

    emp = service.get_employee(75)

    assert emp is not None
    assert emp.id == 75
    assert emp.name == "Ардо Иван Иванович"
    entity_set, kwargs = client.calls[0]
    assert entity_set == "IEmployees"
    assert "Id eq 75" in kwargs["filter_"]


def test_get_employee_returns_none_when_absent():
    service = ActionItemService(_EmpClient([]))
    assert service.get_employee(123) is None


class _ContainsClient:
    """Фейк: возвращает контрагента, только если его имя содержит искомую подстроку."""
    def __init__(self, name):
        self.name = name
        self.calls = []

    def query(self, entity_set, **kwargs):
        import re
        self.calls.append(kwargs)
        f = kwargs.get("filter_", "")
        m = re.search(r"contains\(Name,'(.*?)'\)", f)
        needle = m.group(1) if m else ""
        if needle and needle in self.name:
            return [{"Id": 1498, "Name": self.name, "TIN": None}]
        return []


def test_counterparty_fallback_excludes_noise_tokens():
    service = ActionItemService(_DocClient([]))
    tokens = service._fallback_counterparty_tokens("Минкульта РФ")
    assert "РФ" not in tokens
    assert "Минкульта" in tokens


def test_counterparty_fallback_excludes_legal_forms():
    service = ActionItemService(_DocClient([]))
    tokens = service._fallback_counterparty_tokens("ООО Ромашка")
    assert "ООО" not in tokens
    assert "Ромашка" in tokens


def test_search_counterparty_not_matched_by_noise_token():
    # «Минкульт» не зарегистрирован → не должны ложно матчить «...Президента РФ» по «РФ».
    client = _ContainsClient("Администрация Президента РФ (УРОГ)")
    service = ActionItemService(client)
    assert service.search_counterparty("Минкульта РФ") == []


def test_search_counterparty_matches_word_form_variation():
    # LLM может передать иную словоформу («Минцифра» вместо «Минцифры»).
    # Стемминг fallback-токена должен находить «Минцифры России» по «Минцифр».
    client = _ContainsClient("Минцифры России")
    service = ActionItemService(client)
    result = service.search_counterparty("Минцифра РФ")
    assert result
    assert result[0].name == "Минцифры России"


def test_counterparty_fallback_includes_stem_variant():
    service = ActionItemService(_DocClient([]))
    tokens = service._fallback_counterparty_tokens("Минцифра РФ")
    assert "Минцифр" in tokens  # стем без окончания
