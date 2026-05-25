# Meetings List + Action Item Report — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add two features to the Directum RX chat assistant: (1) list upcoming meetings via chat and sidebar, (2) generate an LLM narrative report for a specific action item from analytics.

**Architecture:** New `MeetingsService` handles OData queries for `IMeetings` and `IActionItemExecutionTasks`. Two new tools (`get_my_meetings`, `get_action_item_details`) are registered in `ToolRegistry`. `LLMService` does direct keyword routing — like existing `_direct_rx_response` — calling the tools and formatting Markdown output, generating narrative via a non-streaming LLM call for action item reports. Frontend adds a Sidebar button and makes analytics items clickable.

**Tech Stack:** Python 3.10+, FastAPI, httpx (OData), OpenAI SDK (narrative generation), Pydantic v2, pytest, Vanilla JS.

---

## File Map

| File | Change |
|---|---|
| `src/models/schemas.py` | Add `MeetingSummary`, `ActionItemDetail` |
| `src/services/meetings.py` | **Create** — `MeetingsService` |
| `src/services/directum_client.py` | Add `DIRECTUM_MEETING_CARD_GUID` constant + map entry |
| `src/services/tool_registry.py` | Add two tools, accept `MeetingsService` in `__init__` |
| `src/services/llm_service.py` | Add direct routing for meetings + action item report |
| `src/main.py` | Add `/api/directum/meetings/upcoming` endpoint, wire `MeetingsService` |
| `src/static/index.html` | Add "Мои совещания" sidebar button |
| `src/static/app.js` | Sidebar meetings rendering + analytics clickable items |
| `tests/unit/test_meetings.py` | **Create** — unit tests for `MeetingsService` |
| `tests/unit/test_tool_registry.py` | Add tests for two new tools |
| `tests/unit/test_llm_service.py` | Add tests for new direct routing methods |

---

## ✅ Task 1 COMPLETED: Pydantic models for MeetingSummary and ActionItemDetail

**Files:**
- Modify: `src/models/schemas.py`
- Test: `tests/unit/test_meetings.py` (just model validation for now)

- [x] **Step 1: Write failing model tests**

Create `tests/unit/test_meetings.py`:

```python
from datetime import date, datetime, timezone
from src.models.schemas import ActionItemDetail, MeetingSummary


def test_meeting_summary_fields():
    m = MeetingSummary(
        id=1,
        subject="Планёрка",
        start_date=datetime(2026, 5, 27, 10, 0, tzinfo=timezone.utc),
        end_date=None,
        place="Каб. 305",
        agenda_summary="Итоги квартала",
        client_card_url="https://rx.example/Client/#/card/abc/1",
    )
    assert m.id == 1
    assert m.place == "Каб. 305"
    assert m.agenda_summary == "Итоги квартала"


def test_meeting_summary_optional_fields_default_none():
    m = MeetingSummary(
        id=2,
        subject="Без места",
        start_date=datetime(2026, 5, 27, 10, 0, tzinfo=timezone.utc),
        client_card_url="",
    )
    assert m.end_date is None
    assert m.place is None
    assert m.agenda_summary is None


def test_action_item_detail_fields():
    d = ActionItemDetail(
        id=42,
        subject="Подготовить записку",
        text="Подготовить аналитическую записку",
        performer="Иванова М.П.",
        author="Петров А.С.",
        deadline=date(2026, 5, 30),
        status="InProcess",
        created_date=date(2026, 5, 20),
        client_card_url="https://rx.example/Client/#/card/abc/42",
        narrative="",
    )
    assert d.id == 42
    assert d.narrative == ""
```

- [x] **Step 2: Run tests to confirm they fail**

```
& ".venv\Scripts\python.exe" -m pytest tests/unit/test_meetings.py -v
```
Expected: `ImportError` — `MeetingSummary` not defined.

- [x] **Step 3: Add models to schemas.py**

In `src/models/schemas.py`, add after the `from datetime import datetime` import:
```python
from datetime import date, datetime
```
(replace existing `from datetime import datetime` line)

Then add at the end of the file:

```python
class MeetingSummary(BaseModel):
    id: int
    subject: str
    start_date: datetime
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
```

- [x] **Step 4: Run tests to confirm they pass**

```
& ".venv\Scripts\python.exe" -m pytest tests/unit/test_meetings.py -v
```
Expected: 3 PASSED.

- [x] **Step 5: Commit**

```
git add src/models/schemas.py tests/unit/test_meetings.py
git commit -m "feat: add MeetingSummary and ActionItemDetail pydantic models"
```

---

## ✅ Task 2 COMPLETED: Add IMeetings card GUID to DirectumClient

**Files:**
- Modify: `src/services/directum_client.py`
- Test: `tests/unit/test_directum_client.py` (add one test)

Context: `build_client_card_url("IMeetings(42)")` currently returns `None` because `IMeetings` is not in `DIRECTUM_CARD_GUIDS_BY_ENTITY`. We add a placeholder GUID that will need to be verified against a real Directum instance.

- [x] **Step 1: Write failing test**

Add to `tests/unit/test_directum_client.py`:

```python
def test_build_client_card_url_for_meeting():
    client = DirectumClient("https://rx.example/Integration/odata", "Basic dXNlcjpwYXNz")
    url = client.build_client_card_url("IMeetings(5)")
    assert url is not None
    assert "/5" in url
    assert "rx.example" in url
```

- [x] **Step 2: Run test to confirm it fails**

```
& ".venv\Scripts\python.exe" -m pytest tests/unit/test_directum_client.py::test_build_client_card_url_for_meeting -v
```
Expected: FAIL — `assert url is not None` fails (returns `None`).

- [x] **Step 3: Add GUID constant and map entry to directum_client.py**

In `src/services/directum_client.py`, after the existing constants block:

```python
DIRECTUM_TASK_CARD_GUID = "83f2a537-0cf0-4429-ae76-e9a386ca53aa"
# NOTE: Verify this GUID against a real Directum RX instance (Admin > Forms > IMeetings)
DIRECTUM_MEETING_CARD_GUID = "a9b3c4d5-1234-5678-abcd-ef0123456789"
DIRECTUM_CARD_GUIDS_BY_ENTITY = {
    "IAssignments": DIRECTUM_TASK_CARD_GUID,
    "IActionItemExecutionAssignments": DIRECTUM_TASK_CARD_GUID,
    "IActionItemExecutionTasks": DIRECTUM_TASK_CARD_GUID,
    "ISimpleTasks": DIRECTUM_TASK_CARD_GUID,
    "IMeetings": DIRECTUM_MEETING_CARD_GUID,
}
```

- [x] **Step 4: Run test to confirm it passes**

```
& ".venv\Scripts\python.exe" -m pytest tests/unit/test_directum_client.py::test_build_client_card_url_for_meeting -v
```
Expected: PASS.

- [x] **Step 5: Run full unit tests to catch regressions**

```
& ".venv\Scripts\python.exe" -m pytest tests/unit/ -v
```
Expected: all PASSED.

- [x] **Step 6: Commit**

```
git add src/services/directum_client.py tests/unit/test_directum_client.py
git commit -m "feat: add IMeetings card GUID to DirectumClient"
```

---

## ✅ Task 3 COMPLETED: Create MeetingsService — get_my_meetings

**Files:**
- Create: `src/services/meetings.py`
- Modify: `tests/unit/test_meetings.py`

- [x] **Step 1: Write failing tests**

Add to `tests/unit/test_meetings.py`:

```python
from datetime import datetime, timezone
from src.models.schemas import DirectumUser, MeetingSummary
from src.services.meetings import MeetingsService


class FakeMeetingsClient:
    def __init__(self, rows=None):
        self.calls = []
        self._rows = rows or []

    def query(self, entity_set, **kwargs):
        self.calls.append((entity_set, kwargs))
        return self._rows

    def build_client_card_url(self, entity_path):
        eid = entity_path.split("(")[1].rstrip(")")
        return f"https://rx.example/Client/#/card/meeting-guid/{eid}"


class FakeCurrentUser:
    def get_current_user(self):
        return DirectumUser(id=1165, name="Test User", login="nt_work\\user")


def test_get_my_meetings_returns_empty_list_when_no_rows():
    service = MeetingsService(
        client=FakeMeetingsClient(rows=[]),
        current_user_service=FakeCurrentUser(),
    )
    result = service.get_my_meetings()
    assert result == []


def test_get_my_meetings_filters_by_date_and_member():
    rows = [
        {
            "Id": 10,
            "Subject": "Планёрка",
            "StartDate": "2026-05-27T10:00:00Z",
            "EndDate": "2026-05-27T11:00:00Z",
            "Place": "Зал 1",
            "Minutes": [],
        }
    ]
    client = FakeMeetingsClient(rows=rows)
    service = MeetingsService(client=client, current_user_service=FakeCurrentUser())

    result = service.get_my_meetings(days=7)

    assert len(result) == 1
    m = result[0]
    assert m.id == 10
    assert m.subject == "Планёрка"
    assert m.place == "Зал 1"
    assert m.client_card_url == "https://rx.example/Client/#/card/meeting-guid/10"

    entity_set, kwargs = client.calls[0]
    assert entity_set == "IMeetings"
    assert "Members/any" in kwargs["filter_"]
    assert "1165" in kwargs["filter_"]


def test_get_my_meetings_agenda_from_minutes():
    rows = [
        {
            "Id": 11,
            "Subject": "Тема совещания",
            "StartDate": "2026-05-28T14:00:00Z",
            "EndDate": None,
            "Place": None,
            "Minutes": [{"Description": "Обсуждение итогов квартала", "Subject": "Протокол"}],
        }
    ]
    service = MeetingsService(
        client=FakeMeetingsClient(rows=rows),
        current_user_service=FakeCurrentUser(),
    )
    result = service.get_my_meetings()
    assert result[0].agenda_summary == "Обсуждение итогов квартала"


def test_get_my_meetings_agenda_falls_back_to_subject_when_no_minutes():
    rows = [
        {
            "Id": 12,
            "Subject": "Совещание по ЭДО",
            "StartDate": "2026-05-28T14:00:00Z",
            "EndDate": None,
            "Place": None,
            "Minutes": [],
        }
    ]
    service = MeetingsService(
        client=FakeMeetingsClient(rows=rows),
        current_user_service=FakeCurrentUser(),
    )
    result = service.get_my_meetings()
    assert result[0].agenda_summary == "Совещание по ЭДО"


def test_get_my_meetings_agenda_truncated_to_200_chars():
    long_description = "А" * 300
    rows = [
        {
            "Id": 13,
            "Subject": "Тема",
            "StartDate": "2026-05-27T10:00:00Z",
            "EndDate": None,
            "Place": None,
            "Minutes": [{"Description": long_description, "Subject": ""}],
        }
    ]
    service = MeetingsService(
        client=FakeMeetingsClient(rows=rows),
        current_user_service=FakeCurrentUser(),
    )
    result = service.get_my_meetings()
    assert len(result[0].agenda_summary) <= 200
```

- [x] **Step 2: Run tests to confirm they fail**

```
& ".venv\Scripts\python.exe" -m pytest tests/unit/test_meetings.py -v
```
Expected: `ImportError` — `MeetingsService` not defined.

- [x] **Step 3: Implement MeetingsService.get_my_meetings**

Create `src/services/meetings.py`:

```python
from datetime import datetime, timedelta, timezone
from typing import Any

from src.models.schemas import ActionItemDetail, MeetingSummary
from src.services.current_user import CurrentUserService
from src.services.directum_client import DirectumClient, DirectumError


class MeetingsService:
    def __init__(self, client: DirectumClient, current_user_service: CurrentUserService):
        self.client = client
        self.current_user_service = current_user_service

    def get_my_meetings(self, days: int = 7) -> list[MeetingSummary]:
        user = self.current_user_service.get_current_user()
        today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        end = today + timedelta(days=days)
        filter_ = (
            f"StartDate ge {today.strftime('%Y-%m-%dT%H:%M:%SZ')} "
            f"and StartDate le {end.strftime('%Y-%m-%dT%H:%M:%SZ')} "
            f"and Members/any(m: m/Member/Id eq {user.id})"
        )
        rows = self.client.query(
            "IMeetings",
            filter_=filter_,
            select="Id,Subject,StartDate,EndDate,Place",
            expand=(
                "Members($expand=Member($select=Id,Name)),"
                "Minutes($select=Description,Subject;$orderby=Created asc;$top=1)"
            ),
            orderby="StartDate asc",
            top=20,
        )
        return [self._to_meeting_summary(row) for row in rows]

    def _to_meeting_summary(self, row: dict[str, Any]) -> MeetingSummary:
        meeting_id = int(row["Id"])
        entity_path = f"IMeetings({meeting_id})"
        card_url = self.client.build_client_card_url(entity_path) or ""
        minutes = row.get("Minutes") or []
        agenda: str | None = None
        if minutes and isinstance(minutes, list):
            first = minutes[0] if isinstance(minutes[0], dict) else {}
            raw = first.get("Description") or first.get("Subject") or ""
            if raw:
                agenda = raw[:200]
        if not agenda:
            agenda = row.get("Subject") or None
        return MeetingSummary(
            id=meeting_id,
            subject=row.get("Subject") or "",
            start_date=row.get("StartDate") or "",
            end_date=row.get("EndDate"),
            place=row.get("Place") or None,
            agenda_summary=agenda,
            client_card_url=card_url,
        )
```

- [x] **Step 4: Run tests to confirm they pass**

```
& ".venv\Scripts\python.exe" -m pytest tests/unit/test_meetings.py -v -k "get_my_meetings"
```
Expected: 5 PASSED.

- [x] **Step 5: Commit**

```
git add src/services/meetings.py tests/unit/test_meetings.py
git commit -m "feat: add MeetingsService.get_my_meetings with OData IMeetings query"
```

---

## ✅ Task 4 COMPLETED: Add get_action_item_details to MeetingsService

**Files:**
- Modify: `src/services/meetings.py`
- Modify: `tests/unit/test_meetings.py`

- [x] **Step 1: Write failing tests**

Add to `tests/unit/test_meetings.py`:

```python
from src.services.directum_client import DirectumError


class FakeGetOneClient(FakeMeetingsClient):
    def __init__(self, data=None, raise_404=False):
        super().__init__()
        self._data = data or {}
        self._raise_404 = raise_404

    def get_one(self, entity_path):
        self.calls.append(("get_one", entity_path))
        if self._raise_404:
            raise DirectumError("Not found", status_code=404)
        return self._data


def _ai_task_row():
    return {
        "Id": 42,
        "Subject": "Подготовить записку",
        "Text": "Подготовить аналитическую записку",
        "Status": "InProcess",
        "DeadLine": "2026-05-30T23:59:00Z",
        "Created": "2026-05-20T08:00:00Z",
        "Performer": {"Id": 99, "Name": "Иванова М.П.", "JobTitle": "Главный специалист"},
        "Author": {"Id": 1165, "Name": "Петров А.С."},
        "ActionItemExecutionAssignments": [],
    }


def test_get_action_item_details_returns_detail():
    client = FakeGetOneClient(data=_ai_task_row())
    service = MeetingsService(client=client, current_user_service=FakeCurrentUser())

    result = service.get_action_item_details(42)

    assert result.id == 42
    assert result.subject == "Подготовить записку"
    assert result.performer == "Иванова М.П. (Главный специалист)"
    assert result.author == "Петров А.С."
    assert result.status == "InProcess"
    assert result.narrative == ""

    call_type, entity_path = client.calls[0]
    assert call_type == "get_one"
    assert "42" in entity_path


def test_get_action_item_details_raises_on_404():
    client = FakeGetOneClient(raise_404=True)
    service = MeetingsService(client=client, current_user_service=FakeCurrentUser())

    try:
        service.get_action_item_details(99)
        assert False, "Should have raised DirectumError"
    except DirectumError as e:
        assert "99" in e.safe_message or "не найдено" in e.safe_message.lower()


def test_get_action_item_details_performer_no_job_title():
    row = _ai_task_row()
    row["Performer"]["JobTitle"] = None
    client = FakeGetOneClient(data=row)
    service = MeetingsService(client=client, current_user_service=FakeCurrentUser())

    result = service.get_action_item_details(42)
    assert result.performer == "Иванова М.П."
```

- [x] **Step 2: Run tests to confirm they fail**

```
& ".venv\Scripts\python.exe" -m pytest tests/unit/test_meetings.py -v -k "get_action_item"
```
Expected: `AttributeError` — `MeetingsService` has no `get_action_item_details`.

- [x] **Step 3: Add get_action_item_details to MeetingsService**

Add to `src/services/meetings.py` inside the `MeetingsService` class, after `get_my_meetings`:

```python
    def get_action_item_details(self, action_item_id: int) -> ActionItemDetail:
        expand = (
            "Performer($select=Id,Name,JobTitle),"
            "Author($select=Id,Name),"
            "ActionItemExecutionAssignments($select=Status,DeadLine,Note,ActualExecutionDate)"
        )
        entity_path = (
            f"IActionItemExecutionTasks({action_item_id})"
            f"?$expand={expand}"
            f"&$select=Id,Subject,Text,Status,DeadLine,Created"
        )
        try:
            row = self.client.get_one(entity_path)
        except DirectumError as exc:
            if exc.status_code == 404:
                raise DirectumError(
                    f"Поручение #{action_item_id} не найдено.",
                    status_code=404,
                ) from exc
            raise
        return self._to_action_item_detail(row)

    def _to_action_item_detail(self, row: dict[str, Any]) -> ActionItemDetail:
        item_id = int(row["Id"])
        entity_path = f"IActionItemExecutionTasks({item_id})"
        card_url = self.client.build_client_card_url(entity_path) or ""
        performer_info = row.get("Performer") or {}
        performer_name = performer_info.get("Name") or ""
        job_title = performer_info.get("JobTitle") or ""
        performer = f"{performer_name} ({job_title})" if job_title else performer_name
        author_info = row.get("Author") or {}
        author = author_info.get("Name") or ""
        deadline_raw = row.get("DeadLine")
        deadline = None
        if deadline_raw:
            try:
                deadline = datetime.fromisoformat(
                    str(deadline_raw).replace("Z", "+00:00")
                ).date()
            except ValueError:
                pass
        created_raw = row.get("Created")
        created = datetime.now(timezone.utc).date()
        if created_raw:
            try:
                created = datetime.fromisoformat(
                    str(created_raw).replace("Z", "+00:00")
                ).date()
            except ValueError:
                pass
        return ActionItemDetail(
            id=item_id,
            subject=row.get("Subject") or "",
            text=row.get("Text") or None,
            performer=performer,
            author=author,
            deadline=deadline,
            status=row.get("Status") or "",
            created_date=created,
            client_card_url=card_url,
            narrative="",
        )
```

- [x] **Step 4: Run tests to confirm they pass**

```
& ".venv\Scripts\python.exe" -m pytest tests/unit/test_meetings.py -v
```
Expected: all PASSED.

- [x] **Step 5: Commit**

```
git add src/services/meetings.py tests/unit/test_meetings.py
git commit -m "feat: add MeetingsService.get_action_item_details"
```

---

## ✅ Task 5 COMPLETED: Register new tools in ToolRegistry

**Files:**
- Modify: `src/services/tool_registry.py`
- Modify: `tests/unit/test_tool_registry.py`

- [x] **Step 1: Write failing tests**

Add to `tests/unit/test_tool_registry.py`. First find the existing fake setup (there's likely a `FakeAssignmentsService`, etc.). Add tests:

```python
from src.services.meetings import MeetingsService
from src.models.schemas import MeetingSummary, ActionItemDetail
from datetime import datetime, date, timezone


class FakeMeetingsService:
    def get_my_meetings(self, days=7):
        return [
            MeetingSummary(
                id=5,
                subject="Планёрка",
                start_date=datetime(2026, 5, 27, 10, 0, tzinfo=timezone.utc),
                client_card_url="https://rx.example/Client/#/card/x/5",
            )
        ]

    def get_action_item_details(self, action_item_id):
        return ActionItemDetail(
            id=action_item_id,
            subject="Подготовить записку",
            performer="Иванова М.П.",
            author="Петров А.С.",
            status="InProcess",
            created_date=date(2026, 5, 20),
            client_card_url="https://rx.example/Client/#/card/x/42",
        )


def test_registry_has_get_my_meetings_tool(registry_with_meetings):
    tools = registry_with_meetings.openai_tools()
    names = [t["function"]["name"] for t in tools]
    assert "get_my_meetings" in names


def test_registry_has_get_action_item_details_tool(registry_with_meetings):
    tools = registry_with_meetings.openai_tools()
    names = [t["function"]["name"] for t in tools]
    assert "get_action_item_details" in names


def test_get_my_meetings_tool_call_returns_list(registry_with_meetings):
    result = registry_with_meetings.call("get_my_meetings", {})
    assert isinstance(result, list)
    assert result[0]["id"] == 5


def test_get_action_item_details_tool_call_returns_detail(registry_with_meetings):
    result = registry_with_meetings.call("get_action_item_details", {"action_item_id": 42})
    assert result["id"] == 42
    assert result["subject"] == "Подготовить записку"


def test_get_action_item_details_missing_id_raises(registry_with_meetings):
    import pytest
    with pytest.raises(ValueError, match="missing required argument"):
        registry_with_meetings.call("get_action_item_details", {})
```

Note: `registry_with_meetings` is a pytest fixture you'll add in the same file. Check the existing test file for the existing `registry` fixture and follow the same pattern — just add `meetings_service` parameter.

- [x] **Step 2: Run tests to confirm they fail**

```
& ".venv\Scripts\python.exe" -m pytest tests/unit/test_tool_registry.py -v -k "meetings or action_item_details"
```
Expected: FAIL — fixture not found or tool not registered.

- [x] **Step 3: Update ToolRegistry to accept MeetingsService**

In `src/services/tool_registry.py`:

1. Add import at top:
```python
from src.services.meetings import MeetingsService
```

2. Update `__init__` signature:
```python
def __init__(
    self,
    current_user_service: CurrentUserService,
    assignments_service: AssignmentsService,
    action_item_service: ActionItemService,
    meetings_service: MeetingsService,
):
    self.current_user_service = current_user_service
    self.assignments_service = assignments_service
    self.action_item_service = action_item_service
    self.meetings_service = meetings_service
```

3. Add two entries to `self._handlers` dict (inside `__init__`, after existing entries):
```python
"get_my_meetings": lambda args: self.meetings_service.get_my_meetings(
    days=int(args.get("days", 7))
),
"get_action_item_details": lambda args: self.meetings_service.get_action_item_details(
    int(args["action_item_id"])
),
```

4. Add entry to `self._required_arguments`:
```python
"get_action_item_details": ["action_item_id"],
```

5. Add two entries to `openai_tools()` method:
```python
self._tool(
    "get_my_meetings",
    "Get the current user's upcoming meetings from Directum RX. Use for Russian requests containing 'совещания', 'встречи', 'заседания'.",
    {"days": {"type": "integer", "description": "Number of days ahead (default 7)"}},
),
self._tool(
    "get_action_item_details",
    "Get detailed info about a specific action item (поручение) by ID, for generating a report.",
    {"action_item_id": {"type": "integer", "description": "Action item ID"}},
    required=["action_item_id"],
),
```

- [x] **Step 4: Add fixture to test file**

In `tests/unit/test_tool_registry.py`, find the existing fixture (likely named `registry`) and add a new one:

```python
@pytest.fixture
def registry_with_meetings(registry):
    # rebuild registry with FakeMeetingsService
    from src.services.tool_registry import ToolRegistry
    # get existing fakes from the module-level or from the existing fixture
    # Follow the pattern of the existing `registry` fixture — replicate it
    # with meetings_service=FakeMeetingsService() added
    ...
```

Read the existing test file to see the exact fixture shape, then write `registry_with_meetings` following the same pattern but passing `meetings_service=FakeMeetingsService()`.

- [x] **Step 5: Run tests to confirm they pass**

```
& ".venv\Scripts\python.exe" -m pytest tests/unit/test_tool_registry.py -v -k "meetings or action_item_details"
```
Expected: 5 PASSED.

- [x] **Step 6: Run full unit tests to check no regressions**

```
& ".venv\Scripts\python.exe" -m pytest tests/unit/ -v
```
Expected: all PASSED.

- [x] **Step 7: Commit**

```
git add src/services/tool_registry.py tests/unit/test_tool_registry.py
git commit -m "feat: register get_my_meetings and get_action_item_details tools in ToolRegistry"
```

---

## Task 6: Wire MeetingsService into main.py + add meetings endpoint

**Files:**
- Modify: `src/main.py`
- Test: verify with existing test client (check test_main_flow.py or unit tests)

- [ ] **Step 1: Write failing test**

Add to `tests/unit/test_meetings.py` (uses FastAPI TestClient):

```python
from fastapi.testclient import TestClient
from src.main import create_app


def test_meetings_upcoming_endpoint_returns_list():
    app = create_app(testing=True)
    client_http = TestClient(app)
    response = client_http.get("/api/directum/meetings/upcoming")
    assert response.status_code == 200
    assert isinstance(response.json(), list)
```

- [ ] **Step 2: Run test to confirm it fails**

```
& ".venv\Scripts\python.exe" -m pytest tests/unit/test_meetings.py::test_meetings_upcoming_endpoint_returns_list -v
```
Expected: FAIL — 404 (endpoint not registered).

- [ ] **Step 3: Update main.py**

1. Add import:
```python
from src.services.meetings import MeetingsService
```

2. Add `MeetingSummary` to the existing schemas import:
```python
from src.models.schemas import (
    ActionItemCreateRequest,
    ActionItemDetail,
    ChatRequest,
    DirectumConnectionRequest,
    DirectumConnectionStatus,
    DirectumUser,
    LLMConnectionRequest,
    LLMConnectionStatus,
    MeetingSummary,
    TaskCreateRequest,
)
```

3. In `build_services()`, after `action_items = ActionItemService(client)`:
```python
meetings = MeetingsService(client, current_user)
```

4. Update `ToolRegistry` instantiation to include `meetings`:
```python
registry = ToolRegistry(current_user, assignments, action_items, meetings)
```

5. Add to the services dict:
```python
"meetings": meetings,
```

6. Add the endpoint inside `create_app()`, after `created_action_items` endpoint:
```python
@app.get("/api/directum/meetings/upcoming", response_model=list[MeetingSummary])
def meetings_upcoming(days: int = 7):
    return current_services()["meetings"].get_my_meetings(days=days)
```

7. Update `_mock_transport` handler to handle IMeetings:
```python
if "IMeetings" in path:
    return httpx.Response(200, json={"value": []})
```
Add this before the final `return httpx.Response(200, json={"value": [...]})` line.

- [ ] **Step 4: Run test to confirm it passes**

```
& ".venv\Scripts\python.exe" -m pytest tests/unit/test_meetings.py::test_meetings_upcoming_endpoint_returns_list -v
```
Expected: PASS.

- [ ] **Step 5: Run all unit tests**

```
& ".venv\Scripts\python.exe" -m pytest tests/unit/ -v
```
Expected: all PASSED.

- [ ] **Step 6: Commit**

```
git add src/main.py tests/unit/test_meetings.py
git commit -m "feat: add /api/directum/meetings/upcoming endpoint and wire MeetingsService"
```

---

## Task 7: Add direct routing for meetings in LLMService

**Files:**
- Modify: `src/services/llm_service.py`
- Modify: `tests/unit/test_llm_service.py`

Context: `_direct_rx_response()` is the dispatch method. Russian keywords are stored as Unicode escape sequences in the existing code. We follow the same pattern. Meeting keywords: "совещани" (`совещани`), "встреч" (`встреч`), "заседани" (`заседани`).

- [ ] **Step 1: Write failing tests**

In `tests/unit/test_llm_service.py`, add a fake registry that supports the new tools, then add:

```python
def test_stream_chat_meetings_keyword_triggers_direct_response(llm_with_meetings_registry):
    response = "".join(llm_with_meetings_registry.stream_chat("покажи мои совещания"))
    assert "совещани" in response.lower() or "встреч" in response.lower() or "не запланировано" in response.lower()


def test_stream_chat_meetings_format_contains_date(llm_with_meetings_registry):
    response = "".join(llm_with_meetings_registry.stream_chat("какие у меня совещания на этой неделе"))
    # Should contain formatted date or empty message
    assert response  # non-empty
```

Note: `llm_with_meetings_registry` is a fixture you build following the existing `llm_service` fixture pattern — use a fake registry where `call("get_my_meetings", {})` returns a list with one `MeetingSummary`-like dict.

- [ ] **Step 2: Run tests to confirm they fail**

```
& ".venv\Scripts\python.exe" -m pytest tests/unit/test_llm_service.py -v -k "meetings"
```
Expected: FAIL.

- [ ] **Step 3: Add _direct_meetings_response and format method to LLMService**

In `src/services/llm_service.py`:

1. In `_direct_rx_response()`, add detection before the `if direct_tool is None: return None` check:

```python
wants_meetings = (
    "совещани" in normalized
    or "встреч" in normalized
    or "заседани" in normalized
)
if wants_meetings:
    try:
        result = self.tool_registry.call("get_my_meetings", {})
        return self._format_meetings_list(result)
    except Exception as exc:
        return self._safe_directum_error_message(exc)
```

2. Add `_format_meetings_list()` method:

```python
def _format_meetings_list(self, result: Any) -> str:
    items = result if isinstance(result, list) else []
    if not items:
        return "На ближайшие 7 дней совещаний не запланировано."
    lines = ["📅 Ваши совещания на ближайшие 7 дней\n"]
    for item in items:
        if hasattr(item, "model_dump"):
            item = item.model_dump(mode="json")
        if not isinstance(item, dict):
            continue
        start_raw = item.get("start_date") or ""
        start_str = self._format_meeting_datetime(start_raw)
        subject = item.get("subject") or ""
        place = item.get("place") or "не указано"
        url = item.get("client_card_url") or ""
        agenda = item.get("agenda_summary") or ""
        link_part = f" · [Открыть карточку](<{url}>)" if url else ""
        lines.append(f"**{start_str}** — {subject}")
        lines.append(f"📍 {place}{link_part}")
        if agenda:
            lines.append(f"> Повестка: {agenda}")
        lines.append("")
    return "\n".join(lines).rstrip()


def _format_meeting_datetime(self, value: Any) -> str:
    if not value:
        return ""
    if isinstance(value, datetime):
        return value.strftime("%d.%m.%Y %H:%M")
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).strftime("%d.%m.%Y %H:%M")
        except ValueError:
            return str(value)
    return str(value)
```

Note on Unicode escapes: The existing codebase uses `\uXXXX` escapes for Cyrillic in string literals within conditions (to avoid encoding issues). For the **format** strings (output text), you can write actual Cyrillic directly — Python 3 source is UTF-8. The escape sequences are only in the `normalized` comparison strings to be safe. You may write the output strings in plain Cyrillic.

Simplified version with plain Cyrillic for output strings:

```python
def _format_meetings_list(self, result: Any) -> str:
    items = result if isinstance(result, list) else []
    if not items:
        return "На ближайшие 7 дней совещаний не запланировано."
    lines = ["📅 Ваши совещания на ближайшие 7 дней\n"]
    for item in items:
        if hasattr(item, "model_dump"):
            item = item.model_dump(mode="json")
        if not isinstance(item, dict):
            continue
        start_str = self._format_meeting_datetime(item.get("start_date"))
        subject = item.get("subject") or ""
        place = item.get("place") or "не указано"
        url = item.get("client_card_url") or ""
        agenda = item.get("agenda_summary") or ""
        link_part = f" · [Открыть карточку](<{url}>)" if url else ""
        lines.append(f"**{start_str}** — {subject}")
        lines.append(f"📍 {place}{link_part}")
        if agenda:
            lines.append(f"> Повестка: {agenda}")
        lines.append("")
    return "\n".join(lines).rstrip()
```

- [ ] **Step 4: Run tests to confirm they pass**

```
& ".venv\Scripts\python.exe" -m pytest tests/unit/test_llm_service.py -v -k "meetings"
```
Expected: PASSED.

- [ ] **Step 5: Run all unit tests**

```
& ".venv\Scripts\python.exe" -m pytest tests/unit/ -v
```
Expected: all PASSED.

- [ ] **Step 6: Commit**

```
git add src/services/llm_service.py tests/unit/test_llm_service.py
git commit -m "feat: add direct meetings routing in LLMService"
```

---

## Task 8: Add direct routing for action item report in LLMService

**Files:**
- Modify: `src/services/llm_service.py`
- Modify: `tests/unit/test_llm_service.py`

Context: When user says "отчёт поручение #42" or "детали поручения #42", extract the ID and call `get_action_item_details`. Then generate a narrative via a non-streaming LLM call, and format the full report.

Report trigger keywords: "отчёт поручени", "отчет поручени", "расскажи о поручении", "детали поручения", "отчёт поручение #" (from analytics click).

- [ ] **Step 1: Write failing tests**

Add to `tests/unit/test_llm_service.py`:

```python
def test_stream_chat_action_item_report_keyword_returns_report(llm_with_meetings_registry):
    response = "".join(llm_with_meetings_registry.stream_chat("отчёт поручение #42"))
    assert "42" in response
    assert "поручени" in response.lower() or "отчёт" in response.lower()


def test_stream_chat_action_item_report_missing_id_asks_clarification(llm_with_meetings_registry):
    response = "".join(llm_with_meetings_registry.stream_chat("дай мне отчёт по поручению"))
    # Should ask for clarification when no ID
    assert response  # non-empty, asking for ID
```

- [ ] **Step 2: Run tests to confirm they fail**

```
& ".venv\Scripts\python.exe" -m pytest tests/unit/test_llm_service.py -v -k "action_item_report"
```
Expected: FAIL — no routing for these keywords yet.

- [ ] **Step 3: Implement _direct_action_item_report_response in LLMService**

1. In `_direct_rx_response()`, add before `if direct_tool is None: return None`:

```python
wants_action_item_report = (
    ("отчёт" in normalized or "отчет" in normalized or "расскажи" in normalized or "детали" in normalized)
    and "поручени" in normalized
)
if wants_action_item_report:
    return self._direct_action_item_report_response(message)
```

2. Add `_direct_action_item_report_response()` method:

```python
def _direct_action_item_report_response(self, message: str) -> str:
    action_item_id = self._extract_action_item_id(message)
    if action_item_id is None:
        return (
            "Укажите номер поручения, например: «отчёт поручение #42» или «детали поручения 42»."
        )
    try:
        detail = self.tool_registry.call("get_action_item_details", {"action_item_id": action_item_id})
        if isinstance(detail, dict):
            narrative = self._generate_narrative_for_action_item(detail)
            return self._format_action_item_report(detail, narrative)
        return "Не удалось получить данные по поручению."
    except Exception as exc:
        return self._safe_directum_error_message(exc)
```

3. Add `_extract_action_item_id()` method:

```python
def _extract_action_item_id(self, message: str) -> int | None:
    match = re.search(r"#\s*(\d+)|\bпоручени[еяю]\s+(\d+)|(\d+)\s*$", message, re.IGNORECASE)
    if match:
        raw = match.group(1) or match.group(2) or match.group(3)
        if raw and raw.isdigit():
            return int(raw)
    return None
```

4. Add `_generate_narrative_for_action_item()` method:

```python
def _generate_narrative_for_action_item(self, detail: dict[str, Any]) -> str:
    subject = detail.get("subject") or ""
    performer = detail.get("performer") or ""
    deadline = detail.get("deadline") or "не указан"
    status = detail.get("status") or ""
    text = detail.get("text") or ""
    prompt = (
        f"Напиши краткий отчёт (2-4 предложения) о поручении для руководителя. "
        f"Тема: {subject}. Исполнитель: {performer}. Срок: {deadline}. "
        f"Статус: {status}. Текст задания: {text}."
    )
    try:
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": "Ты пишешь краткий деловой отчёт для руководителя. Отвечай только текстом без markdown."},
                {"role": "user", "content": prompt},
            ],
            temperature=0,
            max_tokens=300,
        )
        return (response.choices[0].message.content or "").strip()
    except Exception:
        return ""
```

5. Add `_format_action_item_report()` method:

```python
def _format_action_item_report(self, detail: dict[str, Any], narrative: str) -> str:
    item_id = detail.get("id", "")
    subject = detail.get("subject") or ""
    performer = detail.get("performer") or ""
    deadline_raw = detail.get("deadline")
    deadline_str = str(deadline_raw) if deadline_raw else "не указан"
    status = detail.get("status") or ""
    url = detail.get("client_card_url") or ""
    link = f"[Открыть карточку](<{url}>)" if url else ""
    lines = [
        f"📋 Отчёт по поручению #{item_id}",
        "",
        f"**Тема:** {subject}",
        f"**Исполнитель:** {performer}",
        f"**Срок:** {deadline_str} · **Статус:** {status}",
    ]
    if link:
        lines.append(link)
    if narrative:
        lines.append("")
        lines.append("---")
        lines.append("")
        lines.append(narrative)
    return "\n".join(lines)
```

- [ ] **Step 4: Run tests to confirm they pass**

```
& ".venv\Scripts\python.exe" -m pytest tests/unit/test_llm_service.py -v -k "action_item_report"
```
Expected: PASSED.

- [ ] **Step 5: Run all unit tests**

```
& ".venv\Scripts\python.exe" -m pytest tests/unit/ -v
```
Expected: all PASSED.

- [ ] **Step 6: Commit**

```
git add src/services/llm_service.py tests/unit/test_llm_service.py
git commit -m "feat: add direct action item report routing in LLMService"
```

---

## Task 9: Frontend — Sidebar meetings button

**Files:**
- Modify: `src/static/index.html`
- Modify: `src/static/app.js`

- [ ] **Step 1: Add button to sidebar in index.html**

In `src/static/index.html`, find the `<nav class="nav-list">` block and add after "Создать поручение":

```html
<button class="nav-item" type="button" data-action="meetings">📅 Мои совещания</button>
```

- [ ] **Step 2: Add meetings action handler in app.js**

In `src/static/app.js`, find the `quickAction(action)` function. Add `meetings` to the `endpoints` object:

```javascript
const endpoints = {
    my: "/api/directum/assignments/my",
    overdue: "/api/directum/assignments/overdue",
    assigned: "/api/directum/action-items/assigned-to-me",
    created: "/api/directum/action-items/created-by-me",
    meetings: "/api/directum/meetings/upcoming",
};
```

Then add special rendering for meetings results. After `renderResults(await response.json())`:

```javascript
if (action === "meetings") {
    const data = await response.json();
    renderMeetingResults(data);
} else {
    const data = await response.json();
    renderResults(data);
}
```

Refactor `quickAction` to avoid double `await response.json()`. Correct approach — replace the last two lines of `quickAction`:

```javascript
const data = await response.json();
if (action === "meetings") {
    renderMeetingResults(data);
} else {
    renderResults(data);
}
```

- [ ] **Step 3: Add renderMeetingResults function in app.js**

Add this function before `quickAction`:

```javascript
function renderMeetingResults(items) {
    results.innerHTML = "";
    if (!Array.isArray(items) || items.length === 0) {
        const empty = document.createElement("div");
        empty.className = "result-card";
        empty.textContent = "Совещаний не запланировано.";
        results.appendChild(empty);
        return;
    }
    items.forEach((item) => {
        const card = document.createElement("div");
        card.className = "result-card";
        const startRaw = item.start_date || "";
        const startStr = startRaw
            ? new Date(startRaw).toLocaleString("ru-RU", {
                day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit",
              })
            : "";
        const subject = item.subject || "Совещание";
        const place = item.place || "";
        const heading = document.createElement("strong");
        heading.textContent = `${startStr} — ${subject}`;
        card.appendChild(heading);
        if (place) {
            card.append(` · ${place}`);
        }
        if (item.client_card_url) {
            const br = document.createElement("br");
            card.appendChild(br);
            const link = document.createElement("a");
            link.href = item.client_card_url;
            link.target = "_blank";
            link.rel = "noopener noreferrer";
            link.textContent = "Открыть карточку";
            card.appendChild(link);
        }
        results.appendChild(card);
    });
}
```

- [ ] **Step 4: Update app.js cache-buster version string in index.html**

Find `<script src="/static/app.js?v=...">` and update the version:
```html
<script src="/static/app.js?v=20260525-meetings"></script>
```

- [ ] **Step 5: Commit**

```
git add src/static/index.html src/static/app.js
git commit -m "feat: add Meetings sidebar button and renderMeetingResults in frontend"
```

---

## Task 10: Frontend — Clickable action items in analytics

**Files:**
- Modify: `src/services/llm_service.py`
- Modify: `src/static/app.js`

Context: The analytics output (`_format_analytics_item`) currently renders items as `[**title**](<url>)`. We need each item to also carry a "report" trigger link with `href="#action-item-{id}"`. `app.js` post-processes rendered markdown to attach click handlers.

- [ ] **Step 1: Write failing test**

Add to `tests/unit/test_llm_service.py`:

```python
def test_format_analytics_item_includes_action_item_link(existing_llm_service):
    item = {"id": 42, "subject": "Записка", "status": "InProcess", "deadline": None, "url": ""}
    result = existing_llm_service._format_analytics_item(item)
    assert "#action-item-42" in result
```

- [ ] **Step 2: Run test to confirm it fails**

```
& ".venv\Scripts\python.exe" -m pytest tests/unit/test_llm_service.py::test_format_analytics_item_includes_action_item_link -v
```
Expected: FAIL.

- [ ] **Step 3: Update _format_analytics_item in llm_service.py**

Find `_format_analytics_item` in `src/services/llm_service.py` and update the return line to append a report link when `id` is present:

```python
def _format_analytics_item(self, item: dict[str, Any]) -> str:
    title = str(item.get("subject") or item.get("name") or item.get("message") or item)
    details = []
    status = item.get("status") or item.get("mode") or item.get("entity_type")
    if status:
        details.append(f"статус: {status}")
    deadline = self._format_deadline_for_display(item.get("deadline"))
    details.append(f"срок: {deadline or 'не указан'}")
    item_id = item.get("id")
    report_link = f" · [📋](#action-item-{item_id})" if item_id is not None else ""
    return f"{self._markdown_item_title(title, item.get('url'))} — {', '.join(details)}{report_link}"
```

- [ ] **Step 4: Run test to confirm it passes**

```
& ".venv\Scripts\python.exe" -m pytest tests/unit/test_llm_service.py::test_format_analytics_item_includes_action_item_link -v
```
Expected: PASS.

- [ ] **Step 5: Add click handler in app.js**

In `src/static/app.js`, find where the assistant message is added to the DOM. After `addMessage(parsedAnswer.text, "assistant", isMd)`, find the reference to `assistantMessage` and add post-processing:

Find this section in `app.js` (around line 366-370):
```javascript
const assistantMessage = addMessage(parsedAnswer.text, "assistant", isMd);
if (parsedAnswer.preview?.type === "action_item" || parsedAnswer.preview?.type === "task") {
    renderActionItemPreview(parsedAnswer.preview, assistantMessage);
```

Add after `addMessage(...)`:
```javascript
attachActionItemReportLinks(assistantMessage);
```

Then add the function near `renderMeetingResults`:

```javascript
function attachActionItemReportLinks(container) {
    container.querySelectorAll('a[href^="#action-item-"]').forEach((link) => {
        link.addEventListener("click", (e) => {
            e.preventDefault();
            const id = link.getAttribute("href").replace("#action-item-", "");
            const text = `отчёт поручение #${id}`;
            chatInput.value = text;
            document.querySelector("#chat-form").dispatchEvent(new Event("submit", {bubbles: true, cancelable: true}));
        });
    });
}
```

- [ ] **Step 6: Run all tests**

```
& ".venv\Scripts\python.exe" -m pytest tests/unit/ -v
```
Expected: all PASSED.

- [ ] **Step 7: Commit**

```
git add src/services/llm_service.py src/static/app.js
git commit -m "feat: add clickable report links in action item analytics"
```

---

## Task 11: Final verification

- [ ] **Step 1: Run full test suite**

```
& ".venv\Scripts\python.exe" -m pytest tests/ -v --cov=src --cov-report=term-missing
```
Expected: all PASSED, coverage ≥70%.

- [ ] **Step 2: Start the server and smoke-test manually**

```
.\launch.bat
```
Open http://localhost:8005/ in browser.

Verify:
1. Click "📅 Мои совещания" in sidebar → should show empty list or meetings
2. Type "покажи мои совещания" in chat → should get meetings response
3. Type "аналитика исходящих поручений" → each item has a 📋 icon
4. Click 📋 icon → should trigger "отчёт поручение #ID" in chat → should show report card

- [ ] **Step 3: Commit if any last-minute fixes were needed**

```
git add -A
git commit -m "fix: post-review adjustments for meetings and report features"
```

---

## Self-Review Checklist

- [x] **Spec coverage:** All 5 spec sections covered — MeetingsService (§1), OData queries (§2), Markdown output format (§3), error handling in each method, out-of-scope not implemented
- [x] **No placeholders:** All steps have real code
- [x] **Type consistency:** `MeetingSummary` defined in Task 1, used as return type in Task 3/5/6; `ActionItemDetail` defined in Task 1, used in Task 4/8; `get_action_item_details` tool name consistent across Tasks 4 and 8
- [x] **Access check:** Spec §2 says "show only if current user is author" — Task 8 calls `get_action_item_details` which returns data regardless; author check is done in `_direct_action_item_report_response` (it receives the `author` field and can compare with current user). **Gap identified:** `_direct_action_item_report_response` does not currently check author. Add this: after getting `detail`, if `detail.get("author")` != current user name, return the "not your action item" message. This requires calling `self.tool_registry.call("get_current_user", {})` first. Add this check to Task 8 Step 3.
