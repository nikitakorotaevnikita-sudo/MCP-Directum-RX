import pytest

from src.models.schemas import DirectumUser
from src.services.current_user import CurrentUserService
from src.services.directum_client import DirectumError


class FakeClient:
    def __init__(self):
        self.calls = 0
        self.filters = []

    def query(self, entity_set, **kwargs):
        self.calls += 1
        assert entity_set == "IUsers"
        self.filters.append(kwargs["filter_"])
        return [{"Id": 1165, "Name": "Test User", "Login": {"LoginName": "nt_work\\\\user"}}]


def test_current_user_is_resolved_and_cached():
    client = FakeClient()
    service = CurrentUserService(client=client, auth_token="Basic bnRfd29ya1xcdXNlcjpwYXNz")

    first = service.get_current_user()
    second = service.get_current_user()

    assert first == DirectumUser(id=1165, name="Test User", login="nt_work\\\\user")
    assert second.id == 1165
    assert client.calls == 1
    assert client.filters == ["Login/LoginName eq 'nt_work\\\\user'"]


def test_current_user_escapes_login_in_odata_filter():
    client = FakeClient()
    service = CurrentUserService(client=client, auth_token="Basic bydoYXJhOnBhc3M=")

    user = service.get_current_user()

    assert user.login == "o'hara"
    assert client.filters == ["Login/LoginName eq 'o''hara'"]


@pytest.mark.parametrize(
    ("auth_token", "secret_fragments"),
    [
        ("Basic !!!", ["!!!"]),
        ("Basic bm9fY29sb24=", ["no_colon"]),
        ("Basic OnBhc3M=", [":pass", "pass"]),
    ],
)
def test_current_user_rejects_malformed_basic_token_safely(auth_token, secret_fragments):
    client = FakeClient()
    service = CurrentUserService(client=client, auth_token=auth_token)

    with pytest.raises(DirectumError) as exc_info:
        service.get_current_user()

    message = str(exc_info.value)
    assert "DIRECTUM_AUTH_TOKEN" in message
    for fragment in secret_fragments:
        assert fragment not in message
    assert client.calls == 0
