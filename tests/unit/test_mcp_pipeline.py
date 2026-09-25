from types import SimpleNamespace

import httpx
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from src.mcp_server.audit import ToolUsageStore
from src.mcp_server.envelope import clamp_limit, list_envelope, to_jsonable
from src.mcp_server.errors import to_tool_error
from src.mcp_server.runner import READ_ONLY, ToolRunner
from src.models.schemas import DirectumUser
from src.services.directum_client import DirectumError
from tests.unit.mcp_fakes import FakeProvider, run_async


def test_clamp_limit_bounds():
    assert clamp_limit(None) == 20
    assert clamp_limit(0) == 1
    assert clamp_limit(500) == 100
    assert clamp_limit(500, maximum=50) == 50


def test_list_envelope_with_known_total():
    assert list_envelope([1, 2, 3], limit=2, total=1191) == {
        "items": [1, 2], "total": 1191, "returned": 2, "truncated": True,
    }


def test_list_envelope_without_total_uses_extra_item():
    assert list_envelope([1, 2, 3], limit=2) == {"items": [1, 2], "total": None, "returned": 2, "truncated": True}
    assert list_envelope([1, 2], limit=2) == {"items": [1, 2], "total": 2, "returned": 2, "truncated": False}


def test_to_jsonable_dumps_pydantic_models():
    user = DirectumUser(id=1, name="Иванов", login="ivanov")
    expected = user.model_dump(mode="json")

    assert to_jsonable({"user": user, "users": [user]}) == {"user": expected, "users": [expected]}


@pytest.mark.parametrize(
    ("exc", "kind", "fragment"),
    [
        (DirectumError("x", 401), "auth", "Неверный логин или пароль"),
        (DirectumError("x", 403), "forbidden", "Недостаточно прав"),
        (DirectumError("status 400: Превышено ... Используйте фильтрацию.", 400), "too_broad", "Слишком широкий запрос"),
        (DirectumError("Documents not found", 404), "directum", "Documents not found"),
        (httpx.ReadTimeout("slow"), "timeout", "слишком долго"),
        (ToolError("готовое сообщение"), "tool", "готовое сообщение"),
    ],
)
def test_to_tool_error_maps_known_failures(exc, kind, fragment):
    error, error_kind = to_tool_error(exc)

    assert error_kind == kind
    assert fragment in str(error)


def test_to_tool_error_hides_internal_details():
    error, error_kind = to_tool_error(ValueError("secret internals"))

    assert error_kind == "internal"
    assert "Внутренняя ошибка mcpOGV, код" in str(error)
    assert "secret internals" not in str(error)


def test_usage_store_records_and_summarizes(tmp_path):
    store = ToolUsageStore(str(tmp_path / "usage.db"))
    store.record("list_my_assignments", True, None, 120, "a" * 64)
    store.record("list_my_assignments", False, "timeout", 20000, "a" * 64)
    store.record("get_current_user", True, None, 50, None)

    summary = {row["tool"]: row for row in store.summary()}

    assert summary["list_my_assignments"]["calls"] == 2
    assert summary["list_my_assignments"]["errors"] == 1
    assert summary["get_current_user"]["calls"] == 1
    assert store.user_hashes() == ["a" * 16]


class _Ctx:
    headers = {"x-directum-login": "user1"}


def test_runner_returns_result_and_records_usage(tmp_path):
    store = ToolUsageStore(str(tmp_path / "usage.db"))
    provider = FakeProvider(SimpleNamespace(value=42))
    runner = ToolRunner(provider, store)

    assert run_async(runner.run, _Ctx(), "demo", lambda services: services.value) == 42
    assert provider.opened_with == [{"x-directum-login": "user1"}]
    assert store.summary()[0]["calls"] == 1
    assert store.user_hashes() == ["u" * 16]


def test_runner_maps_errors_and_records_them(tmp_path):
    store = ToolUsageStore(str(tmp_path / "usage.db"))
    runner = ToolRunner(FakeProvider(SimpleNamespace()), store)

    def fail(services):
        raise DirectumError("x", 401)

    with pytest.raises(ToolError, match="Неверный логин"):
        run_async(runner.run, _Ctx(), "demo", fail)
    assert store.summary()[0]["errors"] == 1


def test_runner_accepts_missing_context():
    provider = FakeProvider(SimpleNamespace())

    assert run_async(ToolRunner(provider).run, None, "demo", lambda services: 1) == 1
    assert provider.opened_with == [None]


def test_read_only_annotation():
    assert READ_ONLY.read_only_hint is True
