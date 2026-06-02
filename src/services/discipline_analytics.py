from datetime import datetime
from typing import Callable

from src.models.schemas import DisciplineSummary, EmployeeSummary
from src.services.action_items import DIRECTUM_TZ, ActionItemService
from src.services.directum_client import DirectumClient

ASSIGNMENTS_ENTITY_SET = "IAssignments"


class DisciplineAnalyticsService:
    """Описательная аналитика исполнительской дисциплины (принцип ADR-004:
    метрики считает детерминированный код через $count, LLM лишь формулирует)."""

    def __init__(
        self,
        client: DirectumClient,
        action_item_service: ActionItemService,
        now: Callable[[], datetime] | None = None,
    ):
        self.client = client
        self.action_item_service = action_item_service
        self._now = now or (lambda: datetime.now(DIRECTUM_TZ))

    def get_discipline_analytics(
        self,
        employee: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> DisciplineSummary:
        scope = "organization"
        employee_summary: EmployeeSummary | None = None
        base_filters: list[str] = []

        if employee and employee.strip():
            matches = self.action_item_service.search_employee(employee, top=1)
            if not matches:
                return DisciplineSummary(
                    scope="employee",
                    employee=None,
                    message=f"Сотрудник «{employee.strip()}» не найден.",
                )
            employee_summary = matches[0]
            scope = "employee"
            base_filters.append(f"Performer/Id eq {employee_summary.id}")

        start = self.action_item_service._normalize_filter_datetime(date_from)
        end = self.action_item_service._normalize_filter_datetime(date_to, end_of_day=True)
        now_iso = self._now().isoformat()

        period_filters: list[str] = []
        if start:
            period_filters.append(f"Modified ge {start}")
        if end:
            period_filters.append(f"Modified le {end}")

        in_process = self._count(base_filters + ["Status eq 'InProcess'"])
        overdue = self._count(
            base_filters + ["Status eq 'InProcess'", f"Deadline lt {now_iso}"]
        )
        completed_filters = base_filters + ["Status eq 'Completed'"] + period_filters
        completed = self._count(completed_filters)
        completed_late = self._count(completed_filters + ["Modified gt Deadline"])
        completed_on_time = max(completed - completed_late, 0)
        on_time_rate = (
            round(completed_on_time / completed * 100, 1) if completed > 0 else None
        )

        return DisciplineSummary(
            scope=scope,
            employee=employee_summary,
            date_from=start,
            date_to=end,
            in_process=in_process,
            overdue=overdue,
            completed=completed,
            completed_on_time=completed_on_time,
            completed_late=completed_late,
            on_time_rate=on_time_rate,
        )

    def _count(self, filters: list[str]) -> int:
        filter_ = " and ".join(filters)
        return self.client.count(ASSIGNMENTS_ENTITY_SET, filter_=filter_ or None)
