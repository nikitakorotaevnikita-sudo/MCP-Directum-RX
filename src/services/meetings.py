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
        filter_ = (
            f"StartDate ge {today.strftime('%Y-%m-%dT%H:%M:%SZ')} "
            f"and StartDate le {end.strftime('%Y-%m-%dT%H:%M:%SZ')} "
            f"and Members/any(m: m/Member/Id eq {user.id})"
        )
        rows = self.client.query(
            "IMeetings",
            filter_=filter_,
            select="Id,Subject,StartDate,EndDate,Place",
            expand=(
                "Members($expand=Member($select=Id,Name)),"
                "Minutes($select=Description,Subject;$orderby=Created asc;$top=1)"
            ),
            orderby="StartDate asc",
            top=20,
        )
        return [self._to_meeting_summary(row) for row in rows]

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
        if not agenda:
            agenda = row.get("Subject") or None
        return MeetingSummary(
            id=meeting_id,
            subject=row.get("Subject") or "",
            start_date=row.get("StartDate") or None,
            end_date=row.get("EndDate"),
            place=row.get("Place") or None,
            agenda_summary=agenda,
            client_card_url=card_url,
        )

    def get_action_item_details(self, action_item_id: int) -> ActionItemDetail:
        expand = (
            "Performer($select=Id,Name,JobTitle),"
            "Author($select=Id,Name),"
            "ActionItemExecutionAssignments($select=Status,DeadLine,Note,ActualExecutionDate)"
        )
        entity_path = (
            f"IActionItemExecutionTasks({action_item_id})"
            f"?$expand={expand}"
            f"&$select=Id,Subject,Text,Status,DeadLine,Created"
        )
        try:
            row = self.client.get_one(entity_path)
        except DirectumError as exc:
            if exc.status_code == 404:
                raise DirectumError(
                    f"Поручение #{action_item_id} не найдено.",
                    status_code=404,
                ) from exc
            raise
        return self._to_action_item_detail(row)

    def _to_action_item_detail(self, row: dict[str, Any]) -> ActionItemDetail:
        item_id = int(row["Id"])
        entity_path = f"IActionItemExecutionTasks({item_id})"
        card_url = self.client.build_client_card_url(entity_path) or ""
        performer_info = row.get("Performer") or {}
        performer_name = performer_info.get("Name") or ""
        job_title = performer_info.get("JobTitle") or ""
        performer = f"{performer_name} ({job_title})" if job_title else performer_name
        author_info = row.get("Author") or {}
        author = author_info.get("Name") or ""
        deadline_raw = row.get("DeadLine")
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
            text=row.get("Text") or None,
            performer=performer,
            author=author,
            deadline=deadline,
            status=row.get("Status") or "",
            created_date=created,
            client_card_url=card_url,
            narrative="",
        )
