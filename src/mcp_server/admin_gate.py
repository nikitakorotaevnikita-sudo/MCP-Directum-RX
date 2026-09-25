import logging
from typing import Any

import anyio
from mcp.types import CallToolResult, TextContent

from src.mcp_server.context import TtlCache
from src.mcp_server.tools.admin import ADMIN_ONLY_MESSAGE, ADMIN_PREFIX

logger = logging.getLogger("mcp_ogv.admin")


def _tool_name(tool: Any) -> str:
    name = tool.get("name") if isinstance(tool, dict) else getattr(tool, "name", "")
    return name if isinstance(name, str) else ""


class AdminGateMiddleware:
    """Прячет тулы admin_* от не-администраторов Directum и не даёт вызвать их по известному имени.

    Проверка кешируется по отпечатку кредов; любая ошибка проверки — «не администратор».
    """

    def __init__(self, provider: Any, cache: TtlCache):
        self.provider = provider
        self.cache = cache

    async def __call__(self, ctx: Any, call_next: Any) -> Any:
        request = getattr(ctx, "request", None)
        headers = request.headers if request is not None else None
        if ctx.method == "tools/list":
            result = await call_next(ctx)
            if await anyio.to_thread.run_sync(self._is_admin, headers):
                return result
            data = result.model_dump(by_alias=True, exclude_none=True) if hasattr(result, "model_dump") else dict(result)
            data["tools"] = [tool for tool in data.get("tools", []) if not _tool_name(tool).startswith(ADMIN_PREFIX)]
            return data
        if ctx.method == "tools/call":
            name = (ctx.params or {}).get("name", "")
            if isinstance(name, str) and name.startswith(ADMIN_PREFIX):
                if not await anyio.to_thread.run_sync(self._is_admin, headers):
                    return CallToolResult(content=[TextContent(type="text", text=ADMIN_ONLY_MESSAGE)], is_error=True)
        return await call_next(ctx)

    def _is_admin(self, headers: Any) -> bool:
        try:
            with self.provider.open(headers) as (credentials, services):
                cached = self.cache.get(credentials.fingerprint)
                if cached is None:
                    cached = bool(services.admin_access.is_admin())
                    self.cache.set(credentials.fingerprint, cached)
                return cached
        except Exception as exc:
            logger.warning("Directum admin check failed, admin tools hidden: %s", type(exc).__name__)
            return False
