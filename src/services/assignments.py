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
        return AssignmentSummary(
            id=int(row["Id"]),
            subject=row.get("Subject") or row.get("Name") or "",
            status=row.get("Status"),
            deadline=row.get("Deadline"),
            entity_type=entity_type,
            url=row.get("ClientHyperlink") or row.get("EntityHyperlink"),
        )
