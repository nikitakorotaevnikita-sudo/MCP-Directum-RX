import pytest

from src.models.schemas import DirectumUser
from src.services.admin_access import ADMINISTRATORS_ROLE_SID, AdminAccessService
from src.services.directum_client import DirectumError


class FakeClient:
    def __init__(self, rows=None, error=None):
        self.rows = rows or []
        self.error = error
        self.calls = []

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
