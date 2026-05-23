from fastapi.testclient import TestClient

from src.main import create_app


def make_test_client():
    app = create_app(testing=True)
    return TestClient(app)
