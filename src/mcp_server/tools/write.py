"""Тулы записи в Directum RX: письма, правовые акты, поручения, рассмотрение, согласование, задачи.

Регистрируются только при MCP_WRITE_ENABLED=true. Каждый тул в два шага: без confirm_token — предпросмотр (ничего
не создаётся) и токен; с токеном и теми же параметрами — создание. on_behalf_of обязателен: под служебной учёткой
это единственный способ указать реального человека («Подготовил», «Выдал», «Автор»).
"""

from collections.abc import Callable
from typing import Annotated, Any, Literal

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from src.mcp_server.runner import ToolRunner
from src.mcp_server.tools.document_control import _person_id
from src.mcp_server.tools.params import parse_iso_date
from src.mcp_server.write_guard import ConfirmTokens, WriteAudit

WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=False)

OnBehalf = Annotated[str, Field(description="От чьего имени (реальный сотрудник): ФИО или id. Под служебной учёткой «me» нельзя")]
Token = Annotated[
    str | None,
    Field(description="Пусто — только предпросмотр. Токен из предпросмотра — создать (после явного «да» пользователя)"),
]
Body = Annotated[str | None, Field(description="Текст документа; абзацы — через пустую строку. Станет телом .docx")]

NEXT_STEP = ("Ничего не создано. Покажите пользователю предпросмотр (что, от чьего имени, кому, срок) и спросите "
             "«Создать?». Только после явного «да» вызовите этот же тул с теми же параметрами и confirm_token.")
SERVICE_ACCOUNT_NOTE = ("mcpOGV работает под служебной учёткой: в RX «Автор» будет она, а реальный человек указан в "
                        "полях «Подготовил» / «Выдал» / «Автор задачи» и в примечании документа.")


def _required_person(services, value: str | None, label: str) -> int:
    person = _person_id(services, value)
    if person is None:
        raise ToolError(f"Укажите {label}: ФИО или id сотрудника.")
    return person


def _people(services, values: list[str] | None, label: str) -> list[int]:
    return [_required_person(services, v, label) for v in (values or []) if (v or "").strip()]


def register(mcp: MCPServer, runner: ToolRunner, tokens: ConfirmTokens, audit: WriteAudit) -> None:
    async def two_step(ctx: Context, tool: str, confirm_token: str | None,
                       plan_fn: Callable[[Any], dict], create_fn: Callable[[Any, dict], dict]) -> dict:
        def action(s):
            service = getattr(s, "document_create", None)
            if service is None:
                raise ToolError("Создание документов недоступно в этой конфигурации mcpOGV.")
            plan = plan_fn(s)
            fingerprint = getattr(s, "credentials_fingerprint", "")
            preview_extra = {"service_account_note": SERVICE_ACCOUNT_NOTE} if getattr(s, "service_account", False) else {}
            if not (confirm_token or "").strip():
                return {"mode": "preview", "will_create": plan, "confirm_token": tokens.issue(tool, plan, fingerprint),
                        "next_step": NEXT_STEP, **preview_extra}
            problem = tokens.check(confirm_token, tool, plan, fingerprint)
            if problem:
                raise ToolError(problem)
            result = create_fn(s, plan)
            who = plan.get("prepared_by") or plan.get("assigned_by") or plan.get("author") or {}
            audit.record(tool, getattr(s, "usage_id", None), who.get("name"), result)
            return {"mode": "created", **result, **preview_extra}

        return await runner.run(ctx, tool, action)

    @mcp.tool(annotations=WRITE)
    async def create_outgoing_letter(
        subject: Annotated[str, Field(description="Тема (содержание) письма")],
        correspondent: Annotated[str, Field(description="Адресат-организация: название или id корреспондента")],
        on_behalf_of: OnBehalf,
        ctx: Context,
        signatory: Annotated[str | None, Field(description="Подписант: ФИО или id")] = None,
        in_response_to_document_id: Annotated[int | None, Field(description="Id входящего, на которое это ответ", gt=0)] = None,
        body_text: Body = None,
        note: Annotated[str | None, Field(description="Примечание")] = None,
        confirm_token: Token = None,
    ) -> dict:
        """Создать исходящее письмо (черновик, без регистрации и отправки): «Подготовил» = on_behalf_of, подразделение
        и организация — из его карточки; текст — тело .docx. Двухшаговый: предпросмотр → подтверждение пользователя →
        создание с confirm_token. Регистрацию, подписание и отправку делает сотрудник в RX."""
        return await two_step(
            ctx, "create_outgoing_letter", confirm_token,
            lambda s: s.document_create.plan_outgoing_letter(
                subject, correspondent, _required_person(s, on_behalf_of, "от чьего имени (on_behalf_of)"),
                signatory_id=_person_id(s, signatory), in_response_to_id=in_response_to_document_id,
                body_text=body_text, note=note),
            lambda s, plan: s.document_create.create_outgoing_letter(plan, body_text),
        )

    @mcp.tool(annotations=WRITE)
    async def create_legal_act(
        kind: Annotated[Literal["project", "act"], Field(description="project — проект правового акта, act — правовой акт")],
        subject: Annotated[str, Field(description="Наименование акта («О …»)")],
        on_behalf_of: OnBehalf,
        is_normative: Annotated[bool, Field(description="Нормативный правовой акт")],
        ctx: Context,
        signatory: Annotated[str | None, Field(description="Подписант: ФИО или id")] = None,
        body_text: Body = None,
        note: Annotated[str | None, Field(description="Примечание")] = None,
        confirm_token: Token = None,
    ) -> dict:
        """Создать проект правового акта или правовой акт (модуль «Правовые акты»): «Подготовил» = on_behalf_of,
        текст — тело .docx. На согласование — отдельно send_document_for_approval. Двухшаговый с confirm_token."""
        return await two_step(
            ctx, "create_legal_act", confirm_token,
            lambda s: s.document_create.plan_legal_act(
                kind, subject, _required_person(s, on_behalf_of, "от чьего имени (on_behalf_of)"), is_normative,
                signatory_id=_person_id(s, signatory), body_text=body_text, note=note),
            lambda s, plan: s.document_create.create_legal_act(plan, body_text),
        )

    @mcp.tool(annotations=WRITE)
    async def create_action_item(
        document_id: Annotated[int, Field(description="Id документа-основания (поручение в RX выдаётся по документу)", gt=0)],
        assignee: Annotated[str, Field(description="Ответственный исполнитель: ФИО или id")],
        deadline: Annotated[str, Field(description="Срок, YYYY-MM-DD")],
        text: Annotated[str, Field(description="Текст поручения")],
        on_behalf_of: Annotated[str, Field(description="Кто выдаёт поручение («Выдал»): ФИО или id")],
        ctx: Context,
        co_assignee: Annotated[str | None, Field(description="Соисполнитель (один): ФИО или id")] = None,
        under_control: Annotated[bool, Field(description="Поставить на контроль")] = False,
        supervisor: Annotated[str | None, Field(description="Контролёр (только при under_control): ФИО или id")] = None,
        confirm_token: Token = None,
    ) -> dict:
        """Выдать поручение по документу и запустить: «Выдал» = on_behalf_of, исполнитель, соисполнитель, срок, текст,
        контроль. Если «Выдал» проставить не удалось — поручение не запускается (остаётся черновиком). Двухшаговый с
        confirm_token. Проект резолюции — навык kd-resolution."""
        day = parse_iso_date(deadline, "deadline")
        if day is None:
            raise ToolError("Укажите срок поручения (deadline, YYYY-MM-DD).")
        return await two_step(
            ctx, "create_action_item", confirm_token,
            lambda s: s.document_create.plan_action_item(
                document_id, _required_person(s, assignee, "исполнителя"),
                _required_person(s, on_behalf_of, "кто выдаёт поручение (on_behalf_of)"), day, text,
                co_assignee_id=_person_id(s, co_assignee), under_control=under_control,
                supervisor_id=_person_id(s, supervisor)),
            lambda s, plan: s.document_create.create_action_item(plan),
        )

    @mcp.tool(annotations=WRITE)
    async def send_document_for_review(
        document_id: Annotated[int, Field(description="Id документа (например, входящего письма)", gt=0)],
        addressee: Annotated[str, Field(description="Кто рассматривает (руководитель): ФИО или id")],
        on_behalf_of: OnBehalf,
        ctx: Context,
        text: Annotated[str | None, Field(description="Текст задачи, например проект резолюции")] = None,
        confirm_token: Token = None,
    ) -> dict:
        """Отправить документ на рассмотрение руководителю (задача «Рассмотрение документа»): «Автор» = on_behalf_of.
        Двухшаговый с confirm_token."""
        return await two_step(
            ctx, "send_document_for_review", confirm_token,
            lambda s: s.document_create.plan_review(
                document_id, _required_person(s, addressee, "кто рассматривает"),
                _required_person(s, on_behalf_of, "от чьего имени (on_behalf_of)"), text),
            lambda s, plan: s.document_create.create_review(plan),
        )

    @mcp.tool(annotations=WRITE)
    async def send_document_for_approval(
        document_id: Annotated[int, Field(description="Id документа (письмо, проект акта)", gt=0)],
        on_behalf_of: OnBehalf,
        ctx: Context,
        signatory: Annotated[str | None, Field(description="Подписант: ФИО или id")] = None,
        approvers: Annotated[list[str] | None, Field(description="Дополнительные согласующие: ФИО или id (до 10)")] = None,
        text: Annotated[str | None, Field(description="Текст задачи")] = None,
        confirm_token: Token = None,
    ) -> dict:
        """Отправить документ на согласование по регламенту RX (задача согласования по правилам): «Автор» =
        on_behalf_of, подписант, дополнительные согласующие. Двухшаговый с confirm_token."""
        return await two_step(
            ctx, "send_document_for_approval", confirm_token,
            lambda s: s.document_create.plan_approval(
                document_id, _required_person(s, on_behalf_of, "от чьего имени (on_behalf_of)"),
                _person_id(s, signatory), _people(s, approvers, "согласующего"), text),
            lambda s, plan: s.document_create.create_approval(plan),
        )

    @mcp.tool(annotations=WRITE)
    async def create_task(
        subject: Annotated[str, Field(description="Тема задачи")],
        performers: Annotated[list[str], Field(description="Исполнители: ФИО или id (до 20)")],
        on_behalf_of: OnBehalf,
        ctx: Context,
        deadline: Annotated[str | None, Field(description="Срок, YYYY-MM-DD")] = None,
        text: Annotated[str | None, Field(description="Текст задачи")] = None,
        document_ids: Annotated[list[int] | None, Field(description="Id документов во вложение")] = None,
        importance: Annotated[Literal["normal", "high", "low"], Field(description="Важность")] = "normal",
        confirm_token: Token = None,
    ) -> dict:
        """Создать и запустить простую задачу (задание исполнителям): «Автор» = on_behalf_of, срок, текст, документы.
        Двухшаговый с confirm_token."""
        day = parse_iso_date(deadline, "deadline")
        return await two_step(
            ctx, "create_task", confirm_token,
            lambda s: s.document_create.plan_task(
                subject, _people(s, performers, "исполнителя"),
                _required_person(s, on_behalf_of, "от чьего имени (on_behalf_of)"), day, text, document_ids, importance),
            lambda s, plan: s.document_create.create_task(plan),
        )

    @mcp.tool(annotations=WRITE)
    async def request_action_item_report(
        action_item_id: Annotated[int, Field(description="Id поручения (задачи) из реестра контроля или досье", gt=0)],
        on_behalf_of: Annotated[str, Field(description="Кто запрашивает (выдавший поручение или контролёр): ФИО или id")],
        ctx: Context,
        deadline: Annotated[str | None, Field(description="Срок отчёта, YYYY-MM-DD; пусто — через 2 рабочих дня")] = None,
        text: Annotated[str | None, Field(description="Что именно доложить; пусто — «Прошу представить отчёт о ходе исполнения»")] = None,
        assignee: Annotated[str | None, Field(description="У кого запросить; пусто — ответственный исполнитель поручения")] = None,
        confirm_token: Token = None,
    ) -> dict:
        """Запросить отчёт по поручению (кнопка «Запросить отчёт» в RX): ответственному исполнителю приходит задание
        дать статус. Запрашивать может только выдавший поручение или его контролёр (on_behalf_of). Ответ виден в
        get_control_dossier (report_requests). Двухшаговый с confirm_token."""
        day = parse_iso_date(deadline, "deadline")
        return await two_step(
            ctx, "request_action_item_report", confirm_token,
            lambda s: s.document_create.plan_report_request(
                action_item_id, _required_person(s, on_behalf_of, "кто запрашивает отчёт (on_behalf_of)"),
                deadline=day, text=text, assignee_id=_person_id(s, assignee)),
            lambda s, plan: s.document_create.create_report_request(plan),
        )
