import logging
import uuid

import httpx
from mcp.server.mcpserver.exceptions import ToolError

from src.services.directum_client import DirectumError

logger = logging.getLogger("mcp_ogv")

FILTER_REQUIRED_MARKER = "Используйте фильтрацию"


def to_tool_error(exc: Exception) -> tuple[ToolError, str]:
    """Превращает исключение в понятную агенту ошибку тула. Внутренние детали наружу не уходят."""
    if isinstance(exc, ToolError):
        return exc, "tool"
    if isinstance(exc, DirectumError):
        if exc.status_code == 401:
            return ToolError(
                "Неверный логин или пароль Directum. Проверьте учётные данные в настройках mcpOGV в LibreChat."
            ), "auth"
        if exc.status_code == 403:
            return ToolError("Недостаточно прав в Directum для этой операции."), "forbidden"
        if FILTER_REQUIRED_MARKER in exc.safe_message:
            return ToolError(
                "Слишком широкий запрос: Directum требует фильтр. Добавь условие (период, исполнитель, состояние)."
            ), "too_broad"
        return ToolError(exc.safe_message), "directum"
    if isinstance(exc, httpx.TimeoutException):
        return ToolError("Запрос к Directum выполнялся слишком долго. Сузь условия (период, фильтр, limit)."), "timeout"
    code = uuid.uuid4().hex[:8]
    logger.exception("Unexpected mcpOGV error, correlation id %s", code)
    return ToolError(f"Внутренняя ошибка mcpOGV, код {code}."), "internal"
