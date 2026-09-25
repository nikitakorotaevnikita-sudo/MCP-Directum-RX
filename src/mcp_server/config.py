from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


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
