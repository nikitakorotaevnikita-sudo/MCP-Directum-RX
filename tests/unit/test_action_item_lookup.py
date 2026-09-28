import pytest

from src.models.schemas import DirectumUser
from src.services.directum_client import DirectumError
from src.services.meetings import MeetingsService

NOT_EXISTS = "Не удалось прочитать запись с ИД 1155. Запись не существует или у вас нет прав доступа."
SCHEMA_ERROR = "Directum OData request failed with status 400: Could not find a property named 'PerformersGD'"


def task_row(task_id=1072, author_id=63, assignee_id=48):
    return {
        "Id": task_id,
        "Subject": "Поручение: подготовить ответ",
        "Status": "InProcess",
        "Deadline": "2023-08-22T00:00:00+04:00",
        "Created": "2023-08-01T10:00:00+04:00",
        "ActionItem": "Подготовить ответ",
        "Author": {"Id": author_id, "Name": "Концева Надежда Ивановна"},
        "Assignee": {"Id": assignee_id, "Name": "Иванов Иван Иванович"},
    }


class FakeClient:
    """get_one: задачи по id из tasks; остальные id — «запись не существует» (400, как отвечает стенд)."""

    def __init__(self, tasks=None, assignment_tasks=None, schema_error_once=False):
        self.tasks = tasks or {}
        self.assignment_tasks = assignment_tasks or {}
        self.schema_error_once = schema_error_once
        self.paths = []
        self.queries = []

    def get_one(self, entity_path):
        self.paths.append(entity_path)
        if self.schema_error_once:
            self.schema_error_once = False
            raise DirectumError(SCHEMA_ERROR, 400)
        task_id = int(entity_path.split("(", 1)[1].split(")", 1)[0])
        if task_id not in self.tasks:
            raise DirectumError(f"Directum OData request failed with status 400: {NOT_EXISTS}", 400)
        return self.tasks[task_id]

    def query(self, entity_set, **kwargs):
        self.queries.append((entity_set, kwargs))
        assignment_id = int(kwargs["filter_"].split("eq ")[1])
        task_id = self.assignment_tasks.get(assignment_id)
        return [{"Id": assignment_id, "Task": {"Id": task_id}}] if task_id else []

    def build_client_card_url(self, entity_path):
        return f"https://rx.example/card/{entity_path}"


class Me:
    def __init__(self, user_id):
        self.user_id = user_id

    def get_current_user(self):
        return DirectumUser(id=self.user_id, name="Я", login="me")


def service(client, me=63):
    return MeetingsService(client=client, current_user_service=Me(me))


def test_task_id_works_as_before():
    detail = service(FakeClient(tasks={1072: task_row()})).get_action_item_details(1072)

    assert detail.id == 1072
    assert detail.performer == "Иванов Иван Иванович"


def test_assignment_id_resolves_to_its_task():
    client = FakeClient(tasks={1072: task_row()}, assignment_tasks={1155: 1072})

    detail = service(client).get_action_item_details(1155)

    assert detail.id == 1072
    entity_set, kwargs = client.queries[0]
    assert entity_set == "IActionItemExecutionAssignments"
    assert kwargs["filter_"] == "Id eq 1155"
    assert kwargs["expand"] == "Task($select=Id)"
    assert "Performer(" not in " ".join(client.paths)


def test_unknown_id_is_clean_not_found():
    with pytest.raises(DirectumError) as exc:
        service(FakeClient()).get_action_item_details(777)

    assert exc.value.status_code == 404
    assert "не найдено" in exc.value.safe_message
    assert "Performer" not in exc.value.safe_message


def test_schema_mismatch_still_uses_legacy_query():
    client = FakeClient(tasks={1072: task_row()}, schema_error_once=True)

    detail = service(client).get_action_item_details(1072)

    assert detail.id == 1072
    assert len(client.paths) == 2
    assert "Performer(" in client.paths[1]


def test_assignee_may_read_own_action_item():
    detail = service(FakeClient(tasks={1072: task_row()}), me=48).get_action_item_details(1072)

    assert detail.id == 1072


def test_stranger_is_rejected_by_default():
    with pytest.raises(DirectumError) as exc:
        service(FakeClient(tasks={1072: task_row()}), me=5).get_action_item_details(1072)

    assert exc.value.status_code == 403
    assert "автором" in exc.value.safe_message


def test_access_check_can_be_disabled():
    detail = service(FakeClient(tasks={1072: task_row()}), me=5).get_action_item_details(1072, require_author=False)

    assert detail.id == 1072
