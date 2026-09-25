import json
import logging
import time
from typing import Any

import anyio
from mcp.types import CallToolResult, TextContent

from src.mcp_server.audit import ToolUsageStore
from src.mcp_server.context import TtlCache
from src.mcp_server.errors import to_tool_error
from src.services.directum_client import DirectumError

logger = logging.getLogger("mcp_ogv.native")

NATIVE_PREFIX = "drx_native_"
HANDLE_MCP_PATH = "IntegrationAIAgent/HandleMcpRequest"


class NativeMcpClient:
    """Встроенный MCP Directum RX: JSON-RPC передаётся строкой через OData-action HandleMcpRequest."""

    def __init__(self, client: Any):
        self.client = client

    def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        message = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        response = self.client.post(HANDLE_MCP_PATH, {"value": json.dumps(message, ensure_ascii=False)})
        raw = response.get("value") if isinstance(response, dict) else None
        if not isinstance(raw, str):
            raise DirectumError("Встроенный MCP Directum вернул неожиданный ответ")
        payload = json.loads(raw)
        error = payload.get("error")
        if error:
            text = error.get("message", "") if isinstance(error, dict) else str(error)
            raise DirectumError(f"Встроенный MCP Directum: {text}")
        return payload.get("result") or {}

    def list_read_only_tools(self) -> list[dict[str, Any]]:
        tools = self.request("tools/list", {}).get("tools", [])
        return [
            tool for tool in tools
            if isinstance(tool, dict)
            and isinstance(tool.get("name"), str) and tool.get("name")
            and (tool.get("annotations") or {}).get("readOnlyHint") is True
        ]

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return self.request("tools/call", {"name": name, "arguments": arguments or {}})


def _error_result(text: str) -> CallToolResult:
    return CallToolResult(content=[TextContent(type="text", text=text)], is_error=True)


def _build_call_result(result: dict[str, Any]) -> CallToolResult:
    """Возвращает результат родного MCP как есть (текст/картинки/ресурсы/structuredContent).

    Протокол Directum (2025-03-26) не присылает поля новее (например resultType), у которого
    в mcp.types есть значение по умолчанию, так что model_validate справляется с "старым" wire-form.
    Если результат не укладывается в CallToolResult вообще — откатываемся на извлечение текста.
    """
    try:
        return CallToolResult.model_validate(result)
    except Exception:
        texts = [
            item.get("text", "")
            for item in result.get("content", [])
            if isinstance(item, dict) and item.get("type") == "text"
        ]
        return CallToolResult(
            content=[TextContent(type="text", text=text) for text in texts] or [TextContent(type="text", text="")],
            is_error=bool(result.get("isError")),
        )


class NativeProxyMiddleware:
    """Добавляет read-only тулы встроенного MCP Directum в tools/list и проксирует их вызовы.

    Список зависит от пользователя (его креды и права), поэтому кешируется по отпечатку кредов.
    Вызов перепроверяет, что тул read-only: имя пишущего тула угадать и вызвать нельзя.
    """

    def __init__(self, provider: Any, cache: TtlCache, usage: ToolUsageStore | None = None):
        self.provider = provider
        self.cache = cache
        self.usage = usage

    async def __call__(self, ctx: Any, call_next: Any) -> Any:
        request = getattr(ctx, "request", None)
        headers = request.headers if request is not None else None
        if ctx.method == "tools/list":
            result = await call_next(ctx)
            data = result.model_dump(by_alias=True, exclude_none=True) if hasattr(result, "model_dump") else dict(result)
            native = await anyio.to_thread.run_sync(self._native_tools, headers)
            data["tools"] = list(data.get("tools", [])) + native
            return data
        if ctx.method == "tools/call":
            params = ctx.params or {}
            name = params.get("name", "")
            if isinstance(name, str) and name.startswith(NATIVE_PREFIX):
                return await anyio.to_thread.run_sync(self._call, headers, name, params.get("arguments") or {})
        return await call_next(ctx)

    def _read_only_tools(self, credentials: Any, services: Any) -> list[dict[str, Any]]:
        tools = self.cache.get(credentials.fingerprint)
        if tools is None:
            tools = NativeMcpClient(services.client).list_read_only_tools()
            self.cache.set(credentials.fingerprint, tools)
        return tools

    def _native_tools(self, headers: Any) -> list[dict[str, Any]]:
        try:
            with self.provider.open(headers) as (credentials, services):
                tools = self._read_only_tools(credentials, services)
            return [{**tool, "name": NATIVE_PREFIX + tool["name"]} for tool in tools]
        except Exception as exc:
            logger.warning("Directum native MCP tools unavailable: %s", type(exc).__name__)
            return []

    def _call(self, headers: Any, name: str, arguments: dict[str, Any]) -> CallToolResult:
        started = time.perf_counter()
        native_name = name[len(NATIVE_PREFIX):]
        fingerprint = None
        error_kind = None
        try:
            with self.provider.open(headers) as (credentials, services):
                fingerprint = credentials.fingerprint
                allowed = {tool["name"] for tool in self._read_only_tools(credentials, services)}
                if native_name not in allowed:
                    error_kind = "not_allowed"
                    return _error_result(
                        f"Инструмент {name} недоступен: проксируются только read-only инструменты Directum."
                    )
                result = NativeMcpClient(services.client).call_tool(native_name, arguments)
        except Exception as exc:
            error, error_kind = to_tool_error(exc)
            return _error_result(str(error))
        finally:
            if self.usage is not None:
                duration_ms = int((time.perf_counter() - started) * 1000)
                self.usage.record(name, error_kind is None, error_kind, duration_ms, fingerprint)
        return _build_call_result(result)
