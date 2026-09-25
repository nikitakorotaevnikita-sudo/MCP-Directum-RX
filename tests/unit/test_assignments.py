from src.models.schemas import DirectumUser
from src.services.assignments import AssignmentsService


class FakeClient:
    def __init__(self):
        self.calls = []

    def query(self, entity_set, **kwargs):
        self.calls.append((entity_set, kwargs))
        return [{"Id": 10, "Subject": "Prepare answer", "Status": "InProcess"}]

    def build_client_card_url(self, entity_path):
        directum_id = entity_path.split("(", 1)[1].rstrip(")")
        return f"https://rx.example/Client/#/card/83f2a537-0cf0-4429-ae76-e9a386ca53aa/{directum_id}"


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
    assert kwargs["select"] == "Id,Subject,Deadline,Status"
    assert result[0].url == "https://rx.example/Client/#/card/83f2a537-0cf0-4429-ae76-e9a386ca53aa/10"


def test_get_created_action_items_filters_by_author():
    client = FakeClient()
    service = AssignmentsService(client=client, current_user_service=FakeCurrentUser())

    service.get_action_items_created_by_me()

    entity_set, kwargs = client.calls[0]
    assert entity_set == "IActionItemExecutionTasks"
    assert "Author/Id eq 1165" in kwargs["filter_"]
    assert kwargs["select"] == "Id,Subject,Deadline,Status"
    # Исполнителя ("Ответственный") тянем через навигацию Assignee.
    assert kwargs.get("expand") == "Assignee($select=Name)"


class PerformerClient(FakeClient):
    def query(self, entity_set, **kwargs):
        self.calls.append((entity_set, kwargs))
        return [
            {
                "Id": 48,
                "Subject": "Подготовить ответ",
                "Status": "InProcess",
                "Assignee": {"Id": 86, "Name": "Иванов Иван Иванович"},
            }
        ]


def test_created_action_items_expose_performer_name():
    client = PerformerClient()
    service = AssignmentsService(client=client, current_user_service=FakeCurrentUser())

    result = service.get_action_items_created_by_me()

    assert result[0].performer == "Иванов Иван Иванович"


def test_created_action_items_performer_none_when_assignee_absent():
    client = FakeClient()  # не возвращает Assignee
    service = AssignmentsService(client=client, current_user_service=FakeCurrentUser())

    result = service.get_action_items_created_by_me()

    assert result[0].performer is None


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
