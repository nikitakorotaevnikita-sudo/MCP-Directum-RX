from dataclasses import dataclass
from datetime import tzinfo

import httpx

from src.services.action_items import ActionItemService
from src.services.admin_access import AdminAccessService
from src.services.assignments import AssignmentsService
from src.services.current_user import CurrentUserService
from src.services.directum_client import DirectumClient
from src.services.discipline_analytics import DisciplineAnalyticsService
from src.services.meetings import MeetingsService


@dataclass
class DirectumServices:
    """Сервисы Directum RX, собранные поверх одного клиента с одними кредами."""

    client: DirectumClient
    current_user: CurrentUserService
    assignments: AssignmentsService
    action_items: ActionItemService
    meetings: MeetingsService
    discipline: DisciplineAnalyticsService
    admin_access: AdminAccessService

    def close(self) -> None:
        self.client.close()


def build_directum_services(
    base_url: str,
    auth_token: str,
    timeout: float = 30.0,
    transport: httpx.BaseTransport | None = None,
    tz: tzinfo | None = None,
) -> DirectumServices:
    client = DirectumClient(base_url, auth_token, timeout, transport=transport)
    current_user = CurrentUserService(client, auth_token)
    action_items = ActionItemService(client)
    return DirectumServices(
        client=client,
        current_user=current_user,
        assignments=AssignmentsService(client, current_user, tz=tz),
        action_items=action_items,
        meetings=MeetingsService(client, current_user),
        discipline=DisciplineAnalyticsService(client, action_items),
        admin_access=AdminAccessService(client, current_user),
    )
