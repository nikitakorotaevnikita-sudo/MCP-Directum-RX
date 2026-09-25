import base64
import binascii

from src.models.schemas import DirectumUser
from src.services.directum_client import DirectumClient, DirectumError


class CurrentUserService:
    def __init__(self, client: DirectumClient, auth_token: str):
        self.client = client
        self.auth_token = auth_token
        self._cached_user: DirectumUser | None = None

    @property
    def cached_user(self) -> DirectumUser | None:
        return self._cached_user

    def prime(self, user: DirectumUser) -> None:
        self._cached_user = user

    def get_current_user(self) -> DirectumUser:
        if self._cached_user:
            return self._cached_user
        login = self._login_from_basic_token()
        escaped_login = login.replace("'", "''")
        rows = self.client.query(
            "IUsers",
            filter_=f"Login/LoginName eq '{escaped_login}'",
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
        try:
            decoded = base64.b64decode(encoded, validate=True).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError) as exc:
            raise DirectumError("DIRECTUM_AUTH_TOKEN must be a valid Basic token") from exc
        if ":" not in decoded:
            raise DirectumError("DIRECTUM_AUTH_TOKEN must be a valid Basic token")
        login = decoded.split(":", 1)[0]
        if not login:
            raise DirectumError("DIRECTUM_AUTH_TOKEN must be a valid Basic token")
        return login
