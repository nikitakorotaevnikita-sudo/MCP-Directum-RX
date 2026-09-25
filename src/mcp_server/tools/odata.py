import re
from typing import Annotated

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from src.mcp_server.envelope import clamp_limit, list_envelope
from src.mcp_server.odata_meta import (
    NAV_DENY_SUBSTRINGS,
    EntityInfo,
    MetadataCache,
    has_marker,
    is_denied,
    is_denied_navigation_type,
    is_denied_type,
    is_sensitive_property,
    strip_sensitive,
)
from src.mcp_server.resources import DomainGuide
from src.mcp_server.runner import READ_ONLY, ToolRunner

GENERIC_MAX_TOP = 50
MAX_FILTER_LENGTH = 1000
FORBIDDEN_FILTER_CHARS = ("&", "?", "#")
SIMPLE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
# Перекрывающийся поиск (lookahead): в A/B/C проверяются пары A/B и B/C, включая последний сегмент.
FILTER_PATH_SEGMENT = re.compile(r"(?<![A-Za-z0-9_])(?=([A-Za-z_][A-Za-z0-9_]*)\s*/\s*([A-Za-z_][A-Za-z0-9_]*))")
FILTER_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
FILTER_STRING_LITERAL = re.compile(r"'(?:[^']|'')*'")

EntitySet = Annotated[str, Field(description="Имя набора данных Directum RX, например IRequests")]
Filter = Annotated[str, Field(description="Обязательный OData $filter, например Status eq 'InProcess'")]

DENIED_MESSAGE = "Набор данных «{entity_set}» недоступен. Найди нужный набор через odata_list_domains и справочники drx://domains/*."


def resolve_entity(entities: dict[str, EntityInfo], entity_set: str) -> EntityInfo:
    info = entities.get(entity_set)
    if is_denied(entity_set) or info is None or is_denied_type(info.entity_type):
        raise ToolError(DENIED_MESSAGE.format(entity_set=entity_set))
    return info


def check_filter(expression: str, info: EntityInfo) -> str:
    text = (expression or "").strip()
    if not text:
        raise ToolError("Нужен фильтр: Directum отклоняет запросы без $filter. Пример: Status eq 'InProcess'.")
    if len(text) > MAX_FILTER_LENGTH:
        raise ToolError(f"Фильтр длиннее {MAX_FILTER_LENGTH} символов — упрости условие.")
    if any(char in text for char in FORBIDDEN_FILTER_CHARS):
        raise ToolError("В фильтре нельзя использовать символы &, ? и #.")
    for left, right in FILTER_PATH_SEGMENT.findall(text):
        for segment in (left, right):
            if has_marker(segment, NAV_DENY_SUBSTRINGS):
                raise ToolError(f"Фильтр обращается к закрытым данным («{segment}»).")
        nav_type = info.navigation.get(left)
        if nav_type and is_denied_navigation_type(nav_type):
            raise ToolError(f"Фильтр обращается к закрытым данным («{left}»).")
    for identifier in FILTER_IDENTIFIER.findall(FILTER_STRING_LITERAL.sub("''", text)):
        if is_sensitive_property(identifier):
            raise ToolError(f"Фильтр обращается к закрытым данным («{identifier}»).")
    return text


def safe_select(info: EntityInfo) -> str:
    """Явный $select по несекретным полям: без него Directum вернул бы все свойства, включая секреты."""
    return ",".join(info.properties)


def check_fields(csv: str, allowed: dict[str, str], label: str) -> str:
    names = [part.strip() for part in csv.split(",") if part.strip()]
    unknown = [name for name in names if name not in allowed]
    if unknown:
        raise ToolError(
            f"Неизвестные поля в {label}: {', '.join(unknown)}. Доступные: {', '.join(sorted(allowed)[:80])}."
        )
    return ",".join(names)


def check_orderby(csv: str, allowed: dict[str, str]) -> str:
    parts = []
    for part in (piece.strip() for piece in csv.split(",")):
        if not part:
            continue
        tokens = part.split()
        valid_direction = len(tokens) == 1 or (len(tokens) == 2 and tokens[1].lower() in ("asc", "desc"))
        if tokens[0] not in allowed or not valid_direction:
            raise ToolError(
                f"Некорректная сортировка «{part}». Формат: Поле [asc|desc]. Доступные поля: {', '.join(sorted(allowed)[:80])}."
            )
        parts.append(part)
    return ",".join(parts)


def check_expand(csv: str, navigation: dict[str, str]) -> str:
    names = [part.strip() for part in csv.split(",") if part.strip()]
    if any(not SIMPLE_NAME.match(name) for name in names):
        raise ToolError("В expand разрешены только имена навигационных свойств через запятую, без вложенных параметров.")
    checked = check_fields(",".join(names), navigation, "expand")
    for name in names:
        if is_denied_navigation_type(navigation.get(name, "")):
            raise ToolError(f"Навигация «{name}» ведёт к закрытым данным и недоступна.")
    return checked


def register(mcp: MCPServer, runner: ToolRunner, metadata: MetadataCache, guides: dict[str, DomainGuide]) -> None:
    @mcp.tool(annotations=READ_ONLY)
    async def odata_list_domains() -> dict:
        """Домены Directum RX и справочники к ним: какие наборы данных где искать. Подробности — в ресурсе drx://domains/<name>."""
        return {
            "domains": [
                {"name": guide.name, "description": guide.description, "resource": f"drx://domains/{guide.name}"}
                for guide in guides.values()
            ]
        }

    @mcp.tool(annotations=READ_ONLY)
    async def odata_describe_entity(entity_set: EntitySet, ctx: Context) -> dict:
        """Поля и навигационные свойства набора данных. Вызывай перед odata_query, чтобы не гадать с именами полей."""

        def action(s):
            info = resolve_entity(metadata.get(s.client), entity_set)
            return {
                "entity_set": info.entity_set,
                "properties": [{"name": name, "type": kind} for name, kind in sorted(info.properties.items())],
                "navigation": [{"name": name, "type": kind} for name, kind in sorted(info.navigation.items())],
            }

        return await runner.run(ctx, "odata_describe_entity", action)

    @mcp.tool(annotations=READ_ONLY)
    async def odata_query(
        entity_set: EntitySet,
        filter: Filter,
        ctx: Context,
        select: Annotated[str | None, Field(description="Поля через запятую")] = None,
        expand: Annotated[str | None, Field(description="Навигационные свойства через запятую")] = None,
        orderby: Annotated[str | None, Field(description="Сортировка: Поле [asc|desc]")] = None,
        top: Annotated[int, Field(description="Сколько записей (1–50)", ge=1, le=GENERIC_MAX_TOP)] = 20,
    ) -> dict:
        """Универсальный запрос чтения к разрешённому набору данных Directum RX. Всегда указывай filter; поля смотри через odata_describe_entity."""
        size = clamp_limit(top, maximum=GENERIC_MAX_TOP)

        def action(s):
            info = resolve_entity(metadata.get(s.client), entity_set)
            rows = s.client.query(
                entity_set,
                filter_=check_filter(filter, info),
                select=check_fields(select, info.properties, "select") if select else safe_select(info),
                expand=check_expand(expand, info.navigation) if expand else None,
                orderby=check_orderby(orderby, info.properties) if orderby else None,
                top=size + 1,
            )
            return list_envelope(strip_sensitive(rows), size)

        return await runner.run(ctx, "odata_query", action)

    @mcp.tool(annotations=READ_ONLY)
    async def odata_count(entity_set: EntitySet, filter: Filter, ctx: Context) -> dict:
        """Количество записей набора данных по фильтру."""

        def action(s):
            info = resolve_entity(metadata.get(s.client), entity_set)
            expression = check_filter(filter, info)
            return {"entity_set": entity_set, "filter": expression, "count": s.client.count(entity_set, filter_=expression)}

        return await runner.run(ctx, "odata_count", action)

    @mcp.tool(annotations=READ_ONLY)
    async def odata_get(
        entity_set: EntitySet,
        record_id: Annotated[int, Field(description="Id записи", gt=0)],
        ctx: Context,
        expand: Annotated[str | None, Field(description="Навигационные свойства через запятую")] = None,
    ) -> dict:
        """Одна запись набора данных по Id."""

        def action(s):
            info = resolve_entity(metadata.get(s.client), entity_set)
            path = f"{entity_set}({record_id})?$select={safe_select(info)}"
            if expand:
                path += "&$expand=" + check_expand(expand, info.navigation)
            return strip_sensitive(s.client.get_one(path))

        return await runner.run(ctx, "odata_get", action)
