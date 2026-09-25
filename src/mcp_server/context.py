import hashlib
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

import httpx
from mcp.server.mcpserver.exceptions import ToolError

from src.mcp_server.config import McpSettings
from src.services.directum_connection import build_basic_auth_token
from src.services.factory import DirectumServices, build_directum_services

LOGIN_HEADER = "x-directum-login"
PASSWORD_HEADER = "x-directum-password"
MISSING_CREDENTIALS_MESSAGE = "Укажите логин и пароль Directum в настройках сервера mcpOGV в LibreChat."
CURRENT_USER_TTL_SECONDS = 600


@dataclass(frozen=True)
class Credentials:
    auth_token: str = field(repr=False)
    fingerprint: str


def credentials_from_headers(headers: Mapping[str, str] | None, settings: McpSettings) -> Credentials:
    lowered = {str(key).lower(): str(value) for key, value in (headers or {}).items()}
    login = lowered.get(LOGIN_HEADER, "").strip()
    password = lowered.get(PASSWORD_HEADER, "")
    if login and password:
        token = build_basic_auth_token(login, password)
    elif settings.MCP_ALLOW_ENV_CREDENTIALS and settings.env_auth_token:
        token = settings.env_auth_token
    else:
        raise ToolError(MISSING_CREDENTIALS_MESSAGE)
    return Credentials(auth_token=token, fingerprint=hashlib.sha256(token.encode("utf-8")).hexdigest())


class TtlCache:
    def __init__(self, ttl_seconds: float, clock: Callable[[], float] = time.monotonic):
        self.ttl_seconds = ttl_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._items: dict[str, tuple[float, Any]] = {}

    def get(self, key: str) -> Any | None:
        with self._lock:
            entry = self._items.get(key)
            if entry is None:
                return None
            expires_at, value = entry
            if self._clock() >= expires_at:
                del self._items[key]
                return None
            return value

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            self._items[key] = (self._clock() + self.ttl_seconds, value)


class ServicesProvider:
    """Собирает сервисы Directum на время одного вызова тула из кредов пользователя."""

    def __init__(
        self,
        settings: McpSettings,
        transport: httpx.BaseTransport | None = None,
        user_cache: TtlCache | None = None,
    ):
        self.settings = settings
        self.transport = transport
        self.user_cache = user_cache or TtlCache(CURRENT_USER_TTL_SECONDS)

    @contextmanager
    def open(self, headers: Mapping[str, str] | None) -> Iterator[tuple[Credentials, DirectumServices]]:
        credentials = credentials_from_headers(headers, self.settings)
        services = build_directum_services(
            self.settings.directum_base_url,
            credentials.auth_token,
            self.settings.MCP_DIRECTUM_TIMEOUT_SECONDS,
            transport=self.transport,
        )
        cached_user = self.user_cache.get(credentials.fingerprint)
        if cached_user is not None:
            services.current_user.prime(cached_user)
        try:
            yield credentials, services
            if services.current_user.cached_user is not None:
                self.user_cache.set(credentials.fingerprint, services.current_user.cached_user)
        finally:
            services.close()
