from typing import Annotated, Literal

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from src.mcp_server.envelope import MAX_LIMIT, clamp_limit, list_envelope, to_jsonable
from src.mcp_server.runner import READ_ONLY, ToolRunner
from src.mcp_server.tools.params import parse_period
from src.services.document_search import DOCUMENT_KINDS, DocumentCriteria, counterparty_supported

Limit = Annotated[int, Field(description="Сколько записей вернуть (1–100)", ge=1, le=100)]
IsoDate = Annotated[str | None, Field(description="Дата в формате YYYY-MM-DD")]
DocumentKind = Annotated[
    Literal["any", "incoming_letter", "outgoing_letter", "order", "memo", "contract", "citizen_request"],
    Field(
        description="Вид: any — любой, incoming_letter/outgoing_letter — входящее/исходящее письмо, order — приказ или "
        "распоряжение, memo — служебная записка, contract — договорной документ, citizen_request — обращение гражданина"
    ),
]
CandidatesLimit = Annotated[int, Field(description="Сколько вариантов вернуть (1–20)", ge=1, le=20)]


def register(mcp: MCPServer, runner: ToolRunner) -> None:
    @mcp.tool(annotations=READ_ONLY)
    async def search_documents(
        query: Annotated[str, Field(description="Название, номер или тема документа")],
        ctx: Context,
        limit: Limit = 20,
    ) -> dict:
        """Поиск зарегистрированных документов по названию, номеру или теме."""
        size = clamp_limit(limit)
        return await runner.run(
            ctx, "search_documents", lambda s: list_envelope(s.action_items.search_documents(query, top=size + 1), size)
        )

    @mcp.tool(annotations=READ_ONLY)
    async def find_documents(
        ctx: Context,
        text: Annotated[str | None, Field(description="Слова из названия или темы, как помнит пользователь")] = None,
        kind: DocumentKind = "any",
        counterparty: Annotated[str | None, Field(description="От кого пришёл или кому ушёл документ (организация)")] = None,
        employee: Annotated[str | None, Field(description="ФИО: кто подготовил, подписал или исполнитель")] = None,
        date_from: IsoDate = None,
        date_to: IsoDate = None,
        registration_number: Annotated[str | None, Field(description="Регистрационный номер или его часть")] = None,
        limit: CandidatesLimit = 5,
    ) -> dict:
        """Найти документ по тому, что помнит пользователь (слова, примерный период, контрагент, вид, сотрудник, номер).
        Возвращает несколько вариантов по убыванию score: у каждого match_reasons (почему подошёл) и url карточки.
        relaxed — какие условия пришлось ослабить, если точных совпадений нет: скажи об этом пользователю.
        Ищет по реквизитам (название, тема, номер, контрагент), НЕ по тексту: по содержанию — find_documents_by_content."""
        start, end = parse_period(date_from, date_to)
        criteria = DocumentCriteria(
            text=text,
            kind=kind,
            counterparty=counterparty,
            employee=employee,
            date_from=start,
            date_to=end,
            registration_number=registration_number,
        )
        if not criteria.has_any():
            raise ToolError(
                "Укажите хотя бы один признак: слова из названия, период, контрагента, сотрудника или номер."
            )
        if (counterparty or "").strip() and not counterparty_supported(kind):
            raise ToolError(
                f"Контрагент не применим к виду «{DOCUMENT_KINDS[kind][2]}»: он есть у писем и договорных документов."
            )
        size = clamp_limit(limit, default=5, maximum=20)

        def action(s):
            result = s.document_search.find(criteria, limit=size)
            return {**to_jsonable(result), "returned": len(result.items)}

        return await runner.run(ctx, "find_documents", action)

    @mcp.tool(annotations=READ_ONLY)
    async def get_document(
        document_id: Annotated[int, Field(description="Id документа", gt=0)],
        ctx: Context,
    ) -> dict:
        """Карточка документа по Id: название, тема, регистрационный номер и дата, ссылка."""

        def action(s):
            document = s.action_items.get_document(document_id)
            if document is None:
                raise ToolError(f"Документ {document_id} не найден или у вас нет к нему доступа.")
            return to_jsonable(document)

        return await runner.run(ctx, "get_document", action)

    @mcp.tool(annotations=READ_ONLY)
    async def get_document_text(
        document_id: Annotated[int, Field(description="Id документа", gt=0)],
        ctx: Context,
        max_chars: Annotated[int, Field(description="Сколько символов текста вернуть (1000–50000)", ge=1000, le=50000)] = 20000,
    ) -> dict:
        """Текст последней версии документа (docx, pdf, txt; иначе — из PDF-представления), чтобы пересказать или найти в нём нужное.
        truncated=true — текст обрезан до max_chars. message объясняет, если текста нет (скан, неподдерживаемый формат)."""
        return await runner.run(
            ctx, "get_document_text", lambda s: to_jsonable(s.document_text.get_text(document_id, max_chars=max_chars))
        )

    @mcp.tool(annotations=READ_ONLY)
    async def find_documents_by_content(
        text: Annotated[str, Field(description="Фрагмент или пересказ содержания документа (одно-два предложения и больше)")],
        ctx: Context,
        kind: Annotated[
            Literal["any", "incoming_letter", "outgoing_letter", "order", "memo", "contract", "citizen_request"],
            Field(description="Вид документа, если известен; any — любой"),
        ] = "any",
        date_from: Annotated[str | None, Field(description="Создан с, YYYY-MM-DD (по умолчанию — 180 дней назад)")] = None,
        date_to: Annotated[str | None, Field(description="Создан по, YYYY-MM-DD")] = None,
        limit: Annotated[int, Field(description="Сколько документов вернуть (1–10)", ge=1, le=10)] = 5,
        scan_limit: Annotated[int, Field(description="Сколько документов прочитать и сравнить (10–60)", ge=10, le=60)] = 40,
    ) -> dict:
        """Найти документ по примерному СОДЕРЖАНИЮ (тексту), а не по реквизитам: одним вызовом собирает кандидатов
        (смысловой поиск RX по областям документов, редкие слова и фамилия из образца в названии и теме, свежие
        документы за период), читает их тексты и сравнивает с образцом. items — по убыванию похожести: score,
        confidence, matched_terms, snippet (фрагмент документа), url. Не вызывай повторно с перефразированным текстом —
        если не найдено, сузь период или вид и повтори один раз, либо спроси пользователя. По реквизитам (номер,
        контрагент, вид, автор) — find_documents."""
        start, end = parse_period(date_from, date_to)
        entity_set, _, label = DOCUMENT_KINDS[kind]

        def action(s):
            try:
                return s.content_search.search(text, date_from=start, date_to=end, entity_set=entity_set,
                                               kind_label=None if kind == "any" else label, limit=limit, scan_limit=scan_limit)
            except ValueError as exc:
                raise ToolError(str(exc)) from None

        return await runner.run(ctx, "find_documents_by_content", action)

    @mcp.tool(annotations=READ_ONLY)
    async def get_doc_body_base64(
        document_id: Annotated[int, Field(description="Id документа", gt=0)],
        ctx: Context,
        max_bytes: Annotated[int, Field(description="Лимит размера файла, байт (до 5 МБ)", ge=1024, le=5 * 1024 * 1024)] = 1024 * 1024,
    ) -> dict:
        """Файл последней версии документа целиком в base64 (content_base64, extension, bytes) — чтобы передать
        его в другой тул (например, create_document_body_base64) или в навык. Прочитать или пересказать
        документ — get_document_text, не этот тул: base64 занимает много места в контексте."""
        return await runner.run(ctx, "get_doc_body_base64", lambda s: s.document_text.get_body(document_id, max_bytes=max_bytes))

    @mcp.tool(annotations=READ_ONLY)
    async def get_entity_hyperlink(
        kind: Annotated[
            Literal["document", "task", "action_item", "assignment", "meeting"],
            Field(description="Что: документ, задача, поручение (задача), задание, совещание"),
        ],
        entity_id: Annotated[int, Field(description="Id", gt=0)],
        ctx: Context,
    ) -> dict:
        """Ссылка на карточку в веб-клиенте Directum RX (url) и название — для любого документа, задачи, поручения,
        задания или совещания; проверяет, что запись есть и доступна. Выводи url markdown-ссылкой."""
        return await runner.run(ctx, "get_entity_hyperlink", lambda s: s.document_text.entity_link(kind, entity_id))

    @mcp.tool(annotations=READ_ONLY)
    async def get_template_body_md(
        ctx: Context,
        template: Annotated[str | None, Field(description="Название шаблона или его часть, либо id; пусто — список шаблонов")] = None,
        max_chars: Annotated[int, Field(description="Сколько символов текста вернуть (1000–50000)", ge=1000, le=50000)] = 20000,
    ) -> dict:
        """Шаблоны документов RX (письма, уведомления, НПА): без параметра или при нескольких совпадениях — список
        (id, название); при одном — текст шаблона (body_md) и виды документов, к которым он относится. Документ по
        шаблону — create_document(template=…): RX сам заполнит поля из карточки."""
        return await runner.run(ctx, "get_template_body_md", lambda s: s.document_text.template_text(template, max_chars=max_chars))

    @mcp.tool(annotations=READ_ONLY)
    async def list_documents_by_counterparty(
        counterparty: Annotated[str, Field(description="Название организации или аббревиатура, например «МЦ»")],
        ctx: Context,
        limit: Limit = 20,
    ) -> dict:
        """Документы по контрагенту (входящие, исходящие, договоры). Если организация не найдена — см. поле message."""
        size = clamp_limit(limit)

        def action(s):
            result = s.action_items.search_documents_by_counterparty(counterparty, top=size + 1)
            return {
                "counterparty": to_jsonable(result.counterparty),
                "message": result.message,
                **list_envelope(result.documents, size),
            }

        return await runner.run(ctx, "list_documents_by_counterparty", action)

    @mcp.tool(annotations=READ_ONLY)
    async def list_letters(
        direction: Annotated[Literal["incoming", "outgoing"], Field(description="incoming — входящие, outgoing — исходящие")],
        ctx: Context,
        date_from: IsoDate = None,
        date_to: IsoDate = None,
        limit: Limit = 20,
    ) -> dict:
        """Зарегистрированные входящие или исходящие письма за период (по дате регистрации, новые сначала)."""
        size = clamp_limit(limit)
        return await runner.run(
            ctx,
            "list_letters",
            lambda s: list_envelope(
                s.action_items.list_letters(direction, date_from=date_from, date_to=date_to, top=size + 1), size
            ),
        )

    @mcp.tool(annotations=READ_ONLY)
    async def list_my_meetings(
        ctx: Context,
        days: Annotated[int, Field(description="На сколько дней вперёд (1–90)", ge=1, le=90)] = 7,
    ) -> dict:
        """Мои совещания на ближайшие дни: дата, тема, место, ссылка на карточку."""
        return await runner.run(
            ctx, "list_my_meetings", lambda s: list_envelope(s.meetings.get_my_meetings(days=days), MAX_LIMIT)
        )
