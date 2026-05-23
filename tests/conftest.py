import socket
import tempfile
import threading
import time

import pytest
import uvicorn
from fastapi.testclient import TestClient

from src.main import create_app


def make_test_client(tmp_path):
    app = create_app(testing=True, metrics_db_path=str(tmp_path / "metrics.db"))
    return TestClient(app)


@pytest.fixture(scope="session")
def live_server_url():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]

    metrics_dir = tempfile.mkdtemp(prefix="mcp_directum_rx_e2e_")
    app = create_app(testing=True, metrics_db_path=f"{metrics_dir}/metrics.db")
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    deadline = time.time() + 10
    while not server.started:
        if time.time() > deadline:
            raise RuntimeError("Timed out waiting for live test server")
        time.sleep(0.05)

    yield f"http://127.0.0.1:{port}"

    server.should_exit = True
    thread.join(timeout=5)
