import json
from types import SimpleNamespace

import pytest

from src.mcp_server.app import build_server
from src.mcp_server.audit import ToolUsageStore
from src.mcp_server.native import NativeMcpClient
from src.services.directum_client import DirectumError
from tests.unit.mcp_fakes import FakeProvider, call_tool, error_text, list_tools

NATIVE_TOOLS = [
    {
        "name": "gd_dashboard_ai_agent_get_action_items2_info",
        "description": "Найти информацию об исполнении поручений",
        "inputSchema": {"type": "object", "properties": {"assigneeName": {"type": "string"}}, "required": []},
        "annotations": {"title": "Исполнение поручений", "readOnlyHint": True},
    },
    {
        "name": "danger_write_tool",
        "description": "Пишет в систему",
        "inputSchema": {"type": "object", "properties": {}},
        "annotations": {"readOnlyHint": False},
    },
]


class FakeNativeClient:
    def __init__(self, fail=False):
        self.fail = fail
        self.posts = []

    def post(self, entity_set, payload):
        if self.fail:
            raise DirectumError("Directum unavailable", 503)
        message = json.loads(payload["value"])
        self.posts.append((entity_set, message))
        if message["method"] == "tools/list":
            result = {"tools": NATIVE_TOOLS}
        elif message["method"] == "tools/call":
            text = f"called {message['params']['name']} with {json.dumps(message['params']['arguments'], ensure_ascii=False)}"
            result = {"content": [{"type": "text", "text": text}], "isError": False}
        else:
            return {"value": json.dumps({"jsonrpc": "2.0", "id": 1, "error": {"code": -32601, "message": "no method"}})}
        return {"value": json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": result}, ensure_ascii=False)}


def make_server(tmp_path, client):
    return build_server(FakeProvider(SimpleNamespace(client=client)), skills_dir=tmp_path)


def test_native_client_filters_read_only_tools():
    tools = NativeMcpClient(FakeNativeClient()).list_read_only_tools()

    assert [tool["name"] for tool in tools] == ["gd_dashboard_ai_agent_get_action_items2_info"]


def test_native_client_raises_on_jsonrpc_error():
    with pytest.raises(DirectumError, match="no method"):
        NativeMcpClient(FakeNativeClient()).request("prompts/get", {})


def test_native_tools_appear_with_prefix(tmp_path):
    names = [tool.name for tool in list_tools(make_server(tmp_path, FakeNativeClient()))]

    assert "drx_native_gd_dashboard_ai_agent_get_action_items2_info" in names
    assert "drx_native_danger_write_tool" not in names
    assert "get_current_user" in names


def test_native_call_is_forwarded_without_prefix(tmp_path):
    client = FakeNativeClient()
    server = make_server(tmp_path, client)

    result = call_tool(server, "drx_native_gd_dashboard_ai_agent_get_action_items2_info", {"assigneeName": "Ардо"})

    assert not result.is_error
    assert result.content[0].text == 'called gd_dashboard_ai_agent_get_action_items2_info with {"assigneeName": "Ардо"}'
    entity_set, message = client.posts[-1]
    assert entity_set == "IntegrationAIAgent/HandleMcpRequest"
    assert message["method"] == "tools/call"


def test_non_read_only_native_tool_cannot_be_called(tmp_path):
    text = error_text(call_tool(make_server(tmp_path, FakeNativeClient()), "drx_native_danger_write_tool"))

    assert "недоступен" in text


def test_unavailable_native_mcp_does_not_break_tool_list(tmp_path):
    names = [tool.name for tool in list_tools(make_server(tmp_path, FakeNativeClient(fail=True)))]

    assert "get_current_user" in names
    assert not any(name.startswith("drx_native_") for name in names)


class _ListingClient:
    """Fake native MCP client whose tools/list response is supplied by the test."""

    def __init__(self, tools):
        self.tools = tools

    def post(self, entity_set, payload):
        message = json.loads(payload["value"])
        if message["method"] != "tools/list":
            raise AssertionError(f"unexpected method {message['method']}")
        result = {"tools": self.tools}
        return {"value": json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": result}, ensure_ascii=False)}


def test_native_tool_without_name_does_not_break_tool_list(tmp_path):
    tools = [
        {"name": None, "description": "нет имени", "inputSchema": {}, "annotations": {"readOnlyHint": True}},
        {"description": "имя отсутствует вообще", "inputSchema": {}, "annotations": {"readOnlyHint": True}},
        {
            "name": "gd_dashboard_ai_agent_get_action_items2_info",
            "description": "ok",
            "inputSchema": {"type": "object", "properties": {}},
            "annotations": {"readOnlyHint": True},
        },
    ]

    names = [tool.name for tool in list_tools(make_server(tmp_path, _ListingClient(tools)))]

    assert "get_current_user" in names
    assert "drx_native_gd_dashboard_ai_agent_get_action_items2_info" in names
    assert len([name for name in names if name.startswith("drx_native_")]) == 1


def test_native_tool_without_annotations_is_filtered_out(tmp_path):
    tools = [
        {"name": "no_annotations_tool", "description": "нет annotations вообще", "inputSchema": {}},
        {
            "name": "gd_dashboard_ai_agent_get_action_items2_info",
            "description": "ok",
            "inputSchema": {"type": "object", "properties": {}},
            "annotations": {"readOnlyHint": True},
        },
    ]

    names = [tool.name for tool in list_tools(make_server(tmp_path, _ListingClient(tools)))]

    assert "drx_native_no_annotations_tool" not in names
    assert "drx_native_gd_dashboard_ai_agent_get_action_items2_info" in names


class _CallResultClient:
    """Fake native MCP client whose tools/call result is supplied by the test."""

    def __init__(self, call_result):
        self.call_result = call_result

    def post(self, entity_set, payload):
        message = json.loads(payload["value"])
        if message["method"] == "tools/list":
            result = {"tools": NATIVE_TOOLS}
        elif message["method"] == "tools/call":
            result = self.call_result
        else:
            raise AssertionError(f"unexpected method {message['method']}")
        return {"value": json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": result}, ensure_ascii=False)}


def test_native_call_passes_through_non_text_content_and_structured(tmp_path):
    call_result = {
        "content": [
            {"type": "text", "text": "summary"},
            {"type": "image", "data": "base64data", "mimeType": "image/png"},
        ],
        "structuredContent": {"count": 3},
        "isError": False,
    }
    server = make_server(tmp_path, _CallResultClient(call_result))

    result = call_tool(server, "drx_native_gd_dashboard_ai_agent_get_action_items2_info", {})

    assert not result.is_error
    assert result.content[0].text == "summary"
    assert result.content[1].type == "image"
    assert result.content[1].data == "base64data"
    assert result.structured_content == {"count": 3}


def test_native_call_is_error_true_reaches_result(tmp_path):
    call_result = {"content": [{"type": "text", "text": "boom"}], "isError": True}
    server = make_server(tmp_path, _CallResultClient(call_result))

    result = call_tool(server, "drx_native_gd_dashboard_ai_agent_get_action_items2_info", {})

    assert result.is_error
    assert result.content[0].text == "boom"


def make_server_with_usage(tmp_path, client):
    store = ToolUsageStore(str(tmp_path / "usage.db"))
    server = build_server(FakeProvider(SimpleNamespace(client=client)), usage=store, skills_dir=tmp_path)
    return server, store


def test_native_call_records_usage_id_not_fingerprint(tmp_path):
    server, store = make_server_with_usage(tmp_path, FakeNativeClient())

    call_tool(server, "drx_native_gd_dashboard_ai_agent_get_action_items2_info", {})

    assert store.user_hashes() == ["u" * 16]


def test_native_tool_name_in_metrics_is_capped(tmp_path):
    server, store = make_server_with_usage(tmp_path, FakeNativeClient())

    call_tool(server, "drx_native_" + "x" * 300, {})

    assert [len(row["tool"]) for row in store.summary()] == [100]
