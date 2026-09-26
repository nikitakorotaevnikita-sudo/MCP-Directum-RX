from datetime import date

from mcp.server.mcpserver.exceptions import ToolError


def parse_iso_date(value: str | None, label: str) -> date | None:
    if value is None or not value.strip():
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        raise ToolError(f"{label}: ожидается дата в формате YYYY-MM-DD, получено «{value}».") from None


def parse_period(date_from: str | None, date_to: str | None) -> tuple[date | None, date | None]:
    start = parse_iso_date(date_from, "date_from")
    end = parse_iso_date(date_to, "date_to")
    if start and end and start > end:
        raise ToolError("date_from позже date_to.")
    return start, end
