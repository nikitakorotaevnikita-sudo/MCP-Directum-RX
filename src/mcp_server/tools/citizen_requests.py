from typing import Annotated, Literal

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from src.mcp_server.envelope import to_jsonable
from src.mcp_server.runner import READ_ONLY, ToolRunner
from src.mcp_server.tools.params import parse_period
from src.services.citizen_request_analytics import CLASSIFIER_CODE

NOT_FOUND_MESSAGE = "Обращений не найдено. Проверьте номер или ФИО заявителя."


def register(mcp: MCPServer, runner: ToolRunner) -> None:
    @mcp.tool(annotations=READ_ONLY)
    async def get_citizen_request_status(
        ctx: Context,
        registration_number: Annotated[str | None, Field(description="Регистрационный номер обращения, например «13-ОГ»")] = None,
        request_id: Annotated[int | None, Field(description="Id обращения", gt=0)] = None,
        applicant: Annotated[str | None, Field(description="ФИО заявителя или его часть")] = None,
        limit: Annotated[int, Field(description="Сколько обращений вернуть (1–20)", ge=1, le=20)] = 5,
    ) -> dict:
        """Статус обращения гражданина (59-ФЗ): на рассмотрении / рассмотрено / перенаправлено / прекращено,
        срок и сколько дней осталось или на сколько просрочено, продления, исполнитель, вопросы и результат
        их рассмотрения, письмо-ответ со ссылкой. status_text — готовая фраза для пользователя.
        Контакты заявителя (адрес, телефон, email) не возвращаются."""
        if request_id is None and not (registration_number or "").strip() and not (applicant or "").strip():
            raise ToolError("Укажите регистрационный номер, id обращения или ФИО заявителя.")

        def action(s):
            items = s.citizen_requests.find(
                request_id=request_id, registration_number=registration_number, applicant=applicant, limit=limit
            )
            return {
                "items": to_jsonable(items),
                "returned": len(items),
                "message": "" if items else NOT_FOUND_MESSAGE,
            }

        return await runner.run(ctx, "get_citizen_request_status", action)

    @mcp.tool(annotations=READ_ONLY)
    async def get_citizen_requests_analytics(
        ctx: Context,
        level: Annotated[
            Literal["section", "topic", "theme", "question"],
            Field(
                description="Уровень ТОТК: section — раздел, topic — тематика, theme — тема, question — вопрос. "
                "В RX Topic = тематика (2-й уровень), Theme = тема (3-й уровень)"
            ),
        ] = "topic",
        date_from: Annotated[str | None, Field(description="Период регистрации с, YYYY-MM-DD")] = None,
        date_to: Annotated[str | None, Field(description="Период регистрации по, YYYY-MM-DD")] = None,
        classifier_code: Annotated[
            str | None, Field(description="Код ТОТК для сужения, например 0003 или 0003.0008 (см. search_citizen_request_classifier)")
        ] = None,
        top: Annotated[int, Field(description="Сколько групп вернуть (1–50)", ge=1, le=50)] = 10,
    ) -> dict:
        """Обращения граждан по общероссийскому тематическому классификатору (ТОТК): сколько обращений (requests)
        и сколько вопросов (questions) в каждом разделе / тематике / теме / вопросе за период. В одном обращении
        может быть несколько вопросов — не путай эти числа. Также: всего обращений, без классификации, итоги
        рассмотрения (review_results). Используй для «сколько обращений/вопросов по тематикам», а не odata_*."""
        start, end = parse_period(date_from, date_to)
        code = (classifier_code or "").strip() or None
        if code is not None and not CLASSIFIER_CODE.match(code):
            raise ToolError("Код классификатора — цифры по уровням, например 0003 или 0003.0008. Найти код: search_citizen_request_classifier.")
        return await runner.run(
            ctx,
            "get_citizen_requests_analytics",
            lambda s: to_jsonable(
                s.citizen_request_analytics.analytics(level=level, date_from=start, date_to=end, classifier_code=code, top=top)
            ),
        )

    @mcp.tool(annotations=READ_ONLY)
    async def search_citizen_request_classifier(
        query: Annotated[str, Field(description="Слово из названия: «дорог», «ЖКХ», «пенси»")],
        ctx: Context,
    ) -> dict:
        """Поиск по тематическому классификатору обращений (ТОТК): разделы, тематики, темы и вопросы с кодами.
        Код передавай в get_citizen_requests_analytics(classifier_code=...)."""
        if not query.strip():
            raise ToolError("Укажите слово для поиска по классификатору.")
        return await runner.run(
            ctx,
            "search_citizen_request_classifier",
            lambda s: {"items": to_jsonable(s.citizen_request_analytics.search_classifier(query))},
        )
