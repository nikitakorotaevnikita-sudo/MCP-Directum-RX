from typing import Annotated, Literal

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from src.mcp_server.envelope import MAX_LIMIT, clamp_limit, list_envelope, to_jsonable
from src.mcp_server.runner import READ_ONLY, ToolRunner

Limit = Annotated[int, Field(description="Сколько записей вернуть (1–100)", ge=1, le=100)]
IsoDate = Annotated[str | None, Field(description="Дата в формате YYYY-MM-DD")]


def register(mcp: MCPServer, runner: ToolRunner) -> None:
    @mcp.tool(annotations=READ_ONLY)
    async def search_documents(
        query: Annotated[str, Field(description="Название, номер или тема документа")],
        ctx: Context,
        limit: Limit = 20,
    ) -> dict:
        """Поиск зарегистрированных документов по названию, номеру или теме."""
        size = clamp_limit(limit)
        return await runner.run(
            ctx, "search_documents", lambda s: list_envelope(s.action_items.search_documents(query, top=size + 1), size)
        )

    @mcp.tool(annotations=READ_ONLY)
    async def get_document(
        document_id: Annotated[int, Field(description="Id документа", gt=0)],
        ctx: Context,
    ) -> dict:
        """Карточка документа по Id: название, тема, регистрационный номер и дата, ссылка."""

        def action(s):
            document = s.action_items.get_document(document_id)
            if document is None:
                raise ToolError(f"Документ {document_id} не найден или у вас нет к нему доступа.")
            return to_jsonable(document)

        return await runner.run(ctx, "get_document", action)

    @mcp.tool(annotations=READ_ONLY)
    async def list_documents_by_counterparty(
        counterparty: Annotated[str, Field(description="Название организации или аббревиатура, например «МЦ»")],
        ctx: Context,
        limit: Limit = 20,
    ) -> dict:
        """Документы по контрагенту (входящие, исходящие, договоры). Если организация не найдена — см. поле message."""
        size = clamp_limit(limit)

        def action(s):
            result = s.action_items.search_documents_by_counterparty(counterparty, top=size + 1)
            return {
                "counterparty": to_jsonable(result.counterparty),
                "message": result.message,
                **list_envelope(result.documents, size),
            }

        return await runner.run(ctx, "list_documents_by_counterparty", action)

    @mcp.tool(annotations=READ_ONLY)
    async def list_letters(
        direction: Annotated[Literal["incoming", "outgoing"], Field(description="incoming — входящие, outgoing — исходящие")],
        ctx: Context,
        date_from: IsoDate = None,
        date_to: IsoDate = None,
        limit: Limit = 20,
    ) -> dict:
        """Зарегистрированные входящие или исходящие письма за период (по дате регистрации, новые сначала)."""
        size = clamp_limit(limit)
        return await runner.run(
            ctx,
            "list_letters",
            lambda s: list_envelope(
                s.action_items.list_letters(direction, date_from=date_from, date_to=date_to, top=size + 1), size
            ),
        )

    @mcp.tool(annotations=READ_ONLY)
    async def list_my_meetings(
        ctx: Context,
        days: Annotated[int, Field(description="На сколько дней вперёд (1–90)", ge=1, le=90)] = 7,
    ) -> dict:
        """Мои совещания на ближайшие дни: дата, тема, место, ссылка на карточку."""
        return await runner.run(
            ctx, "list_my_meetings", lambda s: list_envelope(s.meetings.get_my_meetings(days=days), MAX_LIMIT)
        )
