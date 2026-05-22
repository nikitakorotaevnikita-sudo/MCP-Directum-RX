import base64

from src.models.schemas import DirectumUser
from src.services.directum_client import DirectumClient, DirectumError


class CurrentUserService:
    def __init__(self, client: DirectumClient, auth_token: str):
        self.client = client
        self.auth_token = auth_token
        self._cached_user: DirectumUser | None = None

    def get_current_user(self) -> DirectumUser:
        if self._cached_user:
            return self._cached_user
        login = self._login_from_basic_token()
        rows = self.client.query(
            "IUsers",
            filter_=f"Login/LoginName eq '{login}'",
            select="Id,Name",
            top=1,
        )
        if not rows:
            raise DirectumError("Current Directum user was not found")
        row = rows[0]
        self._cached_user = DirectumUser(id=int(row["Id"]), name=row.get("Name", ""), login=login)
        return self._cached_user

    def _login_from_basic_token(self) -> str:
        if not self.auth_token.startswith("Basic "):
            raise DirectumError("DIRECTUM_AUTH_TOKEN must be a Basic token")
        encoded = self.auth_token.replace("Basic ", "", 1).strip()
        decoded = base64.b64decode(encoded).decode("utf-8")
        return decoded.split(":", 1)[0]
