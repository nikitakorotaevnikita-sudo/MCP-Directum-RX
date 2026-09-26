from datetime import date, datetime, timedelta, timezone

from src.services.rx_methods import MAX_QUESTIONS, RxMethodsService

STAND_TZ = timezone(timedelta(hours=4))


class FakeClient:
    def __init__(self, results):
        self.results = results
        self.calls = []

    def call_function(self, path, params=None):
        self.calls.append((path, params))
        return self.results[path]


def service(results, today=date(2026, 9, 26)):
    return RxMethodsService(
        FakeClient(results), tz=STAND_TZ, now=lambda: datetime(today.year, today.month, today.day, 9, tzinfo=STAND_TZ)
    )


def test_executive_summary_combines_metrics_and_top_questions():
    questions = [{"Name": f"Вопрос {i}", "Count": i} for i in range(1, 13)]
    svc = service(
        {
            "Dashboard/GetActionItemsMetric": {"TotalCount": 120, "TotalInWorkCount": 40, "OverdueCount": 7},
            "Dashboard/GetRequestQuestionsMetric": questions,
        }
    )

    summary = svc.executive_summary()

    assert summary.action_items == {"total": 120, "in_work": 40, "overdue": 7}
    assert len(summary.request_questions) == MAX_QUESTIONS
    assert summary.request_questions[0] == {"name": "Вопрос 12", "count": 12}
    assert summary.request_questions_total == sum(range(1, 13))
    assert summary.request_question_kinds == 12
    assert summary.message == ""


def test_executive_summary_explains_empty_metrics():
    svc = service(
        {
            "Dashboard/GetActionItemsMetric": {"TotalCount": 0, "TotalInWorkCount": 0, "OverdueCount": 0},
            "Dashboard/GetRequestQuestionsMetric": [],
        }
    )

    assert "карточки сотрудника" in svc.executive_summary().message


def test_add_working_days_defaults_to_today_in_stand_timezone():
    svc = service({"Docflow/AddWorkingDaysAndHours": "2026-10-08T00:00:00+04:00"})

    result = svc.add_working_days(None, 8)

    path, params = svc.client.calls[0]
    assert path == "Docflow/AddWorkingDaysAndHours"
    assert params == {"date": datetime(2026, 9, 26, tzinfo=STAND_TZ), "days": 8, "hours": 0}
    assert result.date_from == date(2026, 9, 26)
    assert result.result == datetime(2026, 10, 8, tzinfo=STAND_TZ)
    assert result.result_date == date(2026, 10, 8)


def test_add_working_days_with_explicit_date_and_hours():
    svc = service({"Docflow/AddWorkingDaysAndHours": "2026-11-02T13:00:00+04:00"})

    result = svc.add_working_days(date(2026, 10, 1), 22, hours=4)

    assert svc.client.calls[0][1] == {"date": datetime(2026, 10, 1, tzinfo=STAND_TZ), "days": 22, "hours": 4}
    assert result.hours == 4
    assert result.result_date == date(2026, 11, 2)
