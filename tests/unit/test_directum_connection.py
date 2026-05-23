import base64

from src.models.schemas import DirectumConnectionRequest
from src.services.directum_connection import build_basic_auth_token


def test_build_basic_auth_token_encodes_login_and_password():
    token = build_basic_auth_token("nt_work\\user", "pass")

    expected = base64.b64encode("nt_work\\user:pass".encode("utf-8")).decode("ascii")
    assert token == f"Basic {expected}"


def test_connection_request_does_not_reveal_password_in_repr_or_dump():
    request = DirectumConnectionRequest(
        base_url=" https://rx.example/Integration/odata/ ",
        username=" nt_work\\user ",
        password="super-secret",
    )

    assert request.base_url == "https://rx.example/Integration/odata"
    assert request.username == "nt_work\\user"
    assert "super-secret" not in repr(request)
    assert "super-secret" not in str(request.model_dump())
