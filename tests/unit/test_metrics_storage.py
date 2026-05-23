from src.services.metrics_storage import MetricsStorage


def test_metrics_storage_records_chat_and_tool_call(tmp_path):
    db_path = tmp_path / "metrics.db"
    storage = MetricsStorage(str(db_path))
    storage.initialize()

    storage.record_chat_request("assignments", 120)
    storage.record_tool_call("get_my_assignments", True, 35)

    summary = storage.summary()

    assert summary["chat_requests"] == 1
    assert summary["scenario_counts"]["assignments"] == 1
    assert summary["latest_tool_calls"][0]["name"] == "get_my_assignments"


def test_metrics_storage_records_create_preview_and_confirm(tmp_path):
    db_path = tmp_path / "metrics.db"
    storage = MetricsStorage(str(db_path))
    storage.initialize()

    storage.record_action_item_create("preview")
    storage.record_action_item_create("confirmed")

    summary = storage.summary()

    assert summary["action_item_previews"] == 1
    assert summary["action_item_confirmed"] == 1


def test_metrics_storage_closes_connections_after_summary(tmp_path):
    db_path = tmp_path / "metrics.db"
    storage = MetricsStorage(str(db_path))
    storage.initialize()
    storage.record_feedback("positive")

    storage.summary()

    db_path.unlink()
    assert not db_path.exists()
