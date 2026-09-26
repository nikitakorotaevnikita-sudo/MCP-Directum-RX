from src.services.current_user import CurrentUserService
from src.services.directum_client import DirectumClient, DirectumError

# Платформенный Sid роли «Администраторы»: одинаков на всех стендах и не зависит от названия роли.
ADMINISTRATORS_ROLE_SID = "9cc6ea59-cd05-4c8e-b041-abefe9432e20"
# Ошибки кредов не маскируем фолбэком: пусть дойдут до пользователя как есть.
AUTH_ERROR_CODES = (401,)


class AdminAccessService:
    """Проверяет, что текущий пользователь входит в роль «Администраторы»."""

    def __init__(self, client: DirectumClient, current_user_service: CurrentUserService):
        self.client = client
        self.current_user_service = current_user_service

    def is_admin(self) -> bool:
        # Платформенный метод учитывает и вложенные группы; запрос к IRoles — запасной путь для стендов без него.
        try:
            return bool(self.client.call_function("Company/IsCurrentUserAdmin"))
        except DirectumError as exc:
            if exc.status_code in AUTH_ERROR_CODES:
                raise
        return self._is_direct_member()

    def _is_direct_member(self) -> bool:
        user = self.current_user_service.get_current_user()
        rows = self.client.query(
            "IRoles",
            filter_=f"Sid eq {ADMINISTRATORS_ROLE_SID} and RecipientLinks/any(l: l/Member/Id eq {int(user.id)})",
            select="Id",
            top=1,
        )
        return bool(rows)
