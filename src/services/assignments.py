from datetime import datetime, timezone
from typing import Any

from src.models.schemas import AssignmentSummary
from src.services.current_user import CurrentUserService
from src.services.directum_client import DirectumClient


class AssignmentsService:
    def __init__(self, client: DirectumClient, current_user_service: CurrentUserService):
        self.client = client
        self.current_user_service = current_user_service

    ACTION_ITEM_SOURCES = {
        "incoming": ("IActionItemExecutionAssignments", "Performer"),
        "outgoing": ("IActionItemExecutionTasks", "Author"),
    }

    def get_my_assignments(self, top: int | None = None) -> list[AssignmentSummary]:
        rows = self.client.query(
            "IAssignments",
            filter_=self._my_assignments_filter(only_overdue=False),
            select="Id,Subject,Deadline,Status",
            expand="Task($select=Id,Subject)",
            orderby="Deadline asc",
            top=top,
            count=True,
        )
        return [self._assignment(row, "assignment") for row in rows]

    def get_overdue_assignments(self, top: int | None = None) -> list[AssignmentSummary]:
        rows = self.client.query(
            "IAssignments",
            filter_=self._my_assignments_filter(only_overdue=True),
            select="Id,Subject,Deadline,Status",
            orderby="Deadline asc",
            top=top,
        )
        return [self._assignment(row, "overdue_assignment") for row in rows]

    def get_action_items_assigned_to_me(self, top: int | None = None) -> list[AssignmentSummary]:
        entity_set, action_filter = self._action_items_source("incoming")
        rows = self.client.query(
            entity_set,
            filter_=action_filter,
            select="Id,Subject,Deadline,Status",
            orderby="Deadline asc",
            top=top,
        )
        return [self._assignment(row, "action_item_assignment") for row in rows]

    def get_action_items_created_by_me(self, top: int | None = None) -> list[AssignmentSummary]:
        entity_set, action_filter = self._action_items_source("outgoing")
        rows = self.client.query(
            entity_set,
            filter_=action_filter,
            select="Id,Subject,Deadline,Status",
            expand="Assignee($select=Name)",
            orderby="Deadline asc",
            top=top,
        )
        return [self._assignment(row, "action_item_task") for row in rows]

    def count_my_assignments(self, only_overdue: bool = False) -> int:
        return self.client.count("IAssignments", filter_=self._my_assignments_filter(only_overdue))

    def count_action_items(self, direction: str) -> int:
        entity_set, action_filter = self._action_items_source(direction)
        return self.client.count(entity_set, filter_=action_filter)

    def _my_assignments_filter(self, only_overdue: bool) -> str:
        user = self.current_user_service.get_current_user()
        base = f"Performer/Id eq {user.id} and Status eq 'InProcess'"
        if not only_overdue:
            return base
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        return f"{base} and Deadline lt {now}"

    def _action_items_source(self, direction: str) -> tuple[str, str]:
        if direction not in self.ACTION_ITEM_SOURCES:
            raise ValueError(f"Unknown action items direction: {direction}")
        entity_set, role = self.ACTION_ITEM_SOURCES[direction]
        user = self.current_user_service.get_current_user()
        return entity_set, f"{role}/Id eq {user.id} and Status eq 'InProcess'"

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
            performer=self._performer_name(row),
        )

    @staticmethod
    def _performer_name(row: dict[str, Any]) -> str | None:
        assignee = row.get("Assignee")
        if isinstance(assignee, dict):
            name = assignee.get("Name")
            if isinstance(name, str) and name.strip():
                return name.strip()
        return None

    def _entity_path(self, entity_type: str, directum_id: int) -> str | None:
        entity_sets = {
            "assignment": "IAssignments",
            "overdue_assignment": "IAssignments",
            "action_item_assignment": "IActionItemExecutionAssignments",
            "action_item_task": "IActionItemExecutionTasks",
        }
        entity_set = entity_sets.get(entity_type)
        return f"{entity_set}({directum_id})" if entity_set else None
