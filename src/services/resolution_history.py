"""История резолюций автора: поручения верхнего уровня, которые он выдавал, — кому, с кем, на контроль ли, какой срок.

Нужна, чтобы по тексту нового входящего документа предложить проект резолюции «как обычно у этого руководителя».
Возвращаются только служебные реквизиты поручений и название документа-основания; права — как у пользователя в RX.
"""

import re
from collections import Counter
from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone, tzinfo
from typing import Any

TASKS = "IActionItemExecutionTasks"
MAX_LIMIT = 2000
TEXT_LIMIT = 600
DEFAULT_PERIOD_DAYS = 730
SELECT = "Id,Subject,ActionItem,Created,Deadline,IsUnderControl,IsCompoundActionItem,HasIndefiniteDeadline,Status"
EXPAND = (
    "Assignee($select=Id,Name),Supervisor($select=Id,Name),SourceDocumentGD($select=Id,Name),"
    "CoAssignees($expand=Assignee($select=Id,Name)),ActionItemParts($select=Number;$expand=Assignee($select=Id,Name))"
)
SUBJECT_PREFIX = re.compile(r"^\s*(?:>>\s*)?Поручение:\s*", re.I)


def _name(value: Any) -> str | None:
    name = value.get("Name") if isinstance(value, dict) else None
    return name.strip() if isinstance(name, str) and name.strip() else None


def _trim(text: Any, limit: int) -> str | None:
    if not isinstance(text, str) or not text.strip():
        return None
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


class ResolutionHistoryService:
    def __init__(self, client: Any, tz: tzinfo | None = None, now: Callable[[], datetime] | None = None):
        self.client = client
        self.tz = tz or datetime.now().astimezone().tzinfo
        self._now = now or (lambda: datetime.now(timezone.utc))

    def history(
        self,
        author_id: int,
        date_from: date | None = None,
        date_to: date | None = None,
        limit: int = 1000,
    ) -> dict[str, Any]:
        today = self._now().astimezone(self.tz).date()
        start = date_from or today - timedelta(days=DEFAULT_PERIOD_DAYS)
        conditions = [
            f"AssignedBy/Id eq {int(author_id)}",
            # Только поручения из резолюций (верхний уровень), без подчинённых и черновиков.
            "ParentAssignment eq null",
            "Status ne 'Draft'",
            f"Created ge {self._day_literal(start)}",
        ]
        if date_to:
            conditions.append(f"Created lt {self._day_literal(date_to + timedelta(days=1))}")
        filter_ = " and ".join(conditions)
        rows = self.client.query(TASKS, filter_=filter_, select=SELECT, expand=EXPAND, orderby="Created desc", top=min(limit, MAX_LIMIT))
        total = self.client.count(TASKS, filter_=filter_)
        items = [self._item(row) for row in rows]
        executors = Counter(name for item in items for name in ([item["assignee"]] if item["assignee"] else item["parts_assignees"][:1]))
        return {
            "author_id": int(author_id),
            "date_from": start.isoformat(),
            "date_to": date_to.isoformat() if date_to else None,
            "total": total,
            "returned": len(items),
            "truncated": total > len(items),
            "under_control_share": round(sum(1 for i in items if i["under_control"]) / len(items), 3) if items else None,
            "top_assignees": [{"name": name, "count": count} for name, count in executors.most_common(10)],
            "items": items,
        }

    def _item(self, row: dict[str, Any]) -> dict[str, Any]:
        created = self._day(row.get("Created"))
        deadline = None if row.get("HasIndefiniteDeadline") else self._day(row.get("Deadline"))
        parts = sorted(row.get("ActionItemParts") or [], key=lambda p: p.get("Number") or 0)
        parts_assignees = list(dict.fromkeys(n for n in (_name(p.get("Assignee")) for p in parts) if n))
        source = row.get("SourceDocumentGD") if isinstance(row.get("SourceDocumentGD"), dict) else None
        return {
            "id": int(row["Id"]),
            "created": created.isoformat() if created else None,
            "subject": SUBJECT_PREFIX.sub("", row.get("Subject") or "").strip(),
            "text": _trim(row.get("ActionItem"), TEXT_LIMIT),
            "source_document": {"id": source.get("Id"), "name": _trim(source.get("Name"), 300)} if source else None,
            "assignee": _name(row.get("Assignee")),
            "co_assignees": list(dict.fromkeys(n for n in (_name(c.get("Assignee")) for c in row.get("CoAssignees") or []) if n)),
            "parts_assignees": parts_assignees,
            "is_compound": bool(row.get("IsCompoundActionItem")),
            "under_control": bool(row.get("IsUnderControl")),
            "supervisor": _name(row.get("Supervisor")),
            "term_days": (deadline - created).days if deadline and created else None,
            "status": row.get("Status"),
            "url": self.client.build_client_card_url(f"{TASKS}({int(row['Id'])})") if hasattr(self.client, "build_client_card_url") else None,
        }

    def _day(self, value: Any) -> date | None:
        if not isinstance(value, str) or not value:
            return None
        try:
            return datetime.fromisoformat(value).astimezone(self.tz).date()
        except ValueError:
            return None

    def _day_literal(self, day: date) -> str:
        return datetime(day.year, day.month, day.day, tzinfo=self.tz).isoformat()
