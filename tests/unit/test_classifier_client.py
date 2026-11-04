import json

import httpx
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from src.services.classifier_client import ClassifierClient

URL = "http://classifier.local:8020/"
ITEM = {"code": "0004.0015.0158.0974", "name": "Рубрика", "level": 4, "full_path": "A / B", "score": 0.61}


def client(handler):
    return ClassifierClient(URL, timeout=5, transport=httpx.MockTransport(handler))


def test_search_posts_query_and_returns_items_as_is():
    seen = {}

    def handler(request):
        seen["method"], seen["url"] = request.method, str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"items": [ITEM], "returned": 1})

    result = client(handler).search("  тарифы ЖКХ  ", 10)
    assert seen == {"method": "POST", "url": "http://classifier.local:8020/integration/candidates",
                    "body": {"query": "тарифы ЖКХ", "top_k": 10}}
    assert result == {"items": [ITEM], "returned": 1}


@pytest.mark.parametrize(("given", "sent"), [(0, 1), (-5, 1), (100, 50), (50, 50)])
def test_search_clamps_top_k(given, sent):
    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"items": [], "returned": 0})

    client(handler).search("запрос", given)
    assert bodies[0]["top_k"] == sent


def test_search_returned_defaults_to_items_count():
    result = client(lambda r: httpx.Response(200, json={"items": [ITEM, ITEM]})).search("q")
    assert result == {"items": [ITEM, ITEM], "returned": 2}


def test_empty_query_is_rejected_without_request():
    def handler(request):
        raise AssertionError("не должно быть запроса")

    with pytest.raises(ToolError, match="Пустой запрос"):
        client(handler).search("   ")


def test_400_passes_detail():
    with pytest.raises(ToolError, match="Запрос пуст"):
        client(lambda r: httpx.Response(400, json={"detail": "Запрос пуст"})).search("q")


def test_503_passes_detail_and_asks_to_retry():
    with pytest.raises(ToolError, match="Агент не инициализирован.*Повторите через минуту"):
        client(lambda r: httpx.Response(503, json={"detail": "Агент не инициализирован"})).search("q")


def test_other_status_is_generic_without_body():
    with pytest.raises(ToolError) as info:
        client(lambda r: httpx.Response(500, text="Traceback secret")).search("q")
    assert "ошибку 500" in str(info.value) and "secret" not in str(info.value)


@pytest.mark.parametrize("exc", [httpx.ConnectTimeout("t"), httpx.ReadTimeout("t"), httpx.ConnectError("refused")])
def test_network_problems_mean_unavailable(exc):
    def handler(request):
        raise exc

    with pytest.raises(ToolError, match="Классификатор недоступен"):
        client(handler).search("q")


def test_invalid_json_is_reported():
    with pytest.raises(ToolError, match="некорректный ответ"):
        client(lambda r: httpx.Response(200, text="<html>")).search("q")


def test_get_requests_code_and_returns_as_is():
    seen = {}

    def handler(request):
        seen["method"], seen["path"] = request.method, request.url.raw_path.decode()
        return httpx.Response(200, json={"code": "0003.0009.0097.0000", "exists": True, "name": "X"})

    result = client(handler).get(" 0003.0009.0097 ")
    assert seen == {"method": "GET", "path": "/integration/classifier/0003.0009.0097"}
    assert result["exists"] is True


def test_get_unknown_code_is_not_an_error():
    result = client(lambda r: httpx.Response(200, json={"code": "9999", "exists": False})).get("9999")
    assert result == {"code": "9999", "exists": False}


def test_get_code_is_url_encoded():
    paths = []

    def handler(request):
        paths.append(request.url.raw_path.decode())
        return httpx.Response(200, json={"exists": False})

    client(handler).get("a/b c")
    assert paths == ["/integration/classifier/a%2Fb%20c"]


def test_get_empty_code_is_rejected():
    with pytest.raises(ToolError, match="Укажите код"):
        client(lambda r: httpx.Response(200, json={})).get(" ")
