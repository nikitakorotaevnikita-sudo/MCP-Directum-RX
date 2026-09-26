from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

from src.mcp_server.app import build_server
from src.models.schemas import ExecutiveSummary, QAAnswer, QASearchArea, QASource, WorkingDaysResult
from tests.unit.mcp_fakes import FakeProvider, call_tool, error_text, payload


def make_server(services):
    return build_server(FakeProvider(services))


def qa_services(answer=None):
    seen = {}

    def ask(question, area_ids=None, wait_seconds=30):
        seen.update(question=question, area_ids=area_ids, wait_seconds=wait_seconds)
        return answer or QAAnswer(status="completed", task_id="t1", answer="30 дней")

    def result(task_id):
        seen.update(task_id=task_id)
        return QAAnswer(status="in_progress", task_id=task_id)

    qa = SimpleNamespace(ask=ask, result=result, areas=lambda: [QASearchArea(id=1, name="Документы")])
    return SimpleNamespace(qa_search=qa), seen


def test_ask_documents_defaults():
    services, seen = qa_services(
        QAAnswer(status="completed", task_id="t1", answer="30 дней", sources=[QASource(name="Регламент", url="https://rx/doc/1")])
    )

    data = payload(call_tool(make_server(services), "ask_documents", {"question": "Какой срок?"}))

    assert seen == {"question": "Какой срок?", "area_ids": None, "wait_seconds": 30}
    assert data["answer"] == "30 дней"
    assert data["sources"][0]["url"] == "https://rx/doc/1"


def test_ask_documents_passes_areas_and_wait():
    services, seen = qa_services()

    payload(call_tool(make_server(services), "ask_documents", {"question": "q", "search_area_ids": [2, 3], "wait_seconds": 10}))

    assert seen["area_ids"] == [2, 3]
    assert seen["wait_seconds"] == 10


def test_ask_documents_rejects_empty_question_and_long_wait():
    services, _ = qa_services()
    server = make_server(services)

    assert "вопрос" in error_text(call_tool(server, "ask_documents", {"question": "   "}))
    assert call_tool(server, "ask_documents", {"question": "q", "wait_seconds": 90}).is_error


def test_get_ask_documents_result():
    services, seen = qa_services()

    data = payload(call_tool(make_server(services), "get_ask_documents_result", {"task_id": "t7"}))

    assert seen["task_id"] == "t7"
    assert data["status"] == "in_progress"


def test_list_qa_search_areas():
    services, _ = qa_services()

    data = payload(call_tool(make_server(services), "list_qa_search_areas"))

    assert data["items"] == [{"id": 1, "name": "Документы"}]


def test_get_executive_summary():
    summary = ExecutiveSummary(action_items={"total": 3, "in_work": 2, "overdue": 1}, request_questions=[{"name": "ЖКХ", "count": 5}])
    services = SimpleNamespace(rx_methods=SimpleNamespace(executive_summary=lambda: summary))

    data = payload(call_tool(make_server(services), "get_executive_summary"))

    assert data["action_items"]["overdue"] == 1
    assert data["request_questions"][0]["name"] == "ЖКХ"


def test_add_working_days():
    seen = {}
    tz = timezone(timedelta(hours=4))

    def add(start, days, hours=0):
        seen.update(start=start, days=days, hours=hours)
        return WorkingDaysResult(date_from=start or date(2026, 9, 26), days=days, hours=hours, result=datetime(2026, 11, 9, tzinfo=tz), result_date=date(2026, 11, 9))

    services = SimpleNamespace(rx_methods=SimpleNamespace(add_working_days=add))
    server = make_server(services)

    data = payload(call_tool(server, "add_working_days", {"days": 30, "date": "2026-09-26"}))
    assert seen == {"start": date(2026, 9, 26), "days": 30, "hours": 0}
    assert data["result_date"] == "2026-11-09"

    payload(call_tool(server, "add_working_days", {"days": 5}))
    assert seen["start"] is None

    assert "YYYY-MM-DD" in error_text(call_tool(server, "add_working_days", {"days": 5, "date": "завтра"}))
    assert call_tool(server, "add_working_days", {"days": 400}).is_error
