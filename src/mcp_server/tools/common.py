from typing import Annotated

from mcp.server.mcpserver import Context, MCPServer
from pydantic import Field

from src.mcp_server.envelope import clamp_limit, list_envelope, to_jsonable
from src.mcp_server.runner import READ_ONLY, ToolRunner

Limit = Annotated[int, Field(description="Сколько записей вернуть (1–100)", ge=1, le=100)]


def register(mcp: MCPServer, runner: ToolRunner) -> None:
    @mcp.tool(annotations=READ_ONLY)
    async def get_current_user(ctx: Context) -> dict:
        """Текущий пользователь Directum RX, от имени которого работает mcpOGV: id, ФИО, логин."""
        return await runner.run(ctx, "get_current_user", lambda s: to_jsonable(s.current_user.get_current_user()))

    @mcp.tool(annotations=READ_ONLY)
    async def search_employees(
        query: Annotated[str, Field(description="ФИО сотрудника или его часть, например «Ардо»")],
        ctx: Context,
        limit: Limit = 20,
    ) -> dict:
        """Найти сотрудников по ФИО (нечёткий поиск по частям имени). Используй, чтобы получить id сотрудника — не выдумывай id."""
        size = clamp_limit(limit)
        return await runner.run(
            ctx,
            "search_employees",
            lambda s: list_envelope(s.action_items.search_employee(query, top=size + 1), size),
        )
