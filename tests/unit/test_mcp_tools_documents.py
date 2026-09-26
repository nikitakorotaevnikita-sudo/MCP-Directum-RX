from datetime import datetime, timezone
from types import SimpleNamespace

from src.mcp_server.app import build_server
from src.models.schemas import CounterpartySummary, DocumentsByCounterpartyResult, DocumentSummary, MeetingSummary
from tests.unit.mcp_fakes import FakeProvider, call_tool, error_text, payload


def make_server(services):
    return build_server(FakeProvider(services))


def docs(count):
    return [DocumentSummary(id=i, name=f"Документ {i}", url=f"https://rx.example/doc/{i}") for i in range(count)]


def test_search_documents_requests_one_extra_row():
    seen = {}

    def search(query, top):
        seen.update(query=query, top=top)
        return docs(2)

    services = SimpleNamespace(action_items=SimpleNamespace(search_documents=search))

    data = payload(call_tool(make_server(services), "search_documents", {"query": "письмо", "limit": 5}))

    assert seen == {"query": "письмо", "top": 6}
    assert data["returned"] == 2
    assert data["total"] == 2


def test_get_document_not_found_is_reported():
    services = SimpleNamespace(action_items=SimpleNamespace(get_document=lambda document_id: None))

    text = error_text(call_tool(make_server(services), "get_document", {"document_id": 999}))

    assert "999" in text
    assert "не найден" in text


def test_get_document_returns_card():
    services = SimpleNamespace(action_items=SimpleNamespace(get_document=lambda document_id: docs(1)[0]))

    assert payload(call_tool(make_server(services), "get_document", {"document_id": 1}))["name"] == "Документ 0"


def test_list_documents_by_counterparty_includes_counterparty():
    result = DocumentsByCounterpartyResult(
        counterparty=CounterpartySummary(id=2, name="Минцифры России"),
        documents=docs(3),
        message="",
    )
    services = SimpleNamespace(action_items=SimpleNamespace(search_documents_by_counterparty=lambda query, top: result))

    data = payload(call_tool(make_server(services), "list_documents_by_counterparty", {"counterparty": "МЦ", "limit": 2}))

    assert data["counterparty"]["name"] == "Минцифры России"
    assert data["returned"] == 2
    assert data["truncated"] is True


def test_list_letters_passes_period():
    seen = {}

    def letters(direction, date_from=None, date_to=None, top=50):
        seen.update(direction=direction, date_from=date_from, date_to=date_to, top=top)
        return docs(1)

    services = SimpleNamespace(action_items=SimpleNamespace(list_letters=letters))

    payload(
        call_tool(
            make_server(services),
            "list_letters",
            {"direction": "incoming", "date_from": "2026-09-01", "date_to": "2026-09-25", "limit": 10},
        )
    )

    assert seen == {"direction": "incoming", "date_from": "2026-09-01", "date_to": "2026-09-25", "top": 11}


def test_list_my_meetings_returns_envelope():
    meeting = MeetingSummary(
        id=5,
        subject="Планёрка",
        start_date=datetime(2026, 9, 26, 10, tzinfo=timezone.utc),
        client_card_url="https://rx.example/Client/#/card/m/5",
    )
    seen = {}

    def meetings(days=7):
        seen["days"] = days
        return [meeting]

    services = SimpleNamespace(meetings=SimpleNamespace(get_my_meetings=meetings))

    data = payload(call_tool(make_server(services), "list_my_meetings", {"days": 14}))

    assert seen["days"] == 14
    assert data["items"][0]["subject"] == "Планёрка"


def search_services(result=None, error=None):
    from src.models.schemas import DocumentSearchResult

    seen = {}

    def find(criteria, limit=5):
        seen.update(criteria=criteria, limit=limit)
        if error:
            raise error
        return result or DocumentSearchResult()

    return SimpleNamespace(document_search=SimpleNamespace(find=find)), seen


def test_find_documents_passes_criteria_and_returns_candidates():
    from datetime import date

    from src.models.schemas import DocumentCandidate, DocumentSearchResult
    from src.services.document_search import DocumentCriteria

    result = DocumentSearchResult(
        items=[DocumentCandidate(id=5, name="Письмо", url="https://rx.example/doc/5", score=20, match_reasons=["слова: ремонт"])],
        candidates_total=7,
        relaxed=["период расширен на 30 дней"],
    )
    services, seen = search_services(result)

    data = payload(
        call_tool(
            make_server(services),
            "find_documents",
            {
                "text": "ремонт дорог",
                "kind": "incoming_letter",
                "counterparty": "Минфин",
                "employee": "Концева",
                "date_from": "2026-08-01",
                "date_to": "2026-08-31",
                "registration_number": "139",
                "limit": 3,
            },
        )
    )

    assert seen["criteria"] == DocumentCriteria(
        text="ремонт дорог",
        kind="incoming_letter",
        counterparty="Минфин",
        employee="Концева",
        date_from=date(2026, 8, 1),
        date_to=date(2026, 8, 31),
        registration_number="139",
    )
    assert seen["limit"] == 3
    assert data["returned"] == 1
    assert data["candidates_total"] == 7
    assert data["relaxed"] == ["период расширен на 30 дней"]
    assert data["items"][0]["url"] == "https://rx.example/doc/5"
    assert data["items"][0]["match_reasons"] == ["слова: ремонт"]


def test_find_documents_default_limit_is_five():
    services, seen = search_services()

    payload(call_tool(make_server(services), "find_documents", {"text": "ремонт"}))

    assert seen["limit"] == 5


def test_find_documents_requires_some_criterion():
    services, seen = search_services()

    text = error_text(call_tool(make_server(services), "find_documents", {"text": "про"}))

    assert "хотя бы один признак" in text
    assert seen == {}


def test_find_documents_rejects_counterparty_for_orders():
    services, seen = search_services()

    text = error_text(call_tool(make_server(services), "find_documents", {"kind": "order", "counterparty": "Минфин"}))

    assert "Контрагент" in text
    assert seen == {}


def test_find_documents_rejects_bad_dates():
    services, _ = search_services()
    server = make_server(services)

    assert "YYYY-MM-DD" in error_text(call_tool(server, "find_documents", {"date_from": "август"}))
    assert "date_from позже date_to" in error_text(
        call_tool(server, "find_documents", {"date_from": "2026-09-01", "date_to": "2026-08-01"})
    )


def test_find_documents_limit_capped_at_twenty():
    services, _ = search_services()

    result = call_tool(make_server(services), "find_documents", {"text": "ремонт", "limit": 50})

    assert result.is_error
