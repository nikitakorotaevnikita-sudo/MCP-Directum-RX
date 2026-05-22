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
