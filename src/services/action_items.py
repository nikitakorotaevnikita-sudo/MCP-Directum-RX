from typing import Any

from src.models.schemas import ActionItemCreateRequest, ActionItemCreateResult, EmployeeSummary
from src.services.directum_client import DirectumClient, DirectumError


class ActionItemService:
    def __init__(self, client: DirectumClient):
        self.client = client

    def search_employee(self, query: str, top: int = 10) -> list[EmployeeSummary]:
        cleaned = query.strip()
        if not cleaned:
            return []

        escaped_query = cleaned.replace("'", "''")
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
        directum_id = self._directum_id(response)
        url = self._action_item_url(directum_id)
        return ActionItemCreateResult(
            mode="created",
            payload=payload,
            success=True,
            directum_id=directum_id,
            url=url,
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
            if request.deadline.tzinfo is None or request.deadline.utcoffset() is None:
                raise DirectumError("Action item deadline must include timezone")
            payload["Deadline"] = request.deadline.isoformat()
        return payload

    def _directum_id(self, response: dict[str, Any]) -> int:
        raw_id = response.get("Id")
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
