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
