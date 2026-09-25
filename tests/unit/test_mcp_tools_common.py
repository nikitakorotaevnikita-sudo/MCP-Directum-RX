from types import SimpleNamespace

import pytest

from src.mcp_server.app import build_asgi_app, build_server
from src.mcp_server.config import McpSettings
from src.models.schemas import DirectumUser, EmployeeSummary
from tests.unit.mcp_fakes import FakeProvider, call_tool, list_tools, payload


def make_server(services):
    return build_server(FakeProvider(services))


def test_get_current_user_tool():
    services = SimpleNamespace(
        current_user=SimpleNamespace(
            get_current_user=lambda: DirectumUser(id=63, name="Концева Надежда Ивановна", login="user1")
        )
    )

    data = payload(call_tool(make_server(services), "get_current_user"))

    assert data["id"] == 63
    assert data["name"] == "Концева Надежда Ивановна"


def test_search_employees_requests_one_extra_row():
    calls = []

    def search_employee(query, top):
        calls.append((query, top))
        return [EmployeeSummary(id=i, name=f"Сотрудник {i}", status="Active") for i in range(3)]

    services = SimpleNamespace(action_items=SimpleNamespace(search_employee=search_employee))

    data = payload(call_tool(make_server(services), "search_employees", {"query": "Ардо", "limit": 2}))

    assert calls == [("Ардо", 3)]
    assert data["returned"] == 2
    assert data["truncated"] is True
    assert data["total"] is None


def test_common_tools_are_read_only():
    tools = {tool.name: tool for tool in list_tools(make_server(SimpleNamespace()))}

    assert tools["get_current_user"].annotations.read_only_hint is True
    assert tools["search_employees"].annotations.read_only_hint is True


def test_asgi_app_requires_key_unless_debug(tmp_path):
    common = {
        "DIRECTUM_BASE_URL": "https://rx.example/Integration/odata",
        "MCP_USAGE_DB_PATH": str(tmp_path / "usage.db"),
        "_env_file": None,
    }

    with pytest.raises(RuntimeError, match="MCP_OGV_KEY"):
        build_asgi_app(McpSettings(**common), provider=FakeProvider(SimpleNamespace()))

    debug = McpSettings(MCP_ALLOW_ENV_CREDENTIALS=True, **common)
    assert build_asgi_app(debug, provider=FakeProvider(SimpleNamespace())) is not None
