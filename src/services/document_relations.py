"""Связи документов — как панель «Связи» карточки RX (функции DocumentProcessing сервиса интеграции).

GetDocumentRelations / GetDocumentsRelations отдают связи в обе стороны с заголовком группы, GetRelationTypes —
справочник типов. Особенности стенда: документ без связей — 204 без тела; неизвестный Id или тип связи — 400 с
текстом; в пакете один ненайденный Id роняет весь запрос, поэтому такие Id убираются и запрос повторяется.
"""

import re
import time
from collections.abc import Callable
from datetime import datetime, tzinfo
from typing import Any

from src.services.directum_client import DirectumError

MODULE = "DocumentProcessing"
DIRECTIONS = {"both": "Both", "from": "From", "to": "To"}
MAX_BATCH = 50
MAX_GRAPH_NODES = 50
TYPES_TTL_SECONDS = 3600
NOT_FOUND = re.compile(r"Не найден документ с ИД = (\d+)")
UNKNOWN_TYPE = re.compile(r"Не найден тип связи")

# Справочник типов один на стенд и меняется редко — держим между запросами.
_types_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}


def _parse(value: Any) -> datetime | None:
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


class RelationsUnavailable(DirectumError):
    """На стенде нет функций чтения связей (старая версия решения)."""


class DocumentRelationsService:
    def __init__(self, client: Any, tz: tzinfo | None = None, clock: Callable[[], float] = time.monotonic):
        self.client = client
        self.tz = tz or datetime.now().astimezone().tzinfo
        self._clock = clock

    # --- справочник ---

    def relation_types(self) -> list[dict[str, Any]]:
        key = getattr(self.client, "base_url", "") or "default"
        cached = _types_cache.get(key)
        if cached and self._clock() - cached[0] < TYPES_TTL_SECONDS:
            return cached[1]
        rows = self._call("GetRelationTypes", {}) or []
        types = [
            {
                "name": row.get("Name"),
                "title": row.get("Description"),
                "source_group": row.get("SourceTitle"),
                "target_group": row.get("TargetTitle"),
            }
            for row in rows
            if row.get("Name")
        ]
        _types_cache[key] = (self._clock(), types)
        return types

    def resolve_names(self, relation: str | list[str] | None) -> list[str]:
        """Системные имена по тому, как назвал пользователь: имя типа, его название или заголовок группы
        («Заключения и отчеты», «Приложения», «Ответное письмо»)."""
        if not relation:
            return []
        wanted = [relation] if isinstance(relation, str) else list(relation)
        types = self.relation_types()
        names: list[str] = []
        for item in wanted:
            for part in str(item).split(","):
                key = part.strip().lower().replace("ё", "е")
                if not key:
                    continue
                match = next(
                    (
                        t["name"]
                        for t in types
                        if key in {(t.get(f) or "").lower().replace("ё", "е") for f in ("name", "title", "source_group", "target_group")}
                    ),
                    None,
                )
                if match is None:
                    known = ", ".join(f"{t['name']} ({t['title']})" for t in types)
                    raise ValueError(f"Неизвестный тип связи «{part.strip()}». Допустимые: {known}.")
                if match not in names:
                    names.append(match)
        return names

    # --- связи ---

    def get(self, document_id: int, relation: str | list[str] | None = None, direction: str = "both",
            depth: int = 1) -> dict[str, Any]:
        names = self.resolve_names(relation)
        rx_direction = self._direction(direction)
        rows = self._relations([int(document_id)], names, rx_direction, single=True)[int(document_id)]
        result = {
            "document_id": int(document_id),
            "direction": direction,
            "relation_filter": names,
            "total": len(rows),
            "groups": self._groups(rows),
        }
        if depth > 1 and rows:
            result["second_level"] = self._second_level(int(document_id), rows, names, rx_direction)
        if not rows:
            result["message"] = "Связей нет" + (" указанного типа" if names else "") + "."
        return result

    def get_rows(self, document_id: int) -> list[dict[str, Any]]:
        """Связи одного документа как есть (для карточек вроде get_legal_act)."""
        return self._relations([int(document_id)], [], "Both", single=True)[int(document_id)]

    def item(self, row: dict[str, Any]) -> dict[str, Any]:
        related_id = int(row["RelatedId"])
        registered = _parse(row.get("RegistrationDate"))
        created = _parse(row.get("Created"))
        return {
            "id": related_id,
            "name": row.get("RelatedName") or "",
            "kind": row.get("RelatedKind"),
            "registration_number": row.get("RegistrationNumber"),
            "registration_date": registered.astimezone(self.tz).date().isoformat() if registered else None,
            "created": created.astimezone(self.tz).date().isoformat() if created else None,
            "has_text": bool(row.get("HasVersions")),
            "url": self.client.build_document_card_url(related_id) if hasattr(self.client, "build_document_card_url") else None,
        }

    # --- внутреннее ---

    def _groups(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        groups: dict[tuple, dict[str, Any]] = {}
        for row in rows:
            key = (row.get("GroupTitle"), row.get("RelationName"), row.get("Direction"))
            group = groups.setdefault(key, {"group": row.get("GroupTitle"), "relation": row.get("RelationName"),
                                            "direction": row.get("Direction"), "items": []})
            group["items"].append(self.item(row))
        return list(groups.values())

    def _second_level(self, root: int, rows: list[dict[str, Any]], names: list[str], direction: str) -> dict[str, Any]:
        neighbours = list(dict.fromkeys(int(row["RelatedId"]) for row in rows if int(row["RelatedId"]) != root))
        truncated = len(neighbours) > MAX_GRAPH_NODES
        neighbours = neighbours[:MAX_GRAPH_NODES]
        by_doc = self._relations(neighbours, names, direction)
        seen = {root, *neighbours}
        level = []
        for doc_id, doc_rows in by_doc.items():
            extra = [row for row in doc_rows if int(row["RelatedId"]) not in seen]
            if extra:
                level.append({"document_id": doc_id, "groups": self._groups(extra)})
        return {"documents": level, "truncated": truncated}

    def _relations(self, ids: list[int], names: list[str], direction: str,
                   single: bool = False) -> dict[int, list[dict[str, Any]]]:
        out: dict[int, list[dict[str, Any]]] = {doc_id: [] for doc_id in ids}
        params_tail = {"relationNames": ",".join(names), "direction": direction}
        if single:
            rows = self._call("GetDocumentRelations", {"documentId": ids[0], **params_tail}) or []
            out[ids[0]] = list(rows)
            return out
        pending = list(ids)
        for start in range(0, len(pending), MAX_BATCH):
            chunk = pending[start:start + MAX_BATCH]
            while chunk:
                try:
                    rows = self._call("GetDocumentsRelations",
                                      {"documentIds": ",".join(map(str, chunk)), **params_tail}) or []
                    break
                except DirectumError as exc:
                    # Один ненайденный Id роняет весь пакет — выкидываем его и повторяем.
                    missing = NOT_FOUND.search(exc.safe_message or "")
                    if not missing or int(missing.group(1)) not in chunk:
                        raise
                    chunk.remove(int(missing.group(1)))
                    out.pop(int(missing.group(1)), None)
            else:
                rows = []
            for row in rows:
                out.setdefault(int(row["DocumentId"]), []).append(row)
        return out

    def _call(self, method: str, params: dict[str, Any]) -> Any:
        try:
            return self.client.call_function(f"{MODULE}/{method}", params)
        except DirectumError as exc:
            message = exc.safe_message or ""
            if exc.status_code == 404 and "Не найден" not in message:
                raise RelationsUnavailable(
                    "На стенде нет функций чтения связей (DocumentProcessing/GetDocumentRelations).", 404
                ) from None
            found = NOT_FOUND.search(message)
            if found and method == "GetDocumentRelations":
                raise DirectumError(f"Документ {found.group(1)} не найден или у вас нет к нему доступа.", 404) from None
            if UNKNOWN_TYPE.search(message):
                raise ValueError(message.split(": ", 1)[-1]) from None
            raise

    @staticmethod
    def _direction(direction: str) -> str:
        value = DIRECTIONS.get((direction or "both").lower())
        if value is None:
            raise ValueError("direction: both, from (документ — источник связи) или to (документ — цель связи).")
        return value
