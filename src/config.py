from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    APP_HOST: str = "0.0.0.0"
    APP_PORT: int = 8000
    APP_ENV: str = "development"

    LLM_PROVIDER: Literal["ario", "openai-compatible", "ollama"] = "ollama"
    OPENAI_BASE_URL: str = "http://localhost:11434/v1"
    OPENAI_API_KEY: str = "ollama"
    OPENAI_MODEL: str = "qwen3:8b"
    LLM_TOOL_CALLING: Literal["auto", "enabled", "disabled"] = "auto"

    DIRECTUM_BASE_URL: str
    DIRECTUM_AUTH_MODE: Literal["basic_token"] = "basic_token"
    DIRECTUM_AUTH_TOKEN: str = Field(repr=False)
    DIRECTUM_REQUEST_TIMEOUT_SECONDS: float = 30.0

    BACKOFFICE_USERNAME: str = "admin"
    BACKOFFICE_PASSWORD: str = Field(default="change-me", repr=False)
    METRICS_DB_PATH: str = "data/metrics.db"

    @property
    def llm_provider(self) -> str:
        return self.LLM_PROVIDER

    @property
    def openai_base_url(self) -> str:
        return self.OPENAI_BASE_URL.rstrip("/")

    @property
    def openai_api_key(self) -> str:
        return self.OPENAI_API_KEY

    @property
    def openai_model(self) -> str:
        return self.OPENAI_MODEL

    @property
    def directum_base_url(self) -> str:
        return self.DIRECTUM_BASE_URL.rstrip("/")

    def directum_headers(self) -> dict[str, str]:
        return {
            "Authorization": self.DIRECTUM_AUTH_TOKEN,
            "Accept": "application/json",
        }

    def public_config(self) -> dict[str, object]:
        return {
            "app_env": self.APP_ENV,
            "llm_provider": self.LLM_PROVIDER,
            "openai_base_url": self.openai_base_url,
            "openai_model": self.OPENAI_MODEL,
            "openai_api_key_set": bool(self.OPENAI_API_KEY),
            "llm_tool_calling": self.LLM_TOOL_CALLING,
            "directum_base_url": self.directum_base_url,
            "directum_auth_mode": self.DIRECTUM_AUTH_MODE,
            "directum_auth_token_set": bool(self.DIRECTUM_AUTH_TOKEN),
            "metrics_db_path": self.METRICS_DB_PATH,
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()
