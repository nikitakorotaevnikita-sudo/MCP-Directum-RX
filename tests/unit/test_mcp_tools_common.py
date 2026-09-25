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

    debug = McpSettings(MCP_ALLOW_ENV_CREDENTIALS=True, MCP_HOST="127.0.0.1", **common)
    assert build_asgi_app(debug, provider=FakeProvider(SimpleNamespace())) is not None


@pytest.mark.parametrize("host", ["0.0.0.0", "10.1.2.3", "mcp-ogv"])
def test_debug_without_key_refused_on_non_loopback_host(tmp_path, host):
    settings = McpSettings(
        DIRECTUM_BASE_URL="https://rx.example/Integration/odata",
        MCP_USAGE_DB_PATH=str(tmp_path / "usage.db"),
        MCP_ALLOW_ENV_CREDENTIALS=True,
        MCP_HOST=host,
        _env_file=None,
    )

    with pytest.raises(RuntimeError, match="MCP_HOST"):
        build_asgi_app(settings, provider=FakeProvider(SimpleNamespace()))


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1"])
def test_debug_without_key_allowed_on_loopback(tmp_path, host):
    settings = McpSettings(
        DIRECTUM_BASE_URL="https://rx.example/Integration/odata",
        MCP_USAGE_DB_PATH=str(tmp_path / "usage.db"),
        MCP_ALLOW_ENV_CREDENTIALS=True,
        MCP_HOST=host,
        _env_file=None,
    )

    assert build_asgi_app(settings, provider=FakeProvider(SimpleNamespace())) is not None


def test_debug_with_key_logs_warning_without_secrets(tmp_path, caplog):
    settings = McpSettings(
        DIRECTUM_BASE_URL="https://rx.example/Integration/odata",
        MCP_USAGE_DB_PATH=str(tmp_path / "usage.db"),
        MCP_ALLOW_ENV_CREDENTIALS=True,
        MCP_OGV_KEY="key-value-123",
        DIRECTUM_AUTH_TOKEN="Basic dXNlcjpwYXNz",
        _env_file=None,
    )

    with caplog.at_level("WARNING", logger="mcp_ogv"):
        build_asgi_app(settings, provider=FakeProvider(SimpleNamespace()))

    assert "MCP_ALLOW_ENV_CREDENTIALS" in caplog.text
    assert "key-value-123" not in caplog.text and "dXNlcjpwYXNz" not in caplog.text
