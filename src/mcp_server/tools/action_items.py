from typing import Annotated, Literal

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from src.mcp_server.envelope import clamp_limit, list_envelope, to_jsonable
from src.mcp_server.runner import READ_ONLY, ToolRunner
from src.services.assignments import OVERDUE_COMPATIBLE_STATUSES, ActionItemFilters
from src.services.outgoing_analytics import categorize_outgoing

Limit = Annotated[int, Field(description="Сколько записей вернуть (1–100)", ge=1, le=100)]
IsoDate = Annotated[str | None, Field(description="Дата в формате YYYY-MM-DD")]
Direction = Annotated[
    Literal["incoming", "outgoing"],
    Field(description="incoming — поручения мне на исполнение, outgoing — поручения, выданные мной"),
]
Due = Annotated[
    Literal["today", "week"] | None,
    Field(description="Срок: today — сегодня, week — ближайшие 7 дней начиная с сегодня. Только поручения в работе"),
]


def check_due(due: str | None, status: str = "in_process", only_overdue: bool = False, has_period: bool = False) -> None:
    """Окно срока задаёт свой период и статус «в работе» — с другими фильтрами срока не сочетается."""
    if due is None:
        return
    if only_overdue:
        raise ToolError("due и only_overdue несовместимы: выберите что-то одно.")
    if status not in OVERDUE_COMPATIBLE_STATUSES:
        raise ToolError("due применим только к поручениям в работе: укажите status=in_process.")
    if has_period:
        raise ToolError("due нельзя сочетать с date_from/date_to: выберите что-то одно.")


def register(mcp: MCPServer, runner: ToolRunner) -> None:
    @mcp.tool(annotations=READ_ONLY)
    async def list_my_assignments(ctx: Context, only_overdue: bool = False, limit: Limit = 20) -> dict:
        """Мои задания в работе (или только просроченные), по возрастанию срока. total — сколько всего."""
        size = clamp_limit(limit)

        def action(s):
            fetch = s.assignments.get_overdue_assignments if only_overdue else s.assignments.get_my_assignments
            return list_envelope(fetch(top=size), size, total=s.assignments.count_my_assignments(only_overdue=only_overdue))

        return await runner.run(ctx, "list_my_assignments", action)

    @mcp.tool(annotations=READ_ONLY)
    async def list_action_items(direction: Direction, ctx: Context, limit: Limit = 20, due: Due = None) -> dict:
        """Поручения в работе: входящие (мне) или исходящие (выданные мной, с исполнителем), при необходимости — со сроком сегодня или в ближайшие 7 дней. total — сколько всего."""
        size = clamp_limit(limit)

        def action(s):
            if due is not None:
                filters = ActionItemFilters(due=due)
                me = s.current_user.get_current_user()
                items = s.assignments.list_employee_action_items(direction, me.id, filters, top=size)
                return list_envelope(items, size, total=s.assignments.count_employee_action_items(direction, me.id, filters))
            fetch = (
                s.assignments.get_action_items_assigned_to_me
                if direction == "incoming"
                else s.assignments.get_action_items_created_by_me
            )
            return list_envelope(fetch(top=size), size, total=s.assignments.count_action_items(direction))

        return await runner.run(ctx, "list_action_items", action)

    @mcp.tool(annotations=READ_ONLY)
    async def get_action_item(
        action_item_id: Annotated[int, Field(description="Id поручения", gt=0)],
        ctx: Context,
    ) -> dict:
        """Карточка поручения: тема, исполнитель, автор, статус, даты, ссылка. Отчёт о поручении формулируй сам по этим фактам."""
        return await runner.run(
            ctx, "get_action_item", lambda s: to_jsonable(s.meetings.get_action_item_details(action_item_id))
        )

    @mcp.tool(annotations=READ_ONLY)
    async def get_discipline_analytics(
        ctx: Context,
        employee: Annotated[str | None, Field(description="ФИО сотрудника; пусто — вся организация")] = None,
        date_from: IsoDate = None,
        date_to: IsoDate = None,
    ) -> dict:
        """Исполнительская дисциплина по заданиям: в работе, просрочено, завершено, в срок, с опозданием, % в срок. По организации или сотруднику, за период."""
        return await runner.run(
            ctx,
            "get_discipline_analytics",
            lambda s: to_jsonable(
                s.discipline.get_discipline_analytics(employee=employee, date_from=date_from, date_to=date_to)
            ),
        )

    @mcp.tool(annotations=READ_ONLY)
    async def get_outgoing_action_items_analytics(ctx: Context, limit_per_category: Limit = 20) -> dict:
        """Мои исходящие поручения по срокам: в работе, срок в ближайшие сутки, просрочено — с количеством и списками."""
        size = clamp_limit(limit_per_category)

        def action(s):
            items = [to_jsonable(item) for item in s.assignments.get_action_items_created_by_me()]
            groups = categorize_outgoing(items)
            return {
                "total": len(items),
                "categories": {name: list_envelope(group, size) for name, group in groups.items()},
            }

        return await runner.run(ctx, "get_outgoing_action_items_analytics", action)
