from typing import Any

from src.models.schemas import (
    ActionItemCreateRequest,
    ActionItemCreateResult,
    DocumentSummary,
    EmployeeSummary,
    TaskCreateRequest,
    TaskCreateResult,
)
from src.services.directum_client import DirectumClient, DirectumError


EMPLOYEE_QUERY_STRIP_CHARS = " \t\r\n.,;:!?\"'\u00ab\u00bb"


class ActionItemService:
    def __init__(self, client: DirectumClient):
        self.client = client

    def search_employee(self, query: str, top: int = 10) -> list[EmployeeSummary]:
        cleaned = self._clean_employee_query(query)
        if not cleaned:
            return []

        rows = self._query_employees(cleaned, top)
        if not rows:
            for token in self._fallback_name_tokens(cleaned):
                rows = self._query_employees(token, top)
                if rows:
                    break
        return [
            EmployeeSummary(
                id=int(row["Id"]),
                name=row["Name"],
                status=row.get("Status"),
            )
            for row in rows
        ]

    def _query_employees(self, query: str, top: int) -> list[dict[str, Any]]:
        escaped_query = query.replace("'", "''")
        return self.client.query(
            "IEmployees",
            filter_=f"contains(Name,'{escaped_query}') and Status eq 'Active'",
            select="Id,Name,Status",
            top=top,
        )

    def _clean_employee_query(self, query: str) -> str:
        return query.strip(EMPLOYEE_QUERY_STRIP_CHARS)

    def _fallback_name_tokens(self, query: str) -> list[str]:
        tokens = [token.strip(EMPLOYEE_QUERY_STRIP_CHARS) for token in query.split()]
        return [token for token in reversed(tokens) if len(token) > 1 and token != query]

    def search_documents(self, query: str, top: int = 10) -> list[DocumentSummary]:
        cleaned = self._clean_employee_query(query)
        if not cleaned:
            return []

        rows = self._query_documents(cleaned, top)
        if not rows:
            for token in self._fallback_document_tokens(cleaned):
                rows = self._query_documents(token, top)
                if rows:
                    break
        return [
            DocumentSummary(
                id=int(row["Id"]),
                name=row.get("Name") or "",
                subject=row.get("Subject"),
                registration_number=row.get("RegistrationNumber"),
                registration_date=row.get("RegistrationDate"),
            )
            for row in rows
        ]

    def _query_documents(self, query: str, top: int) -> list[dict[str, Any]]:
        escaped_query = query.replace("'", "''")
        return self.client.query(
            "IOfficialDocuments",
            filter_=(
                f"(contains(Name,'{escaped_query}') or contains(Subject,'{escaped_query}')) "
                "and RegistrationDate ne null"
            ),
            select="Id,Name,Subject,RegistrationNumber,RegistrationDate",
            orderby="RegistrationDate desc",
            top=top,
        )

    def _fallback_document_tokens(self, query: str) -> list[str]:
        tokens = [token.strip(EMPLOYEE_QUERY_STRIP_CHARS) for token in query.split()]
        ignored = {"для", "документы", "документ", "проверь", "проверить", "подготовь", "подготовьте", "подготовить"}
        fallback: list[str] = []
        for token in reversed(tokens):
            lowered = token.lower()
            if lowered == "мц":
                fallback.append("Минцифр")
                continue
            if len(token) <= 3 or lowered in ignored:
                continue
            fallback.append(token)
            if len(token) > 5 and lowered[-1:] in {"а", "е", "у", "ы", "я", "ю"}:
                fallback.append(token[:-1])
        return fallback

    def create_action_item(self, request: ActionItemCreateRequest) -> ActionItemCreateResult:
        payload = self._payload(request)
        if not request.confirm:
            return ActionItemCreateResult(
                mode="preview",
                payload=payload,
                success=True,
                directum_id=None,
                message="Preview generated; confirm to create the action item.",
            )

        document_id = request.document_id or self._resolve_document_id(request)
        if document_id is None:
            raise DirectumError("Action item creation requires document_id for Directum RX")

        payload = self._payload(request, document_id=document_id)
        response = self.client.post("RecordManagement/CreateActionItemExecution", payload)
        directum_id = self._directum_id(response)
        self.client.post("Docflow/StartTask", {"taskId": directum_id})
        url = self._action_item_url(directum_id)
        return ActionItemCreateResult(
            mode="created",
            payload=payload,
            success=True,
            directum_id=directum_id,
            url=url,
            message="Action item created.",
        )

    def _resolve_document_id(self, request: ActionItemCreateRequest) -> int | None:
        documents = self.search_documents(f"{request.subject} {request.action_text}", top=1)
        return documents[0].id if documents else None

    def _payload(self, request: ActionItemCreateRequest, document_id: int | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "documentId": request.document_id if document_id is None else document_id,
            "assigneeId": request.performer_id,
            "isUnderControl": False,
            "supervisorId": None,
            "coassigneeId": None,
            "deadline": None,
            "activeText": request.action_text,
        }
        if request.deadline is not None:
            if request.deadline.tzinfo is None or request.deadline.utcoffset() is None:
                raise DirectumError("Action item deadline must include timezone")
            payload["deadline"] = request.deadline.isoformat()
        return payload

    def _directum_id(self, response: dict[str, Any]) -> int:
        raw_id = response.get("value", response.get("Id"))
        if isinstance(raw_id, int):
            return raw_id
        if isinstance(raw_id, str) and raw_id.isdecimal():
            return int(raw_id)
        raise DirectumError("Directum returned an invalid action item id")

    def _action_item_url(self, directum_id: int) -> str | None:
        entity_path = f"IActionItemExecutionTasks({directum_id})"
        try:
            item = self.client.get_one(entity_path)
            hyperlink = item.get("ClientHyperlink") or item.get("EntityHyperlink")
            if isinstance(hyperlink, str) and hyperlink.strip():
                return hyperlink.strip()
        except DirectumError:
            pass
        if hasattr(self.client, "build_url"):
            return self.client.build_url(entity_path)
        return None

    def create_task(self, request: TaskCreateRequest) -> TaskCreateResult:
        payload = self._task_payload(request)
        if not request.confirm:
            return TaskCreateResult(
                mode="preview",
                payload=payload,
                success=True,
                directum_id=None,
                message="Preview generated; confirm to create the task.",
            )

        response = self.client.post("Docflow/CreateSimpleTask", payload)
        directum_id = self._directum_id(response)
        self.client.post("Docflow/StartTask", {"taskId": directum_id})
        url = self._task_url(directum_id)
        return TaskCreateResult(
            mode="created",
            payload=payload,
            success=True,
            directum_id=directum_id,
            url=url,
            message="Task created.",
        )

    def _task_payload(self, request: TaskCreateRequest) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "assignmentType": "Assignment",
            "subject": request.subject,
            "deadline": None,
            "importance": "Normal",
            "text": request.action_text,
            "performerIds": [request.performer_id],
            "observerIds": [],
            "documentIds": [],
        }
        if request.deadline is not None:
            if request.deadline.tzinfo is None or request.deadline.utcoffset() is None:
                raise DirectumError("Task deadline must include timezone")
            payload["deadline"] = request.deadline.isoformat()
        return payload

    def _task_url(self, directum_id: int) -> str | None:
        entity_path = f"ISimpleTasks({directum_id})"
        try:
            item = self.client.get_one(entity_path)
            hyperlink = item.get("ClientHyperlink") or item.get("EntityHyperlink")
            if isinstance(hyperlink, str) and hyperlink.strip():
                return hyperlink.strip()
        except DirectumError:
            pass
        if hasattr(self.client, "build_url"):
            return self.client.build_url(entity_path)
        return None
