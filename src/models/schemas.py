from datetime import datetime
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


class AssignmentSummary(BaseModel):
    id: int
    subject: str
    status: str | None = None
    deadline: datetime | None = None
    entity_type: str
    url: str | None = None


class EmployeeSummary(BaseModel):
    id: int
    name: str
    status: str | None = None


class ActionItemCreateRequest(BaseModel):
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


class ActionItemCreateResult(BaseModel):
    mode: Literal["preview", "created"]
    payload: dict[str, Any]
    success: bool
    directum_id: int | None = None
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
