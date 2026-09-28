from typing import Annotated, Any, Literal

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from src.mcp_server.envelope import clamp_limit, list_envelope
from src.mcp_server.access import ensure_can_view_employee
from src.mcp_server.runner import READ_ONLY, ToolRunner
from src.mcp_server.tools.action_items import Due, check_due
from src.mcp_server.tools.params import parse_period
from src.models.schemas import EmployeeSummary
from src.services.assignments import OVERDUE_COMPATIBLE_STATUSES, ActionItemFilters

MAX_CANDIDATES = 10

Limit = Annotated[int, Field(description="Сколько записей вернуть (1–100)", ge=1, le=100)]
IsoDate = Annotated[str | None, Field(description="Дата в формате YYYY-MM-DD, граница включительно")]


def resolve_employee(action_items: Any, query: str) -> EmployeeSummary:
    text = query.strip()
    if not text:
        raise ToolError("Укажите ФИО или id сотрудника.")
    if text.isdigit():
        found = action_items.get_employee(int(text))
        if found is None:
            raise ToolError(f"Сотрудник с id {text} не найден.")
        return found
    matches = action_items.search_employee(text)
    if not matches:
        raise ToolError(f"Сотрудник «{text}» не найден. Уточните ФИО.")
    if len(matches) == 1:
        return matches[0]
    exact = [match for match in matches if match.name.casefold() == text.casefold()]
    if len(exact) == 1:
        return exact[0]
    listing = "; ".join(f"{match.id} — {match.name}" for match in matches[:MAX_CANDIDATES])
    raise ToolError(f"Найдено несколько сотрудников: {listing}. Уточните, кого именно (можно передать id).")


def register(mcp: MCPServer, runner: ToolRunner) -> None:
    @mcp.tool(annotations=READ_ONLY)
    async def list_employee_action_items(
        employee: Annotated[str, Field(description="ФИО или id сотрудника")],
        direction: Annotated[
            Literal["incoming", "outgoing"],
            Field(description="incoming — сотрудник исполнитель, outgoing — сотрудник автор поручений"),
        ],
        ctx: Context,
        status: Annotated[
            Literal["in_process", "completed", "aborted", "all"],
            Field(description="Статус: в работе, завершены, прекращены или все"),
        ] = "in_process",
        only_overdue: Annotated[bool, Field(description="Только просроченные (срок прошёл, в работе)")] = False,
        date_field: Annotated[
            Literal["deadline", "created"], Field(description="К какой дате применять период: срок или создание")
        ] = "deadline",
        date_from: IsoDate = None,
        date_to: IsoDate = None,
        limit: Limit = 20,
        due: Due = None,
    ) -> dict:
        """СПИСОК поручений сотрудника — входящие (он исполнитель) или исходящие (он автор). Доступ: свои — любому,
        подчинённых — руководителю их подразделения (или вышестоящего), любых — администратору;
        с фильтром по статусу, просрочке, периоду или сроку (сегодня / 7 дней). У каждого: тема, срок, days_overdue
        (дней просрочки), author (кто выдал), performer, url карточки; подробности — get_action_item(id). total — сколько всего.
        Для таблиц «просроченные поручения сотрудника» используй этот тул, а не get_discipline_analytics (там только числа)."""
        size = clamp_limit(limit)
        check_due(due, status=status, only_overdue=only_overdue, has_period=bool(date_from or date_to))
        if only_overdue and status not in OVERDUE_COMPATIBLE_STATUSES:
            raise ToolError(
                "Просроченными бывают только поручения в работе: уберите only_overdue или укажите status=in_process."
            )
        start, end = parse_period(date_from, date_to)
        filters = ActionItemFilters(
            status=status, only_overdue=only_overdue, date_field=date_field, date_from=start, date_to=end, due=due
        )

        def action(s):
            person = resolve_employee(s.action_items, employee)
            ensure_can_view_employee(s, person.id)
            items = s.assignments.list_employee_action_items(direction, person.id, filters, top=size)
            total = s.assignments.count_employee_action_items(direction, person.id, filters)
            return {
                **list_envelope(items, size, total=total),
                "employee": {"id": person.id, "name": person.name},
                "filters": {
                    "direction": direction,
                    "status": status,
                    "only_overdue": only_overdue,
                    "date_field": date_field,
                    "date_from": start.isoformat() if start else None,
                    "date_to": end.isoformat() if end else None,
                    "due": due,
                },
            }

        return await runner.run(ctx, "list_employee_action_items", action)
