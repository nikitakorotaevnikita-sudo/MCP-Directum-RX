# mcpOGV — этап 1: ядро, универсальный слой, прокси родного MCP, перенос тулов

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Работающий MCP-сервер `mcpOGV` (Streamable HTTP) поверх API Directum RX с личными кредами пользователя: курируемые тулы чтения (перенос существующих), универсальный слой OData, справочники доменов и прокси родного MCP DRX.

**Architecture:** Отдельный процесс `python -m src.mcp_server` в этом репозитории. Тулы — тонкие async-обёртки над существующим сервисным слоем `src/services/`; сервисы собираются на каждый вызов из кредов, пришедших в заголовках от LibreChat. Синхронные вызовы DRX выполняются в потоке (`anyio.to_thread`), ошибки превращаются в MCP tool error с русским текстом. Родные тулы DRX добавляются в `tools/list` и проксируются через `ServerMiddleware` в `IntegrationAIAgent/HandleMcpRequest`.

**Tech Stack:** Python 3.11, `mcp==2.2.0` (`mcp.server.mcpserver.MCPServer`), Starlette/uvicorn, httpx (OData-клиент), pydantic-settings, pytest, anyio.

**Spec:** `docs/superpowers/specs/2026-09-25-mcp-ogv-server-design.md`

## Global Constraints

- Python ≥ 3.10 (venv — 3.11). Зависимость: `mcp==2.2.0`, версия закреплена.
- Установка зависимостей: в venv нет pip — только `uv pip install --python .venv\Scripts\python.exe -e ".[dev]"`.
- Имя сервера `mcpOGV`; Docker-сервис и хост `mcp-ogv`; порт `8010`; путь `/mcp`.
- Заголовки: `X-Directum-Login`, `X-Directum-Password`, `X-MCP-Key`.
- Лимиты списков: по умолчанию 20, максимум 100; универсальный слой — максимум 50. Формат списка: `{items, total, returned, truncated}`; `total` = `null`, когда общее число неизвестно.
- Все сообщения пользователю и описания тулов — на русском.
- Секреты (пароли, токены, ключи) — никогда в код, тесты, документацию, коммиты. Креды для live-проверок — только через переменные окружения.
- `.env` не создавать и не редактировать; новые ключи — только в `.env.example`.
- Запрещённые действия DRX (не вызывать ни в одном слое): `CoreEntities.*`, `Company.CreateLogin`, `Company.SetLoginPassword`, `Shell.AddUserToGroup`, `Shell.RemoveUserFromGroup`, `Docflow.GrantAccessRights*`, `SmartProcessing.ElasticsearchReindex`, `Company.*TransferSubstitutedAccessRights`. На этапе 1 записи в DRX нет вообще.
- В SDK v2 атрибуты моделей в Python — snake_case: `ToolAnnotations(read_only_hint=True)`, `result.is_error`, `result.structured_content`.
- TDD: тест до реализации. Перед любым `git push` — полный прогон тестов.
- Команды — PowerShell; Python — `& ".venv\Scripts\python.exe"`.
- Коммит-сообщения заканчиваются строкой `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Scope этапа 1 и отличия от спеки

Входит: разделы спеки 4, 5, 8, 9, 10 (без аудита записи), 11, 12 и тулы раздела 6 из групп «Общие», «Поручения» (без `get_action_items_dashboard`, `get_discipline_by_performers`), «Документы», «Совещания», слои 2–4.

Сознательно перенесено в следующие планы:
- `list_action_items(on_control)`, `get_action_items_dashboard`, `get_discipline_by_performers`, `list_my_reviews`, `list_my_approvals` — этап 3.
- Обращения граждан — этап 2; `list_legal_acts` — этап 4; справочники ОГВ-модулей пишутся вместе с тулами своего домена.
- Запись (`preview_*`, `confirm_operation`, `confirm_store`, аудит записи, `MCP_CONFIRM_TOKEN_TTL`) — этап 5.

Дополнения к спеке: настройки `MCP_HOST`, `MCP_USAGE_DB_PATH`, `MCP_DIRECTUM_TIMEOUT_SECONDS` (таймаут DRX для MCP, по умолчанию 20 с, отдельно от таймаута чата).

Уточнение к разделу 10 спеки: лог вызова содержит тул, статус, вид ошибки и длительность; логин в лог не пишется — пользователя в метриках идентифицирует усечённый хеш кредов. Correlation-id выдаётся для непредвиденных ошибок.

## File Structure

| Файл | Ответственность |
|---|---|
| `src/services/directum_client.py` (изм.) | строковые ошибки OData, `get_metadata_xml()` |
| `src/services/current_user.py` (изм.) | `prime()`, `cached_user` |
| `src/services/factory.py` (нов.) | `DirectumServices`, `build_directum_services()` |
| `src/services/outgoing_analytics.py` (нов.) | раскладка исходящих поручений по срокам |
| `src/services/assignments.py` (изм.) | `top` в списках, `count_my_assignments`, `count_action_items` |
| `src/main.py` (изм.) | сборка сервисов через factory |
| `src/services/llm_service.py` (изм.) | использует `outgoing_analytics` |
| `src/mcp_server/__init__.py` | пакет |
| `src/mcp_server/config.py` | `McpSettings` |
| `src/mcp_server/context.py` | креды из заголовков, `TtlCache`, `ServicesProvider` |
| `src/mcp_server/envelope.py` | лимиты, формат списков, JSON-сериализация |
| `src/mcp_server/errors.py` | исключения → `ToolError` |
| `src/mcp_server/audit.py` | `ToolUsageStore` (метрики использования в SQLite) |
| `src/mcp_server/runner.py` | `ToolRunner`, `READ_ONLY` |
| `src/mcp_server/app.py` | `build_server`, `build_asgi_app`, `McpKeyMiddleware`, `/health` |
| `src/mcp_server/__main__.py` | запуск uvicorn |
| `src/mcp_server/tools/common.py` | `get_current_user`, `search_employees` |
| `src/mcp_server/tools/action_items.py` | задания, поручения, дисциплина, аналитика исходящих |
| `src/mcp_server/tools/documents.py` | документы, письма, совещания |
| `src/mcp_server/odata_meta.py` | разбор и кеш `$metadata`, чёрный список |
| `src/mcp_server/tools/odata.py` | универсальный слой чтения |
| `src/mcp_server/resources.py` | справочники `drx://domains/*` |
| `src/mcp_server/native.py` | клиент и middleware родного MCP DRX |
| `tests/unit/mcp_fakes.py` | тестовые дублёры и хелперы клиента MCP |
| `tests/unit/test_mcp_*.py`, `tests/integration/test_mcp_http.py`, `tests/live/test_mcp_live.py` | тесты |
| `Dockerfile`, `docker-compose.yml`, `.env.example`, `pyproject.toml`, `README.md`, `docs/mcp-ogv/librechat.example.yaml` | деплой-артефакты для отдельного стенда |

---

### Task 1: Зависимость MCP SDK и доработки `DirectumClient`

**Files:**
- Modify: `pyproject.toml` (секция `dependencies`)
- Modify: `src/services/directum_client.py` (`_odata_error_message`, новый метод `get_metadata_xml`)
- Test: `tests/unit/test_directum_client.py`

**Interfaces:**
- Produces: `DirectumClient.get_metadata_xml() -> str`; `DirectumError.safe_message` теперь содержит текст строковой ошибки OData (`{"error": "..."}`).

- [ ] **Step 1: Добавить зависимость и установить**

В `pyproject.toml` в список `dependencies` добавить строку:

```toml
  "mcp==2.2.0",
```

Установить:

```powershell
uv pip install --python .venv\Scripts\python.exe -e ".[dev]"
& ".venv\Scripts\python.exe" -c "import importlib.metadata as m; print(m.version('mcp'))"
```

Expected: `2.2.0`

- [ ] **Step 2: Написать падающие тесты**

Добавить в конец `tests/unit/test_directum_client.py` (если в файле нет `import pytest` — добавить его к импортам):

```python
def test_error_detail_supports_plain_string_error():
    client = DirectumClient(
        base_url="https://rx.example/Integration/odata",
        auth_token="Basic token",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                400,
                json={"error": "Превышено максимальное количество сущностей в запросе. Используйте фильтрацию."},
            )
        ),
    )

    with pytest.raises(DirectumError) as info:
        client.query("IAssignments", top=2)

    assert "Используйте фильтрацию" in info.value.safe_message
    assert info.value.status_code == 400


def test_get_metadata_xml_returns_raw_text():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["accept"] = request.headers.get("accept")
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, text="<edmx:Edmx/>")

    client = DirectumClient(
        base_url="https://rx.example/Integration/odata",
        auth_token="Basic token",
        transport=httpx.MockTransport(handler),
    )

    assert client.get_metadata_xml() == "<edmx:Edmx/>"
    assert seen["url"].endswith("/$metadata")
    assert "xml" in seen["accept"]
    assert seen["auth"] == "Basic token"


def test_get_metadata_xml_raises_on_error_status():
    client = DirectumClient(
        base_url="https://rx.example/Integration/odata",
        auth_token="Basic token",
        transport=httpx.MockTransport(lambda request: httpx.Response(401)),
    )

    with pytest.raises(DirectumError) as info:
        client.get_metadata_xml()

    assert info.value.status_code == 401
```

- [ ] **Step 3: Запустить — должны упасть**

```powershell
& ".venv\Scripts\python.exe" -m pytest tests/unit/test_directum_client.py -k "plain_string_error or metadata_xml" -v
```

Expected: FAIL — `assert "Используйте фильтрацию" in ...` и `AttributeError: 'DirectumClient' object has no attribute 'get_metadata_xml'`.

- [ ] **Step 4: Реализация**

В `src/services/directum_client.py` в методе `_odata_error_message` сразу после строки `error = data.get("error")` вставить:

```python
        if isinstance(error, str):
            return error
```

В класс `DirectumClient` после метода `get_one` добавить:

```python
    def get_metadata_xml(self) -> str:
        response = self.client.get(
            self.build_url("$metadata"),
            headers={"Authorization": self.auth_token, "Accept": "application/xml"},
        )
        if response.status_code >= 400:
            raise DirectumError(
                safe_message=f"Directum metadata request failed with status {response.status_code}",
                status_code=response.status_code,
            )
        return response.text
```

- [ ] **Step 5: Запустить — должны пройти, остальные не сломаны**

```powershell
& ".venv\Scripts\python.exe" -m pytest tests/unit/test_directum_client.py -v
```

Expected: все PASS.

- [ ] **Step 6: Commit**

```powershell
git add pyproject.toml src/services/directum_client.py tests/unit/test_directum_client.py
git commit -m "feat(mcp): зависимость mcp 2.2.0, строковые ошибки OData, get_metadata_xml" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Фабрика сервисов и предзагрузка текущего пользователя

**Files:**
- Create: `src/services/factory.py`
- Modify: `src/services/current_user.py`
- Modify: `src/main.py` (функция `build_services`)
- Test: `tests/unit/test_services_factory.py`, `tests/unit/test_current_user.py`

**Interfaces:**
- Consumes: `DirectumClient(base_url, auth_token, timeout, transport=...)`, конструкторы сервисов из `src/services/`.
- Produces:
  - `@dataclass DirectumServices(client, current_user, assignments, action_items, meetings, discipline)` с методом `close() -> None`.
  - `build_directum_services(base_url: str, auth_token: str, timeout: float = 30.0, transport: httpx.BaseTransport | None = None) -> DirectumServices`.
  - `CurrentUserService.prime(user: DirectumUser) -> None`, свойство `CurrentUserService.cached_user -> DirectumUser | None`.

- [ ] **Step 1: Падающие тесты**

Создать `tests/unit/test_services_factory.py`:

```python
import httpx

from src.services.factory import DirectumServices, build_directum_services


def test_build_directum_services_wires_one_shared_client():
    services = build_directum_services(
        "https://rx.example/Integration/odata",
        "Basic bG9naW46cGFzcw==",
        12.5,
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"value": []})),
    )

    assert isinstance(services, DirectumServices)
    assert services.client.base_url == "https://rx.example/Integration/odata"
    assert services.current_user.client is services.client
    assert services.assignments.client is services.client
    assert services.assignments.current_user_service is services.current_user
    assert services.action_items.client is services.client
    assert services.meetings.client is services.client
    assert services.discipline.client is services.client
    services.close()
    assert services.client.client.is_closed
```

Добавить в конец `tests/unit/test_current_user.py`:

```python
class _NoQueryClient:
    def query(self, *args, **kwargs):
        raise AssertionError("prime() must prevent the lookup")


def test_prime_makes_lookup_unnecessary():
    from src.models.schemas import DirectumUser
    from src.services.current_user import CurrentUserService

    service = CurrentUserService(_NoQueryClient(), "Basic bG9naW46cGFzcw==")
    user = DirectumUser(id=63, name="Концева Надежда Ивановна", login="login")

    assert service.cached_user is None
    service.prime(user)

    assert service.get_current_user() is user
    assert service.cached_user is user
```

- [ ] **Step 2: Запустить — должны упасть**

```powershell
& ".venv\Scripts\python.exe" -m pytest tests/unit/test_services_factory.py tests/unit/test_current_user.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'src.services.factory'` и `AttributeError: ... 'cached_user'`.

- [ ] **Step 3: Реализация**

В `src/services/current_user.py` в класс `CurrentUserService` после `__init__` добавить:

```python
    @property
    def cached_user(self) -> DirectumUser | None:
        return self._cached_user

    def prime(self, user: DirectumUser) -> None:
        self._cached_user = user
```

Создать `src/services/factory.py`:

```python
from dataclasses import dataclass

import httpx

from src.services.action_items import ActionItemService
from src.services.assignments import AssignmentsService
from src.services.current_user import CurrentUserService
from src.services.directum_client import DirectumClient
from src.services.discipline_analytics import DisciplineAnalyticsService
from src.services.meetings import MeetingsService


@dataclass
class DirectumServices:
    """Сервисы Directum RX, собранные поверх одного клиента с одними кредами."""

    client: DirectumClient
    current_user: CurrentUserService
    assignments: AssignmentsService
    action_items: ActionItemService
    meetings: MeetingsService
    discipline: DisciplineAnalyticsService

    def close(self) -> None:
        self.client.close()


def build_directum_services(
    base_url: str,
    auth_token: str,
    timeout: float = 30.0,
    transport: httpx.BaseTransport | None = None,
) -> DirectumServices:
    client = DirectumClient(base_url, auth_token, timeout, transport=transport)
    current_user = CurrentUserService(client, auth_token)
    action_items = ActionItemService(client)
    return DirectumServices(
        client=client,
        current_user=current_user,
        assignments=AssignmentsService(client, current_user),
        action_items=action_items,
        meetings=MeetingsService(client, current_user),
        discipline=DisciplineAnalyticsService(client, action_items),
    )
```

В `src/main.py` заменить тело `build_services` целиком на:

```python
def build_services(settings: Settings, testing: bool = False) -> dict[str, Any]:
    transport = _mock_transport() if testing else None
    auth_token = settings.directum_headers()["Authorization"]
    directum = build_directum_services(
        settings.directum_base_url,
        auth_token,
        settings.DIRECTUM_REQUEST_TIMEOUT_SECONDS,
        transport=transport,
    )
    metrics = MetricsStorage(settings.METRICS_DB_PATH)
    metrics.initialize()
    registry = ToolRegistry(
        directum.current_user,
        directum.assignments,
        directum.action_items,
        directum.meetings,
        directum.discipline,
    )
    llm = build_llm_service(settings, registry, testing=testing)
    return {
        "directum": directum.client,
        "current_user": directum.current_user,
        "assignments": directum.assignments,
        "action_items": directum.action_items,
        "meetings": directum.meetings,
        "discipline": directum.discipline,
        "metrics": metrics,
        "registry": registry,
        "llm": llm,
    }
```

Добавить в импорты `src/main.py`:

```python
from src.services.factory import build_directum_services
```

Затем проверить, какие старые импорты больше не нужны:

```powershell
Select-String -Path src\main.py -Pattern "CurrentUserService|AssignmentsService|ActionItemService|MeetingsService|DisciplineAnalyticsService|DirectumClient\("
```

Удалить из импортов только те классы, которые больше нигде в `src/main.py` не встречаются (кроме строки самого импорта). `DirectumClient` и `DirectumError` оставить, если на них есть ссылки.

- [ ] **Step 4: Запустить весь unit-набор**

```powershell
& ".venv\Scripts\python.exe" -m pytest tests/unit -q
```

Expected: все PASS (раньше было 206, плюс 2 новых).

- [ ] **Step 5: Commit**

```powershell
git add src/services/factory.py src/services/current_user.py src/main.py tests/unit/test_services_factory.py tests/unit/test_current_user.py
git commit -m "refactor: фабрика сервисов Directum и предзагрузка текущего пользователя" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Вынести раскладку исходящих поручений в сервис

**Files:**
- Create: `src/services/outgoing_analytics.py`
- Modify: `src/services/llm_service.py` (`_deadline_from_item`, `_format_outgoing_action_item_analytics`)
- Test: `tests/unit/test_outgoing_analytics.py`

**Interfaces:**
- Produces:
  - `parse_deadline_value(raw: Any, fallback: Callable[[str], datetime | None] | None = None) -> datetime | None` — всегда возвращает aware datetime или `None`.
  - `categorize_outgoing(items: list[dict], now: datetime | None = None, fallback=None) -> dict[str, list[dict]]` с ключами `work`, `due_soon`, `overdue` (срок ≤ now+1 день → `due_soon`, срок < now → `overdue`, нет срока → `work`).

- [ ] **Step 1: Падающие тесты**

Создать `tests/unit/test_outgoing_analytics.py`:

```python
from datetime import datetime, timedelta, timezone

from src.services.outgoing_analytics import categorize_outgoing, parse_deadline_value

NOW = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)


def test_categorize_outgoing_by_deadline():
    items = [
        {"id": 1, "deadline": (NOW + timedelta(days=3)).isoformat()},
        {"id": 2, "deadline": (NOW + timedelta(hours=12)).isoformat()},
        {"id": 3, "deadline": (NOW - timedelta(hours=1)).isoformat()},
        {"id": 4, "deadline": None},
    ]

    groups = categorize_outgoing(items, now=NOW)

    assert [item["id"] for item in groups["work"]] == [1, 4]
    assert [item["id"] for item in groups["due_soon"]] == [2]
    assert [item["id"] for item in groups["overdue"]] == [3]


def test_parse_deadline_value_handles_z_and_naive_strings():
    assert parse_deadline_value("2026-09-25T10:00:00Z") == datetime(2026, 9, 25, 10, tzinfo=timezone.utc)
    assert parse_deadline_value("2026-09-25T10:00:00") == datetime(2026, 9, 25, 10, tzinfo=timezone.utc)


def test_parse_deadline_value_uses_fallback_for_non_iso_text():
    assert parse_deadline_value("завтра", fallback=lambda text: NOW) == NOW
    assert parse_deadline_value("завтра") is None


def test_parse_deadline_value_accepts_datetime_and_empty():
    assert parse_deadline_value(datetime(2026, 9, 25, 10)) == datetime(2026, 9, 25, 10, tzinfo=timezone.utc)
    assert parse_deadline_value(None) is None
    assert parse_deadline_value("   ") is None
```

- [ ] **Step 2: Запустить — должны упасть**

```powershell
& ".venv\Scripts\python.exe" -m pytest tests/unit/test_outgoing_analytics.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'src.services.outgoing_analytics'`.

- [ ] **Step 3: Реализация**

Создать `src/services/outgoing_analytics.py`:

```python
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

DeadlineParser = Callable[[str], datetime | None]


def _as_aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def parse_deadline_value(raw: Any, fallback: DeadlineParser | None = None) -> datetime | None:
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return _as_aware(raw)
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        parsed = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    except ValueError:
        parsed = fallback(raw) if fallback is not None else None
    return _as_aware(parsed) if parsed is not None else None


def categorize_outgoing(
    items: list[dict[str, Any]],
    now: datetime | None = None,
    fallback: DeadlineParser | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Раскладывает поручения по срокам: в работе / срок в ближайшие сутки / просрочено."""
    current = now or datetime.now(timezone.utc)
    due_soon_limit = current + timedelta(days=1)
    groups: dict[str, list[dict[str, Any]]] = {"work": [], "due_soon": [], "overdue": []}
    for item in items:
        deadline = parse_deadline_value(item.get("deadline"), fallback)
        if deadline is not None and deadline < current:
            groups["overdue"].append(item)
        elif deadline is not None and deadline <= due_soon_limit:
            groups["due_soon"].append(item)
        else:
            groups["work"].append(item)
    return groups
```

В `src/services/llm_service.py`:

1. Добавить импорт: `from src.services.outgoing_analytics import categorize_outgoing, parse_deadline_value`.
2. Заменить тело метода `_deadline_from_item` целиком на:

```python
    def _deadline_from_item(self, item: dict[str, Any]) -> datetime | None:
        return parse_deadline_value(item.get("deadline"), self._parse_deadline)
```

3. В методе `_format_outgoing_action_item_analytics` заменить блок от строки `now = datetime.now(timezone.utc)` до последней строки цикла `categories["work"].append(item)` включительно на:

```python
        categories = categorize_outgoing(items, fallback=self._parse_deadline)
```

(Блок, который удаляется: присваивания `now`, `due_soon_limit`, словарь `categories` с тремя пустыми списками и цикл `for item in items:` с ветками `overdue` / `due_soon` / `work`.)

- [ ] **Step 4: Запустить новые и существующие тесты аналитики**

```powershell
& ".venv\Scripts\python.exe" -m pytest tests/unit/test_outgoing_analytics.py tests/unit/test_llm_service.py -q
```

Expected: все PASS.

- [ ] **Step 5: Commit**

```powershell
git add src/services/outgoing_analytics.py src/services/llm_service.py tests/unit/test_outgoing_analytics.py
git commit -m "refactor: раскладка исходящих поручений по срокам вынесена в сервис" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Лимиты и счётчики в `AssignmentsService`

**Files:**
- Modify: `src/services/assignments.py`
- Test: `tests/unit/test_assignments.py`

**Interfaces:**
- Produces:
  - `get_my_assignments(top: int | None = None)`, `get_overdue_assignments(top: int | None = None)`, `get_action_items_assigned_to_me(top: int | None = None)`, `get_action_items_created_by_me(top: int | None = None)` — прежнее поведение при `top=None`.
  - `count_my_assignments(only_overdue: bool = False) -> int`.
  - `count_action_items(direction: str) -> int`, где `direction` ∈ {`"incoming"`, `"outgoing"`}; иначе `ValueError`.

- [ ] **Step 1: Падающие тесты**

Добавить в конец `tests/unit/test_assignments.py`:

```python
import pytest


class CountingClient(FakeClient):
    def count(self, entity_set, filter_=None):
        self.calls.append((entity_set, {"count_filter": filter_}))
        return 1191


def test_assignment_lists_pass_top_to_odata():
    client = FakeClient()
    service = AssignmentsService(client=client, current_user_service=FakeCurrentUser())

    service.get_my_assignments(top=21)
    service.get_overdue_assignments(top=21)
    service.get_action_items_assigned_to_me(top=21)
    service.get_action_items_created_by_me(top=21)

    assert [kwargs["top"] for _, kwargs in client.calls] == [21, 21, 21, 21]


def test_count_my_assignments_uses_list_filter():
    client = CountingClient()
    service = AssignmentsService(client=client, current_user_service=FakeCurrentUser())

    assert service.count_my_assignments() == 1191
    entity_set, kwargs = client.calls[-1]
    assert entity_set == "IAssignments"
    assert kwargs["count_filter"] == "Performer/Id eq 1165 and Status eq 'InProcess'"


def test_count_overdue_assignments_adds_deadline_condition():
    client = CountingClient()
    service = AssignmentsService(client=client, current_user_service=FakeCurrentUser())

    service.count_my_assignments(only_overdue=True)

    _, kwargs = client.calls[-1]
    assert kwargs["count_filter"].startswith("Performer/Id eq 1165 and Status eq 'InProcess' and Deadline lt ")


def test_count_action_items_by_direction():
    client = CountingClient()
    service = AssignmentsService(client=client, current_user_service=FakeCurrentUser())

    service.count_action_items("incoming")
    service.count_action_items("outgoing")

    assert client.calls[-2] == ("IActionItemExecutionAssignments", {"count_filter": "Performer/Id eq 1165 and Status eq 'InProcess'"})
    assert client.calls[-1] == ("IActionItemExecutionTasks", {"count_filter": "Author/Id eq 1165 and Status eq 'InProcess'"})


def test_count_action_items_rejects_unknown_direction():
    service = AssignmentsService(client=CountingClient(), current_user_service=FakeCurrentUser())

    with pytest.raises(ValueError):
        service.count_action_items("sideways")
```

- [ ] **Step 2: Запустить — должны упасть**

```powershell
& ".venv\Scripts\python.exe" -m pytest tests/unit/test_assignments.py -v
```

Expected: FAIL — `TypeError: ... unexpected keyword argument 'top'` и `AttributeError: ... 'count_my_assignments'`.

- [ ] **Step 3: Реализация**

В `src/services/assignments.py` заменить четыре публичных метода-списка (от `def get_my_assignments` до конца `get_action_items_created_by_me`) на:

```python
    ACTION_ITEM_SOURCES = {
        "incoming": ("IActionItemExecutionAssignments", "Performer"),
        "outgoing": ("IActionItemExecutionTasks", "Author"),
    }

    def get_my_assignments(self, top: int | None = None) -> list[AssignmentSummary]:
        rows = self.client.query(
            "IAssignments",
            filter_=self._my_assignments_filter(only_overdue=False),
            select="Id,Subject,Deadline,Status",
            expand="Task($select=Id,Subject)",
            orderby="Deadline asc",
            top=top,
            count=True,
        )
        return [self._assignment(row, "assignment") for row in rows]

    def get_overdue_assignments(self, top: int | None = None) -> list[AssignmentSummary]:
        rows = self.client.query(
            "IAssignments",
            filter_=self._my_assignments_filter(only_overdue=True),
            select="Id,Subject,Deadline,Status",
            orderby="Deadline asc",
            top=top,
        )
        return [self._assignment(row, "overdue_assignment") for row in rows]

    def get_action_items_assigned_to_me(self, top: int | None = None) -> list[AssignmentSummary]:
        entity_set, action_filter = self._action_items_source("incoming")
        rows = self.client.query(
            entity_set,
            filter_=action_filter,
            select="Id,Subject,Deadline,Status",
            orderby="Deadline asc",
            top=top,
        )
        return [self._assignment(row, "action_item_assignment") for row in rows]

    def get_action_items_created_by_me(self, top: int | None = None) -> list[AssignmentSummary]:
        entity_set, action_filter = self._action_items_source("outgoing")
        rows = self.client.query(
            entity_set,
            filter_=action_filter,
            select="Id,Subject,Deadline,Status",
            expand="Assignee($select=Name)",
            orderby="Deadline asc",
            top=top,
        )
        return [self._assignment(row, "action_item_task") for row in rows]

    def count_my_assignments(self, only_overdue: bool = False) -> int:
        return self.client.count("IAssignments", filter_=self._my_assignments_filter(only_overdue))

    def count_action_items(self, direction: str) -> int:
        entity_set, action_filter = self._action_items_source(direction)
        return self.client.count(entity_set, filter_=action_filter)

    def _my_assignments_filter(self, only_overdue: bool) -> str:
        user = self.current_user_service.get_current_user()
        base = f"Performer/Id eq {user.id} and Status eq 'InProcess'"
        if not only_overdue:
            return base
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        return f"{base} and Deadline lt {now}"

    def _action_items_source(self, direction: str) -> tuple[str, str]:
        if direction not in self.ACTION_ITEM_SOURCES:
            raise ValueError(f"Unknown action items direction: {direction}")
        entity_set, role = self.ACTION_ITEM_SOURCES[direction]
        user = self.current_user_service.get_current_user()
        return entity_set, f"{role}/Id eq {user.id} and Status eq 'InProcess'"
```

- [ ] **Step 4: Запустить unit-набор**

```powershell
& ".venv\Scripts\python.exe" -m pytest tests/unit -q
```

Expected: все PASS.

- [ ] **Step 5: Commit**

```powershell
git add src/services/assignments.py tests/unit/test_assignments.py
git commit -m "feat: лимиты и счётчики заданий и поручений в AssignmentsService" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Настройки и контекст запроса MCP (личные креды)

**Files:**
- Create: `src/mcp_server/__init__.py`, `src/mcp_server/config.py`, `src/mcp_server/context.py`
- Test: `tests/unit/test_mcp_context.py`

**Interfaces:**
- Consumes: `build_directum_services`, `CurrentUserService.prime/cached_user`, `build_basic_auth_token(username, password) -> str`.
- Produces:
  - `McpSettings` с полями `DIRECTUM_BASE_URL`, `DIRECTUM_AUTH_TOKEN`, `MCP_DIRECTUM_TIMEOUT_SECONDS` (20.0), `MCP_HOST` ("0.0.0.0"), `MCP_PORT` (8010), `MCP_ALLOWED_HOSTS`, `MCP_OGV_KEY`, `MCP_ALLOW_ENV_CREDENTIALS` (False), `MCP_USAGE_DB_PATH`; свойства `directum_base_url`, `allowed_hosts: list[str]`, `mcp_key: str | None`, `env_auth_token: str | None`.
  - `Credentials(auth_token: str, fingerprint: str)` (frozen; `auth_token` не попадает в `repr`).
  - `credentials_from_headers(headers: Mapping[str, str] | None, settings: McpSettings) -> Credentials` (бросает `ToolError`).
  - `TtlCache(ttl_seconds: float, clock=time.monotonic)` с `get(key)` / `set(key, value)`.
  - `ServicesProvider(settings, transport=None, user_cache=None)` с контекстным менеджером `open(headers) -> (Credentials, DirectumServices)`.
  - Константы `LOGIN_HEADER = "x-directum-login"`, `PASSWORD_HEADER = "x-directum-password"`, `MISSING_CREDENTIALS_MESSAGE`.

- [ ] **Step 1: Падающие тесты**

Создать `tests/unit/test_mcp_context.py`:

```python
import base64

import httpx
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from src.mcp_server.config import McpSettings
from src.mcp_server.context import ServicesProvider, TtlCache, credentials_from_headers


def make_settings(**overrides):
    values = {"DIRECTUM_BASE_URL": "https://rx.example/Integration/odata/", "MCP_OGV_KEY": "k", "_env_file": None}
    values.update(overrides)
    return McpSettings(**values)


def basic(login: str, password: str) -> str:
    return "Basic " + base64.b64encode(f"{login}:{password}".encode()).decode()


def test_settings_normalize_url_hosts_and_secrets():
    settings = make_settings(MCP_ALLOWED_HOSTS=" a:1 , b:2 ,", MCP_OGV_KEY="  ")

    assert settings.directum_base_url == "https://rx.example/Integration/odata"
    assert settings.allowed_hosts == ["a:1", "b:2"]
    assert settings.mcp_key is None
    assert settings.MCP_PORT == 8010
    assert settings.MCP_DIRECTUM_TIMEOUT_SECONDS == 20.0


def test_credentials_from_headers_builds_basic_token_case_insensitively():
    credentials = credentials_from_headers({"X-Directum-Login": "user1", "X-Directum-Password": "pw"}, make_settings())

    assert credentials.auth_token == basic("user1", "pw")
    assert len(credentials.fingerprint) == 64
    assert "pw" not in repr(credentials)


def test_missing_credentials_raise_tool_error():
    with pytest.raises(ToolError, match="Укажите логин и пароль Directum"):
        credentials_from_headers({}, make_settings())


def test_env_credentials_only_in_debug_mode():
    token = basic("env", "pass")

    debug = make_settings(MCP_ALLOW_ENV_CREDENTIALS=True, DIRECTUM_AUTH_TOKEN=token)
    assert credentials_from_headers(None, debug).auth_token == token

    with pytest.raises(ToolError):
        credentials_from_headers(None, make_settings(DIRECTUM_AUTH_TOKEN=token))


def test_ttl_cache_expires_entries():
    now = [100.0]
    cache = TtlCache(10, clock=lambda: now[0])

    cache.set("a", 1)
    assert cache.get("a") == 1
    now[0] = 111.0
    assert cache.get("a") is None


def test_provider_uses_user_token_and_caches_current_user():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers["authorization"])
        return httpx.Response(200, json={"value": [{"Id": 63, "Name": "Концева Надежда Ивановна"}]})

    provider = ServicesProvider(make_settings(), transport=httpx.MockTransport(handler))
    headers = {"x-directum-login": "user1", "x-directum-password": "pw"}

    with provider.open(headers) as (_, services):
        assert services.current_user.get_current_user().id == 63
    with provider.open(headers) as (_, services):
        assert services.current_user.get_current_user().id == 63

    assert seen == [basic("user1", "pw")]


def test_provider_closes_client_when_tool_fails():
    provider = ServicesProvider(
        make_settings(), transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"value": []}))
    )

    with pytest.raises(RuntimeError):
        with provider.open({"x-directum-login": "a", "x-directum-password": "b"}) as (_, services):
            http_client = services.client.client
            raise RuntimeError("boom")

    assert http_client.is_closed
```

- [ ] **Step 2: Запустить — должны упасть**

```powershell
& ".venv\Scripts\python.exe" -m pytest tests/unit/test_mcp_context.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'src.mcp_server'`.

- [ ] **Step 3: Реализация**

Создать `src/mcp_server/__init__.py`:

```python
"""mcpOGV — MCP-сервер поверх API Directum RX."""
```

Создать `src/mcp_server/config.py`:

```python
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
```

Создать `src/mcp_server/context.py`:

```python
import hashlib
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

import httpx
from mcp.server.mcpserver.exceptions import ToolError

from src.mcp_server.config import McpSettings
from src.services.directum_connection import build_basic_auth_token
from src.services.factory import DirectumServices, build_directum_services

LOGIN_HEADER = "x-directum-login"
PASSWORD_HEADER = "x-directum-password"
MISSING_CREDENTIALS_MESSAGE = "Укажите логин и пароль Directum в настройках сервера mcpOGV в LibreChat."
CURRENT_USER_TTL_SECONDS = 600


@dataclass(frozen=True)
class Credentials:
    auth_token: str = field(repr=False)
    fingerprint: str


def credentials_from_headers(headers: Mapping[str, str] | None, settings: McpSettings) -> Credentials:
    lowered = {str(key).lower(): str(value) for key, value in (headers or {}).items()}
    login = lowered.get(LOGIN_HEADER, "").strip()
    password = lowered.get(PASSWORD_HEADER, "")
    if login and password:
        token = build_basic_auth_token(login, password)
    elif settings.MCP_ALLOW_ENV_CREDENTIALS and settings.env_auth_token:
        token = settings.env_auth_token
    else:
        raise ToolError(MISSING_CREDENTIALS_MESSAGE)
    return Credentials(auth_token=token, fingerprint=hashlib.sha256(token.encode("utf-8")).hexdigest())


class TtlCache:
    def __init__(self, ttl_seconds: float, clock: Callable[[], float] = time.monotonic):
        self.ttl_seconds = ttl_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._items: dict[str, tuple[float, Any]] = {}

    def get(self, key: str) -> Any | None:
        with self._lock:
            entry = self._items.get(key)
            if entry is None:
                return None
            expires_at, value = entry
            if self._clock() >= expires_at:
                del self._items[key]
                return None
            return value

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            self._items[key] = (self._clock() + self.ttl_seconds, value)


class ServicesProvider:
    """Собирает сервисы Directum на время одного вызова тула из кредов пользователя."""

    def __init__(
        self,
        settings: McpSettings,
        transport: httpx.BaseTransport | None = None,
        user_cache: TtlCache | None = None,
    ):
        self.settings = settings
        self.transport = transport
        self.user_cache = user_cache or TtlCache(CURRENT_USER_TTL_SECONDS)

    @contextmanager
    def open(self, headers: Mapping[str, str] | None) -> Iterator[tuple[Credentials, DirectumServices]]:
        credentials = credentials_from_headers(headers, self.settings)
        services = build_directum_services(
            self.settings.directum_base_url,
            credentials.auth_token,
            self.settings.MCP_DIRECTUM_TIMEOUT_SECONDS,
            transport=self.transport,
        )
        cached_user = self.user_cache.get(credentials.fingerprint)
        if cached_user is not None:
            services.current_user.prime(cached_user)
        try:
            yield credentials, services
            if services.current_user.cached_user is not None:
                self.user_cache.set(credentials.fingerprint, services.current_user.cached_user)
        finally:
            services.close()
```

- [ ] **Step 4: Запустить — должны пройти**

```powershell
& ".venv\Scripts\python.exe" -m pytest tests/unit/test_mcp_context.py -v
```

Expected: все PASS.

- [ ] **Step 5: Commit**

```powershell
git add src/mcp_server/__init__.py src/mcp_server/config.py src/mcp_server/context.py tests/unit/test_mcp_context.py
git commit -m "feat(mcp): настройки mcpOGV и контекст запроса с личными кредами" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Конвейер выполнения тулов: формат ответа, ошибки, метрики, runner

**Files:**
- Create: `src/mcp_server/envelope.py`, `src/mcp_server/errors.py`, `src/mcp_server/audit.py`, `src/mcp_server/runner.py`
- Create: `tests/unit/mcp_fakes.py`
- Test: `tests/unit/test_mcp_pipeline.py`

**Interfaces:**
- Consumes: `Credentials`, `ServicesProvider.open`, `DirectumError(safe_message, status_code)`.
- Produces:
  - `DEFAULT_LIMIT = 20`, `MAX_LIMIT = 100`, `clamp_limit(limit, default=20, maximum=100) -> int`, `to_jsonable(value) -> Any`, `list_envelope(items, limit, total=None) -> dict`.
  - `to_tool_error(exc: Exception) -> tuple[ToolError, str]`; виды: `tool`, `auth`, `forbidden`, `too_broad`, `directum`, `timeout`, `internal`.
  - `ToolUsageStore(db_path)` с `record(tool, ok, error_kind, duration_ms, user_hash)` и `summary() -> list[dict]` (ключи `tool`, `calls`, `errors`, `avg_duration_ms`).
  - `READ_ONLY = ToolAnnotations(read_only_hint=True)`; `ToolRunner(provider, usage=None)` с `async run(ctx, tool: str, action: Callable[[DirectumServices], T]) -> T`.
  - Тестовые хелперы `FakeProvider`, `call_tool`, `list_tools`, `payload`, `error_text`, `run_async`.

- [ ] **Step 1: Тестовые хелперы**

Создать `tests/unit/mcp_fakes.py`:

```python
"""Тестовые дублёры mcpOGV: без HTTP и без Directum."""

import json
from contextlib import contextmanager

import anyio
from mcp import Client

from src.mcp_server.context import Credentials

FAKE_CREDENTIALS = Credentials(auth_token="Basic fake", fingerprint="f" * 64)


class FakeProvider:
    def __init__(self, services):
        self.services = services
        self.opened_with = []

    @contextmanager
    def open(self, headers):
        self.opened_with.append(headers)
        yield FAKE_CREDENTIALS, self.services


def run_async(func, *args):
    return anyio.run(func, *args)


def call_tool(server, name, arguments=None):
    async def main():
        async with Client(server) as client:
            return await client.call_tool(name, arguments or {})

    return anyio.run(main)


def list_tools(server):
    async def main():
        async with Client(server) as client:
            return (await client.list_tools()).tools

    return anyio.run(main)


def payload(result):
    assert not result.is_error, result.content[0].text
    return json.loads(result.content[0].text)


def error_text(result):
    assert result.is_error, "expected a tool error"
    return result.content[0].text
```

- [ ] **Step 2: Падающие тесты**

Создать `tests/unit/test_mcp_pipeline.py`:

```python
from types import SimpleNamespace

import httpx
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from src.mcp_server.audit import ToolUsageStore
from src.mcp_server.envelope import clamp_limit, list_envelope, to_jsonable
from src.mcp_server.errors import to_tool_error
from src.mcp_server.runner import READ_ONLY, ToolRunner
from src.models.schemas import DirectumUser
from src.services.directum_client import DirectumError
from tests.unit.mcp_fakes import FakeProvider, run_async


def test_clamp_limit_bounds():
    assert clamp_limit(None) == 20
    assert clamp_limit(0) == 1
    assert clamp_limit(500) == 100
    assert clamp_limit(500, maximum=50) == 50


def test_list_envelope_with_known_total():
    assert list_envelope([1, 2, 3], limit=2, total=1191) == {
        "items": [1, 2], "total": 1191, "returned": 2, "truncated": True,
    }


def test_list_envelope_without_total_uses_extra_item():
    assert list_envelope([1, 2, 3], limit=2) == {"items": [1, 2], "total": None, "returned": 2, "truncated": True}
    assert list_envelope([1, 2], limit=2) == {"items": [1, 2], "total": 2, "returned": 2, "truncated": False}


def test_to_jsonable_dumps_pydantic_models():
    user = DirectumUser(id=1, name="Иванов", login="ivanov")
    expected = user.model_dump(mode="json")

    assert to_jsonable({"user": user, "users": [user]}) == {"user": expected, "users": [expected]}


@pytest.mark.parametrize(
    ("exc", "kind", "fragment"),
    [
        (DirectumError("x", 401), "auth", "Неверный логин или пароль"),
        (DirectumError("x", 403), "forbidden", "Недостаточно прав"),
        (DirectumError("status 400: Превышено ... Используйте фильтрацию.", 400), "too_broad", "Слишком широкий запрос"),
        (DirectumError("Documents not found", 404), "directum", "Documents not found"),
        (httpx.ReadTimeout("slow"), "timeout", "слишком долго"),
        (ToolError("готовое сообщение"), "tool", "готовое сообщение"),
    ],
)
def test_to_tool_error_maps_known_failures(exc, kind, fragment):
    error, error_kind = to_tool_error(exc)

    assert error_kind == kind
    assert fragment in str(error)


def test_to_tool_error_hides_internal_details():
    error, error_kind = to_tool_error(ValueError("secret internals"))

    assert error_kind == "internal"
    assert "Внутренняя ошибка mcpOGV, код" in str(error)
    assert "secret internals" not in str(error)


def test_usage_store_records_and_summarizes(tmp_path):
    store = ToolUsageStore(str(tmp_path / "usage.db"))
    store.record("list_my_assignments", True, None, 120, "a" * 64)
    store.record("list_my_assignments", False, "timeout", 20000, "a" * 64)
    store.record("get_current_user", True, None, 50, None)

    summary = {row["tool"]: row for row in store.summary()}

    assert summary["list_my_assignments"]["calls"] == 2
    assert summary["list_my_assignments"]["errors"] == 1
    assert summary["get_current_user"]["calls"] == 1
    assert store.user_hashes() == ["a" * 16]


class _Ctx:
    headers = {"x-directum-login": "user1"}


def test_runner_returns_result_and_records_usage(tmp_path):
    store = ToolUsageStore(str(tmp_path / "usage.db"))
    provider = FakeProvider(SimpleNamespace(value=42))
    runner = ToolRunner(provider, store)

    assert run_async(runner.run, _Ctx(), "demo", lambda services: services.value) == 42
    assert provider.opened_with == [{"x-directum-login": "user1"}]
    assert store.summary()[0]["calls"] == 1


def test_runner_maps_errors_and_records_them(tmp_path):
    store = ToolUsageStore(str(tmp_path / "usage.db"))
    runner = ToolRunner(FakeProvider(SimpleNamespace()), store)

    def fail(services):
        raise DirectumError("x", 401)

    with pytest.raises(ToolError, match="Неверный логин"):
        run_async(runner.run, _Ctx(), "demo", fail)
    assert store.summary()[0]["errors"] == 1


def test_runner_accepts_missing_context():
    provider = FakeProvider(SimpleNamespace())

    assert run_async(ToolRunner(provider).run, None, "demo", lambda services: 1) == 1
    assert provider.opened_with == [None]


def test_read_only_annotation():
    assert READ_ONLY.read_only_hint is True
```

- [ ] **Step 3: Запустить — должны упасть**

```powershell
& ".venv\Scripts\python.exe" -m pytest tests/unit/test_mcp_pipeline.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'src.mcp_server.audit'` (и др.).

- [ ] **Step 4: Реализация**

Создать `src/mcp_server/envelope.py`:

```python
from typing import Any

DEFAULT_LIMIT = 20
MAX_LIMIT = 100


def clamp_limit(limit: int | None, default: int = DEFAULT_LIMIT, maximum: int = MAX_LIMIT) -> int:
    if limit is None:
        return default
    return max(1, min(int(limit), maximum))


def to_jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {key: to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(item) for item in value]
    return value


def list_envelope(items: list[Any], limit: int, total: int | None = None) -> dict[str, Any]:
    """Единый формат списков. Без total сервисы запрашивают limit+1 записей, чтобы понять, есть ли ещё."""
    visible = [to_jsonable(item) for item in items[:limit]]
    has_more = len(items) > limit
    if total is None:
        known_total = None if has_more else len(visible)
        truncated = has_more
    else:
        known_total = total
        truncated = has_more or total > len(visible)
    return {"items": visible, "total": known_total, "returned": len(visible), "truncated": truncated}
```

Создать `src/mcp_server/errors.py`:

```python
import logging
import uuid

import httpx
from mcp.server.mcpserver.exceptions import ToolError

from src.services.directum_client import DirectumError

logger = logging.getLogger("mcp_ogv")

FILTER_REQUIRED_MARKER = "Используйте фильтрацию"


def to_tool_error(exc: Exception) -> tuple[ToolError, str]:
    """Превращает исключение в понятную агенту ошибку тула. Внутренние детали наружу не уходят."""
    if isinstance(exc, ToolError):
        return exc, "tool"
    if isinstance(exc, DirectumError):
        if exc.status_code == 401:
            return ToolError(
                "Неверный логин или пароль Directum. Проверьте учётные данные в настройках mcpOGV в LibreChat."
            ), "auth"
        if exc.status_code == 403:
            return ToolError("Недостаточно прав в Directum для этой операции."), "forbidden"
        if FILTER_REQUIRED_MARKER in exc.safe_message:
            return ToolError(
                "Слишком широкий запрос: Directum требует фильтр. Добавь условие (период, исполнитель, состояние)."
            ), "too_broad"
        return ToolError(exc.safe_message), "directum"
    if isinstance(exc, httpx.TimeoutException):
        return ToolError("Запрос к Directum выполнялся слишком долго. Сузь условия (период, фильтр, limit)."), "timeout"
    code = uuid.uuid4().hex[:8]
    logger.exception("Unexpected mcpOGV error, correlation id %s", code)
    return ToolError(f"Внутренняя ошибка mcpOGV, код {code}."), "internal"
```

Создать `src/mcp_server/audit.py`:

```python
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

USER_HASH_LENGTH = 16


class ToolUsageStore:
    """Метрики использования тулов для анализа гипотез: какой тул, успех, длительность. Без кредов."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._lock = threading.Lock()
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS tool_calls ("
                "ts REAL NOT NULL, tool TEXT NOT NULL, ok INTEGER NOT NULL, "
                "error_kind TEXT, duration_ms INTEGER NOT NULL, user_hash TEXT)"
            )

    def record(self, tool: str, ok: bool, error_kind: str | None, duration_ms: int, user_hash: str | None) -> None:
        short_hash = user_hash[:USER_HASH_LENGTH] if user_hash else None
        with self._lock, sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO tool_calls (ts, tool, ok, error_kind, duration_ms, user_hash) VALUES (?, ?, ?, ?, ?, ?)",
                (time.time(), tool, int(ok), error_kind, duration_ms, short_hash),
            )

    def summary(self) -> list[dict[str, Any]]:
        with self._lock, sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT tool, COUNT(*), SUM(CASE WHEN ok = 0 THEN 1 ELSE 0 END), AVG(duration_ms) "
                "FROM tool_calls GROUP BY tool ORDER BY COUNT(*) DESC"
            ).fetchall()
        return [
            {"tool": tool, "calls": calls, "errors": errors, "avg_duration_ms": round(avg or 0)}
            for tool, calls, errors, avg in rows
        ]

    def user_hashes(self) -> list[str]:
        with self._lock, sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT DISTINCT user_hash FROM tool_calls WHERE user_hash IS NOT NULL ORDER BY user_hash"
            ).fetchall()
        return [row[0] for row in rows]
```

Создать `src/mcp_server/runner.py`:

```python
import logging
import time
from collections.abc import Callable
from typing import Any, TypeVar

import anyio
from mcp.types import ToolAnnotations

from src.mcp_server.audit import ToolUsageStore
from src.mcp_server.errors import to_tool_error

logger = logging.getLogger("mcp_ogv")

T = TypeVar("T")

READ_ONLY = ToolAnnotations(read_only_hint=True)


def _headers_of(ctx: Any):
    if ctx is None:
        return None
    try:
        return ctx.headers
    except Exception:
        return None


class ToolRunner:
    """Выполняет действие тула: открывает сервисы из кредов, маппит ошибки, пишет метрики.

    Синхронные вызовы Directum уходят в поток, чтобы медленный запрос одного пользователя
    не блокировал сервер для остальных.
    """

    def __init__(self, provider: Any, usage: ToolUsageStore | None = None):
        self.provider = provider
        self.usage = usage

    async def run(self, ctx: Any, tool: str, action: Callable[[Any], T]) -> T:
        return await anyio.to_thread.run_sync(self._execute, _headers_of(ctx), tool, action)

    def _execute(self, headers: Any, tool: str, action: Callable[[Any], T]) -> T:
        started = time.perf_counter()
        fingerprint = None
        ok = False
        error_kind = None
        try:
            with self.provider.open(headers) as (credentials, services):
                fingerprint = credentials.fingerprint
                result = action(services)
            ok = True
            return result
        except Exception as exc:
            error, error_kind = to_tool_error(exc)
            raise error from None
        finally:
            duration_ms = int((time.perf_counter() - started) * 1000)
            logger.info("tool=%s ok=%s error=%s duration_ms=%d", tool, ok, error_kind, duration_ms)
            if self.usage is not None:
                self.usage.record(tool, ok, error_kind, duration_ms, fingerprint)
```

- [ ] **Step 5: Запустить — должны пройти**

```powershell
& ".venv\Scripts\python.exe" -m pytest tests/unit/test_mcp_pipeline.py -v
```

Expected: все PASS.

- [ ] **Step 6: Commit**

```powershell
git add src/mcp_server/envelope.py src/mcp_server/errors.py src/mcp_server/audit.py src/mcp_server/runner.py tests/unit/mcp_fakes.py tests/unit/test_mcp_pipeline.py
git commit -m "feat(mcp): формат ответов, маппинг ошибок, метрики и runner тулов" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Сервер mcpOGV: приложение, защита ключом, `/health`, общие тулы

**Files:**
- Create: `src/mcp_server/tools/__init__.py`, `src/mcp_server/tools/common.py`, `src/mcp_server/app.py`, `src/mcp_server/__main__.py`
- Test: `tests/unit/test_mcp_tools_common.py`, `tests/integration/test_mcp_http.py`

**Interfaces:**
- Consumes: `ToolRunner`, `READ_ONLY`, `clamp_limit`, `list_envelope`, `to_jsonable`, `ServicesProvider`, `ToolUsageStore`, `McpSettings`.
- Produces:
  - `SERVER_NAME = "mcpOGV"`, `INSTRUCTIONS: str`.
  - `build_server(provider, usage=None) -> MCPServer` (последующие задачи добавляют параметры и регистрацию).
  - `build_asgi_app(settings, provider=None, usage=None)` — ASGI-приложение; без `MCP_OGV_KEY` падает `RuntimeError`, если не включён `MCP_ALLOW_ENV_CREDENTIALS`.
  - `McpKeyMiddleware(app, key)` — 401 на `/mcp*` без правильного `X-MCP-Key`.
  - `tools.common.register(mcp, runner)`: тулы `get_current_user`, `search_employees(query, limit=20)`.

- [ ] **Step 1: Падающие unit-тесты тулов и приложения**

Создать `tests/unit/test_mcp_tools_common.py`:

```python
from types import SimpleNamespace

import pytest

from src.mcp_server.app import build_asgi_app, build_server
from src.mcp_server.config import McpSettings
from src.models.schemas import DirectumUser, EmployeeSummary
from tests.unit.mcp_fakes import FakeProvider, call_tool, list_tools, payload


def make_server(services):
    return build_server(FakeProvider(services))


def test_get_current_user_tool():
    services = SimpleNamespace(
        current_user=SimpleNamespace(
            get_current_user=lambda: DirectumUser(id=63, name="Концева Надежда Ивановна", login="user1")
        )
    )

    data = payload(call_tool(make_server(services), "get_current_user"))

    assert data["id"] == 63
    assert data["name"] == "Концева Надежда Ивановна"


def test_search_employees_requests_one_extra_row():
    calls = []

    def search_employee(query, top):
        calls.append((query, top))
        return [EmployeeSummary(id=i, name=f"Сотрудник {i}", status="Active") for i in range(3)]

    services = SimpleNamespace(action_items=SimpleNamespace(search_employee=search_employee))

    data = payload(call_tool(make_server(services), "search_employees", {"query": "Ардо", "limit": 2}))

    assert calls == [("Ардо", 3)]
    assert data["returned"] == 2
    assert data["truncated"] is True
    assert data["total"] is None


def test_common_tools_are_read_only():
    tools = {tool.name: tool for tool in list_tools(make_server(SimpleNamespace()))}

    assert tools["get_current_user"].annotations.read_only_hint is True
    assert tools["search_employees"].annotations.read_only_hint is True


def test_asgi_app_requires_key_unless_debug(tmp_path):
    common = {
        "DIRECTUM_BASE_URL": "https://rx.example/Integration/odata",
        "MCP_USAGE_DB_PATH": str(tmp_path / "usage.db"),
        "_env_file": None,
    }

    with pytest.raises(RuntimeError, match="MCP_OGV_KEY"):
        build_asgi_app(McpSettings(**common), provider=FakeProvider(SimpleNamespace()))

    debug = McpSettings(MCP_ALLOW_ENV_CREDENTIALS=True, **common)
    assert build_asgi_app(debug, provider=FakeProvider(SimpleNamespace())) is not None
```

- [ ] **Step 2: Падающий интеграционный тест по HTTP**

Создать `tests/integration/test_mcp_http.py`:

```python
import base64
import json
import socket
import threading
import time

import anyio
import httpx
import httpx2
import pytest
import uvicorn
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from src.mcp_server.app import build_asgi_app
from src.mcp_server.config import McpSettings
from src.mcp_server.context import ServicesProvider

LOGIN, PASSWORD, KEY = "user1", "pw1", "test-key"
EXPECTED_TOKEN = "Basic " + base64.b64encode(f"{LOGIN}:{PASSWORD}".encode()).decode()
FULL_HEADERS = {"X-MCP-Key": KEY, "X-Directum-Login": LOGIN, "X-Directum-Password": PASSWORD}


def _directum(request: httpx.Request) -> httpx.Response:
    if request.headers.get("authorization") != EXPECTED_TOKEN:
        return httpx.Response(401)
    if request.url.path.endswith("/IUsers"):
        return httpx.Response(200, json={"value": [{"Id": 63, "Name": "Концева Надежда Ивановна"}]})
    return httpx.Response(404, json={"error": "not found"})


@pytest.fixture(scope="module")
def mcp_url(tmp_path_factory):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    settings = McpSettings(
        DIRECTUM_BASE_URL="https://rx.example/Integration/odata",
        MCP_OGV_KEY=KEY,
        MCP_ALLOWED_HOSTS=f"127.0.0.1:{port}",
        MCP_USAGE_DB_PATH=str(tmp_path_factory.mktemp("usage") / "usage.db"),
        _env_file=None,
    )
    app = build_asgi_app(settings, provider=ServicesProvider(settings, transport=httpx.MockTransport(_directum)))
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{port}"
    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            if httpx.get(f"{url}/health").status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.1)
    yield url
    server.should_exit = True
    thread.join(timeout=5)


def _call(url, headers, tool, arguments=None):
    async def main():
        http = httpx2.AsyncClient(headers=headers)
        async with Client(streamable_http_client(f"{url}/mcp", http_client=http)) as client:
            return await client.call_tool(tool, arguments or {})

    return anyio.run(main)


def test_health_is_public(mcp_url):
    assert httpx.get(f"{mcp_url}/health").json() == {"status": "ok", "server": "mcpOGV"}


def test_mcp_endpoint_requires_shared_key(mcp_url):
    response = httpx.post(
        f"{mcp_url}/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        headers={"Accept": "application/json, text/event-stream"},
    )

    assert response.status_code == 401


def test_tool_runs_with_user_credentials_from_headers(mcp_url):
    result = _call(mcp_url, FULL_HEADERS, "get_current_user")

    assert not result.is_error, result.content[0].text
    assert json.loads(result.content[0].text)["id"] == 63


def test_missing_directum_credentials_reported_to_agent(mcp_url):
    result = _call(mcp_url, {"X-MCP-Key": KEY}, "get_current_user")

    assert result.is_error
    assert "Укажите логин и пароль Directum" in result.content[0].text


def test_wrong_directum_password_reported_to_agent(mcp_url):
    headers = {**FULL_HEADERS, "X-Directum-Password": "wrong"}

    result = _call(mcp_url, headers, "get_current_user")

    assert result.is_error
    assert "Неверный логин или пароль" in result.content[0].text
```

- [ ] **Step 3: Запустить — должны упасть**

```powershell
& ".venv\Scripts\python.exe" -m pytest tests/unit/test_mcp_tools_common.py tests/integration/test_mcp_http.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'src.mcp_server.app'`.

- [ ] **Step 4: Реализация**

Создать `src/mcp_server/tools/__init__.py`:

```python
"""Курируемые и универсальные тулы mcpOGV."""
```

Создать `src/mcp_server/tools/common.py`:

```python
from typing import Annotated

from mcp.server.mcpserver import Context, MCPServer
from pydantic import Field

from src.mcp_server.envelope import clamp_limit, list_envelope, to_jsonable
from src.mcp_server.runner import READ_ONLY, ToolRunner

Limit = Annotated[int, Field(description="Сколько записей вернуть (1–100)", ge=1, le=100)]


def register(mcp: MCPServer, runner: ToolRunner) -> None:
    @mcp.tool(annotations=READ_ONLY)
    async def get_current_user(ctx: Context) -> dict:
        """Текущий пользователь Directum RX, от имени которого работает mcpOGV: id, ФИО, логин."""
        return await runner.run(ctx, "get_current_user", lambda s: to_jsonable(s.current_user.get_current_user()))

    @mcp.tool(annotations=READ_ONLY)
    async def search_employees(
        query: Annotated[str, Field(description="ФИО сотрудника или его часть, например «Ардо»")],
        ctx: Context,
        limit: Limit = 20,
    ) -> dict:
        """Найти сотрудников по ФИО (нечёткий поиск по частям имени). Используй, чтобы получить id сотрудника — не выдумывай id."""
        size = clamp_limit(limit)
        return await runner.run(
            ctx,
            "search_employees",
            lambda s: list_envelope(s.action_items.search_employee(query, top=size + 1), size),
        )
```

Создать `src/mcp_server/app.py`:

```python
import hmac
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.responses import JSONResponse

from src.mcp_server.audit import ToolUsageStore
from src.mcp_server.config import McpSettings
from src.mcp_server.context import ServicesProvider
from src.mcp_server.runner import ToolRunner
from src.mcp_server.tools import common

SERVER_NAME = "mcpOGV"

INSTRUCTIONS = """mcpOGV — доступ к Directum RX от имени текущего пользователя (его права и его данные).
Правила:
1. Для типовых задач используй курируемые инструменты (list_*, get_*, search_*): они надёжнее универсальных.
2. Списки приходят частями: смотри total, returned, truncated. Если truncated=true — уточни фильтр или увеличь limit (до 100).
3. Не выдумывай идентификаторы: сотрудников ищи через search_employees, документы — через search_documents.
4. Универсальные odata_* используй для того, чего нет в курируемых. Всегда указывай filter — Directum отклоняет запросы без фильтра. Поля смотри через odata_describe_entity, наборы данных — в справочниках drx://domains/*.
5. Инструменты drx_native_* — встроенные инструменты самой Directum RX.
6. Даты передавай в формате YYYY-MM-DD.
"""


def _register_health(mcp: MCPServer) -> None:
    @mcp.custom_route("/health", methods=["GET"])
    async def health(request):
        return JSONResponse({"status": "ok", "server": SERVER_NAME})


def build_server(provider: Any, usage: ToolUsageStore | None = None) -> MCPServer:
    runner = ToolRunner(provider, usage)
    mcp = MCPServer(SERVER_NAME, instructions=INSTRUCTIONS)
    common.register(mcp, runner)
    _register_health(mcp)
    return mcp


class McpKeyMiddleware:
    """Отклоняет запросы к /mcp без правильного X-MCP-Key (общий секрет LibreChat ↔ mcpOGV)."""

    def __init__(self, app: Any, key: str | None):
        self.app = app
        self.key = key

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and self.key is not None and scope["path"].startswith("/mcp"):
            provided = dict(scope.get("headers") or []).get(b"x-mcp-key", b"").decode("latin-1")
            if not hmac.compare_digest(provided, self.key):
                response = JSONResponse({"error": "unauthorized"}, status_code=401)
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


def build_asgi_app(
    settings: McpSettings,
    provider: Any | None = None,
    usage: ToolUsageStore | None = None,
) -> McpKeyMiddleware:
    if settings.mcp_key is None and not settings.MCP_ALLOW_ENV_CREDENTIALS:
        raise RuntimeError(
            "MCP_OGV_KEY is required; MCP_ALLOW_ENV_CREDENTIALS=true is allowed only for local debugging"
        )
    provider = provider or ServicesProvider(settings)
    usage = usage or ToolUsageStore(settings.MCP_USAGE_DB_PATH)
    mcp = build_server(provider, usage)
    app = mcp.streamable_http_app(
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(allowed_hosts=settings.allowed_hosts),
    )
    return McpKeyMiddleware(app, settings.mcp_key)
```

Создать `src/mcp_server/__main__.py`:

```python
import logging

import uvicorn

from src.mcp_server.app import build_asgi_app
from src.mcp_server.config import McpSettings


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    # httpx пишет URL запросов, а в $filter бывают логины — держим его логгер тихим.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    settings = McpSettings()
    uvicorn.run(build_asgi_app(settings), host=settings.MCP_HOST, port=settings.MCP_PORT, log_level="info")


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Запустить — должны пройти**

```powershell
& ".venv\Scripts\python.exe" -m pytest tests/unit/test_mcp_tools_common.py tests/integration/test_mcp_http.py -v
```

Expected: все PASS.

- [ ] **Step 6: Commit**

```powershell
git add src/mcp_server/tools/__init__.py src/mcp_server/tools/common.py src/mcp_server/app.py src/mcp_server/__main__.py tests/unit/test_mcp_tools_common.py tests/integration/test_mcp_http.py
git commit -m "feat(mcp): сервер mcpOGV по Streamable HTTP, защита ключом, health, общие тулы" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Тулы заданий, поручений и дисциплины

**Files:**
- Create: `src/mcp_server/tools/action_items.py`
- Modify: `src/mcp_server/app.py` (регистрация)
- Test: `tests/unit/test_mcp_tools_action_items.py`

**Interfaces:**
- Consumes: `AssignmentsService.get_*(top=...)`, `count_my_assignments`, `count_action_items`, `MeetingsService.get_action_item_details(id)`, `DisciplineAnalyticsService.get_discipline_analytics(employee, date_from, date_to)`, `categorize_outgoing`.
- Produces: `tools.action_items.register(mcp, runner)` с тулами `list_my_assignments(only_overdue=False, limit=20)`, `list_action_items(direction, limit=20)`, `get_action_item(action_item_id)`, `get_discipline_analytics(employee=None, date_from=None, date_to=None)`, `get_outgoing_action_items_analytics(limit_per_category=20)`.

- [ ] **Step 1: Падающие тесты**

Создать `tests/unit/test_mcp_tools_action_items.py`:

```python
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

from src.mcp_server.app import build_server
from src.models.schemas import ActionItemDetail, AssignmentSummary, DisciplineSummary
from tests.unit.mcp_fakes import FakeProvider, call_tool, error_text, payload


def make_server(services):
    return build_server(FakeProvider(services))


def summaries(count, deadline=None):
    return [
        AssignmentSummary(id=i, subject=f"Задание {i}", status="InProcess", entity_type="assignment", deadline=deadline)
        for i in range(count)
    ]


def test_list_my_assignments_returns_total_from_count():
    calls = {}

    def get_my(top):
        calls["top"] = top
        return summaries(2)

    services = SimpleNamespace(
        assignments=SimpleNamespace(
            get_my_assignments=get_my,
            get_overdue_assignments=lambda top: [],
            count_my_assignments=lambda only_overdue=False: 1191,
        )
    )

    data = payload(call_tool(make_server(services), "list_my_assignments", {"limit": 2}))

    assert calls["top"] == 2
    assert data["total"] == 1191
    assert data["returned"] == 2
    assert data["truncated"] is True


def test_list_my_assignments_only_overdue():
    seen = {}

    def count(only_overdue=False):
        seen["only_overdue"] = only_overdue
        return 1

    services = SimpleNamespace(
        assignments=SimpleNamespace(
            get_my_assignments=lambda top: [],
            get_overdue_assignments=lambda top: summaries(1),
            count_my_assignments=count,
        )
    )

    data = payload(call_tool(make_server(services), "list_my_assignments", {"only_overdue": True}))

    assert seen["only_overdue"] is True
    assert data["total"] == 1
    assert data["truncated"] is False


def test_list_action_items_outgoing_uses_created_by_me():
    seen = {}

    def created(top):
        seen["top"] = top
        return summaries(1)

    def count(direction):
        seen["direction"] = direction
        return 5

    services = SimpleNamespace(
        assignments=SimpleNamespace(
            get_action_items_assigned_to_me=lambda top: [],
            get_action_items_created_by_me=created,
            count_action_items=count,
        )
    )

    data = payload(call_tool(make_server(services), "list_action_items", {"direction": "outgoing", "limit": 3}))

    assert seen == {"top": 3, "direction": "outgoing"}
    assert data["total"] == 5


def test_list_action_items_rejects_unknown_direction():
    result = call_tool(make_server(SimpleNamespace()), "list_action_items", {"direction": "sideways"})

    assert result.is_error


def test_get_action_item_returns_details():
    detail = ActionItemDetail(
        id=42,
        subject="Подготовить записку",
        performer="Иванова М.П.",
        author="Петров А.С.",
        status="InProcess",
        created_date=date(2026, 9, 20),
        client_card_url="https://rx.example/Client/#/card/x/42",
    )
    services = SimpleNamespace(meetings=SimpleNamespace(get_action_item_details=lambda action_item_id: detail))

    data = payload(call_tool(make_server(services), "get_action_item", {"action_item_id": 42}))

    assert data["id"] == 42
    assert data["performer"] == "Иванова М.П."


def test_get_discipline_analytics_passes_filters():
    seen = {}

    def analytics(employee=None, date_from=None, date_to=None):
        seen.update(employee=employee, date_from=date_from, date_to=date_to)
        return DisciplineSummary(in_process=66, overdue=27, completed=137, completed_on_time=110, completed_late=27, on_time_rate=80.3)

    services = SimpleNamespace(discipline=SimpleNamespace(get_discipline_analytics=analytics))

    data = payload(
        call_tool(
            make_server(services),
            "get_discipline_analytics",
            {"employee": "Ардо", "date_from": "2026-09-01", "date_to": "2026-09-25"},
        )
    )

    assert seen == {"employee": "Ардо", "date_from": "2026-09-01", "date_to": "2026-09-25"}
    assert data["on_time_rate"] == 80.3


def test_outgoing_analytics_groups_by_deadline():
    now = datetime.now(timezone.utc)
    items = [
        AssignmentSummary(id=1, subject="В работе", entity_type="action_item_task", deadline=now + timedelta(days=5)),
        AssignmentSummary(id=2, subject="Завтра", entity_type="action_item_task", deadline=now + timedelta(hours=6)),
        AssignmentSummary(id=3, subject="Просрочено", entity_type="action_item_task", deadline=now - timedelta(hours=2)),
    ]
    services = SimpleNamespace(assignments=SimpleNamespace(get_action_items_created_by_me=lambda top=None: items))

    data = payload(call_tool(make_server(services), "get_outgoing_action_items_analytics"))

    assert data["total"] == 3
    assert [item["id"] for item in data["categories"]["work"]["items"]] == [1]
    assert [item["id"] for item in data["categories"]["due_soon"]["items"]] == [2]
    assert [item["id"] for item in data["categories"]["overdue"]["items"]] == [3]


def test_directum_errors_become_tool_errors():
    def boom(top):
        from src.services.directum_client import DirectumError

        raise DirectumError("status 400: Используйте фильтрацию.", 400)

    services = SimpleNamespace(
        assignments=SimpleNamespace(get_my_assignments=boom, count_my_assignments=lambda only_overdue=False: 0)
    )

    text = error_text(call_tool(make_server(services), "list_my_assignments"))

    assert "Слишком широкий запрос" in text
```

- [ ] **Step 2: Запустить — должны упасть**

```powershell
& ".venv\Scripts\python.exe" -m pytest tests/unit/test_mcp_tools_action_items.py -v
```

Expected: FAIL — тулов нет (`Unknown tool` / `is_error`).

- [ ] **Step 3: Реализация**

Создать `src/mcp_server/tools/action_items.py`:

```python
from typing import Annotated, Literal

from mcp.server.mcpserver import Context, MCPServer
from pydantic import Field

from src.mcp_server.envelope import clamp_limit, list_envelope, to_jsonable
from src.mcp_server.runner import READ_ONLY, ToolRunner
from src.services.outgoing_analytics import categorize_outgoing

Limit = Annotated[int, Field(description="Сколько записей вернуть (1–100)", ge=1, le=100)]
IsoDate = Annotated[str | None, Field(description="Дата в формате YYYY-MM-DD")]
Direction = Annotated[
    Literal["incoming", "outgoing"],
    Field(description="incoming — поручения мне на исполнение, outgoing — поручения, выданные мной"),
]


def register(mcp: MCPServer, runner: ToolRunner) -> None:
    @mcp.tool(annotations=READ_ONLY)
    async def list_my_assignments(ctx: Context, only_overdue: bool = False, limit: Limit = 20) -> dict:
        """Мои задания в работе (или только просроченные), по возрастанию срока. total — сколько всего."""
        size = clamp_limit(limit)

        def action(s):
            fetch = s.assignments.get_overdue_assignments if only_overdue else s.assignments.get_my_assignments
            return list_envelope(fetch(top=size), size, total=s.assignments.count_my_assignments(only_overdue=only_overdue))

        return await runner.run(ctx, "list_my_assignments", action)

    @mcp.tool(annotations=READ_ONLY)
    async def list_action_items(direction: Direction, ctx: Context, limit: Limit = 20) -> dict:
        """Поручения в работе: входящие (мне) или исходящие (выданные мной, с исполнителем). total — сколько всего."""
        size = clamp_limit(limit)

        def action(s):
            fetch = (
                s.assignments.get_action_items_assigned_to_me
                if direction == "incoming"
                else s.assignments.get_action_items_created_by_me
            )
            return list_envelope(fetch(top=size), size, total=s.assignments.count_action_items(direction))

        return await runner.run(ctx, "list_action_items", action)

    @mcp.tool(annotations=READ_ONLY)
    async def get_action_item(
        action_item_id: Annotated[int, Field(description="Id поручения", gt=0)],
        ctx: Context,
    ) -> dict:
        """Карточка поручения: тема, исполнитель, автор, статус, даты, ссылка. Отчёт о поручении формулируй сам по этим фактам."""
        return await runner.run(
            ctx, "get_action_item", lambda s: to_jsonable(s.meetings.get_action_item_details(action_item_id))
        )

    @mcp.tool(annotations=READ_ONLY)
    async def get_discipline_analytics(
        ctx: Context,
        employee: Annotated[str | None, Field(description="ФИО сотрудника; пусто — вся организация")] = None,
        date_from: IsoDate = None,
        date_to: IsoDate = None,
    ) -> dict:
        """Исполнительская дисциплина по заданиям: в работе, просрочено, завершено, в срок, с опозданием, % в срок. По организации или сотруднику, за период."""
        return await runner.run(
            ctx,
            "get_discipline_analytics",
            lambda s: to_jsonable(
                s.discipline.get_discipline_analytics(employee=employee, date_from=date_from, date_to=date_to)
            ),
        )

    @mcp.tool(annotations=READ_ONLY)
    async def get_outgoing_action_items_analytics(ctx: Context, limit_per_category: Limit = 20) -> dict:
        """Мои исходящие поручения по срокам: в работе, срок в ближайшие сутки, просрочено — с количеством и списками."""
        size = clamp_limit(limit_per_category)

        def action(s):
            items = [to_jsonable(item) for item in s.assignments.get_action_items_created_by_me()]
            groups = categorize_outgoing(items)
            return {
                "total": len(items),
                "categories": {name: list_envelope(group, size) for name, group in groups.items()},
            }

        return await runner.run(ctx, "get_outgoing_action_items_analytics", action)
```

В `src/mcp_server/app.py`:
- импорт: `from src.mcp_server.tools import action_items, common`;
- в `build_server` после `common.register(mcp, runner)` добавить `action_items.register(mcp, runner)`.

- [ ] **Step 4: Запустить — должны пройти**

```powershell
& ".venv\Scripts\python.exe" -m pytest tests/unit/test_mcp_tools_action_items.py tests/unit/test_mcp_tools_common.py -v
```

Expected: все PASS.

- [ ] **Step 5: Commit**

```powershell
git add src/mcp_server/tools/action_items.py src/mcp_server/app.py tests/unit/test_mcp_tools_action_items.py
git commit -m "feat(mcp): тулы заданий, поручений, дисциплины и аналитики исходящих" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: Тулы документов, писем и совещаний

**Files:**
- Create: `src/mcp_server/tools/documents.py`
- Modify: `src/mcp_server/app.py` (регистрация)
- Test: `tests/unit/test_mcp_tools_documents.py`

**Interfaces:**
- Consumes: `ActionItemService.search_documents(query, top)`, `get_document(id)`, `search_documents_by_counterparty(query, top)` → `DocumentsByCounterpartyResult(counterparty, documents, message)`, `list_letters(direction, date_from, date_to, top)`, `MeetingsService.get_my_meetings(days)`.
- Produces: `tools.documents.register(mcp, runner)` с тулами `search_documents(query, limit=20)`, `get_document(document_id)`, `list_documents_by_counterparty(counterparty, limit=20)`, `list_letters(direction, date_from=None, date_to=None, limit=20)`, `list_my_meetings(days=7)`.

- [ ] **Step 1: Падающие тесты**

Создать `tests/unit/test_mcp_tools_documents.py`:

```python
from datetime import datetime, timezone
from types import SimpleNamespace

from src.mcp_server.app import build_server
from src.models.schemas import CounterpartySummary, DocumentsByCounterpartyResult, DocumentSummary, MeetingSummary
from tests.unit.mcp_fakes import FakeProvider, call_tool, error_text, payload


def make_server(services):
    return build_server(FakeProvider(services))


def docs(count):
    return [DocumentSummary(id=i, name=f"Документ {i}", url=f"https://rx.example/doc/{i}") for i in range(count)]


def test_search_documents_requests_one_extra_row():
    seen = {}

    def search(query, top):
        seen.update(query=query, top=top)
        return docs(2)

    services = SimpleNamespace(action_items=SimpleNamespace(search_documents=search))

    data = payload(call_tool(make_server(services), "search_documents", {"query": "письмо", "limit": 5}))

    assert seen == {"query": "письмо", "top": 6}
    assert data["returned"] == 2
    assert data["total"] == 2


def test_get_document_not_found_is_reported():
    services = SimpleNamespace(action_items=SimpleNamespace(get_document=lambda document_id: None))

    text = error_text(call_tool(make_server(services), "get_document", {"document_id": 999}))

    assert "999" in text
    assert "не найден" in text


def test_get_document_returns_card():
    services = SimpleNamespace(action_items=SimpleNamespace(get_document=lambda document_id: docs(1)[0]))

    assert payload(call_tool(make_server(services), "get_document", {"document_id": 1}))["name"] == "Документ 0"


def test_list_documents_by_counterparty_includes_counterparty():
    result = DocumentsByCounterpartyResult(
        counterparty=CounterpartySummary(id=2, name="Минцифры России"),
        documents=docs(3),
        message="",
    )
    services = SimpleNamespace(action_items=SimpleNamespace(search_documents_by_counterparty=lambda query, top: result))

    data = payload(call_tool(make_server(services), "list_documents_by_counterparty", {"counterparty": "МЦ", "limit": 2}))

    assert data["counterparty"]["name"] == "Минцифры России"
    assert data["returned"] == 2
    assert data["truncated"] is True


def test_list_letters_passes_period():
    seen = {}

    def letters(direction, date_from=None, date_to=None, top=50):
        seen.update(direction=direction, date_from=date_from, date_to=date_to, top=top)
        return docs(1)

    services = SimpleNamespace(action_items=SimpleNamespace(list_letters=letters))

    payload(
        call_tool(
            make_server(services),
            "list_letters",
            {"direction": "incoming", "date_from": "2026-09-01", "date_to": "2026-09-25", "limit": 10},
        )
    )

    assert seen == {"direction": "incoming", "date_from": "2026-09-01", "date_to": "2026-09-25", "top": 11}


def test_list_my_meetings_returns_envelope():
    meeting = MeetingSummary(
        id=5,
        subject="Планёрка",
        start_date=datetime(2026, 9, 26, 10, tzinfo=timezone.utc),
        client_card_url="https://rx.example/Client/#/card/m/5",
    )
    seen = {}

    def meetings(days=7):
        seen["days"] = days
        return [meeting]

    services = SimpleNamespace(meetings=SimpleNamespace(get_my_meetings=meetings))

    data = payload(call_tool(make_server(services), "list_my_meetings", {"days": 14}))

    assert seen["days"] == 14
    assert data["items"][0]["subject"] == "Планёрка"
```

- [ ] **Step 2: Запустить — должны упасть**

```powershell
& ".venv\Scripts\python.exe" -m pytest tests/unit/test_mcp_tools_documents.py -v
```

Expected: FAIL — тулов нет.

- [ ] **Step 3: Реализация**

Создать `src/mcp_server/tools/documents.py`:

```python
from typing import Annotated, Literal

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from src.mcp_server.envelope import MAX_LIMIT, clamp_limit, list_envelope, to_jsonable
from src.mcp_server.runner import READ_ONLY, ToolRunner

Limit = Annotated[int, Field(description="Сколько записей вернуть (1–100)", ge=1, le=100)]
IsoDate = Annotated[str | None, Field(description="Дата в формате YYYY-MM-DD")]


def register(mcp: MCPServer, runner: ToolRunner) -> None:
    @mcp.tool(annotations=READ_ONLY)
    async def search_documents(
        query: Annotated[str, Field(description="Название, номер или тема документа")],
        ctx: Context,
        limit: Limit = 20,
    ) -> dict:
        """Поиск зарегистрированных документов по названию, номеру или теме."""
        size = clamp_limit(limit)
        return await runner.run(
            ctx, "search_documents", lambda s: list_envelope(s.action_items.search_documents(query, top=size + 1), size)
        )

    @mcp.tool(annotations=READ_ONLY)
    async def get_document(
        document_id: Annotated[int, Field(description="Id документа", gt=0)],
        ctx: Context,
    ) -> dict:
        """Карточка документа по Id: название, тема, регистрационный номер и дата, ссылка."""

        def action(s):
            document = s.action_items.get_document(document_id)
            if document is None:
                raise ToolError(f"Документ {document_id} не найден или у вас нет к нему доступа.")
            return to_jsonable(document)

        return await runner.run(ctx, "get_document", action)

    @mcp.tool(annotations=READ_ONLY)
    async def list_documents_by_counterparty(
        counterparty: Annotated[str, Field(description="Название организации или аббревиатура, например «МЦ»")],
        ctx: Context,
        limit: Limit = 20,
    ) -> dict:
        """Документы по контрагенту (входящие, исходящие, договоры). Если организация не найдена — см. поле message."""
        size = clamp_limit(limit)

        def action(s):
            result = s.action_items.search_documents_by_counterparty(counterparty, top=size + 1)
            return {
                "counterparty": to_jsonable(result.counterparty),
                "message": result.message,
                **list_envelope(result.documents, size),
            }

        return await runner.run(ctx, "list_documents_by_counterparty", action)

    @mcp.tool(annotations=READ_ONLY)
    async def list_letters(
        direction: Annotated[Literal["incoming", "outgoing"], Field(description="incoming — входящие, outgoing — исходящие")],
        ctx: Context,
        date_from: IsoDate = None,
        date_to: IsoDate = None,
        limit: Limit = 20,
    ) -> dict:
        """Зарегистрированные входящие или исходящие письма за период (по дате регистрации, новые сначала)."""
        size = clamp_limit(limit)
        return await runner.run(
            ctx,
            "list_letters",
            lambda s: list_envelope(
                s.action_items.list_letters(direction, date_from=date_from, date_to=date_to, top=size + 1), size
            ),
        )

    @mcp.tool(annotations=READ_ONLY)
    async def list_my_meetings(
        ctx: Context,
        days: Annotated[int, Field(description="На сколько дней вперёд (1–90)", ge=1, le=90)] = 7,
    ) -> dict:
        """Мои совещания на ближайшие дни: дата, тема, место, ссылка на карточку."""
        return await runner.run(
            ctx, "list_my_meetings", lambda s: list_envelope(s.meetings.get_my_meetings(days=days), MAX_LIMIT)
        )
```

В `src/mcp_server/app.py`:
- импорт: `from src.mcp_server.tools import action_items, common, documents`;
- в `build_server` после `action_items.register(mcp, runner)` добавить `documents.register(mcp, runner)`.

- [ ] **Step 4: Запустить — должны пройти**

```powershell
& ".venv\Scripts\python.exe" -m pytest tests/unit -k "mcp" -v
```

Expected: все PASS.

- [ ] **Step 5: Commit**

```powershell
git add src/mcp_server/tools/documents.py src/mcp_server/app.py tests/unit/test_mcp_tools_documents.py
git commit -m "feat(mcp): тулы документов, писем и совещаний" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: Справочники доменов и универсальный слой OData

**Files:**
- Create: `src/mcp_server/resources.py`, `src/mcp_server/odata_meta.py`, `src/mcp_server/tools/odata.py`
- Modify: `src/mcp_server/app.py` (параметр `skills_dir`, регистрация)
- Modify: `tests/unit/mcp_fakes.py` (фикстура метаданных)
- Test: `tests/unit/test_mcp_odata.py`

**Interfaces:**
- Consumes: `DirectumClient.get_metadata_xml()`, `query(entity_set, *, filter_, select, expand, orderby, top)`, `count(entity_set, filter_)`, `get_one(entity_path)`.
- Produces:
  - `DomainGuide(name, description, body)`, `load_domain_guides(skills_dir: Path) -> dict[str, DomainGuide]`, `resources.register(mcp, guides)` → ресурсы `drx://domains/{name}`.
  - `EntityInfo(entity_set, entity_type, properties: dict[str, str], navigation: dict[str, str])`, `parse_metadata(xml_text) -> dict[str, EntityInfo]`, `is_denied(entity_set) -> bool`, `MetadataCache.get(client) -> dict[str, EntityInfo]`.
  - `tools.odata.register(mcp, runner, metadata: MetadataCache, guides: dict[str, DomainGuide])` с тулами `odata_list_domains`, `odata_describe_entity(entity_set)`, `odata_query(entity_set, filter, select=None, expand=None, orderby=None, top=20)`, `odata_count(entity_set, filter)`, `odata_get(entity_set, record_id, expand=None)`.
  - `build_server(provider, usage=None, skills_dir: Path = SKILLS_DIR)`, где `SKILLS_DIR = <корень репо>/.claude/skills`.

- [ ] **Step 1: Фикстура метаданных в хелперах**

Добавить в конец `tests/unit/mcp_fakes.py`:

```python
METADATA_XML = """<?xml version="1.0" encoding="utf-8"?>
<edmx:Edmx Version="4.0" xmlns:edmx="http://docs.oasis-open.org/odata/ns/edmx">
  <edmx:DataServices>
    <Schema Namespace="Demo" xmlns="http://docs.oasis-open.org/odata/ns/edm">
      <EntityType Name="IEntityBase"><Key><PropertyRef Name="Id"/></Key><Property Name="Id" Type="Edm.Int64"/></EntityType>
      <EntityType Name="IRequestDto" BaseType="Demo.IEntityBase">
        <Property Name="Subject" Type="Edm.String"/>
        <Property Name="RegistrationDate" Type="Edm.DateTimeOffset"/>
        <NavigationProperty Name="Author" Type="Demo.IEmployeeDto"/>
      </EntityType>
      <EntityType Name="IEmployeeDto" BaseType="Demo.IEntityBase"><Property Name="Name" Type="Edm.String"/></EntityType>
      <EntityType Name="ILoginDto" BaseType="Demo.IEntityBase"><Property Name="LoginName" Type="Edm.String"/></EntityType>
      <EntityContainer Name="Container">
        <EntitySet Name="IRequests" EntityType="Demo.IRequestDto"/>
        <EntitySet Name="IEmployees" EntityType="Demo.IEmployeeDto"/>
        <EntitySet Name="ILogins" EntityType="Demo.ILoginDto"/>
      </EntityContainer>
    </Schema>
  </edmx:DataServices>
</edmx:Edmx>"""


class FakeODataClient:
    def __init__(self):
        self.metadata_calls = 0
        self.queries = []
        self.counts = []
        self.paths = []

    def get_metadata_xml(self):
        self.metadata_calls += 1
        return METADATA_XML

    def query(self, entity_set, *, filter_=None, select=None, expand=None, orderby=None, top=None, count=False):
        self.queries.append(
            {"entity_set": entity_set, "filter_": filter_, "select": select, "expand": expand, "orderby": orderby, "top": top}
        )
        return [{"Id": i, "Subject": f"Обращение {i}"} for i in range(top or 1)]

    def count(self, entity_set, filter_=None):
        self.counts.append((entity_set, filter_))
        return 1761

    def get_one(self, entity_path):
        self.paths.append(entity_path)
        return {"Id": 5, "Subject": "Обращение 5"}
```

- [ ] **Step 2: Падающие тесты**

Создать `tests/unit/test_mcp_odata.py`:

```python
from types import SimpleNamespace

import anyio
from mcp import Client

from src.mcp_server.app import build_server
from src.mcp_server.odata_meta import MetadataCache, is_denied, parse_metadata
from src.mcp_server.resources import load_domain_guides
from tests.unit.mcp_fakes import METADATA_XML, FakeODataClient, FakeProvider, call_tool, error_text, payload


def write_skill(root, name, description, body="# Справочник\nНаборы: IRequests"):
    folder = root / f"rxapi-{name}"
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(f"---\nname: rxapi-{name}\ndescription: {description}\n---\n{body}", encoding="utf-8")


def make_server(tmp_path, client=None):
    services = SimpleNamespace(client=client or FakeODataClient())
    return build_server(FakeProvider(services), skills_dir=tmp_path), services.client


def test_parse_metadata_resolves_inheritance_and_navigation():
    entities = parse_metadata(METADATA_XML)

    request = entities["IRequests"]
    assert set(request.properties) == {"Id", "Subject", "RegistrationDate"}
    assert set(request.navigation) == {"Author"}


def test_deny_list_blocks_sensitive_sets():
    assert is_denied("ILogins")
    assert is_denied("IUsers")
    assert not is_denied("IRequests")


def test_metadata_cache_fetches_once():
    client = FakeODataClient()
    cache = MetadataCache()

    cache.get(client)
    cache.get(client)

    assert client.metadata_calls == 1


def test_load_domain_guides_reads_frontmatter(tmp_path):
    write_skill(tmp_path, "hr", "HR-данные: отпуска, командировки")

    guides = load_domain_guides(tmp_path)

    assert guides["hr"].description == "HR-данные: отпуска, командировки"
    assert "IRequests" in guides["hr"].body


def test_domain_guides_exposed_as_resources(tmp_path):
    write_skill(tmp_path, "hr", "HR-данные")
    server, _ = make_server(tmp_path)

    async def main():
        async with Client(server) as client:
            uris = [str(resource.uri) for resource in (await client.list_resources()).resources]
            content = await client.read_resource("drx://domains/hr")
            return uris, content.contents[0].text

    uris, text = anyio.run(main)

    assert "drx://domains/hr" in uris
    assert "Справочник" in text


def test_odata_list_domains(tmp_path):
    write_skill(tmp_path, "hr", "HR-данные")
    server, _ = make_server(tmp_path)

    data = payload(call_tool(server, "odata_list_domains"))

    assert data["domains"] == [{"name": "hr", "description": "HR-данные", "resource": "drx://domains/hr"}]


def test_odata_describe_entity(tmp_path):
    server, _ = make_server(tmp_path)

    data = payload(call_tool(server, "odata_describe_entity", {"entity_set": "IRequests"}))

    assert {"name": "Subject", "type": "Edm.String"} in data["properties"]
    assert data["navigation"] == [{"name": "Author", "type": "Demo.IEmployeeDto"}]


def test_odata_query_requires_filter(tmp_path):
    server, _ = make_server(tmp_path)

    text = error_text(call_tool(server, "odata_query", {"entity_set": "IRequests", "filter": "  "}))

    assert "Нужен фильтр" in text


def test_odata_query_blocks_denied_and_unknown_sets(tmp_path):
    server, _ = make_server(tmp_path)

    assert "недоступен" in error_text(call_tool(server, "odata_query", {"entity_set": "ILogins", "filter": "Id gt 0"}))
    assert "недоступен" in error_text(call_tool(server, "odata_query", {"entity_set": "INope", "filter": "Id gt 0"}))


def test_odata_query_rejects_parameter_smuggling(tmp_path):
    server, _ = make_server(tmp_path)

    text = error_text(call_tool(server, "odata_query", {"entity_set": "IRequests", "filter": "Id gt 0&$top=999"}))

    assert "&" in text


def test_odata_query_validates_fields(tmp_path):
    server, _ = make_server(tmp_path)

    text = error_text(
        call_tool(server, "odata_query", {"entity_set": "IRequests", "filter": "Id gt 0", "select": "Subject,Nope"})
    )
    assert "Nope" in text and "Subject" in text

    text = error_text(
        call_tool(server, "odata_query", {"entity_set": "IRequests", "filter": "Id gt 0", "expand": "Author($select=Name)"})
    )
    assert "expand" in text


def test_odata_query_passes_validated_params(tmp_path):
    server, client = make_server(tmp_path)

    data = payload(
        call_tool(
            server,
            "odata_query",
            {
                "entity_set": "IRequests",
                "filter": "RegistrationDate ge 2026-09-01T00:00:00+04:00",
                "select": "Id, Subject",
                "expand": "Author",
                "orderby": "RegistrationDate desc",
                "top": 2,
            },
        )
    )

    assert client.queries[-1] == {
        "entity_set": "IRequests",
        "filter_": "RegistrationDate ge 2026-09-01T00:00:00+04:00",
        "select": "Id,Subject",
        "expand": "Author",
        "orderby": "RegistrationDate desc",
        "top": 3,
    }
    assert data["returned"] == 2
    assert data["truncated"] is True


def test_odata_count_and_get(tmp_path):
    server, client = make_server(tmp_path)

    count = payload(call_tool(server, "odata_count", {"entity_set": "IRequests", "filter": "Id gt 0"}))
    record = payload(call_tool(server, "odata_get", {"entity_set": "IRequests", "record_id": 5, "expand": "Author"}))

    assert count == {"entity_set": "IRequests", "filter": "Id gt 0", "count": 1761}
    assert record["Id"] == 5
    assert client.paths[-1] == "IRequests(5)?$expand=Author"
```

- [ ] **Step 3: Запустить — должны упасть**

```powershell
& ".venv\Scripts\python.exe" -m pytest tests/unit/test_mcp_odata.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'src.mcp_server.odata_meta'`.

- [ ] **Step 4: Реализация**

Создать `src/mcp_server/resources.py`:

```python
import re
from dataclasses import dataclass
from pathlib import Path

from mcp.server.mcpserver import MCPServer

DESCRIPTION_LINE = re.compile(r"^description:\s*(.+)$", re.MULTILINE)


@dataclass(frozen=True)
class DomainGuide:
    name: str
    description: str
    body: str


def load_domain_guides(skills_dir: Path) -> dict[str, DomainGuide]:
    """Справочники доменов DRX из .claude/skills/rxapi-*/SKILL.md (только SKILL.md — без скриптов и словарей)."""
    guides: dict[str, DomainGuide] = {}
    for skill_file in sorted(Path(skills_dir).glob("rxapi-*/SKILL.md")):
        text = skill_file.read_text(encoding="utf-8")
        name = skill_file.parent.name.removeprefix("rxapi-")
        match = DESCRIPTION_LINE.search(text)
        guides[name] = DomainGuide(name=name, description=match.group(1).strip() if match else name, body=text)
    return guides


def _register_guide(mcp: MCPServer, guide: DomainGuide) -> None:
    @mcp.resource(
        f"drx://domains/{guide.name}",
        name=f"domain-{guide.name}",
        description=guide.description,
        mime_type="text/markdown",
    )
    def read_guide() -> str:
        return guide.body


def register(mcp: MCPServer, guides: dict[str, DomainGuide]) -> None:
    for guide in guides.values():
        _register_guide(mcp, guide)
```

Создать `src/mcp_server/odata_meta.py`:

```python
import threading
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any

EDM_NS = "{http://docs.oasis-open.org/odata/ns/edm}"

# Наборы с учётками, правами, сертификатами, лицензиями и аудитом в универсальный слой не пускаем.
DENY_SUBSTRINGS = (
    "login", "user", "certificate", "accessright", "license", "audit",
    "personalsetting", "password", "secret", "token", "session", "permission", "signature",
)


@dataclass(frozen=True)
class EntityInfo:
    entity_set: str
    entity_type: str
    properties: dict[str, str]
    navigation: dict[str, str]


def parse_metadata(xml_text: str) -> dict[str, EntityInfo]:
    root = ET.fromstring(xml_text)
    types: dict[str, tuple[dict[str, str], dict[str, str], str | None]] = {}
    for schema in root.iter(f"{EDM_NS}Schema"):
        namespace = schema.get("Namespace", "")
        for entity_type in schema.findall(f"{EDM_NS}EntityType"):
            properties = {p.get("Name"): p.get("Type") for p in entity_type.findall(f"{EDM_NS}Property")}
            navigation = {n.get("Name"): n.get("Type") for n in entity_type.findall(f"{EDM_NS}NavigationProperty")}
            types[f"{namespace}.{entity_type.get('Name')}"] = (properties, navigation, entity_type.get("BaseType"))

    def collect(type_name: str | None, seen: frozenset[str]) -> tuple[dict[str, str], dict[str, str]]:
        if not type_name or type_name not in types or type_name in seen:
            return {}, {}
        properties, navigation, base = types[type_name]
        base_properties, base_navigation = collect(base, seen | {type_name})
        return {**base_properties, **properties}, {**base_navigation, **navigation}

    entities: dict[str, EntityInfo] = {}
    for entity_set in root.iter(f"{EDM_NS}EntitySet"):
        name, type_name = entity_set.get("Name"), entity_set.get("EntityType")
        properties, navigation = collect(type_name, frozenset())
        entities[name] = EntityInfo(name, type_name, properties, navigation)
    return entities


def is_denied(entity_set: str) -> bool:
    lowered = entity_set.lower()
    return any(marker in lowered for marker in DENY_SUBSTRINGS)


class MetadataCache:
    """$metadata одинаков для всех пользователей — скачиваем один раз кредами первого вызвавшего."""

    def __init__(self):
        self._lock = threading.Lock()
        self._entities: dict[str, EntityInfo] | None = None

    def get(self, client: Any) -> dict[str, EntityInfo]:
        with self._lock:
            if self._entities is None:
                self._entities = parse_metadata(client.get_metadata_xml())
            return self._entities
```

Создать `src/mcp_server/tools/odata.py`:

```python
import re
from typing import Annotated

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from src.mcp_server.envelope import clamp_limit, list_envelope
from src.mcp_server.odata_meta import EntityInfo, MetadataCache, is_denied
from src.mcp_server.resources import DomainGuide
from src.mcp_server.runner import READ_ONLY, ToolRunner

GENERIC_MAX_TOP = 50
MAX_FILTER_LENGTH = 1000
FORBIDDEN_FILTER_CHARS = ("&", "?", "#")
SIMPLE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

EntitySet = Annotated[str, Field(description="Имя набора данных Directum RX, например IRequests")]
Filter = Annotated[str, Field(description="Обязательный OData $filter, например Status eq 'InProcess'")]


def resolve_entity(entities: dict[str, EntityInfo], entity_set: str) -> EntityInfo:
    if is_denied(entity_set) or entity_set not in entities:
        raise ToolError(
            f"Набор данных «{entity_set}» недоступен. Найди нужный набор через odata_list_domains и справочники drx://domains/*."
        )
    return entities[entity_set]


def check_filter(expression: str) -> str:
    text = (expression or "").strip()
    if not text:
        raise ToolError("Нужен фильтр: Directum отклоняет запросы без $filter. Пример: Status eq 'InProcess'.")
    if len(text) > MAX_FILTER_LENGTH:
        raise ToolError(f"Фильтр длиннее {MAX_FILTER_LENGTH} символов — упрости условие.")
    if any(char in text for char in FORBIDDEN_FILTER_CHARS):
        raise ToolError("В фильтре нельзя использовать символы &, ? и #.")
    return text


def check_fields(csv: str, allowed: dict[str, str], label: str) -> str:
    names = [part.strip() for part in csv.split(",") if part.strip()]
    unknown = [name for name in names if name not in allowed]
    if unknown:
        raise ToolError(
            f"Неизвестные поля в {label}: {', '.join(unknown)}. Доступные: {', '.join(sorted(allowed)[:80])}."
        )
    return ",".join(names)


def check_orderby(csv: str, allowed: dict[str, str]) -> str:
    parts = []
    for part in (piece.strip() for piece in csv.split(",")):
        if not part:
            continue
        tokens = part.split()
        valid_direction = len(tokens) == 1 or (len(tokens) == 2 and tokens[1].lower() in ("asc", "desc"))
        if tokens[0] not in allowed or not valid_direction:
            raise ToolError(
                f"Некорректная сортировка «{part}». Формат: Поле [asc|desc]. Доступные поля: {', '.join(sorted(allowed)[:80])}."
            )
        parts.append(part)
    return ",".join(parts)


def check_expand(csv: str, navigation: dict[str, str]) -> str:
    names = [part.strip() for part in csv.split(",") if part.strip()]
    if any(not SIMPLE_NAME.match(name) for name in names):
        raise ToolError("В expand разрешены только имена навигационных свойств через запятую, без вложенных параметров.")
    return check_fields(",".join(names), navigation, "expand")


def register(mcp: MCPServer, runner: ToolRunner, metadata: MetadataCache, guides: dict[str, DomainGuide]) -> None:
    @mcp.tool(annotations=READ_ONLY)
    async def odata_list_domains() -> dict:
        """Домены Directum RX и справочники к ним: какие наборы данных где искать. Подробности — в ресурсе drx://domains/<name>."""
        return {
            "domains": [
                {"name": guide.name, "description": guide.description, "resource": f"drx://domains/{guide.name}"}
                for guide in guides.values()
            ]
        }

    @mcp.tool(annotations=READ_ONLY)
    async def odata_describe_entity(entity_set: EntitySet, ctx: Context) -> dict:
        """Поля и навигационные свойства набора данных. Вызывай перед odata_query, чтобы не гадать с именами полей."""

        def action(s):
            info = resolve_entity(metadata.get(s.client), entity_set)
            return {
                "entity_set": info.entity_set,
                "properties": [{"name": name, "type": kind} for name, kind in sorted(info.properties.items())],
                "navigation": [{"name": name, "type": kind} for name, kind in sorted(info.navigation.items())],
            }

        return await runner.run(ctx, "odata_describe_entity", action)

    @mcp.tool(annotations=READ_ONLY)
    async def odata_query(
        entity_set: EntitySet,
        filter: Filter,
        ctx: Context,
        select: Annotated[str | None, Field(description="Поля через запятую")] = None,
        expand: Annotated[str | None, Field(description="Навигационные свойства через запятую")] = None,
        orderby: Annotated[str | None, Field(description="Сортировка: Поле [asc|desc]")] = None,
        top: Annotated[int, Field(description="Сколько записей (1–50)", ge=1, le=GENERIC_MAX_TOP)] = 20,
    ) -> dict:
        """Универсальный запрос чтения к разрешённому набору данных Directum RX. Всегда указывай filter; поля смотри через odata_describe_entity."""
        size = clamp_limit(top, maximum=GENERIC_MAX_TOP)

        def action(s):
            info = resolve_entity(metadata.get(s.client), entity_set)
            rows = s.client.query(
                entity_set,
                filter_=check_filter(filter),
                select=check_fields(select, info.properties, "select") if select else None,
                expand=check_expand(expand, info.navigation) if expand else None,
                orderby=check_orderby(orderby, info.properties) if orderby else None,
                top=size + 1,
            )
            return list_envelope(rows, size)

        return await runner.run(ctx, "odata_query", action)

    @mcp.tool(annotations=READ_ONLY)
    async def odata_count(entity_set: EntitySet, filter: Filter, ctx: Context) -> dict:
        """Количество записей набора данных по фильтру."""

        def action(s):
            resolve_entity(metadata.get(s.client), entity_set)
            expression = check_filter(filter)
            return {"entity_set": entity_set, "filter": expression, "count": s.client.count(entity_set, filter_=expression)}

        return await runner.run(ctx, "odata_count", action)

    @mcp.tool(annotations=READ_ONLY)
    async def odata_get(
        entity_set: EntitySet,
        record_id: Annotated[int, Field(description="Id записи", gt=0)],
        ctx: Context,
        expand: Annotated[str | None, Field(description="Навигационные свойства через запятую")] = None,
    ) -> dict:
        """Одна запись набора данных по Id."""

        def action(s):
            info = resolve_entity(metadata.get(s.client), entity_set)
            path = f"{entity_set}({record_id})"
            if expand:
                path += "?$expand=" + check_expand(expand, info.navigation)
            return s.client.get_one(path)

        return await runner.run(ctx, "odata_get", action)
```

В `src/mcp_server/app.py`:
1. Импорты:

```python
from pathlib import Path

from src.mcp_server import resources
from src.mcp_server.odata_meta import MetadataCache
from src.mcp_server.tools import action_items, common, documents, odata
```

2. Константа после `SERVER_NAME`:

```python
SKILLS_DIR = Path(__file__).resolve().parents[2] / ".claude" / "skills"
```

3. Заменить `build_server` на:

```python
def build_server(provider: Any, usage: ToolUsageStore | None = None, skills_dir: Path = SKILLS_DIR) -> MCPServer:
    runner = ToolRunner(provider, usage)
    mcp = MCPServer(SERVER_NAME, instructions=INSTRUCTIONS)
    common.register(mcp, runner)
    action_items.register(mcp, runner)
    documents.register(mcp, runner)
    guides = resources.load_domain_guides(skills_dir)
    odata.register(mcp, runner, MetadataCache(), guides)
    resources.register(mcp, guides)
    _register_health(mcp)
    return mcp
```

- [ ] **Step 5: Запустить — должны пройти**

```powershell
& ".venv\Scripts\python.exe" -m pytest tests/unit -k "mcp" -v
```

Expected: все PASS.

- [ ] **Step 6: Commit**

```powershell
git add src/mcp_server/resources.py src/mcp_server/odata_meta.py src/mcp_server/tools/odata.py src/mcp_server/app.py tests/unit/mcp_fakes.py tests/unit/test_mcp_odata.py
git commit -m "feat(mcp): справочники доменов и универсальный слой чтения OData" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 11: Прокси родного MCP Directum RX

**Files:**
- Create: `src/mcp_server/native.py`
- Modify: `src/mcp_server/app.py` (middleware)
- Test: `tests/unit/test_mcp_native.py`

**Interfaces:**
- Consumes: `DirectumClient.post(entity_set, payload) -> dict`, `ServicesProvider.open`, `TtlCache`, `to_tool_error`, `ToolUsageStore.record`.
- Produces:
  - `NATIVE_PREFIX = "drx_native_"`, `HANDLE_MCP_PATH = "IntegrationAIAgent/HandleMcpRequest"`.
  - `NativeMcpClient(client)` с `request(method, params) -> dict`, `list_read_only_tools() -> list[dict]`, `call_tool(name, arguments) -> dict`.
  - `NativeProxyMiddleware(provider, cache: TtlCache, usage=None)` — `ServerMiddleware`: дописывает read-only родные тулы в `tools/list`, обрабатывает `tools/call drx_native_*`.

- [ ] **Step 1: Падающие тесты**

Создать `tests/unit/test_mcp_native.py`:

```python
import json
from types import SimpleNamespace

import pytest

from src.mcp_server.app import build_server
from src.mcp_server.native import NativeMcpClient
from src.services.directum_client import DirectumError
from tests.unit.mcp_fakes import FakeProvider, call_tool, error_text, list_tools

NATIVE_TOOLS = [
    {
        "name": "gd_dashboard_ai_agent_get_action_items2_info",
        "description": "Найти информацию об исполнении поручений",
        "inputSchema": {"type": "object", "properties": {"assigneeName": {"type": "string"}}, "required": []},
        "annotations": {"title": "Исполнение поручений", "readOnlyHint": True},
    },
    {
        "name": "danger_write_tool",
        "description": "Пишет в систему",
        "inputSchema": {"type": "object", "properties": {}},
        "annotations": {"readOnlyHint": False},
    },
]


class FakeNativeClient:
    def __init__(self, fail=False):
        self.fail = fail
        self.posts = []

    def post(self, entity_set, payload):
        if self.fail:
            raise DirectumError("Directum unavailable", 503)
        message = json.loads(payload["value"])
        self.posts.append((entity_set, message))
        if message["method"] == "tools/list":
            result = {"tools": NATIVE_TOOLS}
        elif message["method"] == "tools/call":
            text = f"called {message['params']['name']} with {json.dumps(message['params']['arguments'], ensure_ascii=False)}"
            result = {"content": [{"type": "text", "text": text}], "isError": False}
        else:
            return {"value": json.dumps({"jsonrpc": "2.0", "id": 1, "error": {"code": -32601, "message": "no method"}})}
        return {"value": json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": result}, ensure_ascii=False)}


def make_server(tmp_path, client):
    return build_server(FakeProvider(SimpleNamespace(client=client)), skills_dir=tmp_path)


def test_native_client_filters_read_only_tools():
    tools = NativeMcpClient(FakeNativeClient()).list_read_only_tools()

    assert [tool["name"] for tool in tools] == ["gd_dashboard_ai_agent_get_action_items2_info"]


def test_native_client_raises_on_jsonrpc_error():
    with pytest.raises(DirectumError, match="no method"):
        NativeMcpClient(FakeNativeClient()).request("prompts/get", {})


def test_native_tools_appear_with_prefix(tmp_path):
    names = [tool.name for tool in list_tools(make_server(tmp_path, FakeNativeClient()))]

    assert "drx_native_gd_dashboard_ai_agent_get_action_items2_info" in names
    assert "drx_native_danger_write_tool" not in names
    assert "get_current_user" in names


def test_native_call_is_forwarded_without_prefix(tmp_path):
    client = FakeNativeClient()
    server = make_server(tmp_path, client)

    result = call_tool(server, "drx_native_gd_dashboard_ai_agent_get_action_items2_info", {"assigneeName": "Ардо"})

    assert not result.is_error
    assert result.content[0].text == 'called gd_dashboard_ai_agent_get_action_items2_info with {"assigneeName": "Ардо"}'
    entity_set, message = client.posts[-1]
    assert entity_set == "IntegrationAIAgent/HandleMcpRequest"
    assert message["method"] == "tools/call"


def test_non_read_only_native_tool_cannot_be_called(tmp_path):
    text = error_text(call_tool(make_server(tmp_path, FakeNativeClient()), "drx_native_danger_write_tool"))

    assert "недоступен" in text


def test_unavailable_native_mcp_does_not_break_tool_list(tmp_path):
    names = [tool.name for tool in list_tools(make_server(tmp_path, FakeNativeClient(fail=True)))]

    assert "get_current_user" in names
    assert not any(name.startswith("drx_native_") for name in names)
```

- [ ] **Step 2: Запустить — должны упасть**

```powershell
& ".venv\Scripts\python.exe" -m pytest tests/unit/test_mcp_native.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'src.mcp_server.native'`.

- [ ] **Step 3: Реализация**

Создать `src/mcp_server/native.py`:

```python
import json
import logging
import time
from typing import Any

import anyio
from mcp.types import CallToolResult, TextContent

from src.mcp_server.audit import ToolUsageStore
from src.mcp_server.context import TtlCache
from src.mcp_server.errors import to_tool_error
from src.services.directum_client import DirectumError

logger = logging.getLogger("mcp_ogv.native")

NATIVE_PREFIX = "drx_native_"
HANDLE_MCP_PATH = "IntegrationAIAgent/HandleMcpRequest"


class NativeMcpClient:
    """Встроенный MCP Directum RX: JSON-RPC передаётся строкой через OData-action HandleMcpRequest."""

    def __init__(self, client: Any):
        self.client = client

    def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        message = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        response = self.client.post(HANDLE_MCP_PATH, {"value": json.dumps(message, ensure_ascii=False)})
        raw = response.get("value") if isinstance(response, dict) else None
        if not isinstance(raw, str):
            raise DirectumError("Встроенный MCP Directum вернул неожиданный ответ")
        payload = json.loads(raw)
        error = payload.get("error")
        if error:
            text = error.get("message", "") if isinstance(error, dict) else str(error)
            raise DirectumError(f"Встроенный MCP Directum: {text}")
        return payload.get("result") or {}

    def list_read_only_tools(self) -> list[dict[str, Any]]:
        tools = self.request("tools/list", {}).get("tools", [])
        return [
            tool for tool in tools
            if isinstance(tool, dict) and (tool.get("annotations") or {}).get("readOnlyHint") is True
        ]

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return self.request("tools/call", {"name": name, "arguments": arguments or {}})


def _error_result(text: str) -> CallToolResult:
    return CallToolResult(content=[TextContent(type="text", text=text)], is_error=True)


class NativeProxyMiddleware:
    """Добавляет read-only тулы встроенного MCP Directum в tools/list и проксирует их вызовы.

    Список зависит от пользователя (его креды и права), поэтому кешируется по отпечатку кредов.
    Вызов перепроверяет, что тул read-only: имя пишущего тула угадать и вызвать нельзя.
    """

    def __init__(self, provider: Any, cache: TtlCache, usage: ToolUsageStore | None = None):
        self.provider = provider
        self.cache = cache
        self.usage = usage

    async def __call__(self, ctx: Any, call_next: Any) -> Any:
        request = getattr(ctx, "request", None)
        headers = request.headers if request is not None else None
        if ctx.method == "tools/list":
            result = await call_next(ctx)
            data = result.model_dump(by_alias=True, exclude_none=True) if hasattr(result, "model_dump") else dict(result)
            native = await anyio.to_thread.run_sync(self._native_tools, headers)
            data["tools"] = list(data.get("tools", [])) + native
            return data
        if ctx.method == "tools/call":
            params = ctx.params or {}
            name = params.get("name", "")
            if isinstance(name, str) and name.startswith(NATIVE_PREFIX):
                return await anyio.to_thread.run_sync(self._call, headers, name, params.get("arguments") or {})
        return await call_next(ctx)

    def _read_only_tools(self, credentials: Any, services: Any) -> list[dict[str, Any]]:
        tools = self.cache.get(credentials.fingerprint)
        if tools is None:
            tools = NativeMcpClient(services.client).list_read_only_tools()
            self.cache.set(credentials.fingerprint, tools)
        return tools

    def _native_tools(self, headers: Any) -> list[dict[str, Any]]:
        try:
            with self.provider.open(headers) as (credentials, services):
                tools = self._read_only_tools(credentials, services)
        except Exception as exc:
            logger.warning("Directum native MCP tools unavailable: %s", type(exc).__name__)
            return []
        return [{**tool, "name": NATIVE_PREFIX + tool["name"]} for tool in tools]

    def _call(self, headers: Any, name: str, arguments: dict[str, Any]) -> CallToolResult:
        started = time.perf_counter()
        native_name = name[len(NATIVE_PREFIX):]
        fingerprint = None
        error_kind = None
        try:
            with self.provider.open(headers) as (credentials, services):
                fingerprint = credentials.fingerprint
                allowed = {tool["name"] for tool in self._read_only_tools(credentials, services)}
                if native_name not in allowed:
                    error_kind = "not_allowed"
                    return _error_result(
                        f"Инструмент {name} недоступен: проксируются только read-only инструменты Directum."
                    )
                result = NativeMcpClient(services.client).call_tool(native_name, arguments)
        except Exception as exc:
            error, error_kind = to_tool_error(exc)
            return _error_result(str(error))
        finally:
            if self.usage is not None:
                duration_ms = int((time.perf_counter() - started) * 1000)
                self.usage.record(name, error_kind is None, error_kind, duration_ms, fingerprint)
        texts = [
            item.get("text", "")
            for item in result.get("content", [])
            if isinstance(item, dict) and item.get("type") == "text"
        ]
        return CallToolResult(
            content=[TextContent(type="text", text=text) for text in texts] or [TextContent(type="text", text="")],
            is_error=bool(result.get("isError")),
        )
```

В `src/mcp_server/app.py`:
1. Импорты: `from src.mcp_server.context import ServicesProvider, TtlCache` (заменить существующий импорт `ServicesProvider`) и `from src.mcp_server.native import NativeProxyMiddleware`.
2. Константа после `SKILLS_DIR`: `NATIVE_TOOLS_TTL_SECONDS = 600`.
3. В `build_server` заменить строку создания сервера на:

```python
    native = NativeProxyMiddleware(provider, TtlCache(NATIVE_TOOLS_TTL_SECONDS), usage)
    mcp = MCPServer(SERVER_NAME, instructions=INSTRUCTIONS, middleware=[native])
```

- [ ] **Step 4: Запустить — должны пройти, остальное не сломано**

```powershell
& ".venv\Scripts\python.exe" -m pytest tests/unit tests/integration -q
```

Expected: все PASS.

- [ ] **Step 5: Commit**

```powershell
git add src/mcp_server/native.py src/mcp_server/app.py tests/unit/test_mcp_native.py
git commit -m "feat(mcp): прокси read-only тулов встроенного MCP Directum RX" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 12: Артефакты деплоя, документация, live-проверка

**Files:**
- Modify: `Dockerfile`, `docker-compose.yml`, `.env.example`, `pyproject.toml` (маркер `live`), `README.md`
- Create: `docs/mcp-ogv/librechat.example.yaml`, `tests/live/test_mcp_live.py`

**Interfaces:**
- Consumes: всё выше.
- Produces: образ с `python -m src.mcp_server`, сервис `mcp-ogv` в compose, пример конфига LibreChat, раздел README, опциональный live-smoke.

Деплой на целевой стенд выполняется в отдельной сессии — здесь только артефакты и проверки.

- [ ] **Step 1: Live-smoke тест (пропускается без переменных окружения)**

Создать `tests/live/test_mcp_live.py`:

```python
import base64
import os

import pytest

from src.mcp_server.app import build_server
from src.mcp_server.config import McpSettings
from src.mcp_server.context import ServicesProvider
from tests.unit.mcp_fakes import call_tool, list_tools, payload

LOGIN = os.getenv("MCP_LIVE_LOGIN")
PASSWORD = os.getenv("MCP_LIVE_PASSWORD")
BASE_URL = os.getenv("MCP_LIVE_BASE_URL")

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        not (LOGIN and PASSWORD and BASE_URL),
        reason="set MCP_LIVE_BASE_URL, MCP_LIVE_LOGIN, MCP_LIVE_PASSWORD to run the live smoke",
    ),
]


@pytest.fixture(scope="module")
def server():
    token = "Basic " + base64.b64encode(f"{LOGIN}:{PASSWORD}".encode()).decode()
    settings = McpSettings(
        DIRECTUM_BASE_URL=BASE_URL,
        DIRECTUM_AUTH_TOKEN=token,
        MCP_ALLOW_ENV_CREDENTIALS=True,
        _env_file=None,
    )
    return build_server(ServicesProvider(settings))


def test_live_current_user(server):
    assert payload(call_tool(server, "get_current_user"))["id"] > 0


def test_live_my_assignments_has_total(server):
    data = payload(call_tool(server, "list_my_assignments", {"limit": 3}))

    assert data["returned"] <= 3
    assert data["total"] is not None


def test_live_discipline(server):
    assert "in_process" in payload(call_tool(server, "get_discipline_analytics"))


def test_live_odata_count(server):
    data = payload(call_tool(server, "odata_count", {"entity_set": "IAssignments", "filter": "Status eq 'InProcess'"}))

    assert data["count"] >= 0


def test_live_native_tools_listed(server):
    assert any(tool.name.startswith("drx_native_") for tool in list_tools(server))
```

В `pyproject.toml` в секцию `[tool.pytest.ini_options]` добавить:

```toml
markers = ["live: smoke-проверки на реальном стенде Directum RX (пропускаются без MCP_LIVE_*)"]
```

Проверить, что тест корректно пропускается:

```powershell
& ".venv\Scripts\python.exe" -m pytest tests/live -v
```

Expected: 5 SKIPPED.

- [ ] **Step 2: Docker и compose**

В `Dockerfile` после строки `COPY tests ./tests` добавить:

```dockerfile
# Справочники доменов DRX для MCP-ресурсов drx://domains/* (только SKILL.md читаются сервером)
COPY .claude/skills ./.claude/skills
```

В `docker-compose.yml` в `services:` добавить второй сервис:

```yaml
  mcp-ogv:
    build: .
    command: ["python", "-m", "src.mcp_server"]
    env_file:
      - .env
    ports:
      - "8010:8010"
    volumes:
      - ./data:/app/data
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8010/health')"]
      interval: 30s
      timeout: 5s
      retries: 3
```

- [ ] **Step 3: `.env.example`**

Добавить в конец `.env.example` (только плейсхолдеры):

```env

# --- mcpOGV (MCP-сервер для LibreChat) ---
# Общий секрет LibreChat <-> mcpOGV (заголовок X-MCP-Key). Обязателен вне режима отладки.
MCP_OGV_KEY=change-me
MCP_HOST=0.0.0.0
MCP_PORT=8010
# Host-заголовки, которым разрешено обращаться к серверу (защита от DNS rebinding)
MCP_ALLOWED_HOSTS=localhost:8010,127.0.0.1:8010,mcp-ogv:8010
MCP_DIRECTUM_TIMEOUT_SECONDS=20
MCP_USAGE_DB_PATH=data/mcp_usage.db
# Только для локальной отладки: брать DIRECTUM_AUTH_TOKEN, если нет заголовков с кредами
MCP_ALLOW_ENV_CREDENTIALS=false
```

- [ ] **Step 4: Пример конфига LibreChat**

Создать `docs/mcp-ogv/librechat.example.yaml`:

```yaml
# Фрагмент librechat.yaml для подключения mcpOGV.
# MCP_OGV_KEY задаётся в окружении LibreChat и совпадает со значением в .env mcpOGV.
mcpServers:
  mcpOGV:
    type: streamable-http
    url: http://mcp-ogv:8010/mcp
    headers:
      X-Directum-Login: '{{DRX_LOGIN}}'
      X-Directum-Password: '{{DRX_PASSWORD}}'
      X-MCP-Key: '${MCP_OGV_KEY}'
    customUserVars:
      DRX_LOGIN:
        title: 'Логин Directum RX'
        description: 'Ваш логин в Directum RX. mcpOGV работает с вашими правами.'
      DRX_PASSWORD:
        title: 'Пароль Directum RX'
        description: 'Хранится в LibreChat, в mcpOGV не сохраняется и не логируется.'
    serverInstructions: true
    timeout: 60000
```

- [ ] **Step 5: README**

Добавить в `README.md` раздел (перед разделом о тестах, если он есть, иначе в конец):

````markdown
## mcpOGV — MCP-сервер для LibreChat

Отдельный процесс, который даёт агентной платформе на базе LibreChat доступ к Directum RX по протоколу MCP (Streamable HTTP). Работает **от имени пользователя**: логин и пароль Directum пользователь вводит в LibreChat (`customUserVars`), они приходят в заголовках и в mcpOGV не сохраняются.

**Состав (этап 1):**
- курируемые тулы: `get_current_user`, `search_employees`, `list_my_assignments`, `list_action_items`, `get_action_item`, `get_discipline_analytics`, `get_outgoing_action_items_analytics`, `search_documents`, `get_document`, `list_documents_by_counterparty`, `list_letters`, `list_my_meetings`;
- универсальное чтение: `odata_list_domains`, `odata_describe_entity`, `odata_query`, `odata_count`, `odata_get` (фильтр обязателен, чувствительные наборы закрыты);
- справочники доменов — ресурсы `drx://domains/*`;
- тулы встроенного MCP Directum (`drx_native_*`, только read-only).

**Локальный запуск:**

```powershell
& ".venv\Scripts\python.exe" -m src.mcp_server
```

Проверка: `http://localhost:8010/health`. Нужные переменные — в `.env.example` (раздел mcpOGV). Для отладки без LibreChat можно включить `MCP_ALLOW_ENV_CREDENTIALS=true` — тогда используется `DIRECTUM_AUTH_TOKEN`.

**Docker:** сервис `mcp-ogv` в `docker-compose.yml` (порт 8010). Подключение к LibreChat — `docs/mcp-ogv/librechat.example.yaml`; LibreChat и `mcp-ogv` должны быть в одной Docker-сети, хост `mcp-ogv:8010` — в `MCP_ALLOWED_HOSTS`.

**Метрики использования тулов:** SQLite `MCP_USAGE_DB_PATH` (тул, успех, длительность, хеш пользователя — без кредов).

**Live-проверка на стенде** (только чтение, креды из окружения):

```powershell
$env:MCP_LIVE_BASE_URL = "https://<стенд>/Integration/odata"
$env:MCP_LIVE_LOGIN = "<логин>"
$env:MCP_LIVE_PASSWORD = "<пароль>"
& ".venv\Scripts\python.exe" -m pytest tests/live -v
```
````

- [ ] **Step 6: Полный прогон и покрытие**

```powershell
& ".venv\Scripts\python.exe" -m pytest tests/unit tests/integration --cov=src/mcp_server --cov-report=term-missing -q
```

Expected: все PASS; покрытие `src/mcp_server` ≥ 70%.

- [ ] **Step 7: Проверка запуска процесса**

```powershell
$env:DIRECTUM_BASE_URL = "https://rx.example/Integration/odata"; $env:MCP_ALLOW_ENV_CREDENTIALS = "true"
$job = Start-Job { Set-Location $using:PWD; & ".\.venv\Scripts\python.exe" -m src.mcp_server }
Start-Sleep -Seconds 4
Invoke-RestMethod http://127.0.0.1:8010/health
Stop-Job $job; Remove-Job $job
```

Expected: `status = ok`, `server = mcpOGV`.

- [ ] **Step 8: Commit**

```powershell
git add Dockerfile docker-compose.yml .env.example pyproject.toml README.md docs/mcp-ogv/librechat.example.yaml tests/live/test_mcp_live.py
git commit -m "feat(mcp): артефакты деплоя mcpOGV, пример конфига LibreChat, live-smoke" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## Покрытие спеки этапом 1

| Раздел спеки | Задачи |
|---|---|
| 3 — ограничения стенда (фильтр, таймауты, строковые ошибки) | 1, 6, 10 |
| 4 — архитектура, factory, аналитика в сервисе, лимиты | 2, 3, 4, 6–9 |
| 5 — авторизация, заголовки, ключ, кеш пользователя, отладка | 5, 7 |
| 6 — слой 1 (перенос), слой 2, слой 3 (rxapi), слой 4 | 7–11 |
| 8 — защита универсального слоя | 10 |
| 9 — прокси родного MCP | 11 |
| 10 — ошибки, логи, метрики использования, `/health` | 6, 7, 11 |
| 11 — тесты: unit, интеграционные, live | все, 12 |
| 12 — деплой-артефакты | 12 |
| 7 — безопасная запись; 6 — обращения, рассмотрение, согласование, НПА, дашборд, дисциплина по исполнителям | следующие планы (этапы 2–5) |
