import re
from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone, tzinfo
from typing import Any

from src.models.schemas import (
    ApprovalStep,
    LegalActApproval,
    LegalActDetail,
    LegalActsOnApproval,
    LegalActSearchResult,
    LegalActSummary,
)
from src.services.directum_client import DirectumError
from src.services.document_search import text_condition, text_stems

ENTITY_SET = "ILegalActsLegalDocumentBases"
TASKS_SET = "ILegalActsApprovalTasks"
SELECT = (
    "Id,Name,Subject,RegistrationNumber,RegistrationDate,IsNormative,IsPublication,DateOfPublication,"
    "PlaceOfPublication,RegNumberLegalAct,LifeCycleState,InternalApprovalState,HasVersions"
)
DETAIL_EXPAND = "DocumentKind($select=Name),PreparedBy($select=Name),Department($select=Name),OurSignatory($select=Name)"
TASK_EXPAND = (
    "Curator($select=Name),InternalApprovers($expand=Approver($select=Name)),"
    "OuterApprovers($expand=Counterparty($select=Name)),AttachmentDetails"
)
# Вид документа модуля: «Проект правового акта» или «Правовой акт».
KIND_FILTERS = {
    "project": "startswith(DocumentKind/Name,'Проект')",
    "act": "not startswith(DocumentKind/Name,'Проект')",
}
# Подписи из ресурсов решения (Docflow.OfficialDocument, GD.LegalActs.LegalDocumentBase).
APPROVAL_STATES = {
    "OnApproval": "На согласовании",
    "OnRework": "На доработке",
    "PendingSign": "На подписании",
    "Signed": "Подписан",
    "Aborted": "Прекращено",
    "Sending": "Рассылка",
    "Publication": "Публикация",
}
LIFECYCLE_STATES = {
    "Draft": "В разработке",
    "Active": "Действующий",
    "Obsolete": "Устаревший",
    "Cancelled": "Аннулирован",
    "Executed": "Исполнен",
    "Terminated": "Расторгнут",
}
TASK_STATUSES = {
    "InProcess": "в работе",
    "Completed": "завершена",
    "Aborted": "прекращена",
    "Suspended": "приостановлена",
    "Draft": "черновик",
}
# Номер принятого акта в проекте хранится строкой: «От 24.06.2026 № 015/26».
ADOPTED_NUMBER = re.compile(r"№\s*([^\s,;]+)")
MAX_TASKS = 50
CHUNK = 20


def _literal(value: str) -> str:
    return value.replace("'", "''")


def _parse(value: Any) -> datetime | None:
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def _name(value: Any) -> str | None:
    return value.get("Name") if isinstance(value, dict) and value.get("Name") else None


def _chunks(items: list[int]):
    for index in range(0, len(items), CHUNK):
        yield items[index : index + CHUNK]


def adopted_number(text: str | None) -> str | None:
    match = ADOPTED_NUMBER.search(text or "")
    return match.group(1) if match else None


class LegalActsService:
    """Нормативные правовые акты модуля LegalActs: поиск, карточка со связями и ход согласования."""

    def __init__(self, client: Any, tz: tzinfo | None = None, now: Callable[[], datetime] | None = None):
        self.client = client
        self.tz = tz or datetime.now().astimezone().tzinfo
        self._now = now or (lambda: datetime.now(timezone.utc))

    # --- поиск ---------------------------------------------------------

    def find(
        self,
        text: str | None = None,
        kind: str = "any",
        normative: bool | None = None,
        published: bool | None = None,
        approval_state: str | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        limit: int = 20,
    ) -> LegalActSearchResult:
        if kind not in ("any", *KIND_FILTERS):
            raise ValueError(f"Unknown legal act kind: {kind}")
        if approval_state not in (None, "none", *APPROVAL_STATES):
            raise ValueError(f"Unknown approval state: {approval_state}")
        conditions = []
        stems = text_stems(text)
        if stems:
            conditions.append(text_condition(stems))
        if kind in KIND_FILTERS:
            conditions.append(KIND_FILTERS[kind])
        if normative is not None:
            conditions.append(f"IsNormative eq {str(normative).lower()}")
        if published is not None:
            conditions.append(f"IsPublication eq {str(published).lower()}")
        if approval_state == "none":
            conditions.append("InternalApprovalState eq null")
        elif approval_state:
            conditions.append(f"InternalApprovalState eq '{approval_state}'")
        if date_from:
            conditions.append(f"RegistrationDate ge {self._day_literal(date_from)}")
        if date_to:
            conditions.append(f"RegistrationDate lt {self._day_literal(date_to + timedelta(days=1))}")
        # RX не отдаёт списки без фильтра.
        filter_ = " and ".join(conditions) or "Id gt 0"
        rows = self.client.query(
            ENTITY_SET, filter_=filter_, select=SELECT, expand="DocumentKind($select=Name)", orderby="Id desc", top=limit
        )
        items = [self._summary(row) for row in rows]
        return LegalActSearchResult(items=items, total=self.client.count(ENTITY_SET, filter_=filter_), returned=len(items))

    # --- карточка ------------------------------------------------------

    def get(self, act_id: int) -> LegalActDetail:
        rows = self.client.query(ENTITY_SET, filter_=f"Id eq {int(act_id)}", select=SELECT, expand=DETAIL_EXPAND, top=1)
        if not rows:
            raise DirectumError(f"Правовой акт {act_id} не найден или у вас нет к нему доступа.", 404)
        row = rows[0]
        act = self._summary(row)
        detail = LegalActDetail(
            act=act,
            prepared_by=_name(row.get("PreparedBy")),
            department=_name(row.get("Department")),
            signatory=_name(row.get("OurSignatory")),
            has_text=bool(row.get("HasVersions")),
        )
        if act.is_project:
            number = adopted_number(act.adopted_act_number)
            if number:
                found = self.client.query(
                    ENTITY_SET,
                    filter_=f"RegistrationNumber eq '{_literal(number)}' and {KIND_FILTERS['act']}",
                    select=SELECT,
                    expand="DocumentKind($select=Name)",
                    top=1,
                )
                detail.adopted_act = self._summary(found[0]) if found else None
        elif act.registration_number:
            candidates = self.client.query(
                ENTITY_SET,
                filter_=f"contains(RegNumberLegalAct,'{_literal(act.registration_number)}') and {KIND_FILTERS['project']}",
                select=SELECT,
                expand="DocumentKind($select=Name)",
                top=10,
            )
            # contains найдёт и «1015/26» для «015/26» — сверяем номер точно.
            detail.projects = [
                self._summary(item)
                for item in candidates
                if adopted_number(item.get("RegNumberLegalAct")) == act.registration_number
            ]
        tasks = self.client.query(
            TASKS_SET,
            filter_=f"AttachmentDetails/any(a: a/AttachmentId eq {int(act_id)})",
            select="Id,Subject,Status,Started",
            expand=TASK_EXPAND,
            orderby="Id desc",
            top=10,
        )
        steps = self._steps([int(task["Id"]) for task in tasks if task.get("Status") == "InProcess"])
        detail.approvals = [self._approval(task, steps.get(int(task["Id"]), []), act) for task in tasks]
        return detail

    # --- на согласовании -----------------------------------------------

    def on_approval(
        self, curator_id: int | None = None, min_days_waiting: int | None = None, limit: int = 20
    ) -> LegalActsOnApproval:
        filter_ = "Status eq 'InProcess'" + (f" and Curator/Id eq {int(curator_id)}" if curator_id is not None else "")
        tasks = self.client.query(
            TASKS_SET, filter_=filter_, select="Id,Subject,Status,Started", expand=TASK_EXPAND, orderby="Started asc", top=MAX_TASKS
        )
        steps = self._steps([int(task["Id"]) for task in tasks])
        documents = self._documents([att["AttachmentId"] for task in tasks for att in task.get("AttachmentDetails") or []])
        approvals = []
        for task in tasks:
            document = next(
                (documents[att["AttachmentId"]] for att in task.get("AttachmentDetails") or [] if att.get("AttachmentId") in documents),
                None,
            )
            approvals.append(self._approval(task, steps.get(int(task["Id"]), []), document))
        if min_days_waiting is not None:
            approvals = [item for item in approvals if (item.max_days_waiting or 0) >= min_days_waiting]
        approvals.sort(key=lambda item: (item.max_days_waiting or 0, item.days_in_approval or 0), reverse=True)
        return LegalActsOnApproval(items=approvals[:limit], total=len(approvals), truncated=len(tasks) >= MAX_TASKS)

    # --- внутреннее ----------------------------------------------------

    def _documents(self, ids: list[int]) -> dict[int, LegalActSummary]:
        unique = list(dict.fromkeys(int(item) for item in ids if item is not None))
        found: dict[int, LegalActSummary] = {}
        for chunk in _chunks(unique):
            rows = self.client.query(
                ENTITY_SET,
                filter_="(" + " or ".join(f"Id eq {item}" for item in chunk) + ")",
                select=SELECT,
                expand="DocumentKind($select=Name)",
                top=len(chunk),
            )
            for row in rows:
                found[int(row["Id"])] = self._summary(row)
        return found

    def _steps(self, task_ids: list[int]) -> dict[int, list[ApprovalStep]]:
        steps: dict[int, list[ApprovalStep]] = {}
        for chunk in _chunks(task_ids):
            rows = self.client.query(
                "IAssignments",
                filter_="(" + " or ".join(f"Task/Id eq {item}" for item in chunk) + ") and Status eq 'InProcess'",
                select="Id,Subject,Created,Deadline",
                expand="Performer($select=Name),Task($select=Id)",
                top=200,
            )
            for row in rows:
                task = row.get("Task") or {}
                steps.setdefault(int(task.get("Id") or 0), []).append(self._step(row))
        return steps

    def _step(self, row: dict[str, Any]) -> ApprovalStep:
        created = _parse(row.get("Created"))
        deadline = _parse(row.get("Deadline"))
        now = self._now()
        today = now.astimezone(self.tz).date()
        subject = row.get("Subject") or ""
        return ApprovalStep(
            performer=_name(row.get("Performer")),
            action=subject.split(":", 1)[0].strip() if ":" in subject else subject,
            since=created.astimezone(self.tz).date() if created else None,
            deadline=deadline.astimezone(self.tz).date() if deadline else None,
            days_waiting=(today - created.astimezone(self.tz).date()).days if created else None,
            overdue=bool(deadline and deadline < now),
        )

    def _approval(self, task: dict[str, Any], steps: list[ApprovalStep], document: LegalActSummary | None) -> LegalActApproval:
        started = _parse(task.get("Started"))
        today = self._now().astimezone(self.tz).date()
        status = task.get("Status")
        waiting = [step.days_waiting for step in steps if step.days_waiting is not None]
        return LegalActApproval(
            task_id=int(task["Id"]),
            subject=task.get("Subject") or "",
            status=status,
            status_label=TASK_STATUSES.get(status, status),
            started=started.astimezone(self.tz).date() if started else None,
            days_in_approval=(today - started.astimezone(self.tz).date()).days if (started and status == "InProcess") else None,
            curator=_name(task.get("Curator")),
            internal_approvers=[name for item in task.get("InternalApprovers") or [] if (name := _name(item.get("Approver")))],
            outer_approvers=[name for item in task.get("OuterApprovers") or [] if (name := _name(item.get("Counterparty")))],
            current_steps=steps,
            max_days_waiting=max(waiting) if waiting else None,
            document=document,
        )

    def _summary(self, row: dict[str, Any]) -> LegalActSummary:
        kind = _name(row.get("DocumentKind"))
        approval = row.get("InternalApprovalState")
        lifecycle = row.get("LifeCycleState")
        registered = _parse(row.get("RegistrationDate"))
        published = _parse(row.get("DateOfPublication"))
        document_id = int(row["Id"])
        return LegalActSummary(
            id=document_id,
            name=row.get("Name") or "",
            kind=kind,
            is_project=bool(kind and kind.startswith("Проект")),
            subject=row.get("Subject"),
            registration_number=row.get("RegistrationNumber"),
            registration_date=registered.astimezone(self.tz).date() if registered else None,
            is_normative=bool(row.get("IsNormative")),
            is_published=bool(row.get("IsPublication")),
            publication_date=published.astimezone(self.tz).date() if published else None,
            publication_place=row.get("PlaceOfPublication"),
            adopted_act_number=row.get("RegNumberLegalAct"),
            approval_state=approval,
            approval_state_label=APPROVAL_STATES.get(approval, approval) if approval else "Не отправлялся на согласование",
            lifecycle_state_label=LIFECYCLE_STATES.get(lifecycle, lifecycle) if lifecycle else None,
            url=self.client.build_document_card_url(document_id) if hasattr(self.client, "build_document_card_url") else None,
        )

    def _day_literal(self, day: date) -> str:
        return datetime(day.year, day.month, day.day, tzinfo=self.tz).isoformat()
