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
