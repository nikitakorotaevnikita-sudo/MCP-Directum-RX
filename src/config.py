from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


OLLAMA_BASE_URL = "http://localhost:11434/v1"
OLLAMA_MODEL = "qwen3:8b"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
OPENROUTER_MODEL = "google/gemma-4-26b-a4b-it:free"
ARIO_BASE_URL = "https://llm.ario.directum360.ru/v1"
ARIO_MODEL = "Qwen/Qwen3.6-35B-A3B"

# Дефолты провайдеров. Если base_url/model не переопределены пользователем
# (т.е. остались одним из известных дефолтов), при смене провайдера их
# заменяем на дефолты выбранного провайдера.
PROVIDER_DEFAULTS = {
    "ollama": (OLLAMA_BASE_URL, OLLAMA_MODEL),
    "openrouter": (OPENROUTER_BASE_URL, OPENROUTER_MODEL),
    "ario": (ARIO_BASE_URL, ARIO_MODEL),
}
KNOWN_BASE_URLS = {url.rstrip("/") for url, _ in PROVIDER_DEFAULTS.values()}
KNOWN_MODELS = {model for _, model in PROVIDER_DEFAULTS.values()}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    APP_HOST: str = "0.0.0.0"
    APP_PORT: int = 8000
    APP_ENV: str = "development"

    LLM_PROVIDER: Literal["ario", "openai-compatible", "ollama", "openrouter"] = "ario"
    OPENAI_BASE_URL: str = ARIO_BASE_URL
    OPENAI_API_KEY: SecretStr = SecretStr("")
    OPENAI_MODEL: str = ARIO_MODEL
    LLM_TOOL_CALLING: Literal["auto", "enabled", "disabled"] = "auto"
    # Проверка TLS-сертификата LLM-эндпоинта. Для self-signed (напр. внутренний
    # ario) выставить false в .env: LLM_VERIFY_SSL=false
    LLM_VERIFY_SSL: bool = True

    DIRECTUM_BASE_URL: str
    DIRECTUM_AUTH_MODE: Literal["basic_token"] = "basic_token"
    DIRECTUM_AUTH_TOKEN: SecretStr = Field(repr=False)
    DIRECTUM_REQUEST_TIMEOUT_SECONDS: float = 30.0

    BACKOFFICE_USERNAME: str = "admin"
    BACKOFFICE_PASSWORD: SecretStr = Field(default=SecretStr("change-me"), repr=False)
    METRICS_DB_PATH: str = "data/metrics.db"

    @model_validator(mode="after")
    def apply_provider_defaults(self):
        defaults = PROVIDER_DEFAULTS.get(self.LLM_PROVIDER)
        if defaults is None:
            return self
        base_url, model = defaults
        # Переопределяем только если значение осталось одним из известных дефолтов
        # (пользователь не задал собственное).
        if self.OPENAI_BASE_URL.rstrip("/") in KNOWN_BASE_URLS:
            self.OPENAI_BASE_URL = base_url
        if self.OPENAI_MODEL in KNOWN_MODELS:
            self.OPENAI_MODEL = model
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
    def llm_verify_ssl(self) -> bool:
        return self.LLM_VERIFY_SSL

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
            "llm_verify_ssl": self.LLM_VERIFY_SSL,
            "directum_base_url": self.directum_base_url,
            "directum_auth_mode": self.DIRECTUM_AUTH_MODE,
            "directum_auth_token_set": bool(self.DIRECTUM_AUTH_TOKEN.get_secret_value()),
            "metrics_db_path": self.METRICS_DB_PATH,
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()
