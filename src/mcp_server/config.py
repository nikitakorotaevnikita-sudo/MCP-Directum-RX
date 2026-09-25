import re
import secrets
from datetime import timedelta, timezone, tzinfo

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


# Соль метрик на случай, когда ни MCP_METRICS_SALT, ни MCP_OGV_KEY не заданы: живёт до перезапуска процесса.
_PROCESS_METRICS_SALT = secrets.token_hex(32)


UTC_OFFSET_PATTERN = re.compile(r"^([+-])(\d{2}):(\d{2})$")


def _parse_utc_offset(value: str | None) -> tzinfo | None:
    text = (value or "").strip()
    if not text:
        return None
    match = UTC_OFFSET_PATTERN.match(text)
    if match is None:
        raise ValueError("MCP_UTC_OFFSET must look like +04:00 or -03:30")
    sign, hours, minutes = match.groups()
    offset = timedelta(hours=int(hours), minutes=int(minutes))
    if offset > timedelta(hours=14):
        raise ValueError("MCP_UTC_OFFSET is out of range")
    return timezone(-offset if sign == "-" else offset)


def _secret_or_none(value: SecretStr | None) -> str | None:
    if value is None:
        return None
    text = value.get_secret_value().strip()
    return text or None


class McpSettings(BaseSettings):
    """Настройки mcpOGV: окружение и .env (сам .env агенты не редактируют)."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    DIRECTUM_BASE_URL: str
    DIRECTUM_AUTH_TOKEN: SecretStr | None = Field(default=None, repr=False)
    MCP_DIRECTUM_TIMEOUT_SECONDS: float = 20.0
    MCP_HOST: str = "0.0.0.0"
    MCP_PORT: int = 8010
    MCP_ALLOWED_HOSTS: str = "localhost:8010,127.0.0.1:8010,mcp-ogv:8010"
    MCP_OGV_KEY: SecretStr | None = Field(default=None, repr=False)
    MCP_ALLOW_ENV_CREDENTIALS: bool = False
    MCP_USAGE_DB_PATH: str = "data/mcp_usage.db"
    MCP_METRICS_SALT: SecretStr | None = Field(default=None, repr=False)
    # Часовой пояс стенда RX (например +04:00); пусто — пояс машины, где запущен сервер.
    MCP_UTC_OFFSET: str | None = None

    @field_validator("MCP_UTC_OFFSET")
    @classmethod
    def _check_utc_offset(cls, value: str | None) -> str | None:
        _parse_utc_offset(value)
        return value

    @property
    def stand_timezone(self) -> tzinfo | None:
        return _parse_utc_offset(self.MCP_UTC_OFFSET)

    @property
    def directum_base_url(self) -> str:
        return self.DIRECTUM_BASE_URL.rstrip("/")

    @property
    def allowed_hosts(self) -> list[str]:
        return [host.strip() for host in self.MCP_ALLOWED_HOSTS.split(",") if host.strip()]

    @property
    def mcp_key(self) -> str | None:
        return _secret_or_none(self.MCP_OGV_KEY)

    @property
    def env_auth_token(self) -> str | None:
        return _secret_or_none(self.DIRECTUM_AUTH_TOKEN)

    @property
    def metrics_salt(self) -> str:
        return _secret_or_none(self.MCP_METRICS_SALT) or self.mcp_key or _PROCESS_METRICS_SALT
