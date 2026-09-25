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


# --- F2: usage_id для метрик не выводится из пароля ---

def creds(login, password, **settings):
    return credentials_from_headers({"x-directum-login": login, "x-directum-password": password}, make_settings(**settings))


def test_usage_id_stable_per_login_and_independent_of_password():
    first, second = creds("user1", "pw1"), creds("user1", "other-password")

    assert first.usage_id == second.usage_id
    assert len(first.usage_id) == 16
    assert creds("user2", "pw1").usage_id != first.usage_id


def test_usage_id_is_not_password_derived_fingerprint():
    credentials = creds("user1", "pw1")

    assert credentials.usage_id != credentials.fingerprint[:16]


def test_usage_id_is_hmac_of_login_with_metrics_salt():
    import hashlib
    import hmac

    credentials = creds("user1", "pw1", MCP_METRICS_SALT="salt-1")

    assert credentials.usage_id == hmac.new(b"salt-1", b"user1", hashlib.sha256).hexdigest()[:16]
    assert creds("user1", "pw1", MCP_METRICS_SALT="salt-2").usage_id != credentials.usage_id


def test_metrics_salt_fallbacks():
    assert make_settings(MCP_METRICS_SALT="s").metrics_salt == "s"
    assert make_settings(MCP_OGV_KEY="key-1").metrics_salt == "key-1"
    no_key = make_settings(MCP_OGV_KEY=None)
    assert no_key.metrics_salt and no_key.metrics_salt == make_settings(MCP_OGV_KEY=None).metrics_salt
    assert "very-secret-salt" not in repr(make_settings(MCP_METRICS_SALT="very-secret-salt"))


def test_env_credentials_usage_id_uses_login_from_token():
    debug = make_settings(MCP_ALLOW_ENV_CREDENTIALS=True, DIRECTUM_AUTH_TOKEN=basic("user1", "pass"))

    assert credentials_from_headers(None, debug).usage_id == creds("user1", "another").usage_id


def test_env_credentials_with_undecodable_token_use_env_login():
    import hashlib
    import hmac

    debug = make_settings(MCP_ALLOW_ENV_CREDENTIALS=True, DIRECTUM_AUTH_TOKEN="Basic !!!", MCP_METRICS_SALT="s")

    assert credentials_from_headers(None, debug).usage_id == hmac.new(b"s", b"env", hashlib.sha256).hexdigest()[:16]


# --- F6: заголовки Starlette декодированы как latin-1 ---

def test_cyrillic_credentials_decoded_from_latin1_headers():
    login, password = "пользователь", "пароль№1"
    as_latin1 = {
        "x-directum-login": login.encode("utf-8").decode("latin-1"),
        "x-directum-password": password.encode("utf-8").decode("latin-1"),
    }

    credentials = credentials_from_headers(as_latin1, make_settings())

    assert credentials.auth_token == basic(login, password)
    assert credentials.usage_id == creds(login, "x").usage_id


def test_plain_unicode_header_values_are_kept():
    assert creds("пользователь", "пароль").auth_token == basic("пользователь", "пароль")


# --- F12: у разных пользователей — свои закешированные текущие пользователи ---

def test_provider_caches_current_user_per_credentials():
    users = {basic("user1", "pw"): {"Id": 63, "Name": "Первый"}, basic("user2", "pw"): {"Id": 64, "Name": "Второй"}}
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        token = request.headers["authorization"]
        seen.append(token)
        return httpx.Response(200, json={"value": [users[token]]})

    provider = ServicesProvider(make_settings(), transport=httpx.MockTransport(handler))
    first = {"x-directum-login": "user1", "x-directum-password": "pw"}
    second = {"x-directum-login": "user2", "x-directum-password": "pw"}

    with provider.open(first) as (_, services):
        assert services.current_user.get_current_user().id == 63
    with provider.open(second) as (_, services):
        assert services.current_user.get_current_user().id == 64
    with provider.open(first) as (_, services):
        assert services.current_user.get_current_user().id == 63

    assert seen == [basic("user1", "pw"), basic("user2", "pw")]
