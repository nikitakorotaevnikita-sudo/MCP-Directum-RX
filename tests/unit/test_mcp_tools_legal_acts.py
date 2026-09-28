from datetime import date
from types import SimpleNamespace

from src.mcp_server.app import build_server
from src.models.schemas import EmployeeSummary, LegalActDetail, LegalActsOnApproval, LegalActSearchResult, LegalActSummary
from tests.unit.mcp_fakes import FakeProvider, call_tool, error_text, payload

ACT = LegalActSummary(id=15213, name="Проект №26-044", is_project=True, url="https://rx/doc/15213")


def make(employees=None):
    seen = {}

    def find(**kwargs):
        seen["find"] = kwargs
        return LegalActSearchResult(items=[ACT], total=1, returned=1)

    def get(act_id):
        seen["get"] = act_id
        return LegalActDetail(act=ACT)

    def on_approval(curator_id=None, min_days_waiting=None, limit=20):
        seen["on_approval"] = {"curator_id": curator_id, "min_days_waiting": min_days_waiting, "limit": limit}
        return LegalActsOnApproval(total=0)

    people = employees if employees is not None else [EmployeeSummary(id=77, name="Еремеева Елена Ивановна")]
    services = SimpleNamespace(
        legal_acts=SimpleNamespace(find=find, get=get, on_approval=on_approval),
        action_items=SimpleNamespace(search_employee=lambda query: people, get_employee=lambda employee_id: None),
    )
    return build_server(FakeProvider(services)), seen


def test_find_legal_acts_passes_filters():
    server, seen = make()

    data = payload(
        call_tool(
            server,
            "find_legal_acts",
            {"text": "домовые чаты", "kind": "project", "normative": True, "published": False, "approval_state": "OnRework",
             "date_from": "2026-01-01", "date_to": "2026-06-30", "limit": 5},
        )
    )

    assert seen["find"] == {
        "text": "домовые чаты", "kind": "project", "normative": True, "published": False, "approval_state": "OnRework",
        "date_from": date(2026, 1, 1), "date_to": date(2026, 6, 30), "limit": 5,
    }
    assert data["items"][0]["url"] == "https://rx/doc/15213"


def test_find_legal_acts_defaults():
    server, seen = make()

    payload(call_tool(server, "find_legal_acts", {}))

    assert seen["find"]["kind"] == "any" and seen["find"]["normative"] is None and seen["find"]["limit"] == 20


def test_find_legal_acts_bad_period():
    server, _ = make()

    assert "date_from позже date_to" in error_text(call_tool(server, "find_legal_acts", {"date_from": "2026-07-01", "date_to": "2026-01-01"}))


def test_get_legal_act():
    server, seen = make()

    data = payload(call_tool(server, "get_legal_act", {"act_id": 15213}))

    assert seen["get"] == 15213
    assert data["act"]["id"] == 15213


def test_on_approval_defaults_and_curator_resolution():
    server, seen = make()

    payload(call_tool(server, "list_legal_acts_on_approval", {}))
    assert seen["on_approval"] == {"curator_id": None, "min_days_waiting": None, "limit": 20}

    payload(call_tool(server, "list_legal_acts_on_approval", {"curator": "Еремеева", "min_days_waiting": 10, "limit": 5}))
    assert seen["on_approval"] == {"curator_id": 77, "min_days_waiting": 10, "limit": 5}


def test_on_approval_unknown_curator():
    server, seen = make(employees=[])

    assert "не найден" in error_text(call_tool(server, "list_legal_acts_on_approval", {"curator": "Никто"}))
    assert "on_approval" not in seen
