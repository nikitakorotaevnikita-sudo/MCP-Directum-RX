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
    RelatedDocument,
)
from src.services.directum_client import DirectumError
from src.services.content_search import stems
from src.services.document_search import text_condition, text_stems

ENTITY_SET = "ILegalActsLegalDocumentBases"
TASKS_SET = "ILegalActsApprovalTasks"
SELECT = (
    "Id,Name,Subject,RegistrationNumber,RegistrationDate,IsNormative,IsPublication,DateOfPublication,"
    "PlaceOfPublication,RegNumberLegalAct,LifeCycleState,InternalApprovalState,HasVersions,ProjectId,HasRelations,Created"
)
DOCS_SET = "IOfficialDocuments"
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
ADOPTED_DATE = re.compile(r"(\d{2})\.(\d{2})\.(\d{4})")
# Связи документа (панель «Связи» в RX) сервис интеграции не публикует: читаются только приложения
# (ведущий документ) и связь проект ↔ акт (ProjectId). Остальное — кандидаты по названию и периоду.
RELATED_BEFORE_DAYS = 60
RELATED_AFTER_DAYS = 180
RELATED_QUERY_STEMS = 6
RELATED_MIN_SHARED = 3
RELATED_LIMIT = 10
GENERIC_STEMS = {"проект", "правов", "постан", "распор", "решени", "положе", "порядк", "област", "муници",
                 "внесен", "измене", "регион", "докуме", "записк", "объявл"}
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


def adopted_date(text: str | None) -> date | None:
    match = ADOPTED_DATE.search(text or "")
    if not match:
        return None
    try:
        return date(int(match.group(3)), int(match.group(2)), int(match.group(1)))
    except ValueError:
        return None


def title_stems(text: str | None) -> list[str]:
    """Различительные основы названия акта — без служебных слов вроде «проект», «постановление»."""
    return [item for item in dict.fromkeys(stems(text or "")) if item not in GENERIC_STEMS]


class LegalActsService:
    """Нормативные правовые акты модуля LegalActs: поиск, карточка со связями и ход согласования."""

    def __init__(self, client: Any, tz: tzinfo | None = None, now: Callable[[], datetime] | None = None,
                 relations: Any | None = None):
        self.client = client
        self.relations = relations
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
            detail.adopted_act = self._adopted_act(act)
        else:
            detail.projects = self._projects(act, row.get("ProjectId"))
        detail.has_relations = bool(row.get("HasRelations"))
        confirmed = detail.has_relations and self._fill_relations(detail)
        if not confirmed:
            detail.addenda = self._addenda(act.id)
        if detail.has_relations and not confirmed:
            known = {act.id, *(item.id for item in detail.addenda), *(item.id for item in detail.projects)}
            if detail.adopted_act:
                known.add(detail.adopted_act.id)
            detail.possibly_related = self._possibly_related(row, known)
            detail.relations_note = (
                "В карточке RX у документа есть связи (панель «Связи»: заключения и отчёты, правовой акт, прочие), "
                "но сервис интеграции RX их не отдаёт. Подтверждены только приложения (addenda) и связь проект ↔ "
                "принятый акт. possibly_related — документы с похожим названием за тот же период: связь НЕ "
                "подтверждена, называй их «вероятно связанные». Полный список связей — в карточке: " + (act.url or "")
            )
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

    def _adopted_act(self, project: LegalActSummary) -> LegalActSummary | None:
        """Принятый акт проекта: у акта ProjectId = Id проекта. Номер из «От 14.10.2026 № 001/26» — запасной путь;
        номера повторяются по годам, поэтому сверяется и дата."""
        rows = self.client.query(
            ENTITY_SET, filter_=f"ProjectId eq {project.id} and {KIND_FILTERS['act']}", select=SELECT,
            expand="DocumentKind($select=Name)", orderby="Id desc", top=1,
        )
        if rows:
            return self._summary(rows[0])
        number = adopted_number(project.adopted_act_number)
        if not number:
            return None
        rows = self.client.query(
            ENTITY_SET, filter_=f"RegistrationNumber eq '{_literal(number)}' and {KIND_FILTERS['act']}", select=SELECT,
            expand="DocumentKind($select=Name)", orderby="Id desc", top=10,
        )
        candidates = [self._summary(item) for item in rows]
        day = adopted_date(project.adopted_act_number)
        if day:
            candidates = [item for item in candidates if item.registration_date == day]
        return candidates[0] if len(candidates) == 1 else None

    def _projects(self, act: LegalActSummary, project_id: Any) -> list[LegalActSummary]:
        if project_id:
            found = self._documents([int(project_id)])
            if found:
                return list(found.values())
        if not act.registration_number:
            return []
        candidates = self.client.query(
            ENTITY_SET,
            filter_=f"contains(RegNumberLegalAct,'{_literal(act.registration_number)}') and {KIND_FILTERS['project']}",
            select=SELECT,
            expand="DocumentKind($select=Name)",
            top=10,
        )
        # contains найдёт и «1015/26» для «015/26» — сверяем номер точно, а дату — если она указана.
        return [
            self._summary(item)
            for item in candidates
            if adopted_number(item.get("RegNumberLegalAct")) == act.registration_number
            and adopted_date(item.get("RegNumberLegalAct")) in (None, act.registration_date)
        ]

    def _fill_relations(self, detail: LegalActDetail) -> bool:
        """Связи из панели «Связи» RX (DocumentProcessing/GetDocumentRelations). False — функций на стенде нет."""
        if self.relations is None:
            return False
        try:
            rows = self.relations.get_rows(detail.act.id)
        except DirectumError:
            return False
        # Связь «Проект НПА ↔ Нормативный правовой акт» — запасной путь, если ProjectId не заполнен.
        linked = [int(row["RelatedId"]) for row in rows if row.get("RelationName") == "LegalAct"]
        if linked and detail.act.is_project and detail.adopted_act is None:
            found = self._documents(linked[:1])
            detail.adopted_act = next(iter(found.values()), None)
        elif linked and not detail.act.is_project and not detail.projects:
            detail.projects = list(self._documents(linked).values())
        known = {item.id for item in detail.projects}
        if detail.adopted_act:
            known.add(detail.adopted_act.id)
        for row in rows:
            item = self.relations.item(row)
            related = RelatedDocument(
                id=item["id"], name=item["name"], kind=item["kind"],
                created=date.fromisoformat(item["created"]) if item["created"] else None,
                url=item["url"], reason=row.get("GroupTitle") or "", group=row.get("GroupTitle"),
                relation=row.get("RelationName"), registration_number=item["registration_number"],
                has_text=item["has_text"],
            )
            if row.get("RelationName") == "Addendum":
                detail.addenda.append(related)
            elif related.id not in known or row.get("RelationName") != "LegalAct":
                detail.related.append(related)
        return True

    def _addenda(self, document_id: int) -> list[RelatedDocument]:
        try:
            rows = self.client.query(
                DOCS_SET, filter_=f"LeadingDocument/Id eq {int(document_id)}", select="Id,Name,Created",
                expand="DocumentKind($select=Name)", orderby="Id asc", top=50,
            )
        except DirectumError:
            return []
        return [self._related(row, "приложение (ведущий документ — этот акт)") for row in rows]

    def _possibly_related(self, row: dict[str, Any], known: set[int]) -> list[RelatedDocument]:
        title = title_stems(row.get("Subject") or row.get("Name"))
        if not title:
            return []
        created = _parse(row.get("Created"))
        conditions = [f"Id ne {int(row['Id'])}", "HasRelations eq true"]
        if created:
            conditions += [f"Created ge {(created - timedelta(days=RELATED_BEFORE_DAYS)).isoformat()}",
                           f"Created lt {(created + timedelta(days=RELATED_AFTER_DAYS)).isoformat()}"]
        words = " or ".join(f"contains(Name,'{_literal(item)}')" for item in title[:RELATED_QUERY_STEMS])
        try:
            rows = self.client.query(
                DOCS_SET, filter_=" and ".join(conditions + [f"({words})"]), select="Id,Name,Created",
                expand="DocumentKind($select=Name)", orderby="Created desc", top=50,
            )
        except DirectumError:
            return []
        need = min(RELATED_MIN_SHARED, len(title))
        scored = []
        for item in rows:
            if int(item["Id"]) in known:
                continue
            name_stems = set(stems(item.get("Name") or ""))
            shared = [stem for stem in title if stem in name_stems]
            if len(shared) >= need:
                scored.append((len(shared), item, shared))
        scored.sort(key=lambda entry: (-entry[0], int(entry[1]["Id"])))
        return [
            self._related(item, "похожее название и период (связь не подтверждена): " + ", ".join(shared))
            for _, item, shared in scored[:RELATED_LIMIT]
        ]

    def _related(self, row: dict[str, Any], reason: str) -> RelatedDocument:
        created = _parse(row.get("Created"))
        document_id = int(row["Id"])
        return RelatedDocument(
            id=document_id,
            name=row.get("Name") or "",
            kind=_name(row.get("DocumentKind")),
            created=created.astimezone(self.tz).date() if created else None,
            url=self.client.build_document_card_url(document_id) if hasattr(self.client, "build_document_card_url") else None,
            reason=reason,
        )

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
