import base64
import json
import socket
import threading
import time

import anyio
import httpx
import httpx2
import pytest
import uvicorn
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from src.mcp_server.app import build_asgi_app
from src.mcp_server.config import McpSettings
from src.mcp_server.context import ServicesProvider

LOGIN, PASSWORD, KEY = "user1", "pw1", "test-key"
EXPECTED_TOKEN = "Basic " + base64.b64encode(f"{LOGIN}:{PASSWORD}".encode()).decode()
FULL_HEADERS = {"X-MCP-Key": KEY, "X-Directum-Login": LOGIN, "X-Directum-Password": PASSWORD}


def _directum(request: httpx.Request) -> httpx.Response:
    if request.headers.get("authorization") != EXPECTED_TOKEN:
        return httpx.Response(401)
    if request.url.path.endswith("/IUsers"):
        return httpx.Response(200, json={"value": [{"Id": 63, "Name": "Концева Надежда Ивановна"}]})
    return httpx.Response(404, json={"error": "not found"})


@pytest.fixture(scope="module")
def mcp_url(tmp_path_factory):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    settings = McpSettings(
        DIRECTUM_BASE_URL="https://rx.example/Integration/odata",
        MCP_OGV_KEY=KEY,
        MCP_ALLOWED_HOSTS=f"127.0.0.1:{port}",
        MCP_USAGE_DB_PATH=str(tmp_path_factory.mktemp("usage") / "usage.db"),
        _env_file=None,
    )
    app = build_asgi_app(settings, provider=ServicesProvider(settings, transport=httpx.MockTransport(_directum)))
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{port}"
    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            if httpx.get(f"{url}/health").status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.1)
    yield url
    server.should_exit = True
    thread.join(timeout=5)


def _call(url, headers, tool, arguments=None):
    async def main():
        http = httpx2.AsyncClient(headers=headers)
        async with Client(streamable_http_client(f"{url}/mcp", http_client=http)) as client:
            return await client.call_tool(tool, arguments or {})

    return anyio.run(main)


def test_health_is_public(mcp_url):
    assert httpx.get(f"{mcp_url}/health").json() == {"status": "ok", "server": "mcpOGV"}


def test_mcp_endpoint_requires_shared_key(mcp_url):
    response = httpx.post(
        f"{mcp_url}/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        headers={"Accept": "application/json, text/event-stream"},
    )

    assert response.status_code == 401


def test_tool_runs_with_user_credentials_from_headers(mcp_url):
    result = _call(mcp_url, FULL_HEADERS, "get_current_user")

    assert not result.is_error, result.content[0].text
    assert json.loads(result.content[0].text)["id"] == 63


def test_missing_directum_credentials_reported_to_agent(mcp_url):
    result = _call(mcp_url, {"X-MCP-Key": KEY}, "get_current_user")

    assert result.is_error
    assert "Укажите логин и пароль Directum" in result.content[0].text


def test_wrong_directum_password_reported_to_agent(mcp_url):
    headers = {**FULL_HEADERS, "X-Directum-Password": "wrong"}

    result = _call(mcp_url, headers, "get_current_user")

    assert result.is_error
    assert "Неверный логин или пароль" in result.content[0].text
