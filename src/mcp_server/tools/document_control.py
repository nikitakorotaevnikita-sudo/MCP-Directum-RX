from typing import Annotated, Literal

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from src.mcp_server.envelope import to_jsonable
from src.mcp_server.runner import READ_ONLY, ToolRunner
from src.mcp_server.tools.employee_items import resolve_employee
from src.mcp_server.tools.params import parse_period
from src.services.document_control import ControlFilters

Person = Annotated[str | None, Field(description="ФИО или id сотрудника; «me» — текущий пользователь")]
ME = {"me", "я", "мне", "мои"}
ME_UNDER_SERVICE_ACCOUNT = (
    "mcpOGV работает под служебной учётной записью, поэтому «me» — это она, а не пользователь чата. "
    "Спросите у пользователя его ФИО и передайте его вместо «me»."
)


def _person_id(services, value: str | None) -> int | None:
    text = (value or "").strip()
    if not text:
        return None
    if text.casefold() in ME:
        if getattr(services, "service_account", False):
            raise ToolError(ME_UNDER_SERVICE_ACCOUNT)
        return services.current_user.get_current_user().id
    return resolve_employee(services.action_items, text).id


def register(mcp: MCPServer, runner: ToolRunner) -> None:
    @mcp.tool(annotations=READ_ONLY)
    async def list_controlled_action_items(
        ctx: Context,
        state: Annotated[
            Literal["in_work", "overdue", "on_acceptance", "completed", "all"],
            Field(description="in_work — в работе; overdue — срок нарушен; on_acceptance — исполнено и ждёт приёмки "
                  "контролёром; completed — снято с контроля (завершено); all — все"),
        ] = "in_work",
        due: Annotated[
            Literal["today", "tomorrow", "next_3_days", "this_week", "next_week", "this_month"] | None,
            Field(description="Окно контрольного срока (по поясу стенда); не сочетается с date_from/date_to"),
        ] = None,
        date_from: Annotated[str | None, Field(description="Срок с, YYYY-MM-DD")] = None,
        date_to: Annotated[str | None, Field(description="Срок по, YYYY-MM-DD")] = None,
        supervisor: Person = None,
        assigned_by: Annotated[str | None, Field(description="Кто выдал поручение: ФИО, id или «me»")] = None,
        assignee: Annotated[str | None, Field(description="Ответственный исполнитель: ФИО, id или «me»")] = None,
        co_assignee: Annotated[str | None, Field(description="Соисполнитель: ФИО, id или «me»")] = None,
        limit: Annotated[int, Field(description="Сколько записей вернуть (1–100)", ge=1, le=100)] = 30,
    ) -> dict:
        """Реестр поручений на контроле (признак «На контроле» у поручения; отдельных контрольных карт нет).
        Фильтры: состояние, окно срока, контролёр, кто выдал, исполнитель, соисполнитель. По возрастанию срока.
        total — всего по фильтру, overdue_total — просрочено, on_acceptance_total — ждут приёмки контролёром.
        У каждой записи days_left / days_overdue, контролёр, исполнитель, ссылка; id — это id задачи (для
        get_control_dossier)."""
        start, end = parse_period(date_from, date_to)
        if due and (start or end):
            raise ToolError("Укажите либо окно срока due, либо период date_from/date_to.")

        def action(s):
            filters = ControlFilters(
                state=state,
                supervisor_id=_person_id(s, supervisor),
                assigned_by_id=_person_id(s, assigned_by),
                assignee_id=_person_id(s, assignee),
                co_assignee_id=_person_id(s, co_assignee),
                due=due,
                date_from=start,
                date_to=end,
            )
            return to_jsonable(s.document_control.list_controlled(filters, limit=limit))

        return await runner.run(ctx, "list_controlled_action_items", action)

    @mcp.tool(annotations=READ_ONLY)
    async def get_control_dossier(
        action_item_id: Annotated[int, Field(description="Id поручения (задачи) из реестра или id задания", gt=0)],
        ctx: Context,
        entity_type: Annotated[
            Literal["action_item_task", "action_item_assignment"] | None,
            Field(description="Что за id: задача (из list_controlled_action_items) или задание (из списка поручений "
                  "сотрудника). У задач и заданий пересекаются номера — передавай, если известно"),
        ] = "action_item_task",
    ) -> dict:
        """Досье поручения на контроле — вместо контрольной карты: текст и пункты поручения, соисполнители; дерево
        исполнения по всем уровням (исполнитель, срок, состояние, просрочка, отчёт); история продлений срока (кто
        просил, кто решил, старый и новый срок, сдвиг в днях, результат); приёмка контролёром; переписка; документы
        (основание и вложения со ссылками). summary — итоги: узлов в работе и просрочено, продлений и сдвиг,
        ждёт ли приёмки."""
        kind = {"action_item_task": "task", "action_item_assignment": "assignment"}.get(entity_type or "")
        return await runner.run(
            ctx,
            "get_control_dossier",
            lambda s: to_jsonable(s.document_control.dossier(action_item_id, kind=kind)),
        )

    @mcp.tool(annotations=READ_ONLY)
    async def get_resolution_history(
        author: Annotated[str, Field(description="Автор резолюций (кто выдаёт поручения): ФИО, id или «me»")],
        ctx: Context,
        date_from: Annotated[str | None, Field(description="Созданы с, YYYY-MM-DD (по умолчанию — 2 года назад)")] = None,
        date_to: Annotated[str | None, Field(description="Созданы по, YYYY-MM-DD")] = None,
        limit: Annotated[int, Field(description="Сколько поручений вернуть (1–2000), новые первыми", ge=1, le=2000)] = 1000,
    ) -> dict:
        """История резолюций автора — для проекта резолюции по новому входящему «как обычно у этого руководителя».
        Поручения верхнего уровня, которые автор выдавал: тема, текст поручения, документ-основание, исполнитель,
        соисполнители, исполнители пунктов, на контроле ли и кто контролёр, срок в днях от создания. Итоги:
        under_control_share (доля на контроле), top_assignees (частые исполнители). Ответ целиком передавай
        скрипту навыка kd-resolution."""
        start, end = parse_period(date_from, date_to)

        def action(s):
            author_id = _person_id(s, author)
            if author_id is None:
                raise ToolError("Укажите автора резолюций: ФИО, id или «me».")
            return to_jsonable(s.resolution_history.history(author_id, date_from=start, date_to=end, limit=limit))

        return await runner.run(ctx, "get_resolution_history", action)
