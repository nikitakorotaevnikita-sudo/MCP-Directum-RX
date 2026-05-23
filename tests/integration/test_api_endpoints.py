from tests.conftest import make_test_client


def test_health_returns_ok():
    client = make_test_client()

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_config_diagnostics_masks_secrets():
    client = make_test_client()

    response = client.get("/api/diagnostics/config")

    assert response.status_code == 200
    data = response.json()
    assert data["openai_api_key_set"] is True
    assert "secret" not in str(data).lower()


def test_action_item_preview_endpoint_does_not_create():
    client = make_test_client()

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
