from fastapi.testclient import TestClient

from tests.conftest import make_test_client
from src.main import create_app
from src.services.directum_client import DirectumError


def test_health_returns_ok(tmp_path):
    client = make_test_client(tmp_path)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_config_diagnostics_masks_secrets(tmp_path):
    client = make_test_client(tmp_path)

    response = client.get("/api/diagnostics/config")

    assert response.status_code == 200
    data = response.json()
    assert data["openai_api_key_set"] is True
    assert "secret" not in str(data).lower()


def test_directum_connection_status_masks_credentials(tmp_path):
    client = make_test_client(tmp_path)

    response = client.get("/api/directum/connection/status")

    assert response.status_code == 200
    data = response.json()
    assert data == {
        "base_url": "https://rx.example/Integration/odata",
        "auth_configured": True,
        "current_user": None,
    }
    assert "bnRfd29ya" not in str(data)
    assert "pass" not in str(data).lower()


def test_directum_connection_test_checks_current_user_without_leaking_password(tmp_path):
    client = make_test_client(tmp_path)

    response = client.post(
        "/api/directum/connection/test",
        json={
            "base_url": "https://custom.example/Integration/odata",
            "username": "nt_work\\user",
            "password": "new-secret",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["base_url"] == "https://custom.example/Integration/odata"
    assert data["auth_configured"] is True
    assert data["current_user"]["id"] == 1165
    assert "new-secret" not in response.text
    assert "Authorization" not in response.text


def test_directum_connection_apply_updates_runtime_settings_only(tmp_path):
    client = make_test_client(tmp_path)

    response = client.post(
        "/api/directum/connection/apply",
        json={
            "base_url": "https://runtime.example/Integration/odata/",
            "username": "nt_work\\runtime",
            "password": "runtime-secret",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["base_url"] == "https://runtime.example/Integration/odata"
    assert data["auth_configured"] is True
    assert data["current_user"]["name"] == "Test User"
    assert client.app.state.settings.directum_base_url == "https://runtime.example/Integration/odata"
    assert "runtime-secret" not in response.text
    assert "Authorization" not in response.text


def test_directum_connection_apply_rewires_directum_endpoints(tmp_path):
    client = make_test_client(tmp_path)

    apply_response = client.post(
        "/api/directum/connection/apply",
        json={
            "base_url": "https://rewired.example/Integration/odata",
            "username": "nt_work\\runtime",
            "password": "runtime-secret",
        },
    )
    assignments_response = client.get("/api/directum/assignments/my")

    assert apply_response.status_code == 200
    assert assignments_response.status_code == 200
    assert assignments_response.json()[0]["id"] == 1


def test_action_item_preview_endpoint_does_not_create(tmp_path):
    client = make_test_client(tmp_path)

    response = client.post(
        "/api/directum/action-items",
        json={
            "subject": "Prepare response",
            "performer_id": 42,
            "action_text": "Prepare a short response",
            "confirm": False,
        },
    )

    assert response.status_code == 200
    assert response.json()["mode"] == "preview"


def test_chat_endpoint_uses_fake_llm_in_testing(tmp_path):
    client = make_test_client(tmp_path)

    response = client.post("/api/chat", json={"message": "Hello", "history": []})

    assert response.status_code == 200
    assert response.text == "Test LLM response"


def test_testing_apps_without_explicit_metrics_path_use_unique_databases():
    first_app = create_app(testing=True)
    second_app = create_app(testing=True)

    assert first_app.state.settings.METRICS_DB_PATH != second_app.state.settings.METRICS_DB_PATH


def test_directum_errors_return_safe_json(tmp_path):
    client = make_test_client(tmp_path)

    class FailingAssignments:
        def get_my_assignments(self):
            raise DirectumError("Safe Directum failure", status_code=409)

    client.app.state.services["assignments"] = FailingAssignments()

    response = client.get("/api/directum/assignments/my")

    assert response.status_code == 409
    assert response.json() == {"detail": "Safe Directum failure"}


def test_shutdown_closes_directum_client(tmp_path):
    app = create_app(testing=True, metrics_db_path=str(tmp_path / "metrics.db"))

    class FakeDirectum:
        def __init__(self):
            self.closed = False

        def close(self):
            self.closed = True

    fake_directum = FakeDirectum()
    app.state.services["directum"] = fake_directum

    with TestClient(app):
        pass

    assert fake_directum.closed is True
