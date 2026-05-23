from fastapi.testclient import TestClient

from src.main import create_app


def make_test_client(tmp_path):
    app = create_app(testing=True, metrics_db_path=str(tmp_path / "metrics.db"))
    return TestClient(app)
