import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from src.config import Settings, get_settings
from src.models.schemas import ActionItemCreateRequest, ChatRequest
from src.services.action_items import ActionItemService
from src.services.assignments import AssignmentsService
from src.services.current_user import CurrentUserService
from src.services.directum_client import DirectumClient, DirectumError
from src.services.llm_service import LLMService
from src.services.metrics_storage import MetricsStorage
from src.services.tool_registry import ToolRegistry


STATIC_DIR = Path(__file__).parent / "static"


def create_app(testing: bool = False, metrics_db_path: str | None = None) -> FastAPI:
    settings = _test_settings(metrics_db_path) if testing else get_settings()
    services = build_services(settings, testing=testing)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        directum = app.state.services.get("directum")
        if directum is not None and hasattr(directum, "close"):
            directum.close()

    app = FastAPI(title="MCP Directum RX", lifespan=lifespan)
    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    app.state.settings = settings
    app.state.services = services

    @app.exception_handler(DirectumError)
    def directum_error_handler(request: Request, exc: DirectumError):
        return JSONResponse(
            status_code=exc.status_code or 502,
            content={"detail": exc.safe_message},
        )

    @app.get("/")
    def index():
        index_path = STATIC_DIR / "index.html"
        if index_path.exists():
            return FileResponse(index_path)
        return PlainTextResponse("MCP Directum RX")

    @app.get("/backoffice")
    def backoffice():
        backoffice_path = STATIC_DIR / "backoffice.html"
        if backoffice_path.exists():
            return FileResponse(backoffice_path)
        return PlainTextResponse("Backoffice")

    @app.get("/health")
    def health():
        return {"status": "ok", "llm": services["llm"].status()}

    @app.get("/api/diagnostics/config")
    def config():
        return settings.public_config()

    @app.get("/api/diagnostics/current-user")
    def current_user():
        return services["current_user"].get_current_user()

    @app.get("/api/diagnostics/odata")
    def odata():
        return {"status": "configured", "base_url": settings.directum_base_url}

    @app.post("/api/chat")
    def chat(request: ChatRequest):
        services["metrics"].record_chat_request("chat", 0)
        return StreamingResponse(
            services["llm"].stream_chat(request.message, request.history),
            media_type="text/plain",
        )

    @app.get("/api/directum/assignments/my")
    def my_assignments():
        return services["assignments"].get_my_assignments()

    @app.get("/api/directum/assignments/overdue")
    def overdue_assignments():
        return services["assignments"].get_overdue_assignments()

    @app.get("/api/directum/action-items/assigned-to-me")
    def assigned_action_items():
        return services["assignments"].get_action_items_assigned_to_me()

    @app.get("/api/directum/action-items/created-by-me")
    def created_action_items():
        return services["assignments"].get_action_items_created_by_me()

    @app.get("/api/directum/employees/search")
    def employee_search(query: str):
        return services["action_items"].search_employee(query)

    @app.post("/api/directum/action-items")
    def create_action_item(request: ActionItemCreateRequest):
        result = services["action_items"].create_action_item(request)
        services["metrics"].record_action_item_create("confirmed" if request.confirm else "preview")
        return result

    @app.post("/api/feedback")
    def feedback(payload: dict[str, Any]):
        services["metrics"].record_feedback(str(payload.get("rating", "unknown")))
        return {"status": "ok"}

    @app.get("/api/metrics")
    def metrics():
        return services["metrics"].summary()

    return app


def build_services(settings: Settings, testing: bool = False) -> dict[str, Any]:
    transport = _mock_transport() if testing else None
    auth_token = settings.directum_headers()["Authorization"]
    client = DirectumClient(
        settings.directum_base_url,
        auth_token,
        settings.DIRECTUM_REQUEST_TIMEOUT_SECONDS,
        transport=transport,
    )
    current_user = CurrentUserService(client, auth_token)
    assignments = AssignmentsService(client, current_user)
    action_items = ActionItemService(client)
    metrics = MetricsStorage(settings.METRICS_DB_PATH)
    metrics.initialize()
    registry = ToolRegistry(current_user, assignments, action_items)
    if testing:
        llm = _FakeLLMService()
    else:
        llm = LLMService(
            settings.LLM_PROVIDER,
            settings.openai_base_url,
            settings.openai_api_key,
            settings.openai_model,
            settings.LLM_TOOL_CALLING,
            registry,
        )
    return {
        "directum": client,
        "current_user": current_user,
        "assignments": assignments,
        "action_items": action_items,
        "metrics": metrics,
        "registry": registry,
        "llm": llm,
    }


def _test_settings(metrics_db_path: str | None = None) -> Settings:
    db_path = (
        Path(metrics_db_path)
        if metrics_db_path
        else Path(tempfile.mkdtemp(prefix="mcp_directum_rx_test_")) / "metrics.db"
    )
    return Settings(
        OPENAI_API_KEY="test-key",
        OPENAI_BASE_URL="http://localhost:11434/v1",
        OPENAI_MODEL="qwen3:8b",
        DIRECTUM_BASE_URL="https://rx.example/Integration/odata",
        DIRECTUM_AUTH_TOKEN="Basic bnRfd29ya1xcdXNlcjpwYXNz",
        METRICS_DB_PATH=str(db_path),
    )


class _FakeLLMService:
    def status(self) -> dict[str, str]:
        return {
            "provider": "test",
            "base_url": "test://local",
            "model": "test-model",
            "tool_calling": "disabled",
        }

    def stream_chat(self, message: str, history: list[dict[str, str]]):
        yield "Test LLM response"


def _mock_transport() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if "IUsers" in path:
            return httpx.Response(200, json={"value": [{"Id": 1165, "Name": "Test User"}]})
        if "IEmployees" in path:
            return httpx.Response(200, json={"value": [{"Id": 42, "Name": "Ivanov Ivan", "Status": "Active"}]})
        if request.method == "POST":
            return httpx.Response(200, json={"Id": 9001})
        return httpx.Response(200, json={"value": [{"Id": 1, "Subject": "Task", "Status": "InProcess"}]})

    return httpx.MockTransport(handler)


class _ProductionApp:
    def __init__(self):
        self._app: FastAPI | None = None

    async def __call__(self, scope, receive, send):
        if self._app is None:
            self._app = create_app()
        await self._app(scope, receive, send)


app = _ProductionApp()
