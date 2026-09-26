from typing import Annotated

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from src.mcp_server.envelope import to_jsonable
from src.mcp_server.runner import READ_ONLY, ToolRunner

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
