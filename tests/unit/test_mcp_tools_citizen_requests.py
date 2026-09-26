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
