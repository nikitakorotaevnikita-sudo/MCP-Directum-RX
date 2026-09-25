from src.services.current_user import CurrentUserService
from src.services.directum_client import DirectumClient

# Платформенный Sid роли «Администраторы»: одинаков на всех стендах и не зависит от названия роли.
ADMINISTRATORS_ROLE_SID = "9cc6ea59-cd05-4c8e-b041-abefe9432e20"


class AdminAccessService:
    """Проверяет, что текущий пользователь напрямую входит в роль «Администраторы»."""

    def __init__(self, client: DirectumClient, current_user_service: CurrentUserService):
        self.client = client
        self.current_user_service = current_user_service

    def is_admin(self) -> bool:
        user = self.current_user_service.get_current_user()
        rows = self.client.query(
            "IRoles",
            filter_=f"Sid eq {ADMINISTRATORS_ROLE_SID} and RecipientLinks/any(l: l/Member/Id eq {int(user.id)})",
            select="Id",
            top=1,
        )
        return bool(rows)
