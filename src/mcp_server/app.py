import hmac
import logging
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.responses import JSONResponse

from src.mcp_server import resources
from src.mcp_server.admin_gate import AdminGateMiddleware
from src.mcp_server.audit import ToolUsageStore
from src.mcp_server.config import McpSettings
from src.mcp_server.context import ServicesProvider, TtlCache
from src.mcp_server.native import NativeProxyMiddleware
from src.mcp_server.odata_meta import MetadataCache
from src.mcp_server.runner import ToolRunner
from src.mcp_server.write_guard import ConfirmTokens, WriteAudit
from src.mcp_server.tools import (
    action_items,
    citizen_requests,
    common,
    document_control,
    documents,
    employee_items,
    legal_acts,
    odata,
    rx_methods,
    write,
)

logger = logging.getLogger("mcp_ogv")

SERVER_NAME = "mcpOGV"
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
SKILLS_DIR = Path(__file__).resolve().parents[2] / ".claude" / "skills"
NATIVE_TOOLS_TTL_SECONDS = 600
ADMIN_CHECK_TTL_SECONDS = 600

INSTRUCTIONS = """mcpOGV — доступ к Directum RX от имени текущего пользователя (его права и его данные).
Правила:
1. Для типовых задач используй курируемые инструменты (list_*, get_*, search_*): они надёжнее универсальных.
2. Списки приходят частями: смотри total, returned, truncated. Если truncated=true — уточни фильтр или увеличь limit (до 100).
3. Не выдумывай идентификаторы: сотрудников ищи через search_employees, документы — через search_documents.
4. Универсальные odata_* используй для того, чего нет в курируемых. Всегда указывай filter — Directum отклоняет запросы без фильтра. Поля смотри через odata_describe_entity, наборы данных — в справочниках drx://domains/*.
5. Инструменты drx_native_* — встроенные инструменты самой Directum RX.
6. Даты передавай в формате YYYY-MM-DD.
7. Поручения другого сотрудника — list_employee_action_items: свои видит любой, подчинённых — их руководитель,
   любых — администратор. Списки (тема, срок, дни просрочки, ссылка) бери оттуда. Дисциплина по всем сотрудникам
   подразделения — одним вызовом get_department_discipline, а не get_discipline_analytics по каждому (там только счётчики). Не оставляй пропусков вида {{уточнить}}: вызови нужный
   инструмент или прямо скажи, чего не хватает.
8. Ссылки на карточки (поле url) выводи markdown-ссылками: [название](url).
9. Вопрос о содержании документов («что сказано про…») — ask_documents; документ по реквизитам — find_documents;
   документ по фрагменту или пересказу текста — find_documents_by_content одним вызовом (не перебирай формулировки).
10. Обращения граждан: статус — get_citizen_request_status; счёт по тематикам — get_citizen_requests_analytics,
    код классификатора — search_citizen_request_classifier. Уровни ТОТК: раздел → тематика → тема → вопрос;
    в RX поле Topic — это тематика, Theme — тема. Число обращений и число вопросов различай. Ответы за период для
    проверки качества — list_citizen_request_answers (по 5, страницами через next_skip).
11. Нормативные правовые акты и проекты (модуль «Правовые акты» RX): find_legal_acts, get_legal_act,
    list_legal_acts_on_approval (где застряло согласование). Федеральных законов в RX нет — не выдавай их за данные RX.
    Связи любого документа (панель «Связи» RX: приложения, ответы, основания, заключения, отмена, переписка) —
    get_document_relations; у правовых актов они уже в get_legal_act (related, addenda). Не подбирай «связанные»
    документы поиском по названию, если связи можно прочитать.
12. Контроль исполнения: контрольных карт нет — объект контроля само поручение с признаком «На контроле».
    Реестр — list_controlled_action_items, всё по одному поручению (дерево, продления, приёмка) — get_control_dossier.
    Проект резолюции по входящему — get_resolution_history автора + текст документа (get_document_text).
13. Если get_current_user вернул service_account=true, mcpOGV работает под служебной учёткой, а не под
    пользователем чата: «мои» тулы (list_my_assignments, list_action_items, get_outgoing_action_items_analytics) и «me»
    относятся к ней. Спроси ФИО пользователя и передавай его явно (list_employee_action_items, supervisor/author=ФИО).
14. Создание (create_*, send_document_* — если эти тулы включены): всегда два шага. Без confirm_token — предпросмотр; покажи его пользователю,
    спроси «Создать?» и только после явного «да» повтори вызов с теми же параметрами и confirm_token. on_behalf_of —
    реальный сотрудник, от чьего имени документ или поручение. Регистрация, подписание и отправка — в RX.
"""


def _register_health(mcp: MCPServer) -> None:
    @mcp.custom_route("/health", methods=["GET"])
    async def health(request):
        return JSONResponse({"status": "ok", "server": SERVER_NAME})


def build_server(
    provider: Any,
    usage: ToolUsageStore | None = None,
    skills_dir: Path = SKILLS_DIR,
    write_audit: WriteAudit | None = None,
) -> MCPServer:
    """write_audit — включить тулы записи в RX (иначе они не регистрируются)."""
    runner = ToolRunner(provider, usage)
    native = NativeProxyMiddleware(provider, TtlCache(NATIVE_TOOLS_TTL_SECONDS), usage)
    admin_gate = AdminGateMiddleware(provider, TtlCache(ADMIN_CHECK_TTL_SECONDS))
    # Порядок outermost-first: gate видит итоговый список, включая drx_native_*.
    mcp = MCPServer(SERVER_NAME, instructions=INSTRUCTIONS, middleware=[admin_gate, native])
    common.register(mcp, runner)
    action_items.register(mcp, runner)
    employee_items.register(mcp, runner)
    documents.register(mcp, runner)
    rx_methods.register(mcp, runner)
    citizen_requests.register(mcp, runner)
    legal_acts.register(mcp, runner)
    document_control.register(mcp, runner)
    if write_audit is not None:
        write.register(mcp, runner, ConfirmTokens(), write_audit)
    guides = resources.load_domain_guides(skills_dir)
    odata.register(mcp, runner, MetadataCache(), guides)
    resources.register(mcp, guides)
    _register_health(mcp)
    return mcp


class McpKeyMiddleware:
    """Отклоняет запросы к /mcp без правильного X-MCP-Key (общий секрет LibreChat ↔ mcpOGV)."""

    def __init__(self, app: Any, key: str | None):
        self.app = app
        self.key = key

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and self.key is not None and scope["path"].startswith("/mcp"):
            provided = dict(scope.get("headers") or []).get(b"x-mcp-key", b"").decode("latin-1")
            if not hmac.compare_digest(provided, self.key):
                response = JSONResponse({"error": "unauthorized"}, status_code=401)
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


def build_asgi_app(
    settings: McpSettings,
    provider: Any | None = None,
    usage: ToolUsageStore | None = None,
) -> McpKeyMiddleware:
    if settings.mcp_key is None and not settings.MCP_ALLOW_ENV_CREDENTIALS:
        raise RuntimeError(
            "MCP_OGV_KEY is required; MCP_ALLOW_ENV_CREDENTIALS=true is allowed only for local debugging"
        )
    if settings.MCP_ALLOW_ENV_CREDENTIALS:
        if settings.mcp_key is None and settings.MCP_HOST.strip().lower() not in LOOPBACK_HOSTS:
            raise RuntimeError(
                "MCP_ALLOW_ENV_CREDENTIALS=true without MCP_OGV_KEY is allowed only with a loopback "
                "MCP_HOST (127.0.0.1, localhost, ::1)"
            )
        if settings.mcp_key is not None:
            logger.warning(
                "MCP_ALLOW_ENV_CREDENTIALS=true: requests without Directum credential headers "
                "will use DIRECTUM_AUTH_TOKEN; use only for local debugging"
            )
    provider = provider or ServicesProvider(settings)
    usage = usage or ToolUsageStore(settings.MCP_USAGE_DB_PATH)
    write_audit = WriteAudit(settings.MCP_WRITE_AUDIT_PATH) if settings.MCP_WRITE_ENABLED else None
    mcp = build_server(provider, usage, write_audit=write_audit)
    app = mcp.streamable_http_app(
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(allowed_hosts=settings.allowed_hosts),
    )
    return McpKeyMiddleware(app, settings.mcp_key)
