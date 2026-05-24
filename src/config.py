from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


OLLAMA_BASE_URL = "http://localhost:11434/v1"
OLLAMA_MODEL = "qwen3:8b"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
OPENROUTER_MODEL = "google/gemma-4-26b-a4b-it:free"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    APP_HOST: str = "0.0.0.0"
    APP_PORT: int = 8000
    APP_ENV: str = "development"

    LLM_PROVIDER: Literal["ario", "openai-compatible", "ollama", "openrouter"] = "ollama"
    OPENAI_BASE_URL: str = OLLAMA_BASE_URL
    OPENAI_API_KEY: SecretStr = SecretStr("ollama")
    OPENAI_MODEL: str = OLLAMA_MODEL
    LLM_TOOL_CALLING: Literal["auto", "enabled", "disabled"] = "auto"

    DIRECTUM_BASE_URL: str
    DIRECTUM_AUTH_MODE: Literal["basic_token"] = "basic_token"
    DIRECTUM_AUTH_TOKEN: SecretStr = Field(repr=False)
    DIRECTUM_REQUEST_TIMEOUT_SECONDS: float = 30.0

    BACKOFFICE_USERNAME: str = "admin"
    BACKOFFICE_PASSWORD: SecretStr = Field(default=SecretStr("change-me"), repr=False)
    METRICS_DB_PATH: str = "data/metrics.db"

    @model_validator(mode="after")
    def apply_provider_defaults(self):
        if self.LLM_PROVIDER == "openrouter":
            if self.OPENAI_BASE_URL.rstrip("/") == OLLAMA_BASE_URL:
                self.OPENAI_BASE_URL = OPENROUTER_BASE_URL
            if self.OPENAI_MODEL == OLLAMA_MODEL:
                self.OPENAI_MODEL = OPENROUTER_MODEL
        return self

    @property
    def llm_provider(self) -> str:
        return self.LLM_PROVIDER

    @property
    def openai_base_url(self) -> str:
        return self.OPENAI_BASE_URL.rstrip("/")

    @property
    def openai_api_key(self) -> str:
        return self.OPENAI_API_KEY.get_secret_value()

    @property
    def openai_model(self) -> str:
        return self.OPENAI_MODEL

    @property
    def directum_base_url(self) -> str:
        return self.DIRECTUM_BASE_URL.rstrip("/")

    def directum_headers(self) -> dict[str, str]:
        return {
            "Authorization": self.DIRECTUM_AUTH_TOKEN.get_secret_value(),
            "Accept": "application/json",
        }

    def public_config(self) -> dict[str, object]:
        return {
            "app_env": self.APP_ENV,
            "llm_provider": self.LLM_PROVIDER,
            "openai_base_url": self.openai_base_url,
            "openai_model": self.OPENAI_MODEL,
            "openai_api_key_set": bool(self.OPENAI_API_KEY.get_secret_value()),
            "llm_tool_calling": self.LLM_TOOL_CALLING,
            "directum_base_url": self.directum_base_url,
            "directum_auth_mode": self.DIRECTUM_AUTH_MODE,
            "directum_auth_token_set": bool(self.DIRECTUM_AUTH_TOKEN.get_secret_value()),
            "metrics_db_path": self.METRICS_DB_PATH,
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()
