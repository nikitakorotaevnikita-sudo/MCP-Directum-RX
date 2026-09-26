from collections.abc import Callable
from datetime import date, datetime, timezone, tzinfo
from typing import Any

from src.models.schemas import ExecutiveSummary, WorkingDaysResult

MAX_QUESTIONS = 10
EMPTY_METRICS_MESSAGE = (
    "Метрики пустые. Они считаются по нашей организации текущего сотрудника: у учётки без карточки сотрудника "
    "(например, Administrator) или без поручений и обращений будут нули."
)


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


class RxMethodsService:
    """Готовые прикладные методы RX: метрики дашборда руководителя и рабочий календарь."""

    def __init__(self, client: Any, tz: tzinfo | None = None, now: Callable[[], datetime] | None = None):
        self.client = client
        self.tz = tz or datetime.now().astimezone().tzinfo
        self._now = now or (lambda: datetime.now(timezone.utc))

    def executive_summary(self) -> ExecutiveSummary:
        metric = self.client.call_function("Dashboard/GetActionItemsMetric") or {}
        questions = self.client.call_function("Dashboard/GetRequestQuestionsMetric") or []
        ranked = sorted(
            ({"name": row.get("Name") or "", "count": int(row.get("Count") or 0)} for row in questions),
            key=lambda row: row["count"],
            reverse=True,
        )
        action_items = {
            "total": int(metric.get("TotalCount") or 0),
            "in_work": int(metric.get("TotalInWorkCount") or 0),
            "overdue": int(metric.get("OverdueCount") or 0),
        }
        empty = not any(action_items.values()) and not ranked
        return ExecutiveSummary(
            action_items=action_items,
            request_questions=ranked[:MAX_QUESTIONS],
            request_questions_total=sum(row["count"] for row in ranked),
            request_question_kinds=len(ranked),
            message=EMPTY_METRICS_MESSAGE if empty else "",
        )

    def add_working_days(self, start: date | None, days: int, hours: int = 0) -> WorkingDaysResult:
        start = start or self._now().astimezone(self.tz).date()
        moment = datetime(start.year, start.month, start.day, tzinfo=self.tz)
        raw = self.client.call_function(
            "Docflow/AddWorkingDaysAndHours", {"date": moment, "days": int(days), "hours": int(hours)}
        )
        result = _parse_datetime(raw)
        return WorkingDaysResult(
            date_from=start,
            days=days,
            hours=hours,
            result=result,
            result_date=result.astimezone(self.tz).date() if result else None,
        )
