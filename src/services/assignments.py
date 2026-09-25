from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any

from src.models.schemas import AssignmentSummary
from src.services.current_user import CurrentUserService
from src.services.directum_client import DirectumClient


ACTION_ITEM_STATUSES = {"in_process": "InProcess", "completed": "Completed", "aborted": "Aborted", "all": None}
ACTION_ITEM_DATE_FIELDS = {"deadline": "Deadline", "created": "Created"}
OVERDUE_COMPATIBLE_STATUSES = ("in_process", "all")


@dataclass(frozen=True)
class ActionItemFilters:
    status: str = "in_process"
    only_overdue: bool = False
    date_field: str = "deadline"
    date_from: date | None = None
    date_to: date | None = None


def _utc_now_literal() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _day_literal(day: date) -> str:
    return f"{day.isoformat()}T00:00:00Z"


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

    def list_employee_action_items(
        self,
        direction: str,
        employee_id: int,
        filters: ActionItemFilters | None = None,
        top: int | None = None,
    ) -> list[AssignmentSummary]:
        entity_set, action_filter = self.action_items_filter(direction, employee_id, filters)
        rows = self.client.query(
            entity_set,
            filter_=action_filter,
            select="Id,Subject,Deadline,Status",
            expand="Assignee($select=Name)" if direction == "outgoing" else None,
            orderby="Deadline asc",
            top=top,
        )
        entity_type = "action_item_task" if direction == "outgoing" else "action_item_assignment"
        return [self._assignment(row, entity_type) for row in rows]

    def count_employee_action_items(
        self, direction: str, employee_id: int, filters: ActionItemFilters | None = None
    ) -> int:
        entity_set, action_filter = self.action_items_filter(direction, employee_id, filters)
        return self.client.count(entity_set, filter_=action_filter)

    def action_items_filter(
        self, direction: str, employee_id: int, filters: ActionItemFilters | None = None
    ) -> tuple[str, str]:
        filters = filters or ActionItemFilters()
        if direction not in self.ACTION_ITEM_SOURCES:
            raise ValueError(f"Unknown action items direction: {direction}")
        if filters.status not in ACTION_ITEM_STATUSES:
            raise ValueError(f"Unknown action items status: {filters.status}")
        if filters.date_field not in ACTION_ITEM_DATE_FIELDS:
            raise ValueError(f"Unknown action items date field: {filters.date_field}")
        if filters.only_overdue and filters.status not in OVERDUE_COMPATIBLE_STATUSES:
            raise ValueError("Only action items in process can be overdue")
        entity_set, role = self.ACTION_ITEM_SOURCES[direction]
        conditions = [f"{role}/Id eq {int(employee_id)}"]
        status = "InProcess" if filters.only_overdue else ACTION_ITEM_STATUSES[filters.status]
        if status:
            conditions.append(f"Status eq '{status}'")
        if filters.only_overdue:
            conditions.append(f"Deadline lt {_utc_now_literal()}")
        field = ACTION_ITEM_DATE_FIELDS[filters.date_field]
        if filters.date_from:
            conditions.append(f"{field} ge {_day_literal(filters.date_from)}")
        if filters.date_to:
            conditions.append(f"{field} lt {_day_literal(filters.date_to + timedelta(days=1))}")
        return entity_set, " and ".join(conditions)

    def _my_assignments_filter(self, only_overdue: bool) -> str:
        user = self.current_user_service.get_current_user()
        base = f"Performer/Id eq {user.id} and Status eq 'InProcess'"
        if not only_overdue:
            return base
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        return f"{base} and Deadline lt {now}"

    def _action_items_source(self, direction: str) -> tuple[str, str]:
        user = self.current_user_service.get_current_user()
        return self.action_items_filter(direction, user.id)

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
