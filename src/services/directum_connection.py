import base64


def build_basic_auth_token(username: str, password: str) -> str:
    credentials = f"{username}:{password}".encode("utf-8")
    encoded = base64.b64encode(credentials).decode("ascii")
    return f"Basic {encoded}"
