from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, SecretStr, field_validator


class DirectumUser(BaseModel):
    id: int
    name: str
    login: str | None = None


class DirectumConnectionRequest(BaseModel):
    base_url: str = Field(min_length=1)
    username: str = Field(min_length=1)
    password: SecretStr = Field(min_length=1, repr=False)

    @field_validator("base_url", "username")
    @classmethod
    def strip_non_empty(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("value must not be empty")
        if cleaned.startswith("http"):
            cleaned = cleaned.rstrip("/")
        return cleaned


class DirectumConnectionStatus(BaseModel):
    base_url: str
    auth_configured: bool
    current_user: DirectumUser | None = None


class LLMConnectionRequest(BaseModel):
    provider: Literal["ario", "openai-compatible", "ollama", "openrouter"]
    base_url: str = Field(min_length=1)
    api_key: SecretStr | None = Field(default=None, repr=False)
    model: str = Field(min_length=1)
    tool_calling: Literal["auto", "enabled", "disabled"] = "auto"

    @field_validator("base_url", "model")
    @classmethod
    def strip_non_empty(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("value must not be empty")
        if cleaned.startswith("http"):
            cleaned = cleaned.rstrip("/")
        return cleaned

    @field_validator("api_key", mode="before")
    @classmethod
    def empty_api_key_keeps_existing(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value


class LLMConnectionStatus(BaseModel):
    provider: str
    base_url: str
    model: str
    tool_calling: str
    api_key_configured: bool


class AssignmentSummary(BaseModel):
    id: int
    subject: str
    status: str | None = None
    deadline: datetime | None = None
    entity_type: str
    url: str | None = None
    performer: str | None = None


class EmployeeSummary(BaseModel):
    id: int
    name: str
    status: str | None = None


class DocumentSummary(BaseModel):
    id: int
    name: str
    subject: str | None = None
    registration_number: str | None = None
    registration_date: datetime | None = None
    url: str | None = None


class DocumentCandidate(BaseModel):
    id: int
    name: str
    kind: str | None = None
    registration_number: str | None = None
    registration_date: datetime | None = None
    created: datetime | None = None
    url: str | None = None
    score: int = 0
    match_reasons: list[str] = Field(default_factory=list)


class DocumentSearchResult(BaseModel):
    items: list[DocumentCandidate] = Field(default_factory=list)
    candidates_total: int = 0
    relaxed: list[str] = Field(default_factory=list)
    message: str = ""


class CounterpartySummary(BaseModel):
    id: int
    name: str
    tin: str | None = None


class DocumentsByCounterpartyResult(BaseModel):
    counterparty: CounterpartySummary | None = None
    documents: list[DocumentSummary] = Field(default_factory=list)
    message: str = ""


class DisciplineSummary(BaseModel):
    scope: str = "organization"
    employee: EmployeeSummary | None = None
    date_from: datetime | None = None
    date_to: datetime | None = None
    in_process: int = 0
    overdue: int = 0
    completed: int = 0
    completed_on_time: int = 0
    completed_late: int = 0
    on_time_rate: float | None = None
    message: str = ""


class ActionItemCreateRequest(BaseModel):
    subject: str = Field(min_length=1)
    performer_id: int = Field(gt=0)
    action_text: str = Field(min_length=1)
    deadline: datetime | None = None
    document_id: int | None = Field(default=None, gt=0)
    confirm: bool = False

    @field_validator("subject", "action_text")
    @classmethod
    def strip_non_empty(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("value must not be empty")
        return cleaned


class ActionItemCreateResult(BaseModel):
    mode: Literal["preview", "created"]
    payload: dict[str, Any]
    success: bool
    directum_id: int | None = None
    url: str | None = None
    message: str


class TaskCreateRequest(BaseModel):
    subject: str = Field(min_length=1)
    performer_id: int = Field(gt=0)
    action_text: str = Field(min_length=1)
    deadline: datetime | None = None
    confirm: bool = False

    @field_validator("subject", "action_text")
    @classmethod
    def strip_non_empty(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("value must not be empty")
        return cleaned


class TaskCreateResult(BaseModel):
    mode: Literal["preview", "created"]
    payload: dict[str, Any]
    success: bool
    directum_id: int | None = None
    url: str | None = None
    message: str


class ToolCallRecord(BaseModel):
    name: str
    arguments: dict[str, Any]
    result: dict[str, Any] | list[Any] | None = None
    duration_ms: int | None = None
    success: bool = True


class ChatRequest(BaseModel):
    message: str = Field(min_length=1)
    history: list[dict[str, str]] = Field(default_factory=list)


class MeetingSummary(BaseModel):
    id: int
    subject: str
    start_date: datetime | None = None
    end_date: datetime | None = None
    place: str | None = None
    agenda_summary: str | None = None
    client_card_url: str


class ActionItemDetail(BaseModel):
    id: int
    subject: str
    text: str | None = None
    performer: str
    author: str
    deadline: date | None = None
    status: str
    created_date: date
    client_card_url: str
    narrative: str = ""
