import httpx

from src.services.directum_client import DirectumClient, DirectumError


def test_build_url_strips_duplicate_slashes():
    client = DirectumClient(
        base_url="https://rx.example/Integration/odata/",
        auth_token="Basic token",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"value": []})),
    )

    assert client.build_url("IAssignments") == "https://rx.example/Integration/odata/IAssignments"


def test_query_sends_odata_params_and_returns_value():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["Authorization"]
        return httpx.Response(200, json={"value": [{"Id": 1, "Subject": "Task"}]})

    client = DirectumClient(
        base_url="https://rx.example/Integration/odata",
        auth_token="Basic token",
        transport=httpx.MockTransport(handler),
    )

    result = client.query(
        "IAssignments",
        filter_="Status eq 'InProcess'",
        select="Id,Subject",
        top=5,
    )

    assert result == [{"Id": 1, "Subject": "Task"}]
    assert "$filter=Status+eq+%27InProcess%27" in seen["url"]
    assert "$select=Id%2CSubject" in seen["url"]
    assert "$top=5" in seen["url"]
    assert seen["auth"] == "Basic token"


def test_query_raises_normalized_error_on_unauthorized():
    client = DirectumClient(
        base_url="https://rx.example/Integration/odata",
        auth_token="Basic bad",
        transport=httpx.MockTransport(lambda request: httpx.Response(401, text="Unauthorized")),
    )

    try:
        client.query("IAssignments")
    except DirectumError as exc:
        assert exc.status_code == 401
        assert "Unauthorized" in exc.safe_message
    else:
        raise AssertionError("DirectumError was not raised")
