import base64
import binascii
import hashlib
import hmac
import json
import re
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from mcp.server.mcpserver.exceptions import ToolError

from src.mcp_server.config import McpSettings
from src.services.directum_connection import build_basic_auth_token
from src.services.factory import DirectumServices, build_directum_services

LOGIN_HEADER = "x-directum-login"
PASSWORD_HEADER = "x-directum-password"
ACCOUNT_HEADER = "x-mcp-account"
ACCOUNT_NAME = re.compile(r"^[a-z0-9_-]{1,64}$")
MISSING_CREDENTIALS_MESSAGE = "Укажите логин и пароль Directum в настройках сервера mcpOGV в LibreChat."
CURRENT_USER_TTL_SECONDS = 600
ENV_LOGIN_FALLBACK = "env"
USAGE_ID_LENGTH = 16


@dataclass(frozen=True)
class Credentials:
    auth_token: str = field(repr=False)
    fingerprint: str  # ключ in-memory кешей; зависит от пароля, поэтому на диск не пишется
    usage_id: str  # обезличенный id пользователя для метрик: HMAC логина, от пароля не зависит
    # True — запрос без личных кредов, mcpOGV работает под служебной учёткой из окружения (MCP_ALLOW_ENV_CREDENTIALS).
    service_account: bool = False
    # Имя профиля из MCP_ACCOUNTS_FILE, если креды взяты из него (заголовок X-MCP-Account).
    account: str | None = None


def _from_latin1(value: str) -> str:
    """Starlette декодирует байты заголовков как latin-1; возвращаем исходный UTF-8 текст."""
    try:
        return value.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return value


def _login_from_basic_token(token: str) -> str:
    encoded = token[len("Basic "):].strip() if token.startswith("Basic ") else ""
    try:
        decoded = base64.b64decode(encoded, validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return ENV_LOGIN_FALLBACK
    login = decoded.split(":", 1)[0] if ":" in decoded else ""
    return login or ENV_LOGIN_FALLBACK


def usage_id_for(login: str, settings: McpSettings) -> str:
    digest = hmac.new(settings.metrics_salt.encode("utf-8"), login.encode("utf-8"), hashlib.sha256).hexdigest()
    return digest[:USAGE_ID_LENGTH]


def load_accounts(path: str | None) -> dict[str, tuple[str, str]]:
    """Профили учёток из JSON: имя → (логин, Basic-токен). Ошибки — без паролей в тексте."""
    if not (path or "").strip():
        return {}
    file = Path(path)
    if not file.is_file():
        raise ValueError(f"MCP_ACCOUNTS_FILE не найден: {file}")
    try:
        data = json.loads(file.read_text(encoding="utf-8-sig"))
    except ValueError:
        raise ValueError(f"MCP_ACCOUNTS_FILE — не JSON: {file}") from None
    if not isinstance(data, dict):
        raise ValueError("MCP_ACCOUNTS_FILE: ожидается объект {\"имя\": {\"login\": …, \"password\": …}}")
    accounts = {}
    for raw_name, entry in data.items():
        name = str(raw_name).strip().lower()
        if not ACCOUNT_NAME.match(name):
            raise ValueError(f"MCP_ACCOUNTS_FILE: имя профиля {raw_name!r} — только латиница, цифры, _ и -")
        login = str((entry or {}).get("login") or "").strip() if isinstance(entry, dict) else ""
        password = str((entry or {}).get("password") or "") if isinstance(entry, dict) else ""
        if not login or not password:
            raise ValueError(f"MCP_ACCOUNTS_FILE: у профиля {name!r} нужны login и password")
        accounts[name] = (login, build_basic_auth_token(login, password))
    return accounts


def credentials_from_headers(
    headers: Mapping[str, str] | None,
    settings: McpSettings,
    accounts: Mapping[str, tuple[str, str]] | None = None,
) -> Credentials:
    lowered = {str(key).lower(): str(value) for key, value in (headers or {}).items()}
    login = _from_latin1(lowered.get(LOGIN_HEADER, "")).strip()
    password = _from_latin1(lowered.get(PASSWORD_HEADER, ""))
    account = _from_latin1(lowered.get(ACCOUNT_HEADER, "")).strip().lower() or None
    service_account = False
    if login and password:
        token = build_basic_auth_token(login, password)
        account = None
    elif account is not None:
        # Неизвестный профиль — ошибка, а не тихий переход на служебную учётку.
        if account not in (accounts or {}):
            raise ToolError(f"Профиль учётки «{account}» не настроен на сервере mcpOGV (MCP_ACCOUNTS_FILE).")
        login, token = accounts[account]
    elif settings.MCP_ALLOW_ENV_CREDENTIALS and settings.env_auth_token:
        token = settings.env_auth_token
        login = _login_from_basic_token(token)
        service_account = True
    else:
        raise ToolError(MISSING_CREDENTIALS_MESSAGE)
    return Credentials(
        auth_token=token,
        fingerprint=hashlib.sha256(token.encode("utf-8")).hexdigest(),
        usage_id=usage_id_for(login, settings),
        service_account=service_account,
        account=account,
    )


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
        self.accounts = load_accounts(settings.MCP_ACCOUNTS_FILE)

    @contextmanager
    def open(self, headers: Mapping[str, str] | None) -> Iterator[tuple[Credentials, DirectumServices]]:
        credentials = credentials_from_headers(headers, self.settings, self.accounts)
        services = build_directum_services(
            self.settings.directum_base_url,
            credentials.auth_token,
            self.settings.MCP_DIRECTUM_TIMEOUT_SECONDS,
            transport=self.transport,
            tz=self.settings.stand_timezone,
            grant_rights_guid=self.settings.MCP_WRITE_GRANT_RIGHTS_GUID,
        )
        services.service_account = credentials.service_account
        services.credentials_fingerprint = credentials.fingerprint
        services.usage_id = credentials.usage_id
        cached_user = self.user_cache.get(credentials.fingerprint)
        if cached_user is not None:
            services.current_user.prime(cached_user)
        try:
            yield credentials, services
            if services.current_user.cached_user is not None:
                self.user_cache.set(credentials.fingerprint, services.current_user.cached_user)
        finally:
            services.close()
