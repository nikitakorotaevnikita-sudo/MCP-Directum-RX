from datetime import datetime, timedelta, timezone
from typing import Any

from src.models.schemas import ActionItemDetail, MeetingSummary
from src.services.current_user import CurrentUserService
from src.services.directum_client import DirectumClient, DirectumError


class MeetingsService:
    def __init__(self, client: DirectumClient, current_user_service: CurrentUserService):
        self.client = client
        self.current_user_service = current_user_service

    def get_my_meetings(self, days: int = 7) -> list[MeetingSummary]:
        user = self.current_user_service.get_current_user()
        today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        end = today + timedelta(days=days)
        rows = self._query_upcoming_meetings(user.id, today, end)
        return [self._to_meeting_summary(row) for row in rows]

    def _query_upcoming_meetings(
        self,
        user_id: int,
        start: datetime,
        end: datetime,
    ) -> list[dict[str, Any]]:
        start_text = start.strftime("%Y-%m-%dT%H:%M:%SZ")
        end_text = end.strftime("%Y-%m-%dT%H:%M:%SZ")
        last_error: DirectumError | None = None
        for start_field in ("DateTime", "StartDate", "Date"):
            filter_ = (
                f"{start_field} ge {start_text} "
                f"and {start_field} le {end_text} "
                f"and (Members/any(m: m/Member/Id eq {user_id}) "
                f"or President/Id eq {user_id} "
                f"or Secretary/Id eq {user_id})"
            )
            try:
                return self.client.query(
                    "IMeetings",
                    filter_=filter_,
                    select="Id,Name,DateTime,Location,Note,Duration,Status",
                    orderby=f"{start_field} asc",
                    top=20,
                )
            except DirectumError as exc:
                if exc.status_code != 400:
                    raise
                last_error = exc
        if last_error is not None:
            raise last_error
        return []

    def _to_meeting_summary(self, row: dict[str, Any]) -> MeetingSummary:
        meeting_id = int(row["Id"])
        entity_path = f"IMeetings({meeting_id})"
        card_url = self.client.build_client_card_url(entity_path) or ""
        minutes = row.get("Minutes") or []
        agenda: str | None = None
        if minutes and isinstance(minutes, list):
            first = minutes[0] if isinstance(minutes[0], dict) else {}
            raw = first.get("Description") or first.get("Subject") or ""
            if raw:
                agenda = raw[:200]
        subject = self._first_text(row, "Subject", "Name", "Topic")
        if not agenda:
            agenda = self._first_text(row, "Note") or subject or None
        return MeetingSummary(
            id=meeting_id,
            subject=subject,
            start_date=self._first_value(row, "DateTime", "StartDate", "Date"),
            end_date=self._first_value(row, "EndDate", "End", "FinishDateTime"),
            place=self._first_text(row, "Place", "Location"),
            agenda_summary=agenda,
            client_card_url=card_url,
        )

    def _first_value(self, row: dict[str, Any], *keys: str) -> Any:
        for key in keys:
            value = row.get(key)
            if value not in (None, ""):
                return value
        return None

    def _first_text(self, row: dict[str, Any], *keys: str) -> str:
        value = self._first_value(row, *keys)
        return str(value) if value is not None else ""

    def get_action_item_details(self, action_item_id: int) -> ActionItemDetail:
        try:
            row = self._query_action_item_details(action_item_id)
        except DirectumError as exc:
            if exc.status_code == 404:
                raise DirectumError(
                    f"Поручение #{action_item_id} не найдено.",
                    status_code=404,
                ) from exc
            raise
        current_user = self.current_user_service.get_current_user()
        author_id = self._person_id(row, "Author", "AssignedBy")
        if author_id != current_user.id:
            raise DirectumError(
                "Поручение найдено, но вы не являетесь его автором.",
                status_code=403,
            )
        return self._to_action_item_detail(row)

    def _query_action_item_details(self, action_item_id: int) -> dict[str, Any]:
        live_expand = (
            "Author($select=Id,Name),"
            "AssignedBy($select=Id,Name),"
            "Assignee($select=Id,Name),"
            "Supervisor($select=Id,Name)"
        )
        live_select = (
            "Id,Subject,Status,Deadline,Created,ActionItem,PerformersGD,"
            "Report,ReportNote,ExecutionState"
        )
        legacy_expand = (
            "Performer($select=Id,Name,JobTitle),"
            "Author($select=Id,Name),"
            "ActionItemExecutionAssignments($select=Status,DeadLine,Note,ActualExecutionDate)"
        )
        legacy_select = "Id,Subject,Text,Status,DeadLine,Created"
        last_error: DirectumError | None = None
        for expand, select in ((live_expand, live_select), (legacy_expand, legacy_select)):
            entity_path = (
                f"IActionItemExecutionTasks({action_item_id})"
                f"?$expand={expand}"
                f"&$select={select}"
            )
            try:
                return self.client.get_one(entity_path)
            except DirectumError as exc:
                if exc.status_code != 400:
                    raise
                last_error = exc
        if last_error is not None:
            raise last_error
        return {}

    def _to_action_item_detail(self, row: dict[str, Any]) -> ActionItemDetail:
        item_id = int(row["Id"])
        entity_path = f"IActionItemExecutionTasks({item_id})"
        card_url = self.client.build_client_card_url(entity_path) or ""
        performer_info = row.get("Assignee") or row.get("Performer") or {}
        performer_name = performer_info.get("Name") or row.get("PerformersGD") or ""
        job_title = self._person_job_title(performer_info)
        performer = f"{performer_name} ({job_title})" if job_title else performer_name
        author_info = row.get("Author") or row.get("AssignedBy") or {}
        author = author_info.get("Name") or ""
        deadline_raw = self._first_value(row, "Deadline", "DeadLine", "FinalDeadline", "MaxDeadline")
        deadline = None
        if deadline_raw:
            try:
                deadline = datetime.fromisoformat(
                    str(deadline_raw).replace("Z", "+00:00")
                ).date()
            except ValueError:
                pass
        created_raw = row.get("Created")
        created = datetime.now(timezone.utc).date()
        if created_raw:
            try:
                created = datetime.fromisoformat(
                    str(created_raw).replace("Z", "+00:00")
                ).date()
            except ValueError:
                pass
        return ActionItemDetail(
            id=item_id,
            subject=row.get("Subject") or "",
            text=self._first_text(row, "ActionItem", "Text", "Report", "ReportNote") or None,
            performer=performer,
            author=author,
            deadline=deadline,
            status=row.get("Status") or "",
            created_date=created,
            client_card_url=card_url,
            narrative="",
        )

    def _person_id(self, row: dict[str, Any], *keys: str) -> int | None:
        for key in keys:
            person = row.get(key)
            if isinstance(person, dict):
                person_id = person.get("Id")
                if isinstance(person_id, int):
                    return person_id
        return None

    def _person_job_title(self, person: dict[str, Any]) -> str:
        job_title = person.get("JobTitle") or ""
        if isinstance(job_title, dict):
            return str(job_title.get("Name") or "")
        return str(job_title) if job_title else ""
