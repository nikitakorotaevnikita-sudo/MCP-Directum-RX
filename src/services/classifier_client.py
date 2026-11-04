"""HTTP-клиент сервиса классификатора обращений (команда Class OG): поиск рубрик и проверка кода.

Учётка RX не нужна: точки /integration сервиса доступны во внутренней сети без авторизации. Текст запроса
не логируется. Контракт — Class-OG-Final, docs/superpowers/specs/2026-11-04-request-mcp-classifier-search-tool.md.
"""

from typing import Any
from urllib.parse import quote

import httpx
from mcp.server.mcpserver.exceptions import ToolError

MAX_TOP_K = 50
UNAVAILABLE = "Классификатор недоступен (нет связи или истёк таймаут). Повторите позже или сообщите администратору."


class ClassifierClient:
    def __init__(self, base_url: str, timeout: float = 15.0, transport: httpx.BaseTransport | None = None):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.transport = transport

    def search(self, query: str, top_k: int = MAX_TOP_K) -> dict[str, Any]:
        text = (query or "").strip()
        if not text:
            raise ToolError("Пустой запрос: сформулируйте суть вопроса языком классификатора.")
        size = max(1, min(MAX_TOP_K, int(top_k)))
        data = self._call("POST", "/integration/candidates", json={"query": text, "top_k": size})
        items = data.get("items") if isinstance(data.get("items"), list) else []
        returned = data.get("returned")
        return {"items": items, "returned": returned if isinstance(returned, int) else len(items)}

    def get(self, code: str) -> dict[str, Any]:
        value = (code or "").strip()
        if not value:
            raise ToolError("Укажите код рубрики классификатора.")
        return self._call("GET", f"/integration/classifier/{quote(value, safe='.')}")

    def _call(self, method: str, path: str, json: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            with httpx.Client(timeout=self.timeout, transport=self.transport) as http:
                response = http.request(method, self.base_url + path, json=json)
        except httpx.TransportError:
            raise ToolError(UNAVAILABLE) from None
        if response.status_code in (400, 503):
            detail = _detail(response) or f"ошибка {response.status_code}"
            if response.status_code == 503:
                raise ToolError(f"Классификатор не готов: {detail}. Повторите через минуту.")
            raise ToolError(f"Классификатор отклонил запрос: {detail}.")
        if response.status_code != 200:
            raise ToolError(f"Классификатор вернул ошибку {response.status_code}.")
        try:
            data = response.json()
        except ValueError:
            raise ToolError("Классификатор вернул некорректный ответ.") from None
        if not isinstance(data, dict):
            raise ToolError("Классификатор вернул некорректный ответ.")
        return data


def _detail(response: httpx.Response) -> str | None:
    try:
        detail = response.json().get("detail")
    except (ValueError, AttributeError):
        return None
    return detail.strip()[:300] if isinstance(detail, str) and detail.strip() else None
