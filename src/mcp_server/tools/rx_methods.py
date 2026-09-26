from typing import Annotated

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from src.mcp_server.envelope import to_jsonable
from src.mcp_server.runner import READ_ONLY, ToolRunner
from src.mcp_server.tools.params import parse_iso_date

# Опрос RAG-задачи укладываем в таймаут вызова тула в LibreChat (60 с) с запасом.
MAX_WAIT_SECONDS = 45


def register(mcp: MCPServer, runner: ToolRunner) -> None:
    @mcp.tool(annotations=READ_ONLY)
    async def ask_documents(
        question: Annotated[str, Field(description="Вопрос на естественном языке, например «какой срок ответа на обращение?»")],
        ctx: Context,
        search_area_ids: Annotated[
            list[int] | None, Field(description="Id областей поиска (list_qa_search_areas); пусто — все готовые")
        ] = None,
        wait_seconds: Annotated[int, Field(description="Сколько ждать ответа, секунд (5–45)", ge=5, le=MAX_WAIT_SECONDS)] = 30,
    ) -> dict:
        """Вопросно-ответный поиск Directum RX по содержимому документов (RAG самой RX, с учётом прав пользователя).
        Возвращает ответ, источники со ссылками и фрагментами. status=in_progress — ответ ещё готовится:
        вызови get_ask_documents_result с task_id. status=unavailable — поиск на стенде не настроен."""
        if not question.strip():
            raise ToolError("Сформулируйте вопрос.")
        return await runner.run(
            ctx,
            "ask_documents",
            lambda s: to_jsonable(s.qa_search.ask(question.strip(), area_ids=search_area_ids, wait_seconds=wait_seconds)),
        )

    @mcp.tool(annotations=READ_ONLY)
    async def get_ask_documents_result(
        task_id: Annotated[str, Field(description="task_id из ответа ask_documents")],
        ctx: Context,
    ) -> dict:
        """Дочитать результат ask_documents, если он вернул status=in_progress."""
        return await runner.run(ctx, "get_ask_documents_result", lambda s: to_jsonable(s.qa_search.result(task_id)))

    @mcp.tool(annotations=READ_ONLY)
    async def list_qa_search_areas(ctx: Context) -> dict:
        """Готовые области вопросно-ответного поиска RX (id и название) — чтобы сузить ask_documents."""
        return await runner.run(ctx, "list_qa_search_areas", lambda s: {"items": to_jsonable(s.qa_search.areas())})

    @mcp.tool(annotations=READ_ONLY)
    async def get_executive_summary(ctx: Context) -> dict:
        """Сводка руководителя из дашборда RX: поручения нашей организации (всего, в работе, просрочено)
        и самые частые вопросы обращений граждан. Считается по организации текущего сотрудника."""
        return await runner.run(ctx, "get_executive_summary", lambda s: to_jsonable(s.rx_methods.executive_summary()))

    @mcp.tool(annotations=READ_ONLY)
    async def add_working_days(
        days: Annotated[int, Field(description="Сколько рабочих дней прибавить (0–365)", ge=0, le=365)],
        ctx: Context,
        date: Annotated[str | None, Field(description="От какой даты, YYYY-MM-DD; пусто — от сегодня")] = None,
        hours: Annotated[int, Field(description="Сколько рабочих часов прибавить (0–24)", ge=0, le=24)] = 0,
    ) -> dict:
        """Срок по рабочему календарю RX: дата + N рабочих дней (и часов), с учётом выходных и праздников.
        Например, 30 дней на ответ по 59-ФЗ или срок поручения «через 5 рабочих дней»."""
        start = parse_iso_date(date, "date")
        return await runner.run(
            ctx, "add_working_days", lambda s: to_jsonable(s.rx_methods.add_working_days(start, days, hours=hours))
        )
