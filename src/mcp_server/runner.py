import logging
import time
from collections.abc import Callable
from typing import Any, TypeVar

import anyio
from mcp.types import ToolAnnotations

from src.mcp_server.audit import ToolUsageStore
from src.mcp_server.errors import to_tool_error

logger = logging.getLogger("mcp_ogv")

T = TypeVar("T")

READ_ONLY = ToolAnnotations(read_only_hint=True)


def _headers_of(ctx: Any):
    if ctx is None:
        return None
    try:
        return ctx.headers
    except Exception:
        return None


class ToolRunner:
    """Выполняет действие тула: открывает сервисы из кредов, маппит ошибки, пишет метрики.

    Синхронные вызовы Directum уходят в поток, чтобы медленный запрос одного пользователя
    не блокировал сервер для остальных.
    """

    def __init__(self, provider: Any, usage: ToolUsageStore | None = None):
        self.provider = provider
        self.usage = usage

    async def run(self, ctx: Any, tool: str, action: Callable[[Any], T]) -> T:
        return await anyio.to_thread.run_sync(self._execute, _headers_of(ctx), tool, action)

    def _execute(self, headers: Any, tool: str, action: Callable[[Any], T]) -> T:
        started = time.perf_counter()
        usage_id = None
        ok = False
        error_kind = None
        try:
            with self.provider.open(headers) as (credentials, services):
                usage_id = credentials.usage_id
                result = action(services)
            ok = True
            return result
        except Exception as exc:
            error, error_kind = to_tool_error(exc)
            raise error from None
        finally:
            duration_ms = int((time.perf_counter() - started) * 1000)
            logger.info("tool=%s ok=%s error=%s duration_ms=%d", tool, ok, error_kind, duration_ms)
            if self.usage is not None:
                self.usage.record(tool, ok, error_kind, duration_ms, usage_id)
