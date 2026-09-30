from datetime import date, datetime, timedelta, timezone

from src.services.resolution_history import ResolutionHistoryService

TZ = timezone(timedelta(hours=4))
NOW = datetime(2026, 9, 30, 12, 0, tzinfo=TZ)

ROWS = [
    {"Id": 3, "Subject": ">> Поручение: Подготовить ответ", "ActionItem": "Подготовить   ответ\nв министерство", "Created": "2026-09-01T10:00:00+04:00",
     "Deadline": "2026-09-11T00:00:00+04:00", "IsUnderControl": True, "IsCompoundActionItem": False, "HasIndefiniteDeadline": False, "Status": "Completed",
     "Assignee": {"Name": "Иванов"}, "Supervisor": {"Name": "Орлова"}, "SourceDocumentGD": {"Id": 9, "Name": "Вх. письмо Минздрава"},
     "CoAssignees": [{"Assignee": {"Name": "Петров"}}, {"Assignee": {"Name": "Петров"}}], "ActionItemParts": []},
    {"Id": 2, "Subject": "Поручение: Составное", "ActionItem": None, "Created": "2026-08-01T10:00:00+04:00", "Deadline": None,
     "IsUnderControl": False, "IsCompoundActionItem": True, "HasIndefiniteDeadline": True, "Status": "InProcess",
     "Assignee": None, "Supervisor": None, "SourceDocumentGD": None, "CoAssignees": [],
     "ActionItemParts": [{"Number": 2, "Assignee": {"Name": "Сидоров"}}, {"Number": 1, "Assignee": {"Name": "Иванов"}}]},
]


class Client:
    def __init__(self):
        self.calls = []

    def query(self, entity_set, **kw):
        self.calls.append(kw)
        return ROWS

    def count(self, entity_set, filter_=None):
        return 5

    def build_client_card_url(self, path):
        return "https://rx/" + path


def test_history_filter_and_items():
    client = Client()
    h = ResolutionHistoryService(client, tz=TZ, now=lambda: NOW).history(53, date_to=date(2026, 9, 30), limit=5000)
    f = client.calls[0]["filter_"]
    assert f == ("AssignedBy/Id eq 53 and ParentAssignment eq null and Status ne 'Draft' and Created ge 2024-09-30T00:00:00+04:00 "
                 "and Created lt 2026-10-01T00:00:00+04:00")
    assert client.calls[0]["top"] == 2000 and client.calls[0]["orderby"] == "Created desc"
    assert h["total"] == 5 and h["returned"] == 2 and h["truncated"] and h["under_control_share"] == 0.5
    first, second = h["items"]
    assert first["subject"] == "Подготовить ответ" and first["text"] == "Подготовить ответ в министерство"
    assert first["co_assignees"] == ["Петров"] and first["term_days"] == 10 and first["supervisor"] == "Орлова"
    assert first["source_document"] == {"id": 9, "name": "Вх. письмо Минздрава"} and first["url"] == "https://rx/IActionItemExecutionTasks(3)"
    assert second["parts_assignees"] == ["Иванов", "Сидоров"] and second["term_days"] is None and second["assignee"] is None
    assert h["top_assignees"] == [{"name": "Иванов", "count": 2}]


def test_history_empty_and_default_period():
    client = Client()
    client.query = lambda es, **kw: client.calls.append(kw) or []
    h = ResolutionHistoryService(client, tz=TZ, now=lambda: NOW).history(7, date_from=date(2026, 1, 1))
    assert "Created ge 2026-01-01T00:00:00+04:00" in client.calls[0]["filter_"]
    assert h["items"] == [] and h["under_control_share"] is None and h["top_assignees"] == []
