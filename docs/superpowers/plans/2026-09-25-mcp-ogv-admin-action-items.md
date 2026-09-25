# mcpOGV: просмотр поручений сотрудников администратором — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** добавить в mcpOGV read-only тул `admin_list_employee_action_items`, доступный только администраторам Directum RX, для просмотра поручений любого сотрудника с фильтрами.

**Architecture:** фильтр поручений в `AssignmentsService` обобщается до «сотрудник + фильтры» (мои поручения — частный случай). Новый `AdminAccessService` проверяет членство в роли «Администраторы» по Sid. Тул `admin_*` скрывается и блокируется для не-администраторов middleware `AdminGateMiddleware` (по образцу `NativeProxyMiddleware`), а сам тул перепроверяет права.

**Tech Stack:** Python 3.10+, `mcp==2.2.0` (`MCPServer`, middleware), pydantic, httpx, pytest.

**Спека:** `docs/superpowers/specs/2026-09-25-mcp-ogv-admin-action-items-design.md`

## Global Constraints

- Все тулы только читают Directum; запись не добавляется.
- Sid роли «Администраторы»: `9cc6ea59-cd05-4c8e-b041-abefe9432e20`.
- Префикс админ-тулов: `admin_`; текст отказа: `Инструмент доступен только администраторам Directum RX.`
- Кеш проверки администратора: 600 с по `credentials.fingerprint`.
- Ошибка проверки администратора ⇒ «не администратор» (fail closed); в лог — только тип исключения.
- Тексты для пользователя/агента — по-русски. Секреты не логировать, в код/markdown не класть.
- `.env` не создавать и не редактировать.
- Тесты запускать из корня репо: `.venv\Scripts\python.exe -m pytest ...` (ниже — `pytest` для краткости).
- Коммиты — в `feature/mcp-ogv`, без push.

## Файлы

| Файл | Что |
|---|---|
| `src/services/assignments.py` (изменить) | `ActionItemFilters`, `action_items_filter`, `list_employee_action_items`, `count_employee_action_items`; `_action_items_source` через общий фильтр |
| `src/services/admin_access.py` (создать) | `AdminAccessService.is_admin()` |
| `src/services/factory.py` (изменить) | поле `admin_access` в `DirectumServices` |
| `src/mcp_server/tools/admin.py` (создать) | тул, `resolve_employee`, `parse_iso_date`, константы `ADMIN_PREFIX`, `ADMIN_ONLY_MESSAGE` |
| `src/mcp_server/admin_gate.py` (создать) | `AdminGateMiddleware` |
| `src/mcp_server/app.py` (изменить) | регистрация тула и middleware, правило в `INSTRUCTIONS` |
| `tests/unit/test_assignments_employee_filters.py` (создать) | фильтры |
| `tests/unit/test_admin_access.py` (создать) | проверка администратора |
| `tests/unit/test_services_factory.py` (изменить) | wiring `admin_access` |
| `tests/unit/test_mcp_tools_admin.py` (создать) | тул |
| `tests/unit/test_mcp_admin_gate.py` (создать) | скрытие/блокировка |
| `tests/live/test_mcp_live.py` (изменить) | live-проверка `is_admin` |
| `README.md` (изменить) | упоминание тула в разделе mcpOGV |

---

### Task 1: Обобщённый фильтр поручений в `AssignmentsService`

**Files:**
- Modify: `src/services/assignments.py`
- Test: `tests/unit/test_assignments_employee_filters.py`

**Interfaces:**
- Produces:
  - `ActionItemFilters` — frozen dataclass: `status: str = "in_process"`, `only_overdue: bool = False`, `date_field: str = "deadline"`, `date_from: date | None = None`, `date_to: date | None = None`.
  - `AssignmentsService.action_items_filter(direction: str, employee_id: int, filters: ActionItemFilters | None = None) -> tuple[str, str]` — `(entity_set, filter)`; `ValueError` на неизвестные `direction`/`status`/`date_field` и на `only_overdue` при `status` не `in_process`/`all`.
  - `AssignmentsService.list_employee_action_items(direction: str, employee_id: int, filters: ActionItemFilters | None = None, top: int | None = None) -> list[AssignmentSummary]`
  - `AssignmentsService.count_employee_action_items(direction: str, employee_id: int, filters: ActionItemFilters | None = None) -> int`

- [ ] **Step 1: Write the failing tests**

`tests/unit/test_assignments_employee_filters.py`:

```python
from datetime import date

import pytest

from src.models.schemas import DirectumUser
from src.services.assignments import ActionItemFilters, AssignmentsService


class FakeClient:
    def __init__(self):
        self.calls = []
        self.counts = []

    def query(self, entity_set, **kwargs):
        self.calls.append((entity_set, kwargs))
        return [{"Id": 7, "Subject": "Поручение", "Status": "InProcess", "Assignee": {"Name": "Иванов И.И."}}]

    def count(self, entity_set, filter_=None):
        self.counts.append((entity_set, filter_))
        return 42

    def build_client_card_url(self, entity_path):
        return f"https://rx.example/card/{entity_path}"


class FakeCurrentUser:
    def get_current_user(self):
        return DirectumUser(id=1165, name="Test User", login="user")


def service():
    return AssignmentsService(client=FakeClient(), current_user_service=FakeCurrentUser())


def test_incoming_default_is_in_process_for_employee():
    entity_set, filter_ = service().action_items_filter("incoming", 63)

    assert entity_set == "IActionItemExecutionAssignments"
    assert filter_ == "Performer/Id eq 63 and Status eq 'InProcess'"


def test_outgoing_uses_tasks_and_author():
    entity_set, filter_ = service().action_items_filter("outgoing", 63)

    assert entity_set == "IActionItemExecutionTasks"
    assert filter_ == "Author/Id eq 63 and Status eq 'InProcess'"


@pytest.mark.parametrize(
    ("status", "expected"),
    [("completed", " and Status eq 'Completed'"), ("aborted", " and Status eq 'Aborted'")],
)
def test_status_filter(status, expected):
    _, filter_ = service().action_items_filter("incoming", 63, ActionItemFilters(status=status))

    assert filter_ == "Performer/Id eq 63" + expected


def test_status_all_has_no_status_condition():
    _, filter_ = service().action_items_filter("incoming", 63, ActionItemFilters(status="all"))

    assert filter_ == "Performer/Id eq 63"


def test_only_overdue_adds_deadline_and_in_process():
    _, filter_ = service().action_items_filter("incoming", 63, ActionItemFilters(status="all", only_overdue=True))

    assert filter_.startswith("Performer/Id eq 63 and Status eq 'InProcess' and Deadline lt ")
    assert filter_.endswith("Z")


def test_only_overdue_with_completed_is_rejected():
    with pytest.raises(ValueError):
        service().action_items_filter("incoming", 63, ActionItemFilters(status="completed", only_overdue=True))


def test_period_by_deadline_is_inclusive():
    filters = ActionItemFilters(status="all", date_from=date(2026, 1, 1), date_to=date(2026, 1, 31))

    _, filter_ = service().action_items_filter("outgoing", 63, filters)

    assert filter_ == "Author/Id eq 63 and Deadline ge 2026-01-01T00:00:00Z and Deadline lt 2026-02-01T00:00:00Z"


def test_period_by_created():
    filters = ActionItemFilters(status="all", date_field="created", date_from=date(2026, 3, 5))

    _, filter_ = service().action_items_filter("incoming", 63, filters)

    assert filter_ == "Performer/Id eq 63 and Created ge 2026-03-05T00:00:00Z"


@pytest.mark.parametrize(
    "kwargs",
    [{"direction": "sideways"}, {"filters": ActionItemFilters(status="lost")}, {"filters": ActionItemFilters(date_field="modified")}],
)
def test_unknown_values_are_rejected(kwargs):
    args = {"direction": "incoming", "employee_id": 63, **kwargs}
    with pytest.raises(ValueError):
        service().action_items_filter(**args)


def test_list_employee_outgoing_expands_assignee_and_sorts():
    svc = service()

    items = svc.list_employee_action_items("outgoing", 63, top=5)

    entity_set, kwargs = svc.client.calls[0]
    assert entity_set == "IActionItemExecutionTasks"
    assert kwargs["filter_"] == "Author/Id eq 63 and Status eq 'InProcess'"
    assert kwargs["expand"] == "Assignee($select=Name)"
    assert kwargs["orderby"] == "Deadline asc"
    assert kwargs["top"] == 5
    assert items[0].entity_type == "action_item_task"
    assert items[0].performer == "Иванов И.И."


def test_list_employee_incoming_has_no_expand():
    svc = service()

    items = svc.list_employee_action_items("incoming", 63)

    _, kwargs = svc.client.calls[0]
    assert kwargs["expand"] is None
    assert items[0].entity_type == "action_item_assignment"


def test_count_employee_uses_same_filter():
    svc = service()

    total = svc.count_employee_action_items("incoming", 63, ActionItemFilters(status="completed"))

    assert total == 42
    assert svc.client.counts == [("IActionItemExecutionAssignments", "Performer/Id eq 63 and Status eq 'Completed'")]


def test_my_action_items_filter_unchanged():
    svc = service()

    svc.count_action_items("outgoing")

    assert svc.client.counts == [("IActionItemExecutionTasks", "Author/Id eq 1165 and Status eq 'InProcess'")]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_assignments_employee_filters.py -v`
Expected: FAIL — `ImportError: cannot import name 'ActionItemFilters'`.

- [ ] **Step 3: Implement**

В `src/services/assignments.py`:

Импорты вверху заменить на:

```python
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any
```

Перед `class AssignmentsService` добавить:

```python
ACTION_ITEM_STATUSES = {"in_process": "InProcess", "completed": "Completed", "aborted": "Aborted", "all": None}
ACTION_ITEM_DATE_FIELDS = {"deadline": "Deadline", "created": "Created"}
OVERDUE_COMPATIBLE_STATUSES = ("in_process", "all")


@dataclass(frozen=True)
class ActionItemFilters:
    status: str = "in_process"
    only_overdue: bool = False
    date_field: str = "deadline"
    date_from: date | None = None
    date_to: date | None = None


def _utc_now_literal() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _day_literal(day: date) -> str:
    return f"{day.isoformat()}T00:00:00Z"
```

В классе добавить методы (после `count_action_items`):

```python
    def list_employee_action_items(
        self,
        direction: str,
        employee_id: int,
        filters: ActionItemFilters | None = None,
        top: int | None = None,
    ) -> list[AssignmentSummary]:
        entity_set, action_filter = self.action_items_filter(direction, employee_id, filters)
        rows = self.client.query(
            entity_set,
            filter_=action_filter,
            select="Id,Subject,Deadline,Status",
            expand="Assignee($select=Name)" if direction == "outgoing" else None,
            orderby="Deadline asc",
            top=top,
        )
        entity_type = "action_item_task" if direction == "outgoing" else "action_item_assignment"
        return [self._assignment(row, entity_type) for row in rows]

    def count_employee_action_items(
        self, direction: str, employee_id: int, filters: ActionItemFilters | None = None
    ) -> int:
        entity_set, action_filter = self.action_items_filter(direction, employee_id, filters)
        return self.client.count(entity_set, filter_=action_filter)

    def action_items_filter(
        self, direction: str, employee_id: int, filters: ActionItemFilters | None = None
    ) -> tuple[str, str]:
        filters = filters or ActionItemFilters()
        if direction not in self.ACTION_ITEM_SOURCES:
            raise ValueError(f"Unknown action items direction: {direction}")
        if filters.status not in ACTION_ITEM_STATUSES:
            raise ValueError(f"Unknown action items status: {filters.status}")
        if filters.date_field not in ACTION_ITEM_DATE_FIELDS:
            raise ValueError(f"Unknown action items date field: {filters.date_field}")
        if filters.only_overdue and filters.status not in OVERDUE_COMPATIBLE_STATUSES:
            raise ValueError("Only action items in process can be overdue")
        entity_set, role = self.ACTION_ITEM_SOURCES[direction]
        conditions = [f"{role}/Id eq {int(employee_id)}"]
        status = "InProcess" if filters.only_overdue else ACTION_ITEM_STATUSES[filters.status]
        if status:
            conditions.append(f"Status eq '{status}'")
        if filters.only_overdue:
            conditions.append(f"Deadline lt {_utc_now_literal()}")
        field = ACTION_ITEM_DATE_FIELDS[filters.date_field]
        if filters.date_from:
            conditions.append(f"{field} ge {_day_literal(filters.date_from)}")
        if filters.date_to:
            conditions.append(f"{field} lt {_day_literal(filters.date_to + timedelta(days=1))}")
        return entity_set, " and ".join(conditions)
```

Заменить тело `_action_items_source` на:

```python
    def _action_items_source(self, direction: str) -> tuple[str, str]:
        user = self.current_user_service.get_current_user()
        return self.action_items_filter(direction, user.id)
```

Для неизвестного `direction` по-прежнему поднимается `ValueError("Unknown action items direction: ...")`; теперь текущий пользователь запрашивается до этой проверки — на поведение это не влияет.

- [ ] **Step 4: Run tests**

Run: `pytest tests/unit/test_assignments_employee_filters.py tests/unit/test_assignments.py tests/unit/test_mcp_tools_action_items.py -v`
Expected: все PASS.

- [ ] **Step 5: Commit**

```bash
git add src/services/assignments.py tests/unit/test_assignments_employee_filters.py
git commit -m "feat(mcp): фильтр поручений по сотруднику, статусу и периоду"
```

---

### Task 2: `AdminAccessService` и wiring в `DirectumServices`

**Files:**
- Create: `src/services/admin_access.py`
- Modify: `src/services/factory.py`
- Test: `tests/unit/test_admin_access.py`, `tests/unit/test_services_factory.py`

**Interfaces:**
- Produces: `ADMINISTRATORS_ROLE_SID: str`; `AdminAccessService(client, current_user_service)` с полями `client`, `current_user_service` и методом `is_admin() -> bool`; `DirectumServices.admin_access: AdminAccessService`.

- [ ] **Step 1: Write the failing tests**

`tests/unit/test_admin_access.py`:

```python
import pytest

from src.models.schemas import DirectumUser
from src.services.admin_access import ADMINISTRATORS_ROLE_SID, AdminAccessService
from src.services.directum_client import DirectumError


class FakeClient:
    def __init__(self, rows=None, error=None):
        self.rows = rows or []
        self.error = error
        self.calls = []

    def query(self, entity_set, **kwargs):
        self.calls.append((entity_set, kwargs))
        if self.error:
            raise self.error
        return self.rows


class FakeCurrentUser:
    def get_current_user(self):
        return DirectumUser(id=12, name="Administrator", login="Administrator")


def test_is_admin_true_when_role_found():
    client = FakeClient(rows=[{"Id": 2}])

    assert AdminAccessService(client, FakeCurrentUser()).is_admin() is True
    entity_set, kwargs = client.calls[0]
    assert entity_set == "IRoles"
    assert kwargs["filter_"] == f"Sid eq {ADMINISTRATORS_ROLE_SID} and RecipientLinks/any(l: l/Member/Id eq 12)"
    assert kwargs["select"] == "Id"
    assert kwargs["top"] == 1


def test_is_admin_false_when_empty():
    assert AdminAccessService(FakeClient(rows=[]), FakeCurrentUser()).is_admin() is False


def test_is_admin_propagates_errors():
    service = AdminAccessService(FakeClient(error=DirectumError("forbidden", 403)), FakeCurrentUser())

    with pytest.raises(DirectumError):
        service.is_admin()
```

В `tests/unit/test_services_factory.py` перед `services.close()` добавить:

```python
    assert services.admin_access.client is services.client
    assert services.admin_access.current_user_service is services.current_user
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_admin_access.py tests/unit/test_services_factory.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.services.admin_access'`.

- [ ] **Step 3: Implement**

`src/services/admin_access.py`:

```python
from src.services.current_user import CurrentUserService
from src.services.directum_client import DirectumClient

# Платформенный Sid роли «Администраторы»: одинаков на всех стендах и не зависит от названия роли.
ADMINISTRATORS_ROLE_SID = "9cc6ea59-cd05-4c8e-b041-abefe9432e20"


class AdminAccessService:
    """Проверяет, что текущий пользователь напрямую входит в роль «Администраторы»."""

    def __init__(self, client: DirectumClient, current_user_service: CurrentUserService):
        self.client = client
        self.current_user_service = current_user_service

    def is_admin(self) -> bool:
        user = self.current_user_service.get_current_user()
        rows = self.client.query(
            "IRoles",
            filter_=f"Sid eq {ADMINISTRATORS_ROLE_SID} and RecipientLinks/any(l: l/Member/Id eq {int(user.id)})",
            select="Id",
            top=1,
        )
        return bool(rows)
```

`src/services/factory.py`: добавить импорт `from src.services.admin_access import AdminAccessService`, поле `admin_access: AdminAccessService` в конец dataclass `DirectumServices` (после `discipline`), и в `build_directum_services` аргумент `admin_access=AdminAccessService(client, current_user),`.

- [ ] **Step 4: Run tests**

Run: `pytest tests/unit/test_admin_access.py tests/unit/test_services_factory.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/services/admin_access.py src/services/factory.py tests/unit/test_admin_access.py tests/unit/test_services_factory.py
git commit -m "feat(mcp): проверка членства в роли «Администраторы»"
```

---

### Task 3: Тул `admin_list_employee_action_items`

**Files:**
- Create: `src/mcp_server/tools/admin.py`
- Modify: `src/mcp_server/app.py` (импорт `admin`, `admin.register(mcp, runner)` после `action_items.register`)
- Test: `tests/unit/test_mcp_tools_admin.py`

**Interfaces:**
- Consumes: `ActionItemFilters`, `list_employee_action_items`, `count_employee_action_items` (Task 1); `services.admin_access.is_admin()` (Task 2); `services.action_items.get_employee(id) -> EmployeeSummary | None`, `services.action_items.search_employee(query) -> list[EmployeeSummary]` (существуют).
- Produces: `ADMIN_PREFIX = "admin_"`, `ADMIN_ONLY_MESSAGE`, `resolve_employee(action_items, query) -> EmployeeSummary`, `parse_iso_date(value, label) -> date | None`, `register(mcp, runner)`.

- [ ] **Step 1: Write the failing tests**

`tests/unit/test_mcp_tools_admin.py`:

```python
from datetime import date
from types import SimpleNamespace

from src.mcp_server.app import build_server
from src.models.schemas import AssignmentSummary, EmployeeSummary
from src.services.assignments import ActionItemFilters
from tests.unit.mcp_fakes import FakeProvider, call_tool, error_text, list_tools, payload

TOOL = "admin_list_employee_action_items"
NADYA = EmployeeSummary(id=63, name="Концева Надежда Ивановна", status="Active")


class FakeAssignments:
    def __init__(self):
        self.listed = []
        self.counted = []

    def list_employee_action_items(self, direction, employee_id, filters=None, top=None):
        self.listed.append((direction, employee_id, filters, top))
        return [AssignmentSummary(id=1, subject="Поручение", status="InProcess", entity_type="action_item_assignment")]

    def count_employee_action_items(self, direction, employee_id, filters=None):
        self.counted.append((direction, employee_id, filters))
        return 3


def make_services(is_admin=True, search=None, by_id=None):
    return SimpleNamespace(
        admin_access=SimpleNamespace(is_admin=lambda: is_admin),
        action_items=SimpleNamespace(
            search_employee=lambda query: search if search is not None else [NADYA],
            get_employee=lambda employee_id: by_id,
        ),
        assignments=FakeAssignments(),
    )


def call(services, **arguments):
    return call_tool(build_server(FakeProvider(services)), TOOL, arguments)


def test_admin_tool_listed_for_admin():
    assert TOOL in [tool.name for tool in list_tools(build_server(FakeProvider(make_services())))]


def test_returns_envelope_with_employee_and_filters():
    services = make_services()

    data = payload(call(services, employee="Концева", direction="incoming", limit=2))

    assert data["employee"] == {"id": 63, "name": "Концева Надежда Ивановна"}
    assert data["total"] == 3
    assert data["returned"] == 1
    assert data["truncated"] is True
    assert data["filters"] == {
        "direction": "incoming", "status": "in_process", "only_overdue": False,
        "date_field": "deadline", "date_from": None, "date_to": None,
    }
    assert services.assignments.listed == [("incoming", 63, ActionItemFilters(), 2)]


def test_passes_filters_and_dates():
    services = make_services()

    payload(call(
        services, employee="63", direction="outgoing", status="all", only_overdue=True,
        date_field="created", date_from="2026-01-01", date_to="2026-01-31",
    ))

    _, _, filters, _ = services.assignments.listed[0]
    assert filters == ActionItemFilters(
        status="all", only_overdue=True, date_field="created",
        date_from=date(2026, 1, 1), date_to=date(2026, 1, 31),
    )


def test_numeric_employee_uses_get_employee():
    services = make_services(by_id=EmployeeSummary(id=99, name="Петров П.П."))

    data = payload(call(services, employee="99", direction="incoming"))

    assert data["employee"] == {"id": 99, "name": "Петров П.П."}


def test_numeric_employee_not_found():
    assert "id 99 не найден" in error_text(call(make_services(by_id=None), employee="99", direction="incoming"))


def test_employee_not_found_by_name():
    assert "не найден" in error_text(call(make_services(search=[]), employee="Никто", direction="incoming"))


def test_exact_name_wins_among_several():
    other = EmployeeSummary(id=70, name="Концева Надежда Петровна")
    services = make_services(search=[other, NADYA])

    data = payload(call(services, employee="концева надежда ивановна", direction="incoming"))

    assert data["employee"]["id"] == 63


def test_ambiguous_employee_lists_candidates():
    other = EmployeeSummary(id=70, name="Концева Надежда Петровна")

    text = error_text(call(make_services(search=[NADYA, other]), employee="Концева", direction="incoming"))

    assert "63 — Концева Надежда Ивановна" in text
    assert "70 — Концева Надежда Петровна" in text


def test_overdue_with_completed_rejected():
    text = error_text(call(make_services(), employee="63", direction="incoming", status="completed", only_overdue=True))

    assert "только поручения в работе" in text


def test_bad_date_rejected():
    assert "YYYY-MM-DD" in error_text(call(make_services(), employee="63", direction="incoming", date_from="01.02.2026"))


def test_reversed_period_rejected():
    text = error_text(call(make_services(), employee="63", direction="incoming", date_from="2026-02-01", date_to="2026-01-01"))

    assert "date_from позже date_to" in text


def test_non_admin_rejected_inside_tool():
    services = make_services(is_admin=False)

    text = error_text(call(services, employee="63", direction="incoming"))

    assert "только администраторам" in text
    assert services.assignments.listed == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_mcp_tools_admin.py -v`
Expected: FAIL — тула нет (`Unknown tool` / `ImportError` на `ActionItemFilters` не будет — он из Task 1).

- [ ] **Step 3: Implement**

`src/mcp_server/tools/admin.py`:

```python
from datetime import date
from typing import Annotated, Any, Literal

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from src.mcp_server.envelope import clamp_limit, list_envelope
from src.mcp_server.runner import READ_ONLY, ToolRunner
from src.models.schemas import EmployeeSummary
from src.services.assignments import OVERDUE_COMPATIBLE_STATUSES, ActionItemFilters

ADMIN_PREFIX = "admin_"
ADMIN_ONLY_MESSAGE = "Инструмент доступен только администраторам Directum RX."
MAX_CANDIDATES = 10

Limit = Annotated[int, Field(description="Сколько записей вернуть (1–100)", ge=1, le=100)]
IsoDate = Annotated[str | None, Field(description="Дата в формате YYYY-MM-DD, граница включительно")]


def parse_iso_date(value: str | None, label: str) -> date | None:
    if value is None or not value.strip():
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        raise ToolError(f"{label}: ожидается дата в формате YYYY-MM-DD, получено «{value}».") from None


def resolve_employee(action_items: Any, query: str) -> EmployeeSummary:
    text = query.strip()
    if not text:
        raise ToolError("Укажите ФИО или id сотрудника.")
    if text.isdigit():
        found = action_items.get_employee(int(text))
        if found is None:
            raise ToolError(f"Сотрудник с id {text} не найден.")
        return found
    matches = action_items.search_employee(text)
    if not matches:
        raise ToolError(f"Сотрудник «{text}» не найден. Уточните ФИО.")
    if len(matches) == 1:
        return matches[0]
    exact = [match for match in matches if match.name.casefold() == text.casefold()]
    if len(exact) == 1:
        return exact[0]
    listing = "; ".join(f"{match.id} — {match.name}" for match in matches[:MAX_CANDIDATES])
    raise ToolError(f"Найдено несколько сотрудников: {listing}. Уточните, кого именно (можно передать id).")


def register(mcp: MCPServer, runner: ToolRunner) -> None:
    @mcp.tool(annotations=READ_ONLY)
    async def admin_list_employee_action_items(
        employee: Annotated[str, Field(description="ФИО или id сотрудника")],
        direction: Annotated[
            Literal["incoming", "outgoing"],
            Field(description="incoming — сотрудник исполнитель, outgoing — сотрудник автор поручений"),
        ],
        ctx: Context,
        status: Annotated[
            Literal["in_process", "completed", "aborted", "all"],
            Field(description="Статус: в работе, завершены, прекращены или все"),
        ] = "in_process",
        only_overdue: Annotated[bool, Field(description="Только просроченные (срок прошёл, в работе)")] = False,
        date_field: Annotated[
            Literal["deadline", "created"], Field(description="К какой дате применять период: срок или создание")
        ] = "deadline",
        date_from: IsoDate = None,
        date_to: IsoDate = None,
        limit: Limit = 20,
    ) -> dict:
        """Только для администраторов: поручения любого сотрудника — входящие (он исполнитель) или исходящие (он автор), с фильтром по статусу, просрочке и периоду. total — сколько всего."""
        size = clamp_limit(limit)
        if only_overdue and status not in OVERDUE_COMPATIBLE_STATUSES:
            raise ToolError(
                "Просроченными бывают только поручения в работе: уберите only_overdue или укажите status=in_process."
            )
        start = parse_iso_date(date_from, "date_from")
        end = parse_iso_date(date_to, "date_to")
        if start and end and start > end:
            raise ToolError("date_from позже date_to.")
        filters = ActionItemFilters(
            status=status, only_overdue=only_overdue, date_field=date_field, date_from=start, date_to=end
        )

        def action(s):
            if not s.admin_access.is_admin():
                raise ToolError(ADMIN_ONLY_MESSAGE)
            person = resolve_employee(s.action_items, employee)
            items = s.assignments.list_employee_action_items(direction, person.id, filters, top=size)
            total = s.assignments.count_employee_action_items(direction, person.id, filters)
            return {
                **list_envelope(items, size, total=total),
                "employee": {"id": person.id, "name": person.name},
                "filters": {
                    "direction": direction,
                    "status": status,
                    "only_overdue": only_overdue,
                    "date_field": date_field,
                    "date_from": start.isoformat() if start else None,
                    "date_to": end.isoformat() if end else None,
                },
            }

        return await runner.run(ctx, "admin_list_employee_action_items", action)
```

`src/mcp_server/app.py`: `from src.mcp_server.tools import action_items, admin, common, documents, odata` и в `build_server` после `action_items.register(mcp, runner)` добавить `admin.register(mcp, runner)`.

- [ ] **Step 4: Run tests**

Run: `pytest tests/unit/test_mcp_tools_admin.py tests/unit/test_mcp_tools_action_items.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/mcp_server/tools/admin.py src/mcp_server/app.py tests/unit/test_mcp_tools_admin.py
git commit -m "feat(mcp): тул admin_list_employee_action_items"
```

---

### Task 4: `AdminGateMiddleware` — скрытие и блокировка `admin_*`

**Files:**
- Create: `src/mcp_server/admin_gate.py`
- Modify: `src/mcp_server/app.py` (middleware, `ADMIN_CHECK_TTL_SECONDS = 600`, правило 7 в `INSTRUCTIONS`)
- Test: `tests/unit/test_mcp_admin_gate.py`

**Interfaces:**
- Consumes: `ADMIN_PREFIX`, `ADMIN_ONLY_MESSAGE` (Task 3); `TtlCache` (`src/mcp_server/context.py`); `provider.open(headers)` → `(credentials, services)`.
- Produces: `AdminGateMiddleware(provider, cache: TtlCache)` — async-callable `(ctx, call_next)`.

- [ ] **Step 1: Write the failing tests**

`tests/unit/test_mcp_admin_gate.py`:

```python
from types import SimpleNamespace

from src.mcp_server.app import build_server
from src.models.schemas import AssignmentSummary, EmployeeSummary
from tests.unit.mcp_fakes import FakeProvider, call_tool, error_text, list_tools

TOOL = "admin_list_employee_action_items"


class Counter:
    def __init__(self, answer=True, error=None):
        self.answer = answer
        self.error = error
        self.calls = 0

    def is_admin(self):
        self.calls += 1
        if self.error:
            raise self.error
        return self.answer


def make_services(admin):
    listed = []

    def list_items(direction, employee_id, filters=None, top=None):
        listed.append(employee_id)
        return [AssignmentSummary(id=1, subject="П", entity_type="action_item_assignment")]

    return SimpleNamespace(
        admin_access=admin,
        action_items=SimpleNamespace(
            search_employee=lambda query: [EmployeeSummary(id=63, name="Концева")],
            get_employee=lambda employee_id: EmployeeSummary(id=employee_id, name="Концева"),
        ),
        assignments=SimpleNamespace(
            list_employee_action_items=list_items,
            count_employee_action_items=lambda direction, employee_id, filters=None: 1,
            listed=listed,
        ),
    )


def names(server):
    return [tool.name for tool in list_tools(server)]


def test_admin_sees_admin_tool():
    assert TOOL in names(build_server(FakeProvider(make_services(Counter(True)))))


def test_non_admin_does_not_see_admin_tool():
    tool_names = names(build_server(FakeProvider(make_services(Counter(False)))))

    assert TOOL not in tool_names
    assert "get_current_user" in tool_names


def test_check_error_hides_admin_tool():
    assert TOOL not in names(build_server(FakeProvider(make_services(Counter(error=RuntimeError("boom"))))))


def test_missing_admin_service_hides_admin_tool():
    assert TOOL not in names(build_server(FakeProvider(SimpleNamespace())))


def test_non_admin_call_blocked_before_tool():
    services = make_services(Counter(False))

    text = error_text(call_tool(build_server(FakeProvider(services)), TOOL, {"employee": "63", "direction": "incoming"}))

    assert "только администраторам" in text
    assert services.assignments.listed == []


def test_admin_check_is_cached_for_tools_list():
    admin = Counter(True)
    server = build_server(FakeProvider(make_services(admin)))

    names(server)
    names(server)

    assert admin.calls == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_mcp_admin_gate.py -v`
Expected: FAIL — `test_non_admin_does_not_see_admin_tool`, `test_check_error_hides_admin_tool`, `test_missing_admin_service_hides_admin_tool`, `test_admin_check_is_cached_for_tools_list` (тул виден всем, кеша нет). `test_non_admin_call_blocked_before_tool` может пройти за счёт проверки внутри тула — это нормально.

- [ ] **Step 3: Implement**

`src/mcp_server/admin_gate.py`:

```python
import logging
from typing import Any

import anyio
from mcp.types import CallToolResult, TextContent

from src.mcp_server.context import TtlCache
from src.mcp_server.tools.admin import ADMIN_ONLY_MESSAGE, ADMIN_PREFIX

logger = logging.getLogger("mcp_ogv.admin")


def _tool_name(tool: Any) -> str:
    name = tool.get("name") if isinstance(tool, dict) else getattr(tool, "name", "")
    return name if isinstance(name, str) else ""


class AdminGateMiddleware:
    """Прячет тулы admin_* от не-администраторов Directum и не даёт вызвать их по известному имени.

    Проверка кешируется по отпечатку кредов; любая ошибка проверки — «не администратор».
    """

    def __init__(self, provider: Any, cache: TtlCache):
        self.provider = provider
        self.cache = cache

    async def __call__(self, ctx: Any, call_next: Any) -> Any:
        request = getattr(ctx, "request", None)
        headers = request.headers if request is not None else None
        if ctx.method == "tools/list":
            result = await call_next(ctx)
            if await anyio.to_thread.run_sync(self._is_admin, headers):
                return result
            data = result.model_dump(by_alias=True, exclude_none=True) if hasattr(result, "model_dump") else dict(result)
            data["tools"] = [tool for tool in data.get("tools", []) if not _tool_name(tool).startswith(ADMIN_PREFIX)]
            return data
        if ctx.method == "tools/call":
            name = (ctx.params or {}).get("name", "")
            if isinstance(name, str) and name.startswith(ADMIN_PREFIX):
                if not await anyio.to_thread.run_sync(self._is_admin, headers):
                    return CallToolResult(content=[TextContent(type="text", text=ADMIN_ONLY_MESSAGE)], is_error=True)
        return await call_next(ctx)

    def _is_admin(self, headers: Any) -> bool:
        try:
            with self.provider.open(headers) as (credentials, services):
                cached = self.cache.get(credentials.fingerprint)
                if cached is None:
                    cached = bool(services.admin_access.is_admin())
                    self.cache.set(credentials.fingerprint, cached)
                return cached
        except Exception as exc:
            logger.warning("Directum admin check failed, admin tools hidden: %s", type(exc).__name__)
            return False
```

`src/mcp_server/app.py`:

- импорт `from src.mcp_server.admin_gate import AdminGateMiddleware`;
- константа рядом с `NATIVE_TOOLS_TTL_SECONDS`: `ADMIN_CHECK_TTL_SECONDS = 600`;
- в `build_server`:

```python
    native = NativeProxyMiddleware(provider, TtlCache(NATIVE_TOOLS_TTL_SECONDS), usage)
    admin_gate = AdminGateMiddleware(provider, TtlCache(ADMIN_CHECK_TTL_SECONDS))
    # Порядок outermost-first: gate видит итоговый список, включая drx_native_*.
    mcp = MCPServer(SERVER_NAME, instructions=INSTRUCTIONS, middleware=[admin_gate, native])
```

- в `INSTRUCTIONS` после пункта 6 добавить строку:
  `7. Инструменты admin_* видны только администраторам Directum RX: ими смотри данные других сотрудников, когда об этом явно просят.`

- [ ] **Step 4: Run tests**

Run: `pytest tests/unit/ -q`
Expected: всё PASS (включая `test_mcp_native.py`: у его фейковых сервисов нет `admin_access` — gate молча скрывает `admin_*`, нативные тулы на месте).

- [ ] **Step 5: Commit**

```bash
git add src/mcp_server/admin_gate.py src/mcp_server/app.py tests/unit/test_mcp_admin_gate.py
git commit -m "feat(mcp): скрывать admin_* тулы от не-администраторов Directum"
```

---

### Task 5: Live-проверка, README, ручная проверка на стенде

**Files:**
- Modify: `tests/live/test_mcp_live.py`, `README.md`

**Interfaces:**
- Consumes: всё из Task 1–4.

- [ ] **Step 1: Добавить live-тест** в конец `tests/live/test_mcp_live.py`:

```python
def test_live_admin_tool_visibility_matches_role(server):
    visible = "admin_list_employee_action_items" in [tool.name for tool in list_tools(server)]
    me = payload(call_tool(server, "get_current_user"))

    if visible:
        data = payload(call_tool(
            server, "admin_list_employee_action_items",
            {"employee": str(me["id"]), "direction": "incoming", "status": "all", "limit": 3},
        ))
        assert data["employee"]["id"] == me["id"]
        assert data["total"] is not None
```

- [ ] **Step 2: Прогнать live под администратором и под обычным пользователем**

```bash
MCP_LIVE_BASE_URL=https://<стенд>/Integration/odata MCP_LIVE_LOGIN=<админ> MCP_LIVE_PASSWORD=<пароль> pytest tests/live -v
MCP_LIVE_BASE_URL=https://<стенд>/Integration/odata MCP_LIVE_LOGIN=<пользователь> MCP_LIVE_PASSWORD=<пароль> pytest tests/live -v
```
Expected: 6 passed в обоих прогонах (креды — только в переменных окружения разовой команды).

- [ ] **Step 3: README** — в разделе «mcpOGV — MCP-сервер для LibreChat», в списке «Состав (этап 1)» после строки про курируемые тулы добавить пункт:

```markdown
- только для администраторов DRX (роль «Администраторы», прямое членство): `admin_list_employee_action_items` — поручения любого сотрудника (входящие/исходящие, статус, просрочка, период). Не-администраторам тул не показывается и не вызывается;
```

- [ ] **Step 4: Полный прогон**

Run: `pytest tests/ --ignore=tests/e2e -q`
Expected: всё PASS (5+1 live пропущены без `MCP_LIVE_*`).

- [ ] **Step 5: Commit**

```bash
git add tests/live/test_mcp_live.py README.md
git commit -m "test(mcp): live-проверка admin-тула, README"
```

- [ ] **Step 6: Ручная проверка по HTTP** — перезапустить сервер (`C:\Users\vm-operator\mcp-ogv-run\start-mcp.bat`), через MCP-клиент:
  под заголовками Administrator — `admin_list_employee_action_items` в `tools/list`, вызов `{"employee": "Концева", "direction": "outgoing", "status": "all"}` возвращает поручения nadya;
  под заголовками nadya — тула нет в `tools/list`, вызов по имени → «Инструмент доступен только администраторам Directum RX.»
