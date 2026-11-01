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
ProcessTask = Annotated[int | None, Field(
    description="Id задачи RX, в рамках которой создаётся документ (например, процесс «Создать документ»): документ "
                "прикладывается к ней, а права выдаются «Подготовил» и автору задачи. Передавайте id задачи, не задания",
    gt=0)]

NEXT_STEP = ("Это предпросмотр: пока ничего не создано, но создать МОЖНО. Покажите пользователю, что будет создано "
             "(что, от чьего имени, кому, срок) и спросите «Создать?». После явного «да» вызовите этот же тул с теми же "
             "параметрами и confirm_token — тогда документ или задача появятся в RX.")
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
            who = plan.get("prepared_by") or plan.get("assigned_by") or plan.get("author") or plan.get("requested_by") or {}
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
        task_id: ProcessTask = None,
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
                body_text=body_text, note=note, process_task_id=task_id),
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
        task_id: ProcessTask = None,
        confirm_token: Token = None,
    ) -> dict:
        """Создать проект правового акта или правовой акт (модуль «Правовые акты»): «Подготовил» = on_behalf_of,
        текст — тело .docx. На согласование — отдельно send_document_for_approval. Двухшаговый с confirm_token."""
        return await two_step(
            ctx, "create_legal_act", confirm_token,
            lambda s: s.document_create.plan_legal_act(
                kind, subject, _required_person(s, on_behalf_of, "от чьего имени (on_behalf_of)"), is_normative,
                signatory_id=_person_id(s, signatory), body_text=body_text, note=note, process_task_id=task_id),
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

    @mcp.tool(annotations=WRITE)
    async def create_document(
        document_type: Annotated[
            Literal["outgoing_letter", "incoming_letter", "memo", "order", "directive", "simple_document", "addendum", "legal_act"],
            Field(description="Тип: исходящее/входящее письмо, служебная записка, приказ, распоряжение, простой документ, "
                  "приложение, правовой акт или проект"),
        ],
        subject: Annotated[str, Field(description="Тема (содержание) документа")],
        on_behalf_of: OnBehalf,
        ctx: Context,
        document_kind: Annotated[str | None, Field(description="Вид документа, как в RX («Исходящее письмо», «Приказ»…). "
                                                   "Пусто — вид по умолчанию для типа (у приказа и др. — обязателен)")] = None,
        correspondent: Annotated[str | None, Field(description="Корреспондент (для писем): организация, название или id")] = None,
        signatory: Annotated[str | None, Field(description="Подписант: ФИО или id")] = None,
        addressee: Annotated[str | None, Field(description="Адресат-сотрудник (служебная записка, входящее): ФИО или id")] = None,
        in_response_to_document_id: Annotated[int | None, Field(description="В ответ на документ (исходящее письмо)", gt=0)] = None,
        leading_document_id: Annotated[int | None, Field(description="Ведущий документ (для приложения)", gt=0)] = None,
        is_normative: Annotated[bool | None, Field(description="Нормативный (для правового акта)")] = None,
        extra_properties: Annotated[dict[str, str | int | float | bool] | None, Field(
            description="Другие простые свойства карточки по их имени в RX (например PageCount, DocumentDate). "
                        "Ссылки на сотрудников и организации — только параметрами выше")] = None,
        body_text: Body = None,
        body_markdown: Annotated[bool, Field(description="body_text — markdown (# заголовки, - списки, **жирный**)")] = False,
        template: Annotated[str | None, Field(description="Шаблон RX (название или id) — тело по шаблону вместо body_text")] = None,
        note: Annotated[str | None, Field(description="Примечание")] = None,
        task_id: ProcessTask = None,
        confirm_token: Token = None,
    ) -> dict:
        """Создать документ любого из поддержанных типов (черновик, без регистрации): «Подготовил» = on_behalf_of,
        подразделение и организация — из его карточки, вид, тема, реквизиты; тело — из текста (.docx) или по шаблону
        RX. Запрос пришёл из задачи RX (процесс «Создать документ») — передайте её id в task_id: документ приложится
        к задаче и автор задачи получит к нему доступ. Двухшаговый: предпросмотр → подтверждение пользователя →
        создание с confirm_token."""
        return await two_step(
            ctx, "create_document", confirm_token,
            lambda s: s.document_create.plan_document(
                document_type, subject, _required_person(s, on_behalf_of, "от чьего имени (on_behalf_of)"),
                document_kind=document_kind, correspondent=correspondent, signatory_id=_person_id(s, signatory),
                addressee_id=_person_id(s, addressee), in_response_to_id=in_response_to_document_id,
                leading_document_id=leading_document_id, is_normative=is_normative, extra_properties=extra_properties,
                body_text=body_text, body_markdown=body_markdown, template=template, note=note,
                process_task_id=task_id),
            lambda s, plan: s.document_create.create_document(plan, body_text),
        )

    def _requested_by(s, value: str) -> dict:
        person_id = _required_person(s, value, "от чьего имени (on_behalf_of)")
        return s.document_create._person(person_id, receives_work=False)

    @mcp.tool(annotations=WRITE)
    async def create_document_body(
        document_id: Annotated[int, Field(description="Id документа", gt=0)],
        text: Annotated[str, Field(description="Текст новой версии")],
        on_behalf_of: OnBehalf,
        ctx: Context,
        markdown: Annotated[bool, Field(description="text — markdown (# заголовки, - списки, **жирный**)")] = False,
        confirm_token: Token = None,
    ) -> dict:
        """Добавить документу новую версию (.docx) из текста или markdown. Прежние версии сохраняются; on_behalf_of
        — по чьей просьбе (пишется в журнал). Двухшаговый с confirm_token."""
        return await two_step(
            ctx, "create_document_body", confirm_token,
            lambda s: {**s.document_create.plan_body(document_id, body_text=text, markdown=markdown),
                       "requested_by": _requested_by(s, on_behalf_of)},
            lambda s, plan: s.document_create.create_body(plan, body_text=text),
        )

    @mcp.tool(annotations=WRITE)
    async def create_document_body_base64(
        document_id: Annotated[int, Field(description="Id документа", gt=0)],
        content_base64: Annotated[str, Field(description="Файл в base64 (можно data:…;base64,…), до 20 МБ")],
        extension: Annotated[str, Field(description="Расширение файла: pdf, docx, xlsx, odt, rtf, txt, png, jpg…")],
        on_behalf_of: OnBehalf,
        ctx: Context,
        confirm_token: Token = None,
    ) -> dict:
        """Добавить документу новую версию из файла (base64), например файла из get_doc_body_base64 другого документа.
        Содержимое проверяется на соответствие расширению. Двухшаговый с confirm_token."""
        return await two_step(
            ctx, "create_document_body_base64", confirm_token,
            lambda s: {**s.document_create.plan_body(document_id, content_base64=content_base64, extension=extension),
                       "requested_by": _requested_by(s, on_behalf_of)},
            lambda s, plan: s.document_create.create_body(plan, content_base64=content_base64),
        )

    @mcp.tool(annotations=WRITE)
    async def create_action_items(
        document_id: Annotated[int, Field(description="Id документа-основания", gt=0)],
        on_behalf_of: Annotated[str, Field(description="Кто выдаёт поручения («Выдал»): ФИО или id")],
        items: Annotated[list[dict[str, Any]], Field(description=(
            "Поручения (до 20): каждое — assignee (ФИО или id), deadline (YYYY-MM-DD), text; по желанию co_assignee, "
            "under_control (true/false), supervisor"))],
        ctx: Context,
        confirm_token: Token = None,
    ) -> dict:
        """Выдать пакет поручений по одному документу одним подтверждением — вся резолюция (например, из навыка
        kd-resolution). Каждое поручение создаётся и запускается отдельно; итог — по каждому (created, started,
        failed, items). Двухшаговый с confirm_token."""

        def resolve(s) -> list[dict[str, Any]]:
            resolved = []
            for index, item in enumerate(items, start=1):
                if not isinstance(item, dict):
                    raise ToolError(f"Поручение {index}: ожидается объект с assignee, deadline, text.")
                day = parse_iso_date(str(item.get("deadline") or ""), f"Поручение {index}: deadline")
                if day is None:
                    raise ToolError(f"Поручение {index}: укажите срок (deadline, YYYY-MM-DD).")
                resolved.append({
                    "assignee_id": _required_person(s, str(item.get("assignee") or ""), f"исполнителя поручения {index}"),
                    "deadline": day,
                    "text": str(item.get("text") or ""),
                    "co_assignee_id": _person_id(s, item.get("co_assignee")),
                    "under_control": bool(item.get("under_control")),
                    "supervisor_id": _person_id(s, item.get("supervisor")),
                })
            return resolved

        return await two_step(
            ctx, "create_action_items", confirm_token,
            lambda s: s.document_create.plan_action_items(
                document_id, _required_person(s, on_behalf_of, "кто выдаёт поручения (on_behalf_of)"), resolve(s)),
            lambda s, plan: s.document_create.create_action_items(plan),
        )
