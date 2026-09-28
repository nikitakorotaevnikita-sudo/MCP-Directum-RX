from types import SimpleNamespace

from src.mcp_server.app import build_server
from src.models.schemas import CitizenRequestStatus
from tests.unit.mcp_fakes import FakeProvider, call_tool, error_text, payload

TOOL = "get_citizen_request_status"


def make(items=None):
    seen = {}

    def find(request_id=None, registration_number=None, applicant=None, limit=5):
        seen.update(request_id=request_id, registration_number=registration_number, applicant=applicant, limit=limit)
        return items if items is not None else [CitizenRequestStatus(id=900, status_text="На рассмотрении")]

    server = build_server(FakeProvider(SimpleNamespace(citizen_requests=SimpleNamespace(find=find))))
    return server, seen


def test_by_registration_number():
    server, seen = make()

    data = payload(call_tool(server, TOOL, {"registration_number": "13-ОГ"}))

    assert seen == {"request_id": None, "registration_number": "13-ОГ", "applicant": None, "limit": 5}
    assert data["returned"] == 1
    assert data["items"][0]["status_text"] == "На рассмотрении"


def test_by_applicant_with_limit():
    server, seen = make()

    payload(call_tool(server, TOOL, {"applicant": "Степанова", "limit": 3}))

    assert seen["applicant"] == "Степанова"
    assert seen["limit"] == 3


def test_requires_criterion():
    server, seen = make()

    assert "номер" in error_text(call_tool(server, TOOL, {"applicant": "  "}))
    assert seen == {}


def test_not_found_message():
    server, _ = make(items=[])

    data = payload(call_tool(server, TOOL, {"request_id": 5}))

    assert data["items"] == []
    assert "не найдено" in data["message"]


def analytics_server(result=None, matches=None):
    from src.models.schemas import CitizenRequestAnalytics, ClassifierMatch

    seen = {}

    def analytics(level="topic", date_from=None, date_to=None, classifier_code=None, top=10):
        seen.update(level=level, date_from=date_from, date_to=date_to, classifier_code=classifier_code, top=top)
        return result or CitizenRequestAnalytics(level=level, level_label="тематика", requests_total=3)

    def search(query):
        seen.update(query=query)
        return matches if matches is not None else [ClassifierMatch(level="topic", level_label="тематика", code="0003.0008", name="Хоз. деятельность")]

    svc = SimpleNamespace(analytics=analytics, search_classifier=search)
    return build_server(FakeProvider(SimpleNamespace(citizen_request_analytics=svc))), seen


def test_analytics_defaults_to_topic_level():
    server, seen = analytics_server()

    data = payload(call_tool(server, "get_citizen_requests_analytics", {}))

    assert seen == {"level": "topic", "date_from": None, "date_to": None, "classifier_code": None, "top": 10}
    assert data["requests_total"] == 3


def test_analytics_passes_filters():
    from datetime import date

    server, seen = analytics_server()

    payload(
        call_tool(
            server,
            "get_citizen_requests_analytics",
            {"level": "question", "date_from": "2026-01-01", "date_to": "2026-03-31", "classifier_code": "0003", "top": 5},
        )
    )

    assert seen == {"level": "question", "date_from": date(2026, 1, 1), "date_to": date(2026, 3, 31), "classifier_code": "0003", "top": 5}


def test_analytics_rejects_bad_code_and_dates():
    server, _ = analytics_server()

    assert "0003.0008" in error_text(call_tool(server, "get_citizen_requests_analytics", {"classifier_code": "экономика"}))
    assert "date_from позже date_to" in error_text(
        call_tool(server, "get_citizen_requests_analytics", {"date_from": "2026-05-01", "date_to": "2026-01-01"})
    )


def test_search_classifier():
    server, seen = analytics_server()

    data = payload(call_tool(server, "search_citizen_request_classifier", {"query": "дорог"}))

    assert seen["query"] == "дорог"
    assert data["items"][0]["code"] == "0003.0008"


def test_search_classifier_requires_text():
    server, _ = analytics_server()

    assert "слово" in error_text(call_tool(server, "search_citizen_request_classifier", {"query": " "}))
