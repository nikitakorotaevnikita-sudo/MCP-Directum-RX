"""Защита записи в RX: токен подтверждения (двухшаговое создание) и журнал аудита."""

import base64
import hashlib
import hmac
import json
import logging
import secrets
import threading
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("mcp_ogv.write")

TOKEN_TTL_SECONDS = 600


def canonical(plan: dict[str, Any]) -> str:
    return json.dumps(plan, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


class ConfirmTokens:
    """Токен = срок + HMAC(тул, план, отпечаток пользователя, срок). Секрет живёт в памяти процесса.

    План — то, что будет создано, уже с найденными id сотрудников и организаций. Любое изменение параметров
    меняет план, и токен перестаёт подходить; после перезапуска сервера старые токены недействительны.
    """

    def __init__(self, secret: bytes | None = None, ttl: int = TOKEN_TTL_SECONDS, clock: Callable[[], float] = time.time):
        self._secret = secret or secrets.token_bytes(32)
        self.ttl = ttl
        self._clock = clock

    def issue(self, tool: str, plan: dict[str, Any], fingerprint: str) -> str:
        expires = int(self._clock()) + self.ttl
        return f"{expires}.{self._mac(tool, plan, fingerprint, expires)}"

    def check(self, token: str, tool: str, plan: dict[str, Any], fingerprint: str) -> str | None:
        """None — токен подходит; иначе причина по-русски."""
        try:
            expires_text, mac = (token or "").strip().split(".", 1)
            expires = int(expires_text)
        except ValueError:
            return "Токен подтверждения повреждён. Вызовите инструмент без confirm_token, чтобы получить новый."
        if not hmac.compare_digest(mac, self._mac(tool, plan, fingerprint, expires)):
            return ("Токен не подходит: параметры изменились после предпросмотра (или сервер перезапущен). "
                    "Вызовите инструмент без confirm_token, покажите новый предпросмотр и получите подтверждение.")
        if self._clock() > expires:
            return "Срок токена подтверждения истёк (10 минут). Повторите предпросмотр."
        return None

    def _mac(self, tool: str, plan: dict[str, Any], fingerprint: str, expires: int) -> str:
        message = f"{tool}\n{fingerprint}\n{expires}\n{canonical(plan)}".encode("utf-8")
        digest = hmac.new(self._secret, message, hashlib.sha256).digest()
        return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


class WriteAudit:
    """Журнал записей в RX: одна строка JSON на операцию, без текста документов."""

    def __init__(self, path: str | None):
        self.path = Path(path) if path else None
        self._lock = threading.Lock()

    def record(self, tool: str, usage_id: str | None, on_behalf_of: str | None, result: dict[str, Any]) -> None:
        entry = {
            "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "tool": tool,
            "user": usage_id,
            "on_behalf_of": on_behalf_of,
            "created": {k: result.get(k) for k in ("document_id", "task_id", "version_id", "started", "task_ids", "created", "failed") if k in result},
            "warnings": len(result.get("warnings") or []),
        }
        logger.info("write %s", json.dumps(entry, ensure_ascii=False))
        if self.path is None:
            return
        try:
            with self._lock:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError as exc:  # журнал не должен ломать уже выполненную запись
            logger.warning("write audit failed: %s", exc)
