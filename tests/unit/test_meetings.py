from datetime import date, datetime, timezone
from src.models.schemas import ActionItemDetail, DirectumUser, MeetingSummary
from src.services.directum_client import DirectumError
from src.services.meetings import MeetingsService


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


# ---------------------------------------------------------------------------
# Task 3: MeetingsService.get_my_meetings
# ---------------------------------------------------------------------------


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
            "DateTime": "2026-05-27T10:00:00Z",
            "EndDate": "2026-05-27T11:00:00Z",
            "Location": "Зал 1",
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
    assert "StartDate" not in kwargs["filter_"]
    assert kwargs["select"] == "Id,Name,DateTime,Location,Note,Duration,Status"
    assert kwargs["orderby"] == "DateTime asc"
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


# ---------------------------------------------------------------------------
# Task 4: MeetingsService.get_action_item_details
# ---------------------------------------------------------------------------


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


def test_get_action_item_details_raises_when_not_author():
    row = _ai_task_row()
    row["Author"]["Id"] = 999  # different from current user id=1165
    client = FakeGetOneClient(data=row)
    service = MeetingsService(client=client, current_user_service=FakeCurrentUser())

    try:
        service.get_action_item_details(42)
        assert False, "Should have raised DirectumError"
    except DirectumError as e:
        assert "автором" in e.safe_message.lower()


# ---------------------------------------------------------------------------
# Task 6: Endpoint /api/directum/meetings/upcoming
# ---------------------------------------------------------------------------


def test_meetings_upcoming_endpoint_returns_list():
    from fastapi.testclient import TestClient
    from src.main import create_app

    app = create_app(testing=True)
    client_http = TestClient(app)
    response = client_http.get("/api/directum/meetings/upcoming")
    assert response.status_code == 200
    assert isinstance(response.json(), list)
