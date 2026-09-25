import httpx
import pytest

from src.services.directum_client import DirectumClient, DirectumError


def test_build_url_strips_duplicate_slashes():
    client = DirectumClient(
        base_url="https://rx.example/Integration/odata/",
        auth_token="Basic token",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"value": []})),
    )

    assert client.build_url("IAssignments") == "https://rx.example/Integration/odata/IAssignments"


def test_build_client_card_url_uses_client_route_for_task_entities():
    client = DirectumClient(
        base_url="https://rx.example/Integration/odata/",
        auth_token="Basic token",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"value": []})),
    )

    assert (
        client.build_client_card_url("ISimpleTasks(1108)")
        == "https://rx.example/Client/#/card/83f2a537-0cf0-4429-ae76-e9a386ca53aa/1108"
    )
    assert (
        client.build_client_card_url("IActionItemExecutionTasks(987)")
        == "https://rx.example/Client/#/card/83f2a537-0cf0-4429-ae76-e9a386ca53aa/987"
    )


def test_count_hits_count_endpoint_with_filter_and_returns_int():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        # /$count возвращает текст с возможным BOM, не JSON.
        return httpx.Response(200, text="﻿42")

    client = DirectumClient(
        base_url="https://rx.example/Integration/odata",
        auth_token="Basic token",
        transport=httpx.MockTransport(handler),
    )

    result = client.count("IAssignments", filter_="Status eq 'InProcess'")

    assert result == 42
    assert seen["url"].rstrip("/").endswith("/IAssignments/$count") or "/IAssignments/$count?" in seen["url"]
    assert "$filter=Status+eq+%27InProcess%27" in seen["url"]


def test_count_without_filter():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="215")

    client = DirectumClient(
        base_url="https://rx.example/Integration/odata",
        auth_token="Basic token",
        transport=httpx.MockTransport(handler),
    )

    assert client.count("IAssignments") == 215


def test_count_returns_zero_on_204_no_content():
    # Directum отдаёт 204 (пустое тело) когда под фильтр не попало ни одной записи,
    # например `Modified gt Deadline` без совпадений → это 0, а не ошибка.
    client = DirectumClient(
        base_url="https://rx.example/Integration/odata",
        auth_token="Basic token",
        transport=httpx.MockTransport(lambda request: httpx.Response(204)),
    )

    assert client.count("IAssignments", filter_="Modified gt Deadline") == 0


def test_count_returns_zero_on_empty_body():
    client = DirectumClient(
        base_url="https://rx.example/Integration/odata",
        auth_token="Basic token",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text="﻿")),
    )

    assert client.count("IAssignments") == 0


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


def test_query_treats_no_content_as_empty_collection():
    client = DirectumClient(
        base_url="https://rx.example/Integration/odata",
        auth_token="Basic token",
        transport=httpx.MockTransport(lambda request: httpx.Response(204)),
    )

    assert client.query("IActionItemExecutionAssignments") == []


def test_context_manager_allows_requests_and_closes_client():
    client_ref = {}

    with DirectumClient(
        base_url="https://rx.example/Integration/odata",
        auth_token="Basic token",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"value": [{"Id": 2}]})),
    ) as client:
        client_ref["client"] = client

        assert client.query("IAssignments") == [{"Id": 2}]

    assert client_ref["client"].client.is_closed


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
        assert exc.safe_message == "Directum OData request failed with status 401"
    else:
        raise AssertionError("DirectumError was not raised")


def test_query_error_message_does_not_include_response_body_secrets():
    leaked_secret = "Basic leaked-secret"
    client = DirectumClient(
        base_url="https://rx.example/Integration/odata",
        auth_token="Basic bad",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(401, text=f"Unauthorized token={leaked_secret}")
        ),
    )

    try:
        client.query("IAssignments")
    except DirectumError as exc:
        assert exc.status_code == 401
        assert leaked_secret not in exc.safe_message
        assert leaked_secret not in str(exc)
    else:
        raise AssertionError("DirectumError was not raised")


def test_post_400_includes_sanitized_odata_error_detail():
    leaked_secret = "Basic leaked-secret"
    client = DirectumClient(
        base_url="https://rx.example/Integration/odata",
        auth_token="Basic token",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                400,
                json={"error": {"message": {"value": f"Property PerformersGD is invalid. {leaked_secret}"}}},
            )
        ),
    )

    try:
        client.post("IActionItemExecutionTasks", {"Subject": "Task"})
    except DirectumError as exc:
        assert exc.status_code == 400
        assert "Property PerformersGD is invalid" in exc.safe_message
        assert leaked_secret not in exc.safe_message
        assert "Basic [redacted]" in exc.safe_message
    else:
        raise AssertionError("DirectumError was not raised")


def test_post_returns_empty_dict_on_no_content_response():
    client = DirectumClient(
        base_url="https://rx.example/Integration/odata",
        auth_token="Basic token",
        transport=httpx.MockTransport(lambda request: httpx.Response(204)),
    )

    assert client.post("Docflow/StartTask", {"taskId": 987}) == {}


def test_query_raises_error_on_non_object_collection_response():
    client = DirectumClient(
        base_url="https://rx.example/Integration/odata",
        auth_token="Basic token",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=[])),
    )

    try:
        client.query("IAssignments")
    except DirectumError as exc:
        assert exc.status_code == 200
        assert exc.safe_message == "Directum returned an unexpected collection response"
    else:
        raise AssertionError("DirectumError was not raised")


def test_query_raises_error_on_non_list_collection_value():
    client = DirectumClient(
        base_url="https://rx.example/Integration/odata",
        auth_token="Basic token",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"value": None})),
    )

    try:
        client.query("IAssignments")
    except DirectumError as exc:
        assert exc.status_code == 200
        assert exc.safe_message == "Directum returned an unexpected collection response"
    else:
        raise AssertionError("DirectumError was not raised")


def test_build_client_card_url_for_meeting():
    client = DirectumClient("https://rx.example/Integration/odata", "Basic dXNlcjpwYXNz")
    url = client.build_client_card_url("IMeetings(5)")
    assert url is not None
    assert "/5" in url
    assert "dbc0dd63-4d23-4f41-92ae-cab59bb70c8c" in url
    assert "rx.example" in url


def test_error_detail_supports_plain_string_error():
    client = DirectumClient(
        base_url="https://rx.example/Integration/odata",
        auth_token="Basic token",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                400,
                json={"error": "Превышено максимальное количество сущностей в запросе. Используйте фильтрацию."},
            )
        ),
    )

    with pytest.raises(DirectumError) as info:
        client.query("IAssignments", top=2)

    assert "Используйте фильтрацию" in info.value.safe_message
    assert info.value.status_code == 400


def test_get_metadata_xml_returns_raw_text():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["accept"] = request.headers.get("accept")
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, text="<edmx:Edmx/>")

    client = DirectumClient(
        base_url="https://rx.example/Integration/odata",
        auth_token="Basic token",
        transport=httpx.MockTransport(handler),
    )

    assert client.get_metadata_xml() == "<edmx:Edmx/>"
    assert seen["url"].endswith("/$metadata")
    assert "xml" in seen["accept"]
    assert seen["auth"] == "Basic token"


def test_get_metadata_xml_raises_on_error_status():
    client = DirectumClient(
        base_url="https://rx.example/Integration/odata",
        auth_token="Basic token",
        transport=httpx.MockTransport(lambda request: httpx.Response(401)),
    )

    with pytest.raises(DirectumError) as info:
        client.get_metadata_xml()

    assert info.value.status_code == 401
