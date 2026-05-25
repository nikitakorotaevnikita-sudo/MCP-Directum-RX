from datetime import datetime, timezone
from typing import Any

from src.models.schemas import AssignmentSummary
from src.services.current_user import CurrentUserService
from src.services.directum_client import DirectumClient


class AssignmentsService:
    def __init__(self, client: DirectumClient, current_user_service: CurrentUserService):
        self.client = client
        self.current_user_service = current_user_service

    def get_my_assignments(self) -> list[AssignmentSummary]:
        user = self.current_user_service.get_current_user()
        rows = self.client.query(
            "IAssignments",
            filter_=f"Performer/Id eq {user.id} and Status eq 'InProcess'",
            select="Id,Subject,Deadline,Status",
            expand="Task($select=Id,Subject)",
            orderby="Deadline asc",
            count=True,
        )
        return [self._assignment(row, "assignment") for row in rows]

    def get_overdue_assignments(self) -> list[AssignmentSummary]:
        user = self.current_user_service.get_current_user()
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        rows = self.client.query(
            "IAssignments",
            filter_=f"Performer/Id eq {user.id} and Status eq 'InProcess' and Deadline lt {now}",
            select="Id,Subject,Deadline,Status",
            orderby="Deadline asc",
        )
        return [self._assignment(row, "overdue_assignment") for row in rows]

    def get_action_items_assigned_to_me(self) -> list[AssignmentSummary]:
        user = self.current_user_service.get_current_user()
        rows = self.client.query(
            "IActionItemExecutionAssignments",
            filter_=f"Performer/Id eq {user.id} and Status eq 'InProcess'",
            select="Id,Subject,Deadline,Status",
            orderby="Deadline asc",
        )
        return [self._assignment(row, "action_item_assignment") for row in rows]

    def get_action_items_created_by_me(self) -> list[AssignmentSummary]:
        user = self.current_user_service.get_current_user()
        rows = self.client.query(
            "IActionItemExecutionTasks",
            filter_=f"Author/Id eq {user.id} and Status eq 'InProcess'",
            select="Id,Subject,Deadline,Status",
            orderby="Deadline asc",
        )
        return [self._assignment(row, "action_item_task") for row in rows]

    def _assignment(self, row: dict[str, Any], entity_type: str) -> AssignmentSummary:
        directum_id = int(row["Id"])
        entity_path = self._entity_path(entity_type, directum_id)
        client_url = (
            self.client.build_client_card_url(entity_path)
            if entity_path and hasattr(self.client, "build_client_card_url")
            else None
        )
        return AssignmentSummary(
            id=directum_id,
            subject=row.get("Subject") or row.get("Name") or "",
            status=row.get("Status"),
            deadline=row.get("Deadline"),
            entity_type=entity_type,
            url=client_url.strip() if isinstance(client_url, str) and client_url.strip() else None,
        )

    def _entity_path(self, entity_type: str, directum_id: int) -> str | None:
        entity_sets = {
            "assignment": "IAssignments",
            "overdue_assignment": "IAssignments",
            "action_item_assignment": "IActionItemExecutionAssignments",
            "action_item_task": "IActionItemExecutionTasks",
        }
        entity_set = entity_sets.get(entity_type)
        return f"{entity_set}({directum_id})" if entity_set else None
