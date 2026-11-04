from contextlib import contextmanager

from mcp.server.mcpserver.exceptions import ToolError

from src.mcp_server.app import build_server
from src.mcp_server.config import McpSettings
from tests.unit.mcp_fakes import call_tool, error_text, list_tools, payload

CLASSIFIER_TOOLS = {"classifier_search", "classifier_get"}


class NoRxProvider:
    """Учётки RX нет — тулы классификатора всё равно должны работать."""

    @contextmanager
    def open(self, headers):
        raise ToolError("Укажите логин и пароль Directum")
        yield  # pragma: no cover


class FakeClassifier:
    def __init__(self):
        self.calls = []

    def search(self, query, top_k=50):
        self.calls.append(("search", query, top_k))
        if query == "сломано":
            raise ToolError("Классификатор недоступен")
        return {"items": [{"code": "0001.0002.0003.0004", "score": 0.9}], "returned": 1}

    def get(self, code):
        self.calls.append(("get", code))
        return {"code": code, "exists": code != "9999"}


def test_tools_absent_without_classifier():
    names = {t.name for t in list_tools(build_server(NoRxProvider()))}
    assert not CLASSIFIER_TOOLS & names


def test_tools_present_and_read_only():
    tools = {t.name: t for t in list_tools(build_server(NoRxProvider(), classifier=FakeClassifier()))}
    assert CLASSIFIER_TOOLS <= set(tools)
    assert all(tools[name].annotations.read_only_hint for name in CLASSIFIER_TOOLS)
    assert "classifier_get" in tools["classifier_search"].description


def test_search_works_without_rx_credentials():
    fake = FakeClassifier()
    result = payload(call_tool(build_server(NoRxProvider(), classifier=fake), "classifier_search",
                               {"query": "тарифы", "top_k": 20}))
    assert result == {"items": [{"code": "0001.0002.0003.0004", "score": 0.9}], "returned": 1}
    assert fake.calls == [("search", "тарифы", 20)]


def test_get_and_errors():
    server = build_server(NoRxProvider(), classifier=FakeClassifier())
    assert payload(call_tool(server, "classifier_get", {"code": "9999"})) == {"code": "9999", "exists": False}
    assert "Классификатор недоступен" in error_text(call_tool(server, "classifier_search", {"query": "сломано"}))


def test_settings_defaults():
    settings = McpSettings(DIRECTUM_BASE_URL="https://rx/odata", _env_file=None)
    assert settings.MCP_CLASSIFIER_URL is None and settings.MCP_CLASSIFIER_TIMEOUT_SECONDS == 15.0
