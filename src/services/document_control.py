"""Контроль исполнения поручений без контрольных карт.

Объект контроля — само поручение (задача на исполнение) с признаком «На контроле» и контролёром. Реестр строится
запросом по поручениям, досье — по дереву поручения (MainTaskId), истории продлений и приёмке контролёра. Ничего
не дублируется: всё читается из workflow Directum RX с правами текущего пользователя.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone, tzinfo
from typing import Any

from src.services.directum_client import DirectumError

TASKS = "IActionItemExecutionTasks"
ASSIGNMENTS = "IActionItemExecutionAssignments"
CHUNK = 40
TREE_LIMIT = 300
TEXT_LIMIT = 1000
REPORT_LIMIT = 1500
CORRESPONDENCE_LIMIT = 20

LIST_SELECT = "Id,Subject,Deadline,Status,ExecutionState,IsCompoundActionItem,ActionItemType,MainTaskId,HasIndefiniteDeadline,Created"
LIST_EXPAND = "Supervisor($select=Id,Name),Assignee($select=Id,Name),AssignedBy($select=Id,Name)"
NODE_SELECT = (
    "Id,Subject,Deadline,Status,ExecutionState,IsUnderControl,ActualDate,Report,ReportNote,HasIndefiniteDeadline,"
    "ActionItemType,MainTaskId,Created"
)
NODE_EXPAND = (
    "Assignee($select=Id,Name),Supervisor($select=Id,Name),AssignedBy($select=Id,Name),"
    "ParentAssignment($select=Id),ParentTask($select=Id)"
)
ROOT_SELECT = NODE_SELECT + ",ActionItem,IsCompoundActionItem"
ROOT_EXPAND = (
    NODE_EXPAND + ",CoAssignees($expand=Assignee($select=Name)),"
    "ActionItemParts($expand=Assignee($select=Name)),SourceDocumentGD($select=Id,Name),AttachmentDetails,Texts"
)

EXECUTION_STATES = {
    "OnExecution": "на исполнении",
    "OnControl": "на приёмке у контролёра",
    "OnRework": "на доработке",
    "Executed": "исполнено",
    "Aborted": "прекращено",
    "Sending": "отправка",
}
STATUS_LABELS = {"InProcess": "в работе", "Completed": "завершено", "Aborted": "прекращено", "Suspended": "приостановлено", "Draft": "черновик"}
ACTION_ITEM_TYPES = {"Main": "основное", "Additional": "соисполнителю", "Component": "пункт составного"}
EXTENSION_RESULTS = {"Accept": "одобрено", "Decline": "отклонено", "ForRework": "на доработку"}
ACCEPTANCE_RESULTS = {"Agree": "принято", "ForRework": "на доработку", "Accept": "принято"}

# state → условие по статусу/состоянию исполнения (снятие с контроля = поручение завершено после приёмки).
STATES = {
    "in_work": "Status eq 'InProcess'",
    "on_acceptance": "Status eq 'InProcess' and ExecutionState eq 'OnControl'",
    "overdue": "Status eq 'InProcess'",
    "completed": "Status eq 'Completed'",
    "all": None,
}
DUE_WINDOWS = ("today", "tomorrow", "next_3_days", "this_week", "next_week", "this_month")


@dataclass(frozen=True)
class ControlFilters:
    state: str = "in_work"
    supervisor_id: int | None = None
    assigned_by_id: int | None = None
    assignee_id: int | None = None
    co_assignee_id: int | None = None
    due: str | None = None
    date_from: date | None = None
    date_to: date | None = None


def _chunks(items: list[int]):
    for index in range(0, len(items), CHUNK):
        yield items[index : index + CHUNK]


def _name(value: Any) -> str | None:
    name = value.get("Name") if isinstance(value, dict) else None
    return name.strip() if isinstance(name, str) and name.strip() else None


def _nav_id(value: Any) -> int | None:
    ident = value.get("Id") if isinstance(value, dict) else None
    return int(ident) if isinstance(ident, int) else None


def _trim(text: Any, limit: int) -> str | None:
    if not isinstance(text, str) or not text.strip():
        return None
    text = text.strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


class DocumentControlService:
    def __init__(self, client: Any, tz: tzinfo | None = None, now: Callable[[], datetime] | None = None):
        self.client = client
        self.tz = tz or datetime.now().astimezone().tzinfo
        self._now = now or (lambda: datetime.now(timezone.utc))

    # --- реестр ---

    def list_controlled(self, filters: ControlFilters | None = None, limit: int = 30) -> dict[str, Any]:
        filters = filters or ControlFilters()
        base = self._base_filter(filters)
        filter_ = self._state_filter(base, filters.state)
        rows = self.client.query(TASKS, filter_=filter_, select=LIST_SELECT, expand=LIST_EXPAND, orderby="Deadline asc", top=limit)
        total = self.client.count(TASKS, filter_=filter_)
        overdue_total = self.client.count(TASKS, filter_=f"{base} and Status eq 'InProcess' and {self._overdue_condition()}")
        acceptance_total = self.client.count(TASKS, filter_=f"{base} and {STATES['on_acceptance']}")
        items = [self._list_item(row) for row in rows]
        return {
            "state": filters.state,
            "due": filters.due,
            "items": items,
            "returned": len(items),
            "total": total,
            "overdue_total": overdue_total,
            "on_acceptance_total": acceptance_total,
            "truncated": total > len(items),
        }

    def _base_filter(self, filters: ControlFilters) -> str:
        if filters.state not in STATES:
            raise ValueError(f"Unknown control state: {filters.state}")
        if filters.due is not None and filters.due not in DUE_WINDOWS:
            raise ValueError(f"Unknown due window: {filters.due}")
        if filters.due is not None and (filters.date_from or filters.date_to):
            raise ValueError("Due window cannot be combined with a date period")
        conditions = ["IsUnderControl eq true"]
        for field, value in (("Supervisor", filters.supervisor_id), ("AssignedBy", filters.assigned_by_id), ("Assignee", filters.assignee_id)):
            if value is not None:
                conditions.append(f"{field}/Id eq {int(value)}")
        if filters.co_assignee_id is not None:
            conditions.append(f"CoAssignees/any(c: c/Assignee/Id eq {int(filters.co_assignee_id)})")
        start, end = self._window(filters)
        if start is not None:
            conditions.append(f"Deadline ge {self._day_literal(start)}")
        if end is not None:
            conditions.append(f"Deadline lt {self._day_literal(end + timedelta(days=1))}")
        return " and ".join(conditions)

    def _state_filter(self, base: str, state: str) -> str:
        parts = [base]
        if STATES[state]:
            parts.append(STATES[state])
        if state == "overdue":
            parts.append(self._overdue_condition())
        return " and ".join(parts)

    def _overdue_condition(self) -> str:
        return f"Deadline lt {self._day_literal(self._today())}"

    def _window(self, filters: ControlFilters) -> tuple[date | None, date | None]:
        today = self._today()
        if filters.due == "today":
            return today, today
        if filters.due == "tomorrow":
            return today + timedelta(days=1), today + timedelta(days=1)
        if filters.due == "next_3_days":
            return today, today + timedelta(days=3)
        if filters.due == "this_week":
            monday = today - timedelta(days=today.weekday())
            return monday, monday + timedelta(days=6)
        if filters.due == "next_week":
            monday = today - timedelta(days=today.weekday()) + timedelta(days=7)
            return monday, monday + timedelta(days=6)
        if filters.due == "this_month":
            first = today.replace(day=1)
            following = (first + timedelta(days=32)).replace(day=1)
            return first, following - timedelta(days=1)
        return filters.date_from, filters.date_to

    def _list_item(self, row: dict[str, Any]) -> dict[str, Any]:
        item = self._timing(row)
        item.update(
            {
                "id": int(row["Id"]),
                "subject": row.get("Subject") or "",
                "status": row.get("Status"),
                "status_label": STATUS_LABELS.get(row.get("Status"), row.get("Status")),
                "execution_state": row.get("ExecutionState"),
                "execution_state_label": EXECUTION_STATES.get(row.get("ExecutionState"), row.get("ExecutionState")),
                "supervisor": _name(row.get("Supervisor")),
                "assignee": _name(row.get("Assignee")),
                "assigned_by": _name(row.get("AssignedBy")),
                "action_item_type": ACTION_ITEM_TYPES.get(row.get("ActionItemType"), row.get("ActionItemType")),
                "is_compound": bool(row.get("IsCompoundActionItem")),
                "main_task_id": row.get("MainTaskId"),
                "url": self._url(int(row["Id"])),
            }
        )
        return item

    # --- досье ---

    def dossier(self, action_item_id: int, kind: str | None = None) -> dict[str, Any]:
        """Досье поручения: дерево исполнения, продления, приёмка, переписка, документы.

        kind="assignment" — передан id задания на исполнение; "task" — id задачи; None — сначала задача.
        """
        if kind not in (None, "task", "assignment"):
            raise ValueError(f"Unknown action item kind: {kind}")
        root = None if kind == "assignment" else self._root_row(action_item_id)
        if root is None and kind != "task":
            task_id = self._task_for_assignment(action_item_id)
            root = self._root_row(task_id) if task_id else None
        if root is None:
            raise DirectumError(f"Поручение #{action_item_id} не найдено.", status_code=404)
        root_id = int(root["Id"])
        main_id = root.get("MainTaskId") or root_id
        tree_rows = self._tree_rows(int(main_id), root)
        assignments = self._assignments([int(row["Id"]) for row in tree_rows])
        nodes = self._subtree(root_id, tree_rows, assignments)
        node_ids = {node["id"] for node in nodes}
        extensions = self._extensions(int(main_id), assignments, node_ids)
        acceptance = self._acceptance(sorted(node_ids))
        documents = self._documents(root)
        in_work = [n for n in nodes if n["status"] == "InProcess"]
        overdue = [n for n in in_work if n.get("days_overdue")]
        accepted_ext = [e for e in extensions if e["state"] == "одобрено"]
        summary = {
            "nodes_total": len(nodes),
            "nodes_in_work": len(in_work),
            "nodes_overdue": len(overdue),
            "max_days_overdue": max((n["days_overdue"] for n in overdue), default=0),
            "nodes_under_control": sum(1 for n in nodes if n["is_under_control"]),
            "extensions_total": len(extensions),
            "extensions_accepted": len(accepted_ext),
            "extensions_pending": sum(1 for e in extensions if e["state"] == "на рассмотрении"),
            "deadline_shift_days": sum(e["shift_days"] or 0 for e in accepted_ext),
            "acceptance_pending": sum(1 for a in acceptance if a["status"] == "InProcess"),
            "tree_truncated": len(tree_rows) >= TREE_LIMIT,
        }
        root_node = next(n for n in nodes if n["id"] == root_id)
        return {
            "action_item": {
                **root_node,
                "text": _trim(root.get("ActionItem"), TEXT_LIMIT * 3),
                "is_compound": bool(root.get("IsCompoundActionItem")),
                "co_assignees": [n for n in (_name(c.get("Assignee")) for c in root.get("CoAssignees") or []) if n],
                "parts": [
                    {
                        "number": part.get("Number"),
                        "text": _trim(part.get("ActionItemPart"), TEXT_LIMIT),
                        "assignee": _name(part.get("Assignee")),
                        "deadline": self._iso_day(part.get("Deadline")),
                    }
                    for part in sorted(root.get("ActionItemParts") or [], key=lambda p: p.get("Number") or 0)
                ],
                "main_task_id": main_id,
            },
            "summary": summary,
            "tree": nodes,
            "deadline_history": extensions,
            "acceptance": acceptance,
            "correspondence": self._correspondence(root),
            "documents": documents,
        }

    def _root_row(self, task_id: int) -> dict[str, Any] | None:
        try:
            return self.client.get_one(f"{TASKS}({int(task_id)})?$select={ROOT_SELECT}&$expand={ROOT_EXPAND}")
        except DirectumError as exc:
            # Стенд отвечает на несуществующий id 400 «Запись не существует».
            if exc.status_code in (400, 404):
                return None
            raise

    def _task_for_assignment(self, assignment_id: int) -> int | None:
        try:
            rows = self.client.query(ASSIGNMENTS, filter_=f"Id eq {int(assignment_id)}", select="Id", expand="Task($select=Id)", top=1)
        except DirectumError:
            return None
        return _nav_id(rows[0].get("Task")) if rows else None

    def _tree_rows(self, main_id: int, root: dict[str, Any]) -> list[dict[str, Any]]:
        rows = self.client.query(TASKS, filter_=f"MainTaskId eq {main_id}", select=NODE_SELECT, expand=NODE_EXPAND, orderby="Id asc", top=TREE_LIMIT)
        if not any(int(row["Id"]) == int(root["Id"]) for row in rows):
            rows = [root, *rows]
        return rows

    def _assignments(self, task_ids: list[int]) -> list[dict[str, Any]]:
        found: list[dict[str, Any]] = []
        for chunk in _chunks(task_ids):
            found.extend(
                self.client.query(
                    ASSIGNMENTS,
                    filter_="(" + " or ".join(f"Task/Id eq {item}" for item in chunk) + ")",
                    select="Id,Status,Result,Deadline,Completed,Created",
                    expand="Performer($select=Name),Task($select=Id)",
                    top=500,
                )
            )
        return found

    def _subtree(self, root_id: int, rows: list[dict[str, Any]], assignments: list[dict[str, Any]]) -> list[dict[str, Any]]:
        task_of_assignment = {int(a["Id"]): _nav_id(a.get("Task")) for a in assignments}
        children: dict[int, list[dict[str, Any]]] = {}
        for row in rows:
            parent = task_of_assignment.get(_nav_id(row.get("ParentAssignment")) or -1) or _nav_id(row.get("ParentTask"))
            if parent is not None and parent != int(row["Id"]):
                children.setdefault(parent, []).append(row)
        by_id = {int(row["Id"]): row for row in rows}
        nodes: list[dict[str, Any]] = []
        seen: set[int] = set()

        def walk(task_id: int, level: int, parent: int | None) -> None:
            if task_id in seen or task_id not in by_id:
                return
            seen.add(task_id)
            nodes.append(self._node(by_id[task_id], level, parent))
            for child in sorted(children.get(task_id, []), key=lambda r: int(r["Id"])):
                walk(int(child["Id"]), level + 1, task_id)

        walk(root_id, 0, None)
        return nodes

    def _node(self, row: dict[str, Any], level: int, parent: int | None) -> dict[str, Any]:
        node = self._timing(row)
        node.update(
            {
                "id": int(row["Id"]),
                "parent_id": parent,
                "level": level,
                "subject": row.get("Subject") or "",
                "assignee": _name(row.get("Assignee")),
                "supervisor": _name(row.get("Supervisor")),
                "assigned_by": _name(row.get("AssignedBy")),
                "status": row.get("Status"),
                "status_label": STATUS_LABELS.get(row.get("Status"), row.get("Status")),
                "execution_state_label": EXECUTION_STATES.get(row.get("ExecutionState"), row.get("ExecutionState")),
                "is_under_control": bool(row.get("IsUnderControl")),
                "actual_date": self._iso_day(row.get("ActualDate")),
                "report": _trim(row.get("Report") or row.get("ReportNote"), REPORT_LIMIT),
                "action_item_type": ACTION_ITEM_TYPES.get(row.get("ActionItemType"), row.get("ActionItemType")),
                "url": self._url(int(row["Id"])),
            }
        )
        return node

    def _extensions(self, main_id: int, assignments: list[dict[str, Any]], node_ids: set[int]) -> list[dict[str, Any]]:
        try:
            rows = self.client.query(
                "IDeadlineExtensionTasks",
                filter_=f"MainTaskId eq {main_id}",
                select="Id,Status,Created,NewDeadline,CurrentDeadline",
                expand="Author($select=Name),Assignee($select=Name),ParentAssignment($select=Id)",
                orderby="Created asc",
                top=100,
            )
        except DirectumError as exc:
            if exc.status_code in (400, 404):
                return []
            raise
        task_of_assignment = {int(a["Id"]): _nav_id(a.get("Task")) for a in assignments}
        decisions: dict[int, dict[str, Any]] = {}
        ids = [int(row["Id"]) for row in rows]
        for chunk in _chunks(ids):
            for asg in self.client.query(
                "IDeadlineExtensionAssignments",
                filter_="(" + " or ".join(f"Task/Id eq {item}" for item in chunk) + ")",
                select="Id,Status,Result,Completed",
                expand="Task($select=Id)",
                top=200,
            ):
                task = _nav_id(asg.get("Task"))
                if task is not None and (asg.get("Result") or task not in decisions):
                    decisions[task] = asg
        history = []
        for row in rows:
            action_item = task_of_assignment.get(_nav_id(row.get("ParentAssignment")) or -1)
            if action_item is not None and action_item not in node_ids:
                continue
            decision = decisions.get(int(row["Id"])) or {}
            if row.get("Status") == "InProcess":
                state = "на рассмотрении"
            elif decision.get("Result"):
                state = EXTENSION_RESULTS.get(decision["Result"], decision["Result"])
            else:
                state = STATUS_LABELS.get(row.get("Status"), row.get("Status"))
            current, new = self._day(row.get("CurrentDeadline")), self._day(row.get("NewDeadline"))
            history.append(
                {
                    "id": int(row["Id"]),
                    "action_item_id": action_item,
                    "requested": self._iso_day(row.get("Created")),
                    "requested_by": _name(row.get("Author")),
                    "decided_by": _name(row.get("Assignee")),
                    "decided": self._iso_day(decision.get("Completed")),
                    "current_deadline": current.isoformat() if current else None,
                    "new_deadline": new.isoformat() if new else None,
                    "shift_days": (new - current).days if current and new else None,
                    "state": state,
                }
            )
        return history

    def _acceptance(self, task_ids: list[int]) -> list[dict[str, Any]]:
        found = []
        for chunk in _chunks(task_ids):
            try:
                rows = self.client.query(
                    "IActionItemSupervisorAssignments",
                    filter_="(" + " or ".join(f"Task/Id eq {item}" for item in chunk) + ")",
                    select="Id,Status,Result,Created,Completed,Deadline",
                    expand="Performer($select=Name),Task($select=Id)",
                    orderby="Created asc",
                    top=200,
                )
            except DirectumError as exc:
                if exc.status_code in (400, 404):
                    return []
                raise
            for row in rows:
                found.append(
                    {
                        "id": int(row["Id"]),
                        "action_item_id": _nav_id(row.get("Task")),
                        "supervisor": _name(row.get("Performer")),
                        "status": row.get("Status"),
                        "result": ACCEPTANCE_RESULTS.get(row.get("Result"), row.get("Result")),
                        "created": self._iso_day(row.get("Created")),
                        "completed": self._iso_day(row.get("Completed")),
                        "deadline": self._iso_day(row.get("Deadline")),
                    }
                )
        return found

    def _correspondence(self, root: dict[str, Any]) -> list[dict[str, Any]]:
        texts = [t for t in root.get("Texts") or [] if isinstance(t, dict) and _trim(t.get("Body"), TEXT_LIMIT)]
        texts.sort(key=lambda t: t.get("Created") or "")
        return [
            {"created": self._iso_day(t.get("Created")), "text": _trim(t.get("Body"), TEXT_LIMIT), "auto": bool(t.get("IsAutoGenerated"))}
            for t in texts[-CORRESPONDENCE_LIMIT:]
        ]

    def _documents(self, root: dict[str, Any]) -> list[dict[str, Any]]:
        ids: list[int] = []
        source = root.get("SourceDocumentGD")
        if _nav_id(source) is not None:
            ids.append(_nav_id(source))
        ids.extend(int(a["AttachmentId"]) for a in root.get("AttachmentDetails") or [] if isinstance(a.get("AttachmentId"), int))
        unique = list(dict.fromkeys(ids))
        names: dict[int, str] = {}
        for chunk in _chunks(unique):
            try:
                rows = self.client.query(
                    "IOfficialDocuments", filter_="(" + " or ".join(f"Id eq {item}" for item in chunk) + ")", select="Id,Name", top=len(chunk)
                )
            except DirectumError:
                rows = []
            names.update({int(r["Id"]): r.get("Name") or "" for r in rows})
        documents = []
        for doc_id in unique:
            if doc_id not in names:
                continue
            url = self.client.build_document_card_url(doc_id) if hasattr(self.client, "build_document_card_url") else None
            documents.append({"id": doc_id, "name": names[doc_id], "is_source": doc_id == _nav_id(source), "url": url})
        return documents

    # --- даты ---

    def _today(self) -> date:
        return self._now().astimezone(self.tz).date()

    def _day(self, value: Any) -> date | None:
        if not isinstance(value, str) or not value:
            return None
        try:
            return datetime.fromisoformat(value).astimezone(self.tz).date()
        except ValueError:
            return None

    def _iso_day(self, value: Any) -> str | None:
        day = self._day(value)
        return day.isoformat() if day else None

    def _timing(self, row: dict[str, Any]) -> dict[str, Any]:
        deadline = None if row.get("HasIndefiniteDeadline") else self._day(row.get("Deadline"))
        in_work = row.get("Status") == "InProcess"
        left = (deadline - self._today()).days if deadline and in_work else None
        return {
            "created": self._iso_day(row.get("Created")),
            "deadline": deadline.isoformat() if deadline else None,
            "days_left": left if left is not None and left >= 0 else None,
            "days_overdue": -left if left is not None and left < 0 else None,
        }

    def _day_literal(self, day: date) -> str:
        return datetime(day.year, day.month, day.day, tzinfo=self.tz).isoformat()

    def _url(self, task_id: int) -> str | None:
        if hasattr(self.client, "build_client_card_url"):
            return self.client.build_client_card_url(f"{TASKS}({task_id})")
        return None
