import base64
import os

import pytest

from src.mcp_server.app import build_server
from src.mcp_server.config import McpSettings
from src.mcp_server.context import ServicesProvider
from tests.unit.mcp_fakes import call_tool, list_tools, payload

LOGIN = os.getenv("MCP_LIVE_LOGIN")
PASSWORD = os.getenv("MCP_LIVE_PASSWORD")
BASE_URL = os.getenv("MCP_LIVE_BASE_URL")

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        not (LOGIN and PASSWORD and BASE_URL),
        reason="set MCP_LIVE_BASE_URL, MCP_LIVE_LOGIN, MCP_LIVE_PASSWORD to run the live smoke",
    ),
]


@pytest.fixture(scope="module")
def server():
    token = "Basic " + base64.b64encode(f"{LOGIN}:{PASSWORD}".encode()).decode()
    settings = McpSettings(
        DIRECTUM_BASE_URL=BASE_URL,
        DIRECTUM_AUTH_TOKEN=token,
        MCP_ALLOW_ENV_CREDENTIALS=True,
        _env_file=None,
    )
    return build_server(ServicesProvider(settings))


def test_live_current_user(server):
    assert payload(call_tool(server, "get_current_user"))["id"] > 0


def test_live_my_assignments_has_total(server):
    data = payload(call_tool(server, "list_my_assignments", {"limit": 3}))

    assert data["returned"] <= 3
    assert data["total"] is not None


def test_live_discipline(server):
    assert "in_process" in payload(call_tool(server, "get_discipline_analytics"))


def test_live_odata_count(server):
    data = payload(call_tool(server, "odata_count", {"entity_set": "IAssignments", "filter": "Status eq 'InProcess'"}))

    assert data["count"] >= 0


def test_live_native_tools_listed(server):
    assert any(tool.name.startswith("drx_native_") for tool in list_tools(server))


def test_live_admin_tool_visibility_matches_role(server):
    visible = "admin_list_employee_action_items" in [tool.name for tool in list_tools(server)]

    if visible:
        employee = payload(
            call_tool(server, "odata_query", {"entity_set": "IEmployees", "filter": "Status eq 'Active'", "top": 1})
        )["items"][0]
        data = payload(
            call_tool(
                server,
                "admin_list_employee_action_items",
                {"employee": str(employee["Id"]), "direction": "incoming", "status": "all", "limit": 3},
            )
        )
        assert data["employee"]["id"] == employee["Id"]
        assert data["total"] is not None


@pytest.mark.parametrize("due", ["today", "week"])
def test_live_action_items_due_window(server, due):
    data = payload(call_tool(server, "list_action_items", {"direction": "outgoing", "due": due, "limit": 3}))

    assert data["total"] is not None
    assert data["returned"] <= 3
