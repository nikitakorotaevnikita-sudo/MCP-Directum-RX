import pytest

from src.models.schemas import DirectumUser
from src.services.admin_access import ADMINISTRATORS_ROLE_SID, AdminAccessService
from src.services.directum_client import DirectumError


class FakeClient:
    """По умолчанию метода IsCurrentUserAdmin «нет» — проверяется фолбэк на IRoles."""

    def __init__(self, rows=None, error=None, method_result=None, method_error=DirectumError("no method", 404)):
        self.rows = rows or []
        self.error = error
        self.method_result = method_result
        self.method_error = method_error if method_result is None else None
        self.calls = []
        self.functions = []

    def call_function(self, path, params=None):
        self.functions.append(path)
        if self.method_error:
            raise self.method_error
        return self.method_result

    def query(self, entity_set, **kwargs):
        self.calls.append((entity_set, kwargs))
        if self.error:
            raise self.error
        return self.rows


class FakeCurrentUser:
    def get_current_user(self):
        return DirectumUser(id=12, name="Administrator", login="Administrator")


def test_is_admin_true_when_role_found():
    client = FakeClient(rows=[{"Id": 2}])

    assert AdminAccessService(client, FakeCurrentUser()).is_admin() is True
    entity_set, kwargs = client.calls[0]
    assert entity_set == "IRoles"
    assert kwargs["filter_"] == f"Sid eq {ADMINISTRATORS_ROLE_SID} and RecipientLinks/any(l: l/Member/Id eq 12)"
    assert kwargs["select"] == "Id"
    assert kwargs["top"] == 1


def test_is_admin_false_when_empty():
    assert AdminAccessService(FakeClient(rows=[]), FakeCurrentUser()).is_admin() is False


def test_is_admin_propagates_errors():
    service = AdminAccessService(FakeClient(error=DirectumError("forbidden", 403)), FakeCurrentUser())

    with pytest.raises(DirectumError):
        service.is_admin()


@pytest.mark.parametrize("answer", [True, False])
def test_is_admin_uses_platform_method_first(answer):
    client = FakeClient(method_result=answer)

    assert AdminAccessService(client, FakeCurrentUser()).is_admin() is answer
    assert client.functions == ["Company/IsCurrentUserAdmin"]
    assert client.calls == []


def test_is_admin_falls_back_to_roles_when_method_fails():
    client = FakeClient(rows=[{"Id": 2}])

    assert AdminAccessService(client, FakeCurrentUser()).is_admin() is True
    assert client.functions == ["Company/IsCurrentUserAdmin"]
    assert client.calls[0][0] == "IRoles"


def test_auth_error_from_method_is_not_masked_by_fallback():
    client = FakeClient(method_error=DirectumError("unauthorized", 401))

    with pytest.raises(DirectumError):
        AdminAccessService(client, FakeCurrentUser()).is_admin()
    assert client.calls == []
