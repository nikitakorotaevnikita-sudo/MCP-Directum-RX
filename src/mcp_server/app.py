import hmac
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.responses import JSONResponse

from src.mcp_server.audit import ToolUsageStore
from src.mcp_server.config import McpSettings
from src.mcp_server.context import ServicesProvider
from src.mcp_server.runner import ToolRunner
from src.mcp_server.tools import common

SERVER_NAME = "mcpOGV"

INSTRUCTIONS = """mcpOGV — доступ к Directum RX от имени текущего пользователя (его права и его данные).
Правила:
1. Для типовых задач используй курируемые инструменты (list_*, get_*, search_*): они надёжнее универсальных.
2. Списки приходят частями: смотри total, returned, truncated. Если truncated=true — уточни фильтр или увеличь limit (до 100).
3. Не выдумывай идентификаторы: сотрудников ищи через search_employees, документы — через search_documents.
4. Универсальные odata_* используй для того, чего нет в курируемых. Всегда указывай filter — Directum отклоняет запросы без фильтра. Поля смотри через odata_describe_entity, наборы данных — в справочниках drx://domains/*.
5. Инструменты drx_native_* — встроенные инструменты самой Directum RX.
6. Даты передавай в формате YYYY-MM-DD.
"""


def _register_health(mcp: MCPServer) -> None:
    @mcp.custom_route("/health", methods=["GET"])
    async def health(request):
        return JSONResponse({"status": "ok", "server": SERVER_NAME})


def build_server(provider: Any, usage: ToolUsageStore | None = None) -> MCPServer:
    runner = ToolRunner(provider, usage)
    mcp = MCPServer(SERVER_NAME, instructions=INSTRUCTIONS)
    common.register(mcp, runner)
    _register_health(mcp)
    return mcp


class McpKeyMiddleware:
    """Отклоняет запросы к /mcp без правильного X-MCP-Key (общий секрет LibreChat ↔ mcpOGV)."""

    def __init__(self, app: Any, key: str | None):
        self.app = app
        self.key = key

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and self.key is not None and scope["path"].startswith("/mcp"):
            provided = dict(scope.get("headers") or []).get(b"x-mcp-key", b"").decode("latin-1")
            if not hmac.compare_digest(provided, self.key):
                response = JSONResponse({"error": "unauthorized"}, status_code=401)
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


def build_asgi_app(
    settings: McpSettings,
    provider: Any | None = None,
    usage: ToolUsageStore | None = None,
) -> McpKeyMiddleware:
    if settings.mcp_key is None and not settings.MCP_ALLOW_ENV_CREDENTIALS:
        raise RuntimeError(
            "MCP_OGV_KEY is required; MCP_ALLOW_ENV_CREDENTIALS=true is allowed only for local debugging"
        )
    provider = provider or ServicesProvider(settings)
    usage = usage or ToolUsageStore(settings.MCP_USAGE_DB_PATH)
    mcp = build_server(provider, usage)
    app = mcp.streamable_http_app(
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(allowed_hosts=settings.allowed_hosts),
    )
    return McpKeyMiddleware(app, settings.mcp_key)
