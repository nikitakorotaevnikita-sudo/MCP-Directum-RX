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
