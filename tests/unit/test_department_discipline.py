import threading

from src.models.schemas import DisciplineSummary, EmployeeSummary
from src.services.department_discipline import DepartmentDisciplineService

PEOPLE = [
    EmployeeSummary(id=1, name="Анна", department="Отдел А"),
    EmployeeSummary(id=2, name="Борис", department="Отдел А"),
    EmployeeSummary(id=3, name="Вера", department="Отдел Б"),
]
METRICS = {
    1: dict(in_process=10, overdue=2, completed=20, completed_on_time=18, completed_late=2, on_time_rate=90.0),
    2: dict(in_process=5, overdue=4, completed=10, completed_on_time=5, completed_late=5, on_time_rate=50.0),
    3: dict(in_process=0, overdue=0, completed=0, completed_on_time=0, completed_late=0, on_time_rate=None),
}


class FakeDiscipline:
    def __init__(self, fail_for=()):
        self.fail_for = set(fail_for)
        self.threads = set()
        self.calls = []

    def employee_discipline(self, employee, date_from=None, date_to=None):
        self.threads.add(threading.get_ident())
        self.calls.append((employee.id, date_from, date_to))
        if employee.id in self.fail_for:
            raise RuntimeError("stand hiccup")
        return DisciplineSummary(scope="employee", employee=employee, **METRICS[employee.id])


def test_rows_totals_and_sorting_by_overdue():
    result = DepartmentDisciplineService(FakeDiscipline()).compute(PEOPLE, departments=["Отдел А", "Отдел Б"])

    assert [row["employee_id"] for row in result.rows] == [2, 1, 3]
    assert result.rows[0] == {
        "employee_id": 2, "name": "Борис", "department": "Отдел А",
        "in_process": 5, "overdue": 4, "completed": 10, "completed_on_time": 5, "completed_late": 5, "on_time_rate": 50.0,
        "error": None,
    }
    assert result.totals == {"in_process": 15, "overdue": 6, "completed": 30, "completed_on_time": 23, "completed_late": 7}
    assert result.on_time_rate == 76.7
    assert result.employees_total == 3
    assert result.departments == ["Отдел А", "Отдел Б"]


def test_sort_by_on_time_rate_puts_worst_first_and_empty_last():
    result = DepartmentDisciplineService(FakeDiscipline()).compute(PEOPLE, sort_by="on_time_rate")

    assert [row["employee_id"] for row in result.rows] == [2, 1, 3]


def test_period_passed_to_each_employee():
    discipline = FakeDiscipline()

    DepartmentDisciplineService(discipline).compute(PEOPLE, date_from="2026-01-01", date_to="2026-03-31")

    assert sorted(discipline.calls) == [(1, "2026-01-01", "2026-03-31"), (2, "2026-01-01", "2026-03-31"), (3, "2026-01-01", "2026-03-31")]


def test_runs_in_parallel_threads():
    discipline = FakeDiscipline()
    many = [EmployeeSummary(id=1, name=f"С{i}") for i in range(12)]

    DepartmentDisciplineService(discipline, max_workers=4).compute(many)

    assert len(discipline.calls) == 12


def test_one_employee_failure_does_not_break_report():
    result = DepartmentDisciplineService(FakeDiscipline(fail_for={2})).compute(PEOPLE)

    failed = next(row for row in result.rows if row["employee_id"] == 2)
    assert failed["error"] == "не удалось посчитать"
    assert failed["overdue"] is None
    assert result.totals["overdue"] == 2
    assert "1 сотрудник" in result.message


def test_top_limits_rows_and_marks_truncation():
    result = DepartmentDisciplineService(FakeDiscipline()).compute(PEOPLE, top=2)

    assert len(result.rows) == 2
    assert result.truncated is True
    assert result.totals["in_process"] == 15


def test_empty_department():
    result = DepartmentDisciplineService(FakeDiscipline()).compute([])

    assert result.rows == []
    assert "нет сотрудников" in result.message
