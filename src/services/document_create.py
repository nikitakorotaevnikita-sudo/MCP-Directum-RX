"""Создание документов и задач в Directum RX: исходящие письма, правовые акты, поручения, рассмотрение,
согласование, простые задачи.

Каждая операция в два шага: plan_* только читает справочники и возвращает план (что будет создано, с найденными
id); create_* исполняет план. Между шагами пользователь подтверждает предпросмотр (токен — в слое MCP).

mcpOGV может работать под служебной учёткой (Administrator): тогда «Автор» в RX — она. Поэтому реальный человек
передаётся явно: в документе — «Подготовил», в поручении — «Выдал», в задаче — «Автор» (правка черновика до
запуска). Если правка черновика не удалась, задача не запускается.
"""

import base64
import hashlib
import re
from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone, tzinfo
from typing import Any

from src.services.directum_client import DirectumError
from src.services.docx_builder import build_docx

DOCX_APPLICATION = "docx"
OUTGOING_LETTER_KIND = "Исходящее письмо"
LEGAL_ACT_KINDS = {"project": "Проект правового акта", "act": "Правовой акт"}
MAX_SUBJECT = 250
MAX_TASK_TEXT = 4000
MAX_APPROVERS = 10
MAX_PERFORMERS = 20
CANDIDATES = 6
NOTE_TEMPLATE = "Подготовлено ИИ-ассистентом (mcpOGV) по запросу: {name}, {day}."
REPORT_DEFAULT_WORKDAYS = 2
IMPORTANCE = {"normal": "Normal", "high": "High", "low": "Low"}


class CreateError(ValueError):
    """Ошибка параметров создания — понятная пользователю, без обращения к RX."""


def _literal(value: str) -> str:
    return value.replace("'", "''")


def _ref(value: dict[str, Any] | None) -> dict[str, Any] | None:
    return {"Id": int(value["id"])} if value else None


def _clean(text: str | None, limit: int, label: str) -> str:
    value = " ".join((text or "").split())
    if not value:
        raise CreateError(f"Не заполнено: {label}.")
    if len(value) > limit:
        raise CreateError(f"{label}: не длиннее {limit} символов (сейчас {len(value)}).")
    return value


def body_fingerprint(body_text: str | None) -> dict[str, Any] | None:
    if not (body_text or "").strip():
        return None
    return {"chars": len(body_text), "sha256": hashlib.sha256(body_text.encode("utf-8")).hexdigest()[:16]}


class DocumentCreateService:
    def __init__(
        self,
        client: Any,
        tz: tzinfo | None = None,
        grant_rights_guid: str | None = None,
        now: Callable[[], datetime] | None = None,
    ):
        self.client = client
        self.tz = tz or datetime.now().astimezone().tzinfo
        self.grant_rights_guid = (grant_rights_guid or "").strip() or None
        self._now = now or (lambda: datetime.now(timezone.utc))

    # --- справочники ---

    def employee_card(self, employee_id: int) -> dict[str, Any]:
        try:
            row = self.client.get_one(
                f"IEmployees({int(employee_id)})?$select=Id,Name,Status"
                "&$expand=Department($select=Id,Name;$expand=BusinessUnit($select=Id,Name))"
            )
        except DirectumError as exc:
            if exc.status_code in (400, 404):
                raise CreateError(f"Сотрудник с id {employee_id} не найден.") from None
            raise
        department = row.get("Department") or {}
        unit = department.get("BusinessUnit") or {}
        return {
            "id": int(row["Id"]),
            "name": row.get("Name") or "",
            "department": {"id": department["Id"], "name": department.get("Name")} if department.get("Id") else None,
            "business_unit": {"id": unit["Id"], "name": unit.get("Name")} if unit.get("Id") else None,
        }

    def counterparty(self, query: str) -> dict[str, Any]:
        text = (query or "").strip()
        if not text:
            raise CreateError("Укажите корреспондента: название организации или id.")
        if text.isdigit():
            rows = self.client.query("ICounterparties", filter_=f"Id eq {int(text)}", select="Id,Name", top=1)
            if not rows:
                raise CreateError(f"Корреспондент с id {text} не найден.")
            return {"id": int(rows[0]["Id"]), "name": rows[0].get("Name")}
        rows = self.client.query(
            "ICounterparties",
            filter_=f"Status eq 'Active' and contains(Name,'{_literal(text)}')",
            select="Id,Name",
            orderby="Name asc",
            top=CANDIDATES + 1,
        )
        if not rows:
            raise CreateError(f"Корреспондент «{text}» не найден. Уточните название или передайте id.")
        exact = [r for r in rows if (r.get("Name") or "").casefold() == text.casefold()]
        if len(exact) == 1 or len(rows) == 1:
            row = exact[0] if exact else rows[0]
            return {"id": int(row["Id"]), "name": row.get("Name")}
        listing = "; ".join(f"{r['Id']} — {r.get('Name')}" for r in rows[:CANDIDATES])
        raise CreateError(f"Найдено несколько корреспондентов: {listing}. Уточните (можно передать id).")

    def document_kind(self, name: str) -> dict[str, Any]:
        rows = self.client.query(
            "IDocumentKinds", filter_=f"Status eq 'Active' and Name eq '{_literal(name)}'", select="Id,Name", top=2
        )
        if not rows:
            raise CreateError(f"Вид документа «{name}» не найден в RX.")
        return {"id": int(rows[0]["Id"]), "name": rows[0].get("Name")}

    def document(self, document_id: int) -> dict[str, Any]:
        rows = self.client.query("IOfficialDocuments", filter_=f"Id eq {int(document_id)}", select="Id,Name", top=1)
        if not rows:
            raise CreateError(f"Документ с id {document_id} не найден или нет доступа.")
        return {"id": int(rows[0]["Id"]), "name": rows[0].get("Name"), "url": self._doc_url(int(rows[0]["Id"]))}

    def _application_id(self, extension: str) -> int:
        rows = self.client.query("IAssociatedApplications", filter_=f"Extension eq '{extension}'", select="Id", top=1)
        if not rows:
            raise DirectumError(f"В RX нет приложения для .{extension}", status_code=None)
        return int(rows[0]["Id"])

    def _note(self, person: dict[str, Any], extra: str | None) -> str:
        day = self._now().astimezone(self.tz).date().strftime("%d.%m.%Y")
        note = NOTE_TEMPLATE.format(name=person["name"], day=day)
        return f"{extra.strip()}\n{note}" if extra and extra.strip() else note

    # --- исходящее письмо ---

    def plan_outgoing_letter(
        self,
        subject: str,
        correspondent: str,
        prepared_by_id: int,
        signatory_id: int | None = None,
        in_response_to_id: int | None = None,
        body_text: str | None = None,
        note: str | None = None,
    ) -> dict[str, Any]:
        prepared = self.employee_card(prepared_by_id)
        self._require_org(prepared)
        return {
            "operation": "outgoing_letter",
            "subject": _clean(subject, MAX_SUBJECT, "тема письма"),
            "document_kind": self.document_kind(OUTGOING_LETTER_KIND),
            "correspondent": self.counterparty(correspondent),
            "prepared_by": {"id": prepared["id"], "name": prepared["name"]},
            "department": prepared["department"],
            "business_unit": prepared["business_unit"],
            "signatory": self._person(signatory_id),
            "in_response_to": self.document(in_response_to_id) if in_response_to_id else None,
            "note": self._note(prepared, note),
            "body": body_fingerprint(body_text),
        }

    def create_outgoing_letter(self, plan: dict[str, Any], body_text: str | None = None) -> dict[str, Any]:
        payload = {
            "Subject": plan["subject"],
            "DocumentKind": _ref(plan["document_kind"]),
            "Correspondent": _ref(plan["correspondent"]),
            "PreparedBy": _ref(plan["prepared_by"]),
            "Department": _ref(plan["department"]),
            "BusinessUnit": _ref(plan["business_unit"]),
            "Note": plan["note"],
        }
        if plan.get("signatory"):
            payload["OurSignatory"] = _ref(plan["signatory"])
        if plan.get("in_response_to"):
            payload["InResponseTo"] = _ref(plan["in_response_to"])
        return self._create_document("IOutgoingLetters", payload, plan, body_text)

    # --- правовой акт ---

    def plan_legal_act(
        self,
        kind: str,
        subject: str,
        prepared_by_id: int,
        is_normative: bool,
        signatory_id: int | None = None,
        body_text: str | None = None,
        note: str | None = None,
    ) -> dict[str, Any]:
        if kind not in LEGAL_ACT_KINDS:
            raise CreateError("Вид: project — проект правового акта, act — правовой акт.")
        prepared = self.employee_card(prepared_by_id)
        self._require_org(prepared)
        return {
            "operation": "legal_act",
            "kind": kind,
            "subject": _clean(subject, MAX_SUBJECT, "наименование акта"),
            "document_kind": self.document_kind(LEGAL_ACT_KINDS[kind]),
            "is_normative": bool(is_normative),
            "prepared_by": {"id": prepared["id"], "name": prepared["name"]},
            "department": prepared["department"],
            "business_unit": prepared["business_unit"],
            "signatory": self._person(signatory_id),
            "note": self._note(prepared, note),
            "body": body_fingerprint(body_text),
        }

    def create_legal_act(self, plan: dict[str, Any], body_text: str | None = None) -> dict[str, Any]:
        payload = {
            "Subject": plan["subject"],
            "DocumentKind": _ref(plan["document_kind"]),
            "IsNormative": plan["is_normative"],
            "PreparedBy": _ref(plan["prepared_by"]),
            "Department": _ref(plan["department"]),
            "BusinessUnit": _ref(plan["business_unit"]),
            "Note": plan["note"],
        }
        if plan.get("signatory"):
            payload["OurSignatory"] = _ref(plan["signatory"])
        return self._create_document("ILegalActsLegalDocumentBases", payload, plan, body_text)

    # --- поручение ---

    def plan_action_item(
        self,
        document_id: int,
        assignee_id: int,
        assigned_by_id: int,
        deadline: date,
        text: str,
        co_assignee_id: int | None = None,
        under_control: bool = False,
        supervisor_id: int | None = None,
    ) -> dict[str, Any]:
        today = self._now().astimezone(self.tz).date()
        if deadline < today:
            raise CreateError(f"Срок {deadline.strftime('%d.%m.%Y')} уже прошёл.")
        if supervisor_id is not None and not under_control:
            raise CreateError("Контролёр указывается только для поручения на контроле (under_control=true).")
        if co_assignee_id is not None and co_assignee_id == assignee_id:
            raise CreateError("Соисполнитель совпадает с исполнителем.")
        return {
            "operation": "action_item",
            "document": self.document(document_id),
            "assignee": self._person(assignee_id),
            "assigned_by": self._person(assigned_by_id),
            "co_assignee": self._person(co_assignee_id),
            "deadline": deadline.isoformat(),
            "text": _clean(text, MAX_TASK_TEXT, "текст поручения"),
            "under_control": bool(under_control),
            "supervisor": self._person(supervisor_id),
        }

    def create_action_item(self, plan: dict[str, Any]) -> dict[str, Any]:
        payload = {
            "documentId": plan["document"]["id"],
            "assigneeId": plan["assignee"]["id"],
            "isUnderControl": plan["under_control"],
            "supervisorId": plan["supervisor"]["id"] if plan.get("supervisor") else None,
            "coassigneeId": plan["co_assignee"]["id"] if plan.get("co_assignee") else None,
            "deadline": self._deadline_literal(plan["deadline"]),
            "activeText": plan["text"],
        }
        task_id = self._action_id(self.client.post("RecordManagement/CreateActionItemExecution", payload))
        return self._finish_task(task_id, "IActionItemExecutionTasks", {"AssignedBy": _ref(plan["assigned_by"])},
                                 "«Выдал»")

    # --- рассмотрение, согласование, простая задача ---

    def plan_review(self, document_id: int, addressee_id: int, author_id: int, text: str | None = None) -> dict[str, Any]:
        return {
            "operation": "review",
            "document": self.document(document_id),
            "addressee": self._person(addressee_id),
            "author": self._person(author_id),
            "text": " ".join((text or "").split())[:MAX_TASK_TEXT] or None,
        }

    def create_review(self, plan: dict[str, Any]) -> dict[str, Any]:
        payload = {"documentId": plan["document"]["id"], "addresseeId": plan["addressee"]["id"], "activeText": plan.get("text")}
        task_id = self._action_id(self.client.post("RecordManagement/CreateDocumentReviewTask", payload))
        return self._finish_task(task_id, "IDocumentReviewTasks", {"Author": _ref(plan["author"])}, "«Автор»")

    def plan_approval(
        self, document_id: int, author_id: int, signatory_id: int | None, approver_ids: list[int], text: str | None = None
    ) -> dict[str, Any]:
        if len(approver_ids) > MAX_APPROVERS:
            raise CreateError(f"Не больше {MAX_APPROVERS} дополнительных согласующих.")
        return {
            "operation": "approval",
            "document": self.document(document_id),
            "author": self._person(author_id),
            "signatory": self._person(signatory_id),
            "approvers": [self._person(i) for i in dict.fromkeys(approver_ids)],
            "text": " ".join((text or "").split())[:MAX_TASK_TEXT] or None,
        }

    def create_approval(self, plan: dict[str, Any]) -> dict[str, Any]:
        payload = {
            "documentId": plan["document"]["id"],
            "text": plan.get("text"),
            "signatoryId": plan["signatory"]["id"] if plan.get("signatory") else None,
            "addApproverIds": [a["id"] for a in plan["approvers"]],
        }
        task_id = self._action_id(self.client.post("Docflow/CreateApprovalTask", payload))
        return self._finish_task(task_id, "IApprovalTasks", {"Author": _ref(plan["author"])}, "«Автор»")

    def plan_task(
        self,
        subject: str,
        performer_ids: list[int],
        author_id: int,
        deadline: date | None,
        text: str | None = None,
        document_ids: list[int] | None = None,
        importance: str = "normal",
    ) -> dict[str, Any]:
        if not performer_ids:
            raise CreateError("Укажите хотя бы одного исполнителя задачи.")
        if len(performer_ids) > MAX_PERFORMERS:
            raise CreateError(f"Не больше {MAX_PERFORMERS} исполнителей.")
        if importance not in IMPORTANCE:
            raise CreateError("Важность: normal, high или low.")
        today = self._now().astimezone(self.tz).date()
        if deadline is not None and deadline < today:
            raise CreateError(f"Срок {deadline.strftime('%d.%m.%Y')} уже прошёл.")
        return {
            "operation": "task",
            "subject": _clean(subject, MAX_SUBJECT, "тема задачи"),
            "performers": [self._person(i) for i in dict.fromkeys(performer_ids)],
            "author": self._person(author_id),
            "deadline": deadline.isoformat() if deadline else None,
            "text": " ".join((text or "").split())[:MAX_TASK_TEXT] or None,
            "documents": [self.document(i) for i in dict.fromkeys(document_ids or [])],
            "importance": importance,
        }

    def create_task(self, plan: dict[str, Any]) -> dict[str, Any]:
        payload = {
            "assignmentType": "Assignment",
            "subject": plan["subject"],
            "deadline": self._deadline_literal(plan["deadline"]) if plan.get("deadline") else None,
            "importance": IMPORTANCE[plan["importance"]],
            "text": plan.get("text"),
            "performerIds": [p["id"] for p in plan["performers"]],
            "observerIds": [],
            "documentIds": [d["id"] for d in plan["documents"]],
        }
        task_id = self._action_id(self.client.post("Docflow/CreateSimpleTask", payload))
        return self._finish_task(task_id, "ISimpleTasks", {"Author": _ref(plan["author"])}, "«Автор»")

    # --- запрос отчёта по поручению ---

    def plan_report_request(
        self,
        action_item_id: int,
        requested_by_id: int,
        deadline: date | None = None,
        text: str | None = None,
        assignee_id: int | None = None,
    ) -> dict[str, Any]:
        """Запрос отчёта по поручению (кнопка «Запросить отчёт»): подзадача поручения, ответственному — задание дать
        отчёт. Запрашивать может тот, кто выдал поручение, его контролёр или автор задачи: под служебной учёткой
        RX этого не проверит, поэтому проверяем здесь."""
        task = self._action_item_row(action_item_id)
        if task.get("Status") != "InProcess":
            raise CreateError("Отчёт можно запросить только по поручению в работе.")
        allowed = {_id(task.get(k)) for k in ("AssignedBy", "Supervisor", "Author")} - {None}
        if requested_by_id not in allowed:
            names = ", ".join(n for n in (_name_of(task.get(k)) for k in ("AssignedBy", "Supervisor")) if n)
            raise CreateError(f"Запросить отчёт может только тот, кто выдал поручение, или его контролёр ({names}).")
        assignee = self._person(assignee_id) if assignee_id is not None else (
            {"id": _id(task.get("Assignee")), "name": _name_of(task.get("Assignee"))} if _id(task.get("Assignee")) else None)
        if assignee is None:
            raise CreateError("У поручения нет ответственного исполнителя (составное поручение) — укажите, у кого "
                              "запросить отчёт (assignee).")
        today = self._now().astimezone(self.tz).date()
        day = deadline or _plus_workdays(today, REPORT_DEFAULT_WORKDAYS)
        if day < today:
            raise CreateError(f"Срок {day.strftime('%d.%m.%Y')} уже прошёл.")
        subject = (task.get("Subject") or "").strip()
        subject = re.sub(r"^\s*(?:>>\s*)?Поручение:\s*", "", subject)
        return {
            "operation": "report_request",
            "action_item": {"id": int(task["Id"]), "subject": subject[:MAX_SUBJECT],
                            "url": self.client.build_client_card_url(f"IActionItemExecutionTasks({int(task['Id'])})")
                            if hasattr(self.client, "build_client_card_url") else None},
            "assignee": assignee,
            "author": self._person(requested_by_id),
            "deadline": day.isoformat(),
            "deadline_is_default": deadline is None,
            "text": " ".join((text or "").split())[:MAX_TASK_TEXT] or "Прошу представить отчёт о ходе исполнения поручения.",
        }

    def create_report_request(self, plan: dict[str, Any]) -> dict[str, Any]:
        payload = {
            "Subject": f"Запрос отчета: {plan['action_item']['subject']}"[:MAX_SUBJECT],
            "ParentTask": {"Id": plan["action_item"]["id"]},
            "Assignee": _ref(plan["assignee"]),
            "MaxDeadline": self._deadline_literal(plan["deadline"]),
        }
        row = self.client.post("IStatusReportRequestTasks", payload)
        task_id = int(row["Id"])
        warnings: list[str] = []
        try:
            self.client.post(f"IStatusReportRequestTasks({task_id})/Texts", {"Body": plan["text"]})
        except DirectumError as exc:
            warnings.append(f"Текст запроса не добавлен: {_reason(exc)}. Тема задачи и срок заданы.")
        result = self._finish_task(task_id, "IStatusReportRequestTasks", {"Author": _ref(plan["author"])}, "«Автор»")
        result["warnings"] = warnings + result["warnings"]
        return result

    def _action_item_row(self, action_item_id: int) -> dict[str, Any]:
        try:
            return self.client.get_one(
                f"IActionItemExecutionTasks({int(action_item_id)})?$select=Id,Subject,Status"
                "&$expand=Assignee($select=Id,Name),AssignedBy($select=Id,Name),Supervisor($select=Id,Name),Author($select=Id,Name)"
            )
        except DirectumError as exc:
            if exc.status_code in (400, 404):
                raise CreateError(f"Поручение (задача) с id {action_item_id} не найдено. Передайте id задачи "
                                  "из реестра или досье, а не id задания.") from None
            raise

    # --- общие шаги ---

    def _create_document(self, entity_set: str, payload: dict[str, Any], plan: dict[str, Any], body_text: str | None) -> dict[str, Any]:
        warnings: list[str] = []
        try:
            row = self.client.post(entity_set, payload)
        except DirectumError as exc:
            raise DirectumError(f"RX не сохранил документ: {_reason(exc)}", status_code=exc.status_code) from None
        document_id = int(row["Id"])
        result: dict[str, Any] = {"document_id": document_id, "name": row.get("Name"), "url": self._doc_url(document_id)}
        if plan.get("body"):
            try:
                result["version_id"] = self._upload_docx(entity_set, document_id, body_text or "")
            except (DirectumError, ValueError) as exc:
                warnings.append(f"Документ создан, но текст не загружен: {_reason(exc)}. Добавьте версию в RX вручную.")
        warnings.extend(self._grant(document_id, plan["prepared_by"]))
        result["warnings"] = warnings
        return result

    def _upload_docx(self, entity_set: str, document_id: int, body_text: str) -> int:
        version = self.client.post(f"{entity_set}({document_id})/Versions", {"AssociatedApplication": {"Id": self._application_id(DOCX_APPLICATION)}})
        version_id = int(version["Id"])
        data = base64.b64encode(build_docx(body_text)).decode("ascii")
        self.client.patch(f"{entity_set}({document_id})/Versions({version_id})/Body", {"Value": data})
        return version_id

    def _grant(self, document_id: int, person: dict[str, Any]) -> list[str]:
        if not self.grant_rights_guid:
            return ["Права на документ сотруднику не выданы (MCP_WRITE_GRANT_RIGHTS_GUID не задан): документ создан "
                    "служебной учёткой и может быть не виден сотруднику — проверьте доступ в RX."]
        try:
            self.client.post("Docflow/GrantAccessRightsToDocument",
                             {"documentId": document_id, "recipientId": person["id"], "accessRightsTypeGuid": self.grant_rights_guid})
        except DirectumError as exc:
            return [f"Права на документ сотруднику {person['name']} не выданы: {_reason(exc)}."]
        return []

    def _finish_task(self, task_id: int, entity_set: str, fields: dict[str, Any], label: str) -> dict[str, Any]:
        url = self.client.build_client_card_url(f"{entity_set}({task_id})") if hasattr(self.client, "build_client_card_url") else None
        result: dict[str, Any] = {"task_id": task_id, "url": url, "started": False, "warnings": []}
        try:
            self.client.patch(f"{entity_set}({task_id})", fields)
        except DirectumError as exc:
            result["warnings"].append(
                f"Не удалось проставить {label} в черновике: {_reason(exc)}. Задача НЕ запущена — она в черновиках "
                "служебной учётки; запустите её в RX вручную или удалите.")
            return result
        try:
            self.client.post("Docflow/StartTask", {"taskId": task_id})
        except DirectumError as exc:
            result["warnings"].append(f"Задача создана, но не запущена: {_reason(exc)}.")
            return result
        result["started"] = True
        return result

    # --- мелочи ---

    def _person(self, employee_id: int | None) -> dict[str, Any] | None:
        if employee_id is None:
            return None
        card = self.employee_card(employee_id)
        return {"id": card["id"], "name": card["name"]}

    @staticmethod
    def _require_org(person: dict[str, Any]) -> None:
        if not person["department"] or not person["business_unit"]:
            raise CreateError(f"У сотрудника {person['name']} в RX не указано подразделение или организация — "
                              "документ не сохранится. Укажите другого «Подготовил».")

    def _deadline_literal(self, iso_day: str) -> str:
        day = date.fromisoformat(iso_day)
        return datetime(day.year, day.month, day.day, tzinfo=self.tz).isoformat()

    @staticmethod
    def _action_id(response: dict[str, Any]) -> int:
        raw = response.get("value", response.get("Id"))
        if isinstance(raw, int):
            return raw
        if isinstance(raw, str) and raw.isdecimal():
            return int(raw)
        raise DirectumError("RX вернул неожиданный ответ вместо id задачи")

    def _doc_url(self, document_id: int) -> str | None:
        return self.client.build_document_card_url(document_id) if hasattr(self.client, "build_document_card_url") else None


def _id(value: Any) -> int | None:
    ident = value.get("Id") if isinstance(value, dict) else None
    return int(ident) if isinstance(ident, int) else None


def _name_of(value: Any) -> str | None:
    name = value.get("Name") if isinstance(value, dict) else None
    return name if isinstance(name, str) and name.strip() else None


def _plus_workdays(day: date, count: int) -> date:
    while count > 0:
        day += timedelta(days=1)
        if day.weekday() < 5:
            count -= 1
    return day


def _reason(exc: Exception) -> str:
    text = getattr(exc, "safe_message", None) or str(exc)
    # «POST X failed with 400. Payload had … Response detail: <текст RX>» → только текст RX.
    match = re.search(r"Response detail:\s*(.+)$", text, re.S)
    return (match.group(1) if match else text).strip().strip('"') or "без пояснений"
