from concurrent.futures import ThreadPoolExecutor
from typing import Any

from src.models.schemas import DepartmentDiscipline, EmployeeSummary

METRIC_KEYS = ("in_process", "overdue", "completed", "completed_on_time", "completed_late")
SORT_KEYS = ("overdue", "on_time_rate", "in_process")
# Параллельные $count к RX: быстрее последовательных, но без перегрузки стенда.
DEFAULT_WORKERS = 6


class DepartmentDisciplineService:
    """Дисциплина по сотрудникам подразделения одним вызовом: метрики каждого считаются параллельно."""

    def __init__(self, discipline: Any, max_workers: int = DEFAULT_WORKERS):
        self.discipline = discipline
        self.max_workers = max_workers

    def compute(
        self,
        employees: list[EmployeeSummary],
        departments: list[str] | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        sort_by: str = "overdue",
        top: int = 50,
    ) -> DepartmentDiscipline:
        if sort_by not in SORT_KEYS:
            raise ValueError(f"Unknown sort key: {sort_by}")
        result = DepartmentDiscipline(departments=departments or [], date_from=date_from, date_to=date_to, sort_by=sort_by)
        if not employees:
            result.message = "В подразделении нет сотрудников (или они недоступны)."
            return result

        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            rows = list(pool.map(lambda person: self._row(person, date_from, date_to), employees))

        totals = {key: sum(row[key] or 0 for row in rows) for key in METRIC_KEYS}
        failed = sum(1 for row in rows if row["error"])
        result.rows = sorted(rows, key=lambda row: self._sort_key(row, sort_by))[:top]
        result.employees_total = len(rows)
        result.truncated = len(rows) > top
        result.totals = totals
        result.on_time_rate = round(totals["completed_on_time"] / totals["completed"] * 100, 1) if totals["completed"] else None
        if failed:
            result.message = f"Не удалось посчитать метрики для {failed} сотрудник(ов): итоги без них."
        return result

    def _row(self, person: EmployeeSummary, date_from: str | None, date_to: str | None) -> dict[str, Any]:
        row: dict[str, Any] = {"employee_id": person.id, "name": person.name, "department": person.department}
        try:
            summary = self.discipline.employee_discipline(person, date_from=date_from, date_to=date_to)
        except Exception:
            # Сбой по одному сотруднику не должен ронять отчёт по всему подразделению.
            row.update({key: None for key in METRIC_KEYS}, on_time_rate=None, error="не удалось посчитать")
            return row
        row.update({key: getattr(summary, key) for key in METRIC_KEYS}, on_time_rate=summary.on_time_rate, error=None)
        return row

    @staticmethod
    def _sort_key(row: dict[str, Any], sort_by: str):
        failed = row["error"] is not None
        if sort_by == "on_time_rate":
            rate = row["on_time_rate"]
            # Худший процент первым, сотрудники без завершённых — в конце.
            return (failed, rate is None, rate if rate is not None else 0, row["name"])
        return (failed, -(row[sort_by] or 0), row["name"])
