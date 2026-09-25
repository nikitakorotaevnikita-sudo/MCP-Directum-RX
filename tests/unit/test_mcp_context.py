import base64

import httpx
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from src.mcp_server.config import McpSettings
from src.mcp_server.context import ServicesProvider, TtlCache, credentials_from_headers


def make_settings(**overrides):
    values = {"DIRECTUM_BASE_URL": "https://rx.example/Integration/odata/", "MCP_OGV_KEY": "k", "_env_file": None}
    values.update(overrides)
    return McpSettings(**values)


def basic(login: str, password: str) -> str:
    return "Basic " + base64.b64encode(f"{login}:{password}".encode()).decode()


def test_settings_normalize_url_hosts_and_secrets():
    settings = make_settings(MCP_ALLOWED_HOSTS=" a:1 , b:2 ,", MCP_OGV_KEY="  ")

    assert settings.directum_base_url == "https://rx.example/Integration/odata"
    assert settings.allowed_hosts == ["a:1", "b:2"]
    assert settings.mcp_key is None
    assert settings.MCP_PORT == 8010
    assert settings.MCP_DIRECTUM_TIMEOUT_SECONDS == 20.0


def test_credentials_from_headers_builds_basic_token_case_insensitively():
    credentials = credentials_from_headers({"X-Directum-Login": "user1", "X-Directum-Password": "pw"}, make_settings())

    assert credentials.auth_token == basic("user1", "pw")
    assert len(credentials.fingerprint) == 64
    assert "pw" not in repr(credentials)


def test_missing_credentials_raise_tool_error():
    with pytest.raises(ToolError, match="Укажите логин и пароль Directum"):
        credentials_from_headers({}, make_settings())


def test_env_credentials_only_in_debug_mode():
    token = basic("env", "pass")

    debug = make_settings(MCP_ALLOW_ENV_CREDENTIALS=True, DIRECTUM_AUTH_TOKEN=token)
    assert credentials_from_headers(None, debug).auth_token == token

    with pytest.raises(ToolError):
        credentials_from_headers(None, make_settings(DIRECTUM_AUTH_TOKEN=token))


def test_ttl_cache_expires_entries():
    now = [100.0]
    cache = TtlCache(10, clock=lambda: now[0])

    cache.set("a", 1)
    assert cache.get("a") == 1
    now[0] = 111.0
    assert cache.get("a") is None


def test_provider_uses_user_token_and_caches_current_user():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers["authorization"])
        return httpx.Response(200, json={"value": [{"Id": 63, "Name": "Концева Надежда Ивановна"}]})

    provider = ServicesProvider(make_settings(), transport=httpx.MockTransport(handler))
    headers = {"x-directum-login": "user1", "x-directum-password": "pw"}

    with provider.open(headers) as (_, services):
        assert services.current_user.get_current_user().id == 63
    with provider.open(headers) as (_, services):
        assert services.current_user.get_current_user().id == 63

    assert seen == [basic("user1", "pw")]


def test_provider_closes_client_when_tool_fails():
    provider = ServicesProvider(
        make_settings(), transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"value": []}))
    )

    with pytest.raises(RuntimeError):
        with provider.open({"x-directum-login": "a", "x-directum-password": "b"}) as (_, services):
            http_client = services.client.client
            raise RuntimeError("boom")

    assert http_client.is_closed
