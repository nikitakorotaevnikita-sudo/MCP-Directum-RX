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
    # Кто выдал поручение (для входящих — автор задания).
    author: str | None = None
    # Id задачи-поручения: у входящих (заданий) свой номер, и он может совпасть с номером чужой задачи.
    task_id: int | None = None
    # Сколько полных дней просрочено по поясу стенда; None — не просрочено или не в работе.
    days_overdue: int | None = None


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


class DocumentText(BaseModel):
    document_id: int
    name: str = ""
    version: int | None = None
    extension: str | None = None
    text: str = ""
    chars_total: int = 0
    truncated: bool = False
    url: str | None = None
    message: str = ""


class QASearchArea(BaseModel):
    id: int
    name: str


class QASource(BaseModel):
    name: str
    url: str | None = None
    extension: str | None = None
    fragments: list[str] = Field(default_factory=list)


class QAAnswer(BaseModel):
    status: str
    task_id: str | None = None
    answer: str = ""
    score: float | None = None
    search_area: str | None = None
    sources: list[QASource] = Field(default_factory=list)
    message: str = ""


class ExecutiveSummary(BaseModel):
    action_items: dict[str, int] = Field(default_factory=dict)
    request_questions: list[dict[str, Any]] = Field(default_factory=list)
    request_questions_total: int = 0
    request_question_kinds: int = 0
    message: str = ""


class WorkingDaysResult(BaseModel):
    date_from: date
    days: int
    hours: int = 0
    result: datetime | None = None
    result_date: date | None = None


class CitizenRequestStatus(BaseModel):
    """Статус обращения для сотрудника. Контакты заявителя (адрес, телефон, email, ПИН) сюда не попадают."""

    id: int
    name: str = ""
    registration_number: str | None = None
    registration_date: date | None = None
    request_date: date | None = None
    applicant: str | None = None
    request_type: str | None = None
    receipt_form: str | None = None
    is_repeated: bool = False
    questions: list[dict[str, Any]] = Field(default_factory=list)
    assignee: str | None = None
    execution_state: str | None = None
    deadline: date | None = None
    initial_deadline: date | None = None
    days_left: int | None = None
    overdue: bool = False
    prolonged: bool = False
    prolongations: list[dict[str, Any]] = Field(default_factory=list)
    answered: bool = False
    response_date: date | None = None
    answer_letter: dict[str, Any] | None = None
    transferred_to: str | None = None
    status_text: str = ""
    url: str | None = None


class CitizenRequestAnalytics(BaseModel):
    """Счёт по ТОТК. requests — обращений в группе; questions — вопросов (в одном обращении их может быть несколько)."""

    level: str
    level_label: str
    date_from: date | None = None
    date_to: date | None = None
    classifier_code: str | None = None
    requests_total: int = 0
    requests_classified: int = 0
    requests_unclassified: int = 0
    question_entries_total: int = 0
    groups: list[dict[str, Any]] = Field(default_factory=list)
    groups_total: int = 0
    review_results: list[dict[str, Any]] = Field(default_factory=list)
    truncated: bool = False


class ClassifierMatch(BaseModel):
    level: str
    level_label: str
    code: str
    name: str
    path: str = ""


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
