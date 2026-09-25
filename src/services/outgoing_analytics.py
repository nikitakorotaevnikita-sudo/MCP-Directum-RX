from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

DeadlineParser = Callable[[str], datetime | None]


def _as_aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def parse_deadline_value(raw: Any, fallback: DeadlineParser | None = None) -> datetime | None:
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return _as_aware(raw)
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        parsed = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    except ValueError:
        parsed = fallback(raw) if fallback is not None else None
    return _as_aware(parsed) if parsed is not None else None


def categorize_outgoing(
    items: list[dict[str, Any]],
    now: datetime | None = None,
    fallback: DeadlineParser | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Раскладывает поручения по срокам: в работе / срок в ближайшие сутки / просрочено."""
    current = now or datetime.now(timezone.utc)
    due_soon_limit = current + timedelta(days=1)
    groups: dict[str, list[dict[str, Any]]] = {"work": [], "due_soon": [], "overdue": []}
    for item in items:
        deadline = parse_deadline_value(item.get("deadline"), fallback)
        if deadline is not None and deadline < current:
            groups["overdue"].append(item)
        elif deadline is not None and deadline <= due_soon_limit:
            groups["due_soon"].append(item)
        else:
            groups["work"].append(item)
    return groups
