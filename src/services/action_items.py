from typing import Any

from src.models.schemas import ActionItemCreateRequest, ActionItemCreateResult, EmployeeSummary
from src.services.directum_client import DirectumClient


class ActionItemService:
    def __init__(self, client: DirectumClient):
        self.client = client

    def search_employee(self, query: str, top: int = 10) -> list[EmployeeSummary]:
        escaped_query = query.strip().replace("'", "''")
        rows = self.client.query(
            "IEmployees",
            filter_=f"contains(Name,'{escaped_query}') and Status eq 'Active'",
            select="Id,Name,Status",
            top=top,
        )
        return [
            EmployeeSummary(
                id=int(row["Id"]),
                name=row["Name"],
                status=row.get("Status"),
            )
            for row in rows
        ]

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

        response = self.client.post("IActionItemExecutionTasks", payload)
        return ActionItemCreateResult(
            mode="created",
            payload=payload,
            success=True,
            directum_id=int(response["Id"]) if response.get("Id") is not None else None,
            message="Action item created.",
        )

    def _payload(self, request: ActionItemCreateRequest) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "Subject": request.subject,
            "PerformersGD": str(request.performer_id),
            "ActionItem": request.action_text,
            "ExecutionState": "OnExecution",
        }
        if request.deadline is not None:
            payload["Deadline"] = request.deadline.isoformat()
        return payload
