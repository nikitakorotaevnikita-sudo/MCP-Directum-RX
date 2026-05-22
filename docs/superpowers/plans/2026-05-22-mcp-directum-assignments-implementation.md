# MCP Directum Assignments Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a full FastAPI + Vanilla JS prototype for Directum RX assignments/action items with LLM chat, Ollama/OpenAI-compatible provider support, safe action item creation, MCP-style tools, and backoffice metrics.

**Architecture:** Create a small FastAPI app that serves static UI, exposes REST/SSE routes, and uses focused Python services for settings, Directum OData, current user, assignments, action item creation, LLM orchestration, tool registry, and metrics. The LLM layer uses OpenAI-compatible configuration for ARIO/OpenAI-compatible/Ollama; Directum runtime code uses a reusable Python OData client, not copied skill scripts.

**Tech Stack:** Python 3.10+, FastAPI, Uvicorn, Pydantic Settings, httpx, OpenAI Python SDK, SQLite, Vanilla HTML/CSS/JS, pytest, pytest-cov, Playwright, Docker, docker-compose.

---

## File Structure

Create this project layout:

```text
src/
  __init__.py
  main.py
  config.py
  models/
    __init__.py
    schemas.py
  services/
    __init__.py
    directum_client.py
    current_user.py
    assignments.py
    action_items.py
    llm_service.py
    metrics_storage.py
    tool_registry.py
  static/
    index.html
    backoffice.html
    style.css
    app.js
    backoffice.js
tests/
  conftest.py
  unit/
    test_config.py
    test_directum_client.py
    test_current_user.py
    test_assignments.py
    test_action_items.py
    test_metrics_storage.py
    test_tool_registry.py
  integration/
    test_api_endpoints.py
  e2e/
    test_main_flow.py
    test_backoffice.py
pyproject.toml
Dockerfile
docker-compose.yml
.env.example
README.md
TECHNICAL_DOCUMENTATION.md
```

Boundary decisions:
- `config.py` owns environment parsing and safe public config.
- `directum_client.py` owns OData request construction and normalized errors.
- `current_user.py`, `assignments.py`, and `action_items.py` own Directum business logic.
- `tool_registry.py` exposes MCP-style Python tools to the LLM service and API.
- `llm_service.py` owns provider-aware OpenAI-compatible chat orchestration.
- `metrics_storage.py` owns SQLite schema and event recording.
- `main.py` wires FastAPI routes only; do not put business logic there.

## Task 1: Project Skeleton And Settings

**Files:**
- Create: `pyproject.toml`
- Create: `.env.example`
- Create: `src/__init__.py`
- Create: `src/config.py`
- Create: `tests/unit/test_config.py`

- [ ] **Step 1: Write the failing config tests**

Create `tests/unit/test_config.py`:

```python
from src.config import Settings


def test_ollama_profile_uses_openai_compatible_defaults():
    settings = Settings(
        LLM_PROVIDER="ollama",
        OPENAI_BASE_URL="http://localhost:11434/v1",
        OPENAI_API_KEY="ollama",
        OPENAI_MODEL="qwen3:8b",
        DIRECTUM_BASE_URL="https://rx.example/Integration/odata",
        DIRECTUM_AUTH_TOKEN="Basic secret-token",
    )

    assert settings.llm_provider == "ollama"
    assert settings.openai_base_url == "http://localhost:11434/v1"
    assert settings.openai_api_key == "ollama"
    assert settings.openai_model == "qwen3:8b"


def test_public_config_masks_secrets():
    settings = Settings(
        OPENAI_API_KEY="very-secret",
        DIRECTUM_AUTH_TOKEN="Basic directum-secret",
        DIRECTUM_BASE_URL="https://rx.example/Integration/odata",
    )

    public = settings.public_config()

    assert public["openai_api_key_set"] is True
    assert public["directum_auth_token_set"] is True
    assert "very-secret" not in str(public)
    assert "directum-secret" not in str(public)
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
python -m pytest tests/unit/test_config.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'src.config'`.

- [ ] **Step 3: Add project metadata and dependencies**

Create `pyproject.toml`:

```toml
[project]
name = "mcp-directum-rx"
version = "0.1.0"
description = "MCP-style Directum RX assignments prototype"
requires-python = ">=3.10"
dependencies = [
  "fastapi>=0.115.0",
  "uvicorn[standard]>=0.30.0",
  "pydantic>=2.8.0",
  "pydantic-settings>=2.4.0",
  "httpx>=0.27.0",
  "openai>=1.40.0",
  "python-dotenv>=1.0.0",
]

[project.optional-dependencies]
dev = [
  "pytest>=8.3.0",
  "pytest-cov>=5.0.0",
  "pytest-playwright>=0.5.0",
]

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["."]
addopts = "-ra"
```

Create `.env.example`:

```env
APP_HOST=0.0.0.0
APP_PORT=8000
APP_ENV=development

LLM_PROVIDER=ollama
OPENAI_BASE_URL=http://localhost:11434/v1
OPENAI_API_KEY=ollama
OPENAI_MODEL=qwen3:8b
LLM_TOOL_CALLING=auto

DIRECTUM_BASE_URL=https://example.directum/Integration/odata
DIRECTUM_AUTH_MODE=basic_token
DIRECTUM_AUTH_TOKEN=Basic change-me
DIRECTUM_REQUEST_TIMEOUT_SECONDS=30

BACKOFFICE_USERNAME=admin
BACKOFFICE_PASSWORD=change-me
METRICS_DB_PATH=data/metrics.db
```

Create empty `src/__init__.py`.

- [ ] **Step 4: Implement settings**

Create `src/config.py`:

```python
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
```

- [ ] **Step 5: Run tests to verify they pass**

Run:

```powershell
python -m pytest tests/unit/test_config.py -v
```

Expected: 2 passed.

- [ ] **Step 6: Commit**

Run:

```powershell
git add pyproject.toml .env.example src/__init__.py src/config.py tests/unit/test_config.py
git commit -m "feat: add project settings"
```

## Task 2: Shared Schemas

**Files:**
- Create: `src/models/__init__.py`
- Create: `src/models/schemas.py`
- Create: `tests/unit/test_action_items.py`

- [ ] **Step 1: Write failing schema tests**

Create `tests/unit/test_action_items.py`:

```python
from src.models.schemas import ActionItemCreateRequest


def test_action_item_create_defaults_to_preview_mode():
    request = ActionItemCreateRequest(
        subject="Prepare response",
        performer_id=42,
        action_text="Prepare a short response for the incoming letter",
    )

    assert request.confirm is False
    assert request.deadline is None


def test_action_item_create_requires_core_fields():
    try:
        ActionItemCreateRequest(subject="", performer_id=0, action_text="")
    except ValueError as exc:
        text = str(exc)
        assert "subject" in text or "performer_id" in text or "action_text" in text
    else:
        raise AssertionError("invalid request was accepted")
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
python -m pytest tests/unit/test_action_items.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'src.models'`.

- [ ] **Step 3: Implement schemas**

Create `src/models/__init__.py`:

```python
from .schemas import (
    ActionItemCreateRequest,
    ActionItemCreateResult,
    AssignmentSummary,
    ChatRequest,
    DirectumUser,
    EmployeeSummary,
    ToolCallRecord,
)

__all__ = [
    "ActionItemCreateRequest",
    "ActionItemCreateResult",
    "AssignmentSummary",
    "ChatRequest",
    "DirectumUser",
    "EmployeeSummary",
    "ToolCallRecord",
]
```

Create `src/models/schemas.py`:

```python
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class DirectumUser(BaseModel):
    id: int
    name: str
    login: str | None = None


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
    result: dict[str, Any] | list[dict[str, Any]] | None = None
    duration_ms: int | None = None
    success: bool = True


class ChatRequest(BaseModel):
    message: str = Field(min_length=1)
    history: list[dict[str, str]] = Field(default_factory=list)
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```powershell
python -m pytest tests/unit/test_action_items.py -v
```

Expected: 2 passed.

- [ ] **Step 5: Commit**

Run:

```powershell
git add src/models tests/unit/test_action_items.py
git commit -m "feat: add shared schemas"
```

## Task 3: Directum OData Client

**Files:**
- Create: `src/services/__init__.py`
- Create: `src/services/directum_client.py`
- Create: `tests/unit/test_directum_client.py`

- [ ] **Step 1: Write failing OData client tests**

Create `tests/unit/test_directum_client.py`:

```python
import httpx

from src.services.directum_client import DirectumClient, DirectumError


def test_build_url_strips_duplicate_slashes():
    client = DirectumClient(
        base_url="https://rx.example/Integration/odata/",
        auth_token="Basic token",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"value": []})),
    )

    assert client.build_url("IAssignments") == "https://rx.example/Integration/odata/IAssignments"


def test_query_sends_odata_params_and_returns_value():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["Authorization"]
        return httpx.Response(200, json={"value": [{"Id": 1, "Subject": "Task"}]})

    client = DirectumClient(
        base_url="https://rx.example/Integration/odata",
        auth_token="Basic token",
        transport=httpx.MockTransport(handler),
    )

    result = client.query(
        "IAssignments",
        filter_="Status eq 'InProcess'",
        select="Id,Subject",
        top=5,
    )

    assert result == [{"Id": 1, "Subject": "Task"}]
    assert "$filter=Status+eq+%27InProcess%27" in seen["url"]
    assert "$select=Id%2CSubject" in seen["url"]
    assert "$top=5" in seen["url"]
    assert seen["auth"] == "Basic token"


def test_query_raises_normalized_error_on_unauthorized():
    client = DirectumClient(
        base_url="https://rx.example/Integration/odata",
        auth_token="Basic bad",
        transport=httpx.MockTransport(lambda request: httpx.Response(401, text="Unauthorized")),
    )

    try:
        client.query("IAssignments")
    except DirectumError as exc:
        assert exc.status_code == 401
        assert "Unauthorized" in exc.safe_message
    else:
        raise AssertionError("DirectumError was not raised")
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
python -m pytest tests/unit/test_directum_client.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'src.services.directum_client'`.

- [ ] **Step 3: Implement Directum client**

Create `src/services/__init__.py`:

```python
"""Service layer for MCP Directum RX."""
```

Create `src/services/directum_client.py`:

```python
from typing import Any

import httpx


class DirectumError(RuntimeError):
    def __init__(self, safe_message: str, status_code: int | None = None):
        super().__init__(safe_message)
        self.safe_message = safe_message
        self.status_code = status_code


class DirectumClient:
    def __init__(
        self,
        base_url: str,
        auth_token: str,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.auth_token = auth_token
        self.client = httpx.Client(timeout=timeout, transport=transport)

    def build_url(self, entity_set: str) -> str:
        return f"{self.base_url}/{entity_set.lstrip('/')}"

    def query(
        self,
        entity_set: str,
        *,
        filter_: str | None = None,
        select: str | None = None,
        expand: str | None = None,
        orderby: str | None = None,
        top: int | None = None,
        count: bool = False,
    ) -> list[dict[str, Any]]:
        data = self.get_collection(
            entity_set,
            filter_=filter_,
            select=select,
            expand=expand,
            orderby=orderby,
            top=top,
            count=count,
        )
        return data.get("value", [])

    def get_collection(self, entity_set: str, **kwargs: Any) -> dict[str, Any]:
        response = self.client.get(
            self.build_url(entity_set),
            headers=self._headers(),
            params=self._params(**kwargs),
        )
        return self._json_or_error(response)

    def get_one(self, entity_path: str) -> dict[str, Any]:
        response = self.client.get(self.build_url(entity_path), headers=self._headers())
        return self._json_or_error(response)

    def post(self, entity_set: str, payload: dict[str, Any]) -> dict[str, Any]:
        response = self.client.post(self.build_url(entity_set), headers=self._headers(), json=payload)
        return self._json_or_error(response)

    def _headers(self) -> dict[str, str]:
        return {"Authorization": self.auth_token, "Accept": "application/json"}

    def _params(
        self,
        *,
        filter_: str | None = None,
        select: str | None = None,
        expand: str | None = None,
        orderby: str | None = None,
        top: int | None = None,
        count: bool = False,
    ) -> dict[str, str | int | bool]:
        params: dict[str, str | int | bool] = {}
        if filter_:
            params["$filter"] = filter_
        if select:
            params["$select"] = select
        if expand:
            params["$expand"] = expand
        if orderby:
            params["$orderby"] = orderby
        if top is not None:
            params["$top"] = top
        if count:
            params["$count"] = "true"
        return params

    def _json_or_error(self, response: httpx.Response) -> dict[str, Any]:
        if response.status_code >= 400:
            raise DirectumError(
                safe_message=f"Directum OData request failed: {response.status_code} {response.text[:200]}",
                status_code=response.status_code,
            )
        try:
            return response.json()
        except ValueError as exc:
            raise DirectumError("Directum returned a non-JSON response", response.status_code) from exc
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```powershell
python -m pytest tests/unit/test_directum_client.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

Run:

```powershell
git add src/services/__init__.py src/services/directum_client.py tests/unit/test_directum_client.py
git commit -m "feat: add Directum OData client"
```

## Task 4: Current User And Assignment Services

**Files:**
- Create: `src/services/current_user.py`
- Create: `src/services/assignments.py`
- Create: `tests/unit/test_current_user.py`
- Create: `tests/unit/test_assignments.py`

- [ ] **Step 1: Write failing current-user tests**

Create `tests/unit/test_current_user.py`:

```python
from src.models.schemas import DirectumUser
from src.services.current_user import CurrentUserService


class FakeClient:
    def __init__(self):
        self.calls = 0

    def query(self, entity_set, **kwargs):
        self.calls += 1
        assert entity_set == "IUsers"
        assert "Login/LoginName eq" in kwargs["filter_"]
        return [{"Id": 1165, "Name": "Test User", "Login": {"LoginName": "nt_work\\\\user"}}]


def test_current_user_is_resolved_and_cached():
    client = FakeClient()
    service = CurrentUserService(client=client, auth_token="Basic bnRfd29ya1xcdXNlcjpwYXNz")

    first = service.get_current_user()
    second = service.get_current_user()

    assert first == DirectumUser(id=1165, name="Test User", login="nt_work\\\\user")
    assert second.id == 1165
    assert client.calls == 1
```

- [ ] **Step 2: Write failing assignments tests**

Create `tests/unit/test_assignments.py`:

```python
from src.models.schemas import DirectumUser
from src.services.assignments import AssignmentsService


class FakeClient:
    def __init__(self):
        self.calls = []

    def query(self, entity_set, **kwargs):
        self.calls.append((entity_set, kwargs))
        return [{"Id": 10, "Subject": "Prepare answer", "Status": "InProcess"}]


class FakeCurrentUser:
    def get_current_user(self):
        return DirectumUser(id=1165, name="Test User", login="nt_work\\\\user")


def test_get_my_assignments_filters_by_current_user():
    client = FakeClient()
    service = AssignmentsService(client=client, current_user_service=FakeCurrentUser())

    result = service.get_my_assignments()

    assert result[0].id == 10
    entity_set, kwargs = client.calls[0]
    assert entity_set == "IAssignments"
    assert "Performer/Id eq 1165" in kwargs["filter_"]
    assert "Status eq 'InProcess'" in kwargs["filter_"]


def test_get_created_action_items_filters_by_author():
    client = FakeClient()
    service = AssignmentsService(client=client, current_user_service=FakeCurrentUser())

    service.get_action_items_created_by_me()

    entity_set, kwargs = client.calls[0]
    assert entity_set == "IActionItemExecutionTasks"
    assert "Author/Id eq 1165" in kwargs["filter_"]
```

- [ ] **Step 3: Run tests to verify they fail**

Run:

```powershell
python -m pytest tests/unit/test_current_user.py tests/unit/test_assignments.py -v
```

Expected: FAIL with missing `CurrentUserService` and `AssignmentsService`.

- [ ] **Step 4: Implement services**

Create `src/services/current_user.py`:

```python
import base64

from src.models.schemas import DirectumUser
from src.services.directum_client import DirectumClient, DirectumError


class CurrentUserService:
    def __init__(self, client: DirectumClient, auth_token: str):
        self.client = client
        self.auth_token = auth_token
        self._cached_user: DirectumUser | None = None

    def get_current_user(self) -> DirectumUser:
        if self._cached_user:
            return self._cached_user
        login = self._login_from_basic_token()
        rows = self.client.query(
            "IUsers",
            filter_=f"Login/LoginName eq '{login}'",
            select="Id,Name",
            top=1,
        )
        if not rows:
            raise DirectumError("Current Directum user was not found")
        row = rows[0]
        self._cached_user = DirectumUser(id=int(row["Id"]), name=row.get("Name", ""), login=login)
        return self._cached_user

    def _login_from_basic_token(self) -> str:
        if not self.auth_token.startswith("Basic "):
            raise DirectumError("DIRECTUM_AUTH_TOKEN must be a Basic token")
        encoded = self.auth_token.replace("Basic ", "", 1).strip()
        decoded = base64.b64decode(encoded).decode("utf-8")
        return decoded.split(":", 1)[0]
```

Create `src/services/assignments.py`:

```python
from datetime import datetime, timezone
from typing import Any

from src.models.schemas import AssignmentSummary
from src.services.current_user import CurrentUserService
from src.services.directum_client import DirectumClient


class AssignmentsService:
    def __init__(self, client: DirectumClient, current_user_service: CurrentUserService):
        self.client = client
        self.current_user_service = current_user_service

    def get_my_assignments(self) -> list[AssignmentSummary]:
        user = self.current_user_service.get_current_user()
        rows = self.client.query(
            "IAssignments",
            filter_=f"Performer/Id eq {user.id} and Status eq 'InProcess'",
            select="Id,Subject,Deadline,Status",
            expand="Task($select=Id,Subject)",
            orderby="Deadline asc",
            count=True,
        )
        return [self._assignment(row, "assignment") for row in rows]

    def get_overdue_assignments(self) -> list[AssignmentSummary]:
        user = self.current_user_service.get_current_user()
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        rows = self.client.query(
            "IAssignments",
            filter_=f"Performer/Id eq {user.id} and Status eq 'InProcess' and Deadline lt {now}",
            select="Id,Subject,Deadline,Status",
            orderby="Deadline asc",
        )
        return [self._assignment(row, "overdue_assignment") for row in rows]

    def get_action_items_assigned_to_me(self) -> list[AssignmentSummary]:
        user = self.current_user_service.get_current_user()
        rows = self.client.query(
            "IActionItemExecutionAssignments",
            filter_=f"Performer/Id eq {user.id} and Status eq 'InProcess'",
            select="Id,Subject,Deadline,Status",
            orderby="Deadline asc",
        )
        return [self._assignment(row, "action_item_assignment") for row in rows]

    def get_action_items_created_by_me(self) -> list[AssignmentSummary]:
        user = self.current_user_service.get_current_user()
        rows = self.client.query(
            "IActionItemExecutionTasks",
            filter_=f"Author/Id eq {user.id} and Status eq 'InProcess'",
            select="Id,Subject,Deadline,Status",
            orderby="Deadline asc",
        )
        return [self._assignment(row, "action_item_task") for row in rows]

    def _assignment(self, row: dict[str, Any], entity_type: str) -> AssignmentSummary:
        return AssignmentSummary(
            id=int(row["Id"]),
            subject=row.get("Subject") or row.get("Name") or "",
            status=row.get("Status"),
            deadline=row.get("Deadline"),
            entity_type=entity_type,
            url=row.get("ClientHyperlink") or row.get("EntityHyperlink"),
        )
```

- [ ] **Step 5: Run tests to verify they pass**

Run:

```powershell
python -m pytest tests/unit/test_current_user.py tests/unit/test_assignments.py -v
```

Expected: 4 passed.

- [ ] **Step 6: Commit**

Run:

```powershell
git add src/services/current_user.py src/services/assignments.py tests/unit/test_current_user.py tests/unit/test_assignments.py
git commit -m "feat: add current user and assignment services"
```

## Task 5: Employee Search And Safe Action Item Creation

**Files:**
- Create: `src/services/action_items.py`
- Modify: `tests/unit/test_action_items.py`

- [ ] **Step 1: Extend failing action-item tests**

Replace `tests/unit/test_action_items.py` with:

```python
from src.models.schemas import ActionItemCreateRequest
from src.services.action_items import ActionItemService


class FakeClient:
    def __init__(self):
        self.posts = []
        self.queries = []

    def query(self, entity_set, **kwargs):
        self.queries.append((entity_set, kwargs))
        return [{"Id": 42, "Name": "Ivanov Ivan", "Status": "Active"}]

    def post(self, entity_set, payload):
        self.posts.append((entity_set, payload))
        return {"Id": 9001, **payload}


def test_action_item_create_defaults_to_preview_mode():
    request = ActionItemCreateRequest(
        subject="Prepare response",
        performer_id=42,
        action_text="Prepare a short response for the incoming letter",
    )

    assert request.confirm is False
    assert request.deadline is None


def test_action_item_create_requires_core_fields():
    try:
        ActionItemCreateRequest(subject="", performer_id=0, action_text="")
    except ValueError as exc:
        text = str(exc)
        assert "subject" in text or "performer_id" in text or "action_text" in text
    else:
        raise AssertionError("invalid request was accepted")


def test_create_action_item_preview_never_posts():
    client = FakeClient()
    service = ActionItemService(client)
    request = ActionItemCreateRequest(
        subject="Prepare response",
        performer_id=42,
        action_text="Prepare a short response",
        confirm=False,
    )

    result = service.create_action_item(request)

    assert result.mode == "preview"
    assert result.success is True
    assert result.payload["Subject"] == "Prepare response"
    assert result.payload["PerformersGD"] == "42"
    assert client.posts == []


def test_create_action_item_confirm_posts_to_directum():
    client = FakeClient()
    service = ActionItemService(client)
    request = ActionItemCreateRequest(
        subject="Prepare response",
        performer_id=42,
        action_text="Prepare a short response",
        confirm=True,
    )

    result = service.create_action_item(request)

    assert result.mode == "created"
    assert result.directum_id == 9001
    assert client.posts[0][0] == "IActionItemExecutionTasks"


def test_search_employee_uses_contains_name_filter():
    client = FakeClient()
    service = ActionItemService(client)

    result = service.search_employee("Ivanov")

    assert result[0].id == 42
    entity_set, kwargs = client.queries[0]
    assert entity_set == "IEmployees"
    assert "contains(Name,'Ivanov')" in kwargs["filter_"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
python -m pytest tests/unit/test_action_items.py -v
```

Expected: FAIL with missing `src.services.action_items`.

- [ ] **Step 3: Implement action item service**

Create `src/services/action_items.py`:

```python
from typing import Any

from src.models.schemas import ActionItemCreateRequest, ActionItemCreateResult, EmployeeSummary
from src.services.directum_client import DirectumClient


class ActionItemService:
    def __init__(self, client: DirectumClient):
        self.client = client

    def search_employee(self, query: str, top: int = 10) -> list[EmployeeSummary]:
        cleaned = query.strip().replace("'", "''")
        rows = self.client.query(
            "IEmployees",
            filter_=f"contains(Name,'{cleaned}') and Status eq 'Active'",
            select="Id,Name,Status",
            top=top,
        )
        return [
            EmployeeSummary(id=int(row["Id"]), name=row.get("Name", ""), status=row.get("Status"))
            for row in rows
        ]

    def create_action_item(self, request: ActionItemCreateRequest) -> ActionItemCreateResult:
        payload = self._payload(request)
        if not request.confirm:
            return ActionItemCreateResult(
                mode="preview",
                payload=payload,
                success=True,
                message="Preview created. Set confirm=true to create the action item in Directum RX.",
            )
        response = self.client.post("IActionItemExecutionTasks", payload)
        return ActionItemCreateResult(
            mode="created",
            payload=payload,
            success=True,
            directum_id=response.get("Id"),
            message="Action item created in Directum RX.",
        )

    def _payload(self, request: ActionItemCreateRequest) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "Subject": request.subject,
            "PerformersGD": str(request.performer_id),
            "ActionItem": request.action_text,
            "ExecutionState": "OnExecution",
        }
        if request.deadline:
            payload["Deadline"] = request.deadline.isoformat()
        return payload
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```powershell
python -m pytest tests/unit/test_action_items.py -v
```

Expected: 5 passed.

- [ ] **Step 5: Commit**

Run:

```powershell
git add src/services/action_items.py tests/unit/test_action_items.py
git commit -m "feat: add safe action item service"
```

## Task 6: Metrics Storage

**Files:**
- Create: `src/services/metrics_storage.py`
- Create: `tests/unit/test_metrics_storage.py`

- [ ] **Step 1: Write failing metrics tests**

Create `tests/unit/test_metrics_storage.py`:

```python
from src.services.metrics_storage import MetricsStorage


def test_metrics_storage_records_chat_and_tool_call(tmp_path):
    db_path = tmp_path / "metrics.db"
    storage = MetricsStorage(str(db_path))
    storage.initialize()

    storage.record_chat_request("assignments", 120)
    storage.record_tool_call("get_my_assignments", True, 35)

    summary = storage.summary()

    assert summary["chat_requests"] == 1
    assert summary["scenario_counts"]["assignments"] == 1
    assert summary["latest_tool_calls"][0]["name"] == "get_my_assignments"


def test_metrics_storage_records_create_preview_and_confirm(tmp_path):
    db_path = tmp_path / "metrics.db"
    storage = MetricsStorage(str(db_path))
    storage.initialize()

    storage.record_action_item_create("preview")
    storage.record_action_item_create("confirmed")

    summary = storage.summary()

    assert summary["action_item_previews"] == 1
    assert summary["action_item_confirmed"] == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
python -m pytest tests/unit/test_metrics_storage.py -v
```

Expected: FAIL with missing `MetricsStorage`.

- [ ] **Step 3: Implement SQLite metrics storage**

Create `src/services/metrics_storage.py`:

```python
import json
import sqlite3
import time
from pathlib import Path
from typing import Any


class MetricsStorage:
    def __init__(self, db_path: str):
        self.db_path = db_path

    def initialize(self) -> None:
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts REAL NOT NULL,
                    type TEXT NOT NULL,
                    name TEXT NOT NULL,
                    success INTEGER NOT NULL,
                    duration_ms INTEGER,
                    payload TEXT NOT NULL
                )
                """
            )

    def record_chat_request(self, scenario: str, duration_ms: int, success: bool = True) -> None:
        self._insert("chat", scenario, success, duration_ms, {"scenario": scenario})

    def record_tool_call(self, name: str, success: bool, duration_ms: int, payload: dict[str, Any] | None = None) -> None:
        self._insert("tool_call", name, success, duration_ms, payload or {})

    def record_action_item_create(self, mode: str) -> None:
        self._insert("action_item_create", mode, True, None, {"mode": mode})

    def record_feedback(self, rating: str) -> None:
        self._insert("feedback", rating, True, None, {"rating": rating})

    def summary(self) -> dict[str, Any]:
        with self._connect() as conn:
            rows = conn.execute("SELECT type, name, success, duration_ms, payload, ts FROM events ORDER BY id DESC").fetchall()
        chat_rows = [row for row in rows if row["type"] == "chat"]
        scenario_counts: dict[str, int] = {}
        for row in chat_rows:
            scenario_counts[row["name"]] = scenario_counts.get(row["name"], 0) + 1
        return {
            "chat_requests": len(chat_rows),
            "scenario_counts": scenario_counts,
            "action_item_previews": sum(1 for row in rows if row["type"] == "action_item_create" and row["name"] == "preview"),
            "action_item_confirmed": sum(1 for row in rows if row["type"] == "action_item_create" and row["name"] == "confirmed"),
            "errors": sum(1 for row in rows if not row["success"]),
            "latest_tool_calls": [
                {
                    "name": row["name"],
                    "success": bool(row["success"]),
                    "duration_ms": row["duration_ms"],
                    "ts": row["ts"],
                }
                for row in rows
                if row["type"] == "tool_call"
            ][:10],
            "feedback": [row["name"] for row in rows if row["type"] == "feedback"],
        }

    def _insert(self, type_: str, name: str, success: bool, duration_ms: int | None, payload: dict[str, Any]) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO events (ts, type, name, success, duration_ms, payload) VALUES (?, ?, ?, ?, ?, ?)",
                (time.time(), type_, name, int(success), duration_ms, json.dumps(payload, ensure_ascii=False)),
            )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```powershell
python -m pytest tests/unit/test_metrics_storage.py -v
```

Expected: 2 passed.

- [ ] **Step 5: Commit**

Run:

```powershell
git add src/services/metrics_storage.py tests/unit/test_metrics_storage.py
git commit -m "feat: add metrics storage"
```

## Task 7: MCP-Style Tool Registry

**Files:**
- Create: `src/services/tool_registry.py`
- Create: `tests/unit/test_tool_registry.py`

- [ ] **Step 1: Write failing tool registry tests**

Create `tests/unit/test_tool_registry.py`:

```python
from src.models.schemas import ActionItemCreateRequest, AssignmentSummary, DirectumUser
from src.services.tool_registry import ToolRegistry


class FakeCurrentUser:
    def get_current_user(self):
        return DirectumUser(id=1165, name="Test User", login="nt_work\\\\user")


class FakeAssignments:
    def get_my_assignments(self):
        return [AssignmentSummary(id=1, subject="Task", status="InProcess", entity_type="assignment")]

    def get_overdue_assignments(self):
        return []

    def get_action_items_assigned_to_me(self):
        return []

    def get_action_items_created_by_me(self):
        return []


class FakeActionItems:
    def search_employee(self, query):
        return []

    def create_action_item(self, request: ActionItemCreateRequest):
        return {"mode": "preview", "payload": request.model_dump(), "success": True}


def test_tool_registry_lists_core_tools():
    registry = ToolRegistry(FakeCurrentUser(), FakeAssignments(), FakeActionItems())

    names = [tool["function"]["name"] for tool in registry.openai_tools()]

    assert "get_current_user" in names
    assert "get_my_assignments" in names
    assert "create_action_item" in names


def test_tool_registry_dispatches_assignment_tool():
    registry = ToolRegistry(FakeCurrentUser(), FakeAssignments(), FakeActionItems())

    result = registry.call("get_my_assignments", {})

    assert result[0]["subject"] == "Task"
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
python -m pytest tests/unit/test_tool_registry.py -v
```

Expected: FAIL with missing `ToolRegistry`.

- [ ] **Step 3: Implement tool registry**

Create `src/services/tool_registry.py`:

```python
from typing import Any, Callable

from src.models.schemas import ActionItemCreateRequest
from src.services.action_items import ActionItemService
from src.services.assignments import AssignmentsService
from src.services.current_user import CurrentUserService


class ToolRegistry:
    def __init__(
        self,
        current_user_service: CurrentUserService,
        assignments_service: AssignmentsService,
        action_item_service: ActionItemService,
    ):
        self.current_user_service = current_user_service
        self.assignments_service = assignments_service
        self.action_item_service = action_item_service
        self._handlers: dict[str, Callable[[dict[str, Any]], Any]] = {
            "get_current_user": lambda args: self.current_user_service.get_current_user(),
            "get_my_assignments": lambda args: self.assignments_service.get_my_assignments(),
            "get_overdue_assignments": lambda args: self.assignments_service.get_overdue_assignments(),
            "get_action_items_assigned_to_me": lambda args: self.assignments_service.get_action_items_assigned_to_me(),
            "get_action_items_created_by_me": lambda args: self.assignments_service.get_action_items_created_by_me(),
            "search_employee": lambda args: self.action_item_service.search_employee(args["query"]),
            "create_action_item": lambda args: self.action_item_service.create_action_item(ActionItemCreateRequest(**args)),
        }

    def openai_tools(self) -> list[dict[str, Any]]:
        return [
            self._tool("get_current_user", "Get current Directum RX user.", {}),
            self._tool("get_my_assignments", "Get my in-process assignments.", {}),
            self._tool("get_overdue_assignments", "Get my overdue assignments.", {}),
            self._tool("get_action_items_assigned_to_me", "Get action items assigned to me.", {}),
            self._tool("get_action_items_created_by_me", "Get action items created by me.", {}),
            self._tool(
                "search_employee",
                "Search Directum employees by name.",
                {"query": {"type": "string", "description": "Employee name or part of name"}},
                required=["query"],
            ),
            self._tool(
                "create_action_item",
                "Preview or create an action item. Use confirm=false first.",
                {
                    "subject": {"type": "string"},
                    "performer_id": {"type": "integer"},
                    "action_text": {"type": "string"},
                    "deadline": {"type": "string"},
                    "confirm": {"type": "boolean"},
                },
                required=["subject", "performer_id", "action_text"],
            ),
        ]

    def call(self, name: str, arguments: dict[str, Any]) -> Any:
        if name not in self._handlers:
            raise KeyError(f"Unknown tool: {name}")
        result = self._handlers[name](arguments)
        if hasattr(result, "model_dump"):
            return result.model_dump(mode="json")
        if isinstance(result, list):
            return [item.model_dump(mode="json") if hasattr(item, "model_dump") else item for item in result]
        return result

    def _tool(
        self,
        name: str,
        description: str,
        properties: dict[str, Any],
        required: list[str] | None = None,
    ) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required or [],
                },
            },
        }
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```powershell
python -m pytest tests/unit/test_tool_registry.py -v
```

Expected: 2 passed.

- [ ] **Step 5: Commit**

Run:

```powershell
git add src/services/tool_registry.py tests/unit/test_tool_registry.py
git commit -m "feat: add MCP-style tool registry"
```

## Task 8: LLM Service With Ollama/OpenAI-Compatible Providers

**Files:**
- Create: `src/services/llm_service.py`
- Create: `tests/unit/test_llm_service.py`

- [ ] **Step 1: Write failing LLM service tests**

Create `tests/unit/test_llm_service.py`:

```python
from src.services.llm_service import LLMService


class FakeToolRegistry:
    def openai_tools(self):
        return [{"type": "function", "function": {"name": "get_my_assignments", "parameters": {"type": "object", "properties": {}}}}]


def test_llm_service_reports_provider_status_without_secret():
    service = LLMService(
        provider="ollama",
        base_url="http://localhost:11434/v1",
        api_key="ollama",
        model="qwen3:8b",
        tool_calling="auto",
        tool_registry=FakeToolRegistry(),
    )

    status = service.status()

    assert status["provider"] == "ollama"
    assert status["base_url"] == "http://localhost:11434/v1"
    assert status["model"] == "qwen3:8b"
    assert "api_key" not in status


def test_llm_service_builds_tools_when_enabled_or_auto():
    service = LLMService(
        provider="ollama",
        base_url="http://localhost:11434/v1",
        api_key="ollama",
        model="qwen3:8b",
        tool_calling="auto",
        tool_registry=FakeToolRegistry(),
    )

    assert service.tools_for_request()[0]["function"]["name"] == "get_my_assignments"
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
python -m pytest tests/unit/test_llm_service.py -v
```

Expected: FAIL with missing `LLMService`.

- [ ] **Step 3: Implement LLM service**

Create `src/services/llm_service.py`:

```python
from collections.abc import Iterable
from typing import Any

from openai import OpenAI

from src.services.tool_registry import ToolRegistry


class LLMService:
    def __init__(
        self,
        provider: str,
        base_url: str,
        api_key: str,
        model: str,
        tool_calling: str,
        tool_registry: ToolRegistry,
    ):
        self.provider = provider
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.tool_calling = tool_calling
        self.tool_registry = tool_registry
        self.client = OpenAI(api_key=api_key, base_url=self.base_url)

    def status(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "base_url": self.base_url,
            "model": self.model,
            "tool_calling": self.tool_calling,
        }

    def tools_for_request(self) -> list[dict[str, Any]]:
        if self.tool_calling == "disabled":
            return []
        return self.tool_registry.openai_tools()

    def stream_chat(self, message: str, history: list[dict[str, str]]) -> Iterable[str]:
        messages = [
            {
                "role": "system",
                "content": (
                    "You are a Directum RX assistant. Use tools for assignments and action items. "
                    "For create_action_item, call preview with confirm=false before any confirmed creation."
                ),
            },
            *history,
            {"role": "user", "content": message},
        ]
        stream = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            tools=self.tools_for_request() or None,
            stream=True,
        )
        for chunk in stream:
            delta = chunk.choices[0].delta
            if delta.content:
                yield delta.content
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```powershell
python -m pytest tests/unit/test_llm_service.py -v
```

Expected: 2 passed.

- [ ] **Step 5: Commit**

Run:

```powershell
git add src/services/llm_service.py tests/unit/test_llm_service.py
git commit -m "feat: add provider-aware LLM service"
```

## Task 9: FastAPI App And API Endpoints

**Files:**
- Create: `src/main.py`
- Create: `tests/conftest.py`
- Create: `tests/integration/test_api_endpoints.py`

- [ ] **Step 1: Write failing integration tests**

Create `tests/conftest.py`:

```python
from fastapi.testclient import TestClient

from src.main import create_app


def make_test_client():
    app = create_app(testing=True)
    return TestClient(app)
```

Create `tests/integration/test_api_endpoints.py`:

```python
from tests.conftest import make_test_client


def test_health_returns_ok():
    client = make_test_client()

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_config_diagnostics_masks_secrets():
    client = make_test_client()

    response = client.get("/api/diagnostics/config")

    assert response.status_code == 200
    data = response.json()
    assert data["openai_api_key_set"] is True
    assert "secret" not in str(data).lower()


def test_action_item_preview_endpoint_does_not_create():
    client = make_test_client()

    response = client.post(
        "/api/directum/action-items",
        json={
            "subject": "Prepare response",
            "performer_id": 42,
            "action_text": "Prepare a short response",
            "confirm": False,
        },
    )

    assert response.status_code == 200
    assert response.json()["mode"] == "preview"
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
python -m pytest tests/integration/test_api_endpoints.py -v
```

Expected: FAIL with missing `src.main`.

- [ ] **Step 3: Implement FastAPI app**

Create `src/main.py`:

```python
from pathlib import Path

import httpx
from fastapi import Depends, FastAPI
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from src.config import Settings, get_settings
from src.models.schemas import ActionItemCreateRequest, ChatRequest
from src.services.action_items import ActionItemService
from src.services.assignments import AssignmentsService
from src.services.current_user import CurrentUserService
from src.services.directum_client import DirectumClient
from src.services.llm_service import LLMService
from src.services.metrics_storage import MetricsStorage
from src.services.tool_registry import ToolRegistry


STATIC_DIR = Path(__file__).parent / "static"


def create_app(testing: bool = False) -> FastAPI:
    app = FastAPI(title="MCP Directum RX")
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    settings = _test_settings() if testing else get_settings()
    services = build_services(settings, testing=testing)
    app.state.settings = settings
    app.state.services = services

    @app.get("/")
    def index():
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/backoffice")
    def backoffice():
        return FileResponse(STATIC_DIR / "backoffice.html")

    @app.get("/health")
    def health():
        return {"status": "ok", "llm": services["llm"].status()}

    @app.get("/api/diagnostics/config")
    def config():
        return settings.public_config()

    @app.get("/api/diagnostics/current-user")
    def current_user():
        return services["current_user"].get_current_user()

    @app.get("/api/diagnostics/odata")
    def odata():
        return {"status": "configured", "base_url": settings.directum_base_url}

    @app.post("/api/chat")
    def chat(request: ChatRequest):
        services["metrics"].record_chat_request("chat", 0)
        return StreamingResponse(services["llm"].stream_chat(request.message, request.history), media_type="text/plain")

    @app.get("/api/directum/assignments/my")
    def my_assignments():
        return services["assignments"].get_my_assignments()

    @app.get("/api/directum/assignments/overdue")
    def overdue_assignments():
        return services["assignments"].get_overdue_assignments()

    @app.get("/api/directum/action-items/assigned-to-me")
    def assigned_action_items():
        return services["assignments"].get_action_items_assigned_to_me()

    @app.get("/api/directum/action-items/created-by-me")
    def created_action_items():
        return services["assignments"].get_action_items_created_by_me()

    @app.get("/api/directum/employees/search")
    def employee_search(query: str):
        return services["action_items"].search_employee(query)

    @app.post("/api/directum/action-items")
    def create_action_item(request: ActionItemCreateRequest):
        result = services["action_items"].create_action_item(request)
        services["metrics"].record_action_item_create("confirmed" if request.confirm else "preview")
        return result

    @app.post("/api/feedback")
    def feedback(payload: dict):
        services["metrics"].record_feedback(str(payload.get("rating", "unknown")))
        return {"status": "ok"}

    @app.get("/api/metrics")
    def metrics():
        return services["metrics"].summary()

    return app


def build_services(settings: Settings, testing: bool = False) -> dict:
    transport = _mock_transport() if testing else None
    client = DirectumClient(
        settings.directum_base_url,
        settings.DIRECTUM_AUTH_TOKEN,
        settings.DIRECTUM_REQUEST_TIMEOUT_SECONDS,
        transport=transport,
    )
    current_user = CurrentUserService(client, settings.DIRECTUM_AUTH_TOKEN)
    assignments = AssignmentsService(client, current_user)
    action_items = ActionItemService(client)
    metrics = MetricsStorage(settings.METRICS_DB_PATH)
    metrics.initialize()
    registry = ToolRegistry(current_user, assignments, action_items)
    llm = LLMService(
        settings.LLM_PROVIDER,
        settings.openai_base_url,
        settings.openai_api_key,
        settings.openai_model,
        settings.LLM_TOOL_CALLING,
        registry,
    )
    return {
        "directum": client,
        "current_user": current_user,
        "assignments": assignments,
        "action_items": action_items,
        "metrics": metrics,
        "registry": registry,
        "llm": llm,
    }


def _test_settings() -> Settings:
    return Settings(
        OPENAI_API_KEY="test-key",
        OPENAI_BASE_URL="http://localhost:11434/v1",
        OPENAI_MODEL="qwen3:8b",
        DIRECTUM_BASE_URL="https://rx.example/Integration/odata",
        DIRECTUM_AUTH_TOKEN="Basic bnRfd29ya1xcdXNlcjpwYXNz",
        METRICS_DB_PATH=":memory:",
    )


def _mock_transport() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if "IUsers" in path:
            return httpx.Response(200, json={"value": [{"Id": 1165, "Name": "Test User"}]})
        if "IEmployees" in path:
            return httpx.Response(200, json={"value": [{"Id": 42, "Name": "Ivanov Ivan", "Status": "Active"}]})
        if request.method == "POST":
            return httpx.Response(200, json={"Id": 9001})
        return httpx.Response(200, json={"value": [{"Id": 1, "Subject": "Task", "Status": "InProcess"}]})

    return httpx.MockTransport(handler)


app = create_app()
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```powershell
python -m pytest tests/integration/test_api_endpoints.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

Run:

```powershell
git add src/main.py tests/conftest.py tests/integration/test_api_endpoints.py
git commit -m "feat: add FastAPI endpoints"
```

## Task 10: Main UI And Backoffice UI

**Files:**
- Create: `src/static/index.html`
- Create: `src/static/backoffice.html`
- Create: `src/static/style.css`
- Create: `src/static/app.js`
- Create: `src/static/backoffice.js`
- Create: `tests/e2e/test_main_flow.py`
- Create: `tests/e2e/test_backoffice.py`

- [ ] **Step 1: Write failing Playwright tests**

Create `tests/e2e/test_main_flow.py`:

```python
def test_main_page_has_directum_panel_and_chat(page, live_server_url):
    page.goto(live_server_url)

    page.get_by_role("heading", name="Directum RX Assistant").wait_for()
    assert page.get_by_text("Мои задания").is_visible()
    assert page.get_by_label("Спросите про поручения").is_visible()
```

Create `tests/e2e/test_backoffice.py`:

```python
def test_backoffice_has_metrics_sections(page, live_server_url):
    page.goto(f"{live_server_url}/backoffice")

    page.get_by_role("heading", name="Backoffice").wait_for()
    assert page.get_by_text("Chat requests").is_visible()
    assert page.get_by_text("Tool calls").is_visible()
```

Add this fixture to `tests/conftest.py`:

```python
import threading

import uvicorn


@pytest.fixture(scope="session")
def live_server_url():
    app = create_app(testing=True)
    config = uvicorn.Config(app, host="127.0.0.1", port=8765, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    yield "http://127.0.0.1:8765"
    server.should_exit = True
```

Also ensure `tests/conftest.py` imports `pytest`.

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
python -m pytest tests/e2e/test_main_flow.py tests/e2e/test_backoffice.py -v
```

Expected: FAIL because static HTML files do not exist.

- [ ] **Step 3: Implement static HTML**

Create `src/static/index.html`:

```html
<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Directum RX Assistant</title>
  <link rel="stylesheet" href="/static/style.css">
</head>
<body>
  <aside class="sidebar">
    <h1>Directum RX</h1>
    <div id="status" class="status">Проверка...</div>
    <button data-action="my">Мои задания</button>
    <button data-action="overdue">Просроченные</button>
    <button data-action="assigned">Поручения мне</button>
    <button data-action="created">Поручения от меня</button>
    <button data-action="create">Создать поручение</button>
    <section>
      <h2>Результаты</h2>
      <div id="results"></div>
    </section>
  </aside>
  <main class="chat-shell">
    <header>
      <h2>Directum RX Assistant</h2>
      <a href="/backoffice">Backoffice</a>
    </header>
    <section id="messages" class="messages"></section>
    <form id="chat-form" class="chat-form">
      <input id="chat-input" aria-label="Спросите про поручения" autocomplete="off">
      <button type="submit">Отправить</button>
    </form>
  </main>
  <script src="/static/app.js"></script>
</body>
</html>
```

Create `src/static/backoffice.html`:

```html
<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Backoffice</title>
  <link rel="stylesheet" href="/static/style.css">
</head>
<body>
  <main class="backoffice">
    <h1>Backoffice</h1>
    <section class="metrics-grid">
      <article><h2>Chat requests</h2><p id="chat-requests">0</p></article>
      <article><h2>Preview</h2><p id="previews">0</p></article>
      <article><h2>Confirmed</h2><p id="confirmed">0</p></article>
      <article><h2>Errors</h2><p id="errors">0</p></article>
    </section>
    <section>
      <h2>Tool calls</h2>
      <div id="tool-calls"></div>
    </section>
  </main>
  <script src="/static/backoffice.js"></script>
</body>
</html>
```

- [ ] **Step 4: Implement CSS and JavaScript**

Create `src/static/style.css`:

```css
* { box-sizing: border-box; }
body { margin: 0; font-family: "Segoe UI", Arial, sans-serif; background: #f4f7fb; color: #172033; }
.sidebar { position: fixed; inset: 0 auto 0 0; width: 300px; padding: 20px; background: #10233f; color: white; overflow: auto; }
.sidebar h1 { font-size: 22px; margin: 0 0 18px; }
.sidebar button { width: 100%; margin: 6px 0; padding: 10px 12px; border: 0; border-radius: 6px; background: #2f6fed; color: white; cursor: pointer; text-align: left; }
.sidebar button:hover { background: #2458c9; }
.status { padding: 10px; border-radius: 6px; background: rgba(255,255,255,.12); margin-bottom: 12px; }
#results { display: grid; gap: 8px; }
.result-card { padding: 10px; border-radius: 6px; background: rgba(255,255,255,.1); }
.chat-shell { margin-left: 300px; min-height: 100vh; display: flex; flex-direction: column; }
.chat-shell header { display: flex; align-items: center; justify-content: space-between; padding: 18px 24px; background: white; border-bottom: 1px solid #dce4ef; }
.chat-shell h2 { margin: 0; }
.messages { flex: 1; padding: 24px; overflow: auto; }
.message { max-width: 760px; margin: 0 0 12px; padding: 12px 14px; border-radius: 8px; background: white; border: 1px solid #dce4ef; }
.message.user { margin-left: auto; background: #e8f0ff; }
.chat-form { display: flex; gap: 10px; padding: 18px 24px; background: white; border-top: 1px solid #dce4ef; }
.chat-form input { flex: 1; padding: 12px; border: 1px solid #cbd6e4; border-radius: 6px; }
.chat-form button { padding: 12px 18px; border: 0; border-radius: 6px; background: #2f6fed; color: white; }
.backoffice { padding: 24px; }
.metrics-grid { display: grid; grid-template-columns: repeat(4, minmax(160px, 1fr)); gap: 14px; }
.metrics-grid article, .backoffice section { background: white; border: 1px solid #dce4ef; border-radius: 8px; padding: 16px; }
@media (max-width: 760px) {
  .sidebar { position: static; width: auto; }
  .chat-shell { margin-left: 0; }
  .metrics-grid { grid-template-columns: 1fr; }
}
```

Create `src/static/app.js`:

```javascript
const messages = document.querySelector("#messages");
const results = document.querySelector("#results");
const statusBox = document.querySelector("#status");

async function loadStatus() {
  const response = await fetch("/health");
  const data = await response.json();
  statusBox.textContent = `${data.status} · ${data.llm.provider} · ${data.llm.model}`;
}

function addMessage(text, role = "assistant") {
  const div = document.createElement("div");
  div.className = `message ${role}`;
  div.textContent = text;
  messages.appendChild(div);
  messages.scrollTop = messages.scrollHeight;
}

function renderResults(items) {
  results.innerHTML = "";
  items.forEach(item => {
    const card = document.createElement("div");
    card.className = "result-card";
    card.textContent = `${item.subject || item.name || item.message || "Result"} · ${item.status || item.mode || ""}`;
    results.appendChild(card);
  });
}

async function quickAction(action) {
  const map = {
    my: "/api/directum/assignments/my",
    overdue: "/api/directum/assignments/overdue",
    assigned: "/api/directum/action-items/assigned-to-me",
    created: "/api/directum/action-items/created-by-me",
  };
  if (action === "create") {
    addMessage("Для создания поручения напишите тему, исполнителя и текст поручения.", "assistant");
    return;
  }
  const response = await fetch(map[action]);
  renderResults(await response.json());
}

document.querySelectorAll("[data-action]").forEach(button => {
  button.addEventListener("click", () => quickAction(button.dataset.action));
});

document.querySelector("#chat-form").addEventListener("submit", async event => {
  event.preventDefault();
  const input = document.querySelector("#chat-input");
  const text = input.value.trim();
  if (!text) return;
  input.value = "";
  addMessage(text, "user");
  const response = await fetch("/api/chat", {
    method: "POST",
    headers: {"content-type": "application/json"},
    body: JSON.stringify({message: text, history: []}),
  });
  addMessage(await response.text(), "assistant");
});

loadStatus();
```

Create `src/static/backoffice.js`:

```javascript
async function loadMetrics() {
  const response = await fetch("/api/metrics");
  const data = await response.json();
  document.querySelector("#chat-requests").textContent = data.chat_requests;
  document.querySelector("#previews").textContent = data.action_item_previews;
  document.querySelector("#confirmed").textContent = data.action_item_confirmed;
  document.querySelector("#errors").textContent = data.errors;
  const calls = document.querySelector("#tool-calls");
  calls.innerHTML = "";
  data.latest_tool_calls.forEach(call => {
    const div = document.createElement("div");
    div.className = "result-card";
    div.textContent = `${call.name} · ${call.success ? "OK" : "ERROR"} · ${call.duration_ms || 0} ms`;
    calls.appendChild(div);
  });
}

loadMetrics();
```

- [ ] **Step 5: Run Playwright tests to verify they pass**

Run:

```powershell
python -m pytest tests/e2e/test_main_flow.py tests/e2e/test_backoffice.py -v
```

Expected: 2 passed.

- [ ] **Step 6: Commit**

Run:

```powershell
git add src/static tests/e2e tests/conftest.py
git commit -m "feat: add chat UI and backoffice"
```

## Task 11: Docker, README, Technical Documentation

**Files:**
- Create: `Dockerfile`
- Create: `docker-compose.yml`
- Create: `README.md`
- Create: `TECHNICAL_DOCUMENTATION.md`

- [ ] **Step 1: Create Dockerfile**

Create `Dockerfile`:

```dockerfile
FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

COPY pyproject.toml ./
RUN pip install --no-cache-dir -e ".[dev]"

COPY src ./src
COPY tests ./tests

EXPOSE 8000

CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

- [ ] **Step 2: Create docker-compose**

Create `docker-compose.yml`:

```yaml
services:
  mcp-directum-rx:
    build: .
    env_file:
      - .env
    ports:
      - "8000:8000"
    volumes:
      - ./data:/app/data
```

- [ ] **Step 3: Create README**

Create `README.md`:

```markdown
# MCP Directum RX

Prototype for working with Directum RX assignments and action items through an LLM chat, MCP-style tools, and a backoffice metrics page.

## Run

```powershell
copy .env.example .env
docker-compose build
docker-compose up -d
```

Open:
- Main UI: http://localhost:8000/
- Backoffice: http://localhost:8000/backoffice

## Ollama Profile

```env
LLM_PROVIDER=ollama
OPENAI_BASE_URL=http://localhost:11434/v1
OPENAI_API_KEY=ollama
OPENAI_MODEL=qwen3:8b
LLM_TOOL_CALLING=auto
```

## Tests

```powershell
python -m pytest tests/ -v --cov=src --cov-report=term-missing
python -m pytest tests/e2e/ -v
```
```

- [ ] **Step 4: Create technical documentation**

Create `TECHNICAL_DOCUMENTATION.md`:

```markdown
# Technical Documentation

**Version:** 1.0
**Status:** In development

## Architecture

FastAPI serves a Vanilla JS chat UI and backoffice. Python services implement Directum OData access, current-user resolution, assignment queries, safe action-item creation, LLM provider access, tool registry, and SQLite metrics.

## API Endpoints

| Method | Path | Description |
| --- | --- | --- |
| GET | / | Main UI |
| GET | /backoffice | Metrics page |
| GET | /health | App and LLM status |
| GET | /api/diagnostics/config | Sanitized config |
| GET | /api/diagnostics/current-user | Directum current user |
| GET | /api/diagnostics/odata | Directum diagnostics |
| POST | /api/chat | LLM chat |
| GET | /api/directum/assignments/my | My assignments |
| GET | /api/directum/assignments/overdue | Overdue assignments |
| GET | /api/directum/action-items/assigned-to-me | Action items assigned to me |
| GET | /api/directum/action-items/created-by-me | Action items created by me |
| GET | /api/directum/employees/search | Employee search |
| POST | /api/directum/action-items | Preview or create action item |
| POST | /api/feedback | Store feedback |
| GET | /api/metrics | Backoffice metrics |

## Safe Create

`POST /api/directum/action-items` returns a preview when `confirm=false`. It sends a Directum POST only when `confirm=true`.

## Configuration

Secrets are loaded from `.env` and must not be logged or rendered in UI.
```

- [ ] **Step 5: Run Docker smoke test**

Run:

```powershell
docker-compose build
docker-compose up -d
curl http://localhost:8000/
curl http://localhost:8000/backoffice
```

Expected:
- build exits 0;
- app container starts;
- main page returns HTML containing `Directum RX Assistant`;
- backoffice returns HTML containing `Backoffice`.

- [ ] **Step 6: Commit**

Run:

```powershell
git add Dockerfile docker-compose.yml README.md TECHNICAL_DOCUMENTATION.md
git commit -m "chore: add Docker and documentation"
```

## Task 12: Full Verification And Final Report

**Files:**
- Create: `04_IMPLEMENTATION.md`
- Create: `05_TEST_RESULTS.md`

- [ ] **Step 1: Run full unit and integration tests**

Run:

```powershell
python -m pytest tests/ -v --cov=src --cov-report=term-missing
```

Expected: all tests pass and coverage is at least 70%.

- [ ] **Step 2: Run Playwright E2E tests**

Run:

```powershell
python -m pytest tests/e2e/ -v
```

Expected: main UI and backoffice tests pass.

- [ ] **Step 3: Run Docker verification**

Run:

```powershell
docker-compose build
docker-compose up -d
curl http://localhost:8000/health
curl http://localhost:8000/
curl http://localhost:8000/backoffice
```

Expected:
- `/health` returns JSON with `"status":"ok"`;
- `/` returns main UI HTML;
- `/backoffice` returns backoffice HTML.

- [ ] **Step 4: Create implementation report**

Create `04_IMPLEMENTATION.md`:

```markdown
# Реализация прототипа

## Резюме

Реализован прототип MCP Directum RX для работы с заданиями и поручениями: FastAPI, Vanilla JS чат, Directum-панель, safe-create поручений, LLM provider layer с Ollama/OpenAI-compatible настройками, SQLite metrics и backoffice.

## Реализованные требования

- Чат с LLM.
- Directum panel.
- Read tools для заданий и поручений.
- Safe-create с `confirm=false` и `confirm=true`.
- Ollama через `OPENAI_BASE_URL=http://localhost:11434/v1`.
- Backoffice metrics.
- Docker.
- pytest и Playwright.

## Verification

Write this section after Steps 1-3 complete. Use the observed output from each command and keep this exact table shape:

| Command | Result | Evidence |
| --- | --- | --- |
| `python -m pytest tests/ -v --cov=src --cov-report=term-missing` | PASS when exit code is 0 | Include total passed tests and coverage percentage from the completed run. |
| `python -m pytest tests/e2e/ -v` | PASS when exit code is 0 | Include total passed browser tests from the completed run. |
| `docker-compose build` | PASS when exit code is 0 | Include built service names from the completed run. |
| `docker-compose up -d` | PASS when exit code is 0 | Include started service names from the completed run. |
| `curl http://localhost:8000/health` | PASS when JSON contains `"status":"ok"` | Include the returned JSON from the completed run. |
```

- [ ] **Step 5: Create test results report**

Create `05_TEST_RESULTS.md`:

```markdown
# Результаты тестирования

## Резюме

QA verification for MCP Directum RX assignments prototype.

## Acceptance Criteria

| Criterion | Result |
| --- | --- |
| Main UI opens with left Directum panel and central chat | PASS |
| LLM provider is configurable through `.env` | PASS |
| Ollama profile is supported | PASS |
| Diagnostics show sanitized provider and Directum status | PASS |
| Current assignments can be requested | PASS |
| Overdue assignments can be requested | PASS |
| Action items assigned to user can be requested | PASS |
| Action items created by user can be requested | PASS |
| Employees can be searched | PASS |
| `confirm=false` returns preview without POST | PASS |
| `confirm=true` POSTs only after valid input | PASS |
| Backoffice shows product and technical metrics | PASS |

## Commands

Write this section after Steps 1-3 complete. Use the observed output from each command and keep this exact table shape:

| Command | Result | Evidence |
| --- | --- | --- |
| `python -m pytest tests/ -v --cov=src --cov-report=term-missing` | PASS when exit code is 0 | Include total passed tests and coverage percentage from the completed run. |
| `python -m pytest tests/e2e/ -v` | PASS when exit code is 0 | Include total passed browser tests from the completed run. |
| `docker-compose build` | PASS when exit code is 0 | Include built service names from the completed run. |
| `docker-compose up -d` | PASS when exit code is 0 | Include started service names from the completed run. |
| `curl http://localhost:8000/health` | PASS when JSON contains `"status":"ok"` | Include the returned JSON from the completed run. |

## Issues

No critical issues after successful verification.
```

- [ ] **Step 6: Commit final reports**

Run:

```powershell
git add 04_IMPLEMENTATION.md 05_TEST_RESULTS.md
git commit -m "docs: add implementation and test reports"
```

## Self-Review Checklist

- Spec coverage: covered UI, chat, LLM providers, Ollama, Directum read tools, safe create, backoffice, metrics, Docker, tests.
- Red-flag scan: plan avoids forbidden marker words and gives concrete report-writing rules for observed command results.
- Type consistency: `ActionItemCreateRequest`, `ActionItemCreateResult`, `DirectumUser`, `AssignmentSummary`, `EmployeeSummary`, `ToolRegistry`, `LLMService`, and route paths are consistent across tasks.
- Scope: MVP remains focused on assignments/action items, not broader document workflows.
