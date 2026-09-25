import logging

import uvicorn

from src.mcp_server.app import build_asgi_app
from src.mcp_server.config import McpSettings


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    # httpx пишет URL запросов, а в $filter бывают логины — держим его логгер тихим.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    settings = McpSettings()
    uvicorn.run(build_asgi_app(settings), host=settings.MCP_HOST, port=settings.MCP_PORT, log_level="info")


if __name__ == "__main__":
    main()
