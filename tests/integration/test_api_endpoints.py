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
