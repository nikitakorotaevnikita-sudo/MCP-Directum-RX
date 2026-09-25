from typing import Any

DEFAULT_LIMIT = 20
MAX_LIMIT = 100


def clamp_limit(limit: int | None, default: int = DEFAULT_LIMIT, maximum: int = MAX_LIMIT) -> int:
    if limit is None:
        return default
    return max(1, min(int(limit), maximum))


def to_jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {key: to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(item) for item in value]
    return value


def list_envelope(items: list[Any], limit: int, total: int | None = None) -> dict[str, Any]:
    """Единый формат списков. Без total сервисы запрашивают limit+1 записей, чтобы понять, есть ли ещё."""
    visible = [to_jsonable(item) for item in items[:limit]]
    has_more = len(items) > limit
    if total is None:
        known_total = None if has_more else len(visible)
        truncated = has_more
    else:
        known_total = total
        truncated = has_more or total > len(visible)
    return {"items": visible, "total": known_total, "returned": len(visible), "truncated": truncated}
