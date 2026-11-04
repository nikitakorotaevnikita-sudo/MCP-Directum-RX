"""Тулы классификатора обращений поверх сервиса команды Class OG. Только чтение, без учётки RX.

Регистрируются, только если задан MCP_CLASSIFIER_URL.
"""

from typing import Annotated, Any

from mcp.server.mcpserver import Context, MCPServer
from pydantic import Field

from src.mcp_server.runner import READ_ONLY, ToolRunner


def register(mcp: MCPServer, runner: ToolRunner, classifier: Any) -> None:
    @mcp.tool(annotations=READ_ONLY)
    async def classifier_search(
        query: Annotated[str, Field(description="Фраза языком классификатора: суть вопроса, ключевые фразы, "
                                    "официальные формулировки (не дословный текст гражданина)")],
        ctx: Context,
        top_k: Annotated[int, Field(description="Сколько рубрик вернуть (1–50)", ge=1, le=50)] = 50,
    ) -> dict:
        """Смысловой и лексический поиск рубрик классификатора обращений граждан: до 50 кандидатов (code, name,
        level, full_path, score). Искать фразой языком классификатора. Финальный код выбирай сам по навыку
        classifier-code и перед ответом проверяй через classifier_get."""
        return await runner.run_plain(ctx, "classifier_search", lambda: classifier.search(query, top_k))

    @mcp.tool(annotations=READ_ONLY)
    async def classifier_get(
        code: Annotated[str, Field(description="Код рубрики, например 0003.0009.0097.0000 (короткий дополнится .0000)")],
        ctx: Context,
    ) -> dict:
        """Проверить код рубрики классификатора: exists, name, level, full_path, parent_code. exists=false — такого
        кода нет, выбери другой из classifier_search."""
        return await runner.run_plain(ctx, "classifier_get", lambda: classifier.get(code))
