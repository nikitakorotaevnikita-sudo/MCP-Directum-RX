from typing import Annotated, Literal

from mcp.server.mcpserver import Context, MCPServer
from pydantic import Field

from src.mcp_server.envelope import to_jsonable
from src.mcp_server.runner import READ_ONLY, ToolRunner
from src.mcp_server.tools.employee_items import resolve_employee
from src.mcp_server.tools.params import parse_period

ApprovalState = Literal["OnApproval", "OnRework", "PendingSign", "Signed", "Aborted", "Sending", "Publication", "none"]
IsoDate = Annotated[str | None, Field(description="Дата регистрации, YYYY-MM-DD")]


def register(mcp: MCPServer, runner: ToolRunner) -> None:
    @mcp.tool(annotations=READ_ONLY)
    async def find_legal_acts(
        ctx: Context,
        text: Annotated[str | None, Field(description="Слова из названия или темы акта")] = None,
        kind: Annotated[
            Literal["any", "project", "act"], Field(description="project — проект правового акта, act — принятый правовой акт")
        ] = "any",
        normative: Annotated[bool | None, Field(description="true — только нормативные, false — только ненормативные")] = None,
        published: Annotated[bool | None, Field(description="true — только опубликованные, false — неопубликованные")] = None,
        approval_state: Annotated[
            ApprovalState | None,
            Field(
                description="Этап: OnApproval — на согласовании, OnRework — на доработке, PendingSign — на подписании, "
                "Signed — подписан, Aborted — прекращено, Sending — рассылка, Publication — публикация, none — не отправлялся"
            ),
        ] = None,
        date_from: IsoDate = None,
        date_to: IsoDate = None,
        limit: Annotated[int, Field(description="Сколько вернуть (1–50)", ge=1, le=50)] = 20,
    ) -> dict:
        """Поиск нормативных правовых актов и их проектов в Directum RX (модуль «Правовые акты»): по словам, виду,
        нормативности, публикации, этапу согласования и периоду регистрации. У каждого: номер, дата, этап
        (approval_state_label), опубликован ли, номер принятого акта у проекта, ссылка. Федеральных законов в RX нет."""
        start, end = parse_period(date_from, date_to)
        return await runner.run(
            ctx,
            "find_legal_acts",
            lambda s: to_jsonable(
                s.legal_acts.find(
                    text=text, kind=kind, normative=normative, published=published, approval_state=approval_state,
                    date_from=start, date_to=end, limit=limit,
                )
            ),
        )

    @mcp.tool(annotations=READ_ONLY)
    async def get_legal_act(
        act_id: Annotated[int, Field(description="Id правового акта или проекта (из find_legal_acts)", gt=0)],
        ctx: Context,
    ) -> dict:
        """Карточка правового акта: реквизиты, нормативный ли, публикация, кто подготовил и подписант; связь
        проект ↔ принятый акт (adopted_act / projects, по ProjectId); ход согласования — задачи, куратор,
        согласующие и у кого сейчас (current_steps: действие, исполнитель, сколько дней ждёт, просрочено ли).
        Связанные документы из панели «Связи» RX: related (с group — «Заключения и отчеты», «Прочие», «Отменен на
        основании»…) и addenda (приложения) — это подтверждённые связи. possibly_related и relations_note бывают только
        на стенде без функций чтения связей: тогда связь НЕ подтверждена, так и говори. Текст — get_document_text."""
        return await runner.run(ctx, "get_legal_act", lambda s: to_jsonable(s.legal_acts.get(act_id)))

    @mcp.tool(annotations=READ_ONLY)
    async def list_legal_acts_on_approval(
        ctx: Context,
        curator: Annotated[str | None, Field(description="ФИО или id куратора; пусто — все")] = None,
        min_days_waiting: Annotated[
            int | None, Field(description="Только где текущий шаг ждёт не меньше N дней", ge=0, le=3650)
        ] = None,
        limit: Annotated[int, Field(description="Сколько вернуть (1–50)", ge=1, le=50)] = 20,
    ) -> dict:
        """Проекты правовых актов на согласовании сейчас и где они застряли: по каждой задаче — документ, куратор,
        согласующие, сколько дней идёт согласование и текущие шаги (у кого, какое действие, сколько дней ждёт,
        просрочено ли). Сначала самые «застрявшие»."""

        def action(s):
            curator_id = resolve_employee(s.action_items, curator).id if (curator or "").strip() else None
            return to_jsonable(s.legal_acts.on_approval(curator_id=curator_id, min_days_waiting=min_days_waiting, limit=limit))

        return await runner.run(ctx, "list_legal_acts_on_approval", action)
