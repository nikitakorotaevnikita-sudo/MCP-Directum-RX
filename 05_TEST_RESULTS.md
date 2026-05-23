# Результаты тестирования

## Резюме

QA verification for MCP Directum RX assignments prototype completed. Application tests pass locally; Docker runtime smoke is blocked by the host Docker daemon being unavailable.

## Acceptance Criteria

| Criterion | Result |
| --- | --- |
| Main UI opens with left Directum panel and central chat | PASS |
| LLM provider is configurable through `.env` | PASS |
| Ollama profile is supported | PASS |
| Diagnostics show sanitized provider and Directum status | PASS |
| Current assignments can be requested | PASS |
| Overdue assignments can be requested | PASS |
| Action items assigned to user can be requested | PASS |
| Action items created by user can be requested | PASS |
| Employees can be searched | PASS |
| `confirm=false` returns preview without POST | PASS |
| `confirm=true` POSTs only after valid input | PASS |
| Backoffice shows product and technical metrics | PASS |
| Docker configuration is present and parseable | PASS |
| Docker image build and container run | BLOCKED: Docker daemon is not running |

## Commands

| Command | Result | Evidence |
| --- | --- | --- |
| `uv run python -m pytest tests/ -v --cov=src --cov-report=term-missing` | PASS | 58 passed, 2 warnings; total coverage 91%. |
| `uv run python -m pytest tests/e2e/ -v` | PASS | 2 passed; Chromium was installed with `uv run playwright install chromium`. |
| `docker-compose config` | PASS | Compose rendered valid service configuration for `mcp-directum-rx`. |
| `docker-compose build` | BLOCKED | `failed to connect to the docker API at npipe:////./pipe/dockerDesktopLinuxEngine`; Docker daemon is not running. |
| `curl.exe -s /health` against local test server | PASS | Returned `{"status":"ok","llm":{"provider":"test","base_url":"test://local","model":"test-model","tool_calling":"disabled"}}`. |
| `curl.exe -s /` against local test server | PASS | HTML contained `Directum RX Assistant`. |
| `curl.exe -s /backoffice` against local test server | PASS | HTML contained `Backoffice`. |

## Issues

- Docker image build and `docker-compose up -d` were not executed because Docker Desktop Linux engine is unavailable in the current host session.
- Python launcher via plain `python -m pytest` is unreliable in this Windows/Cyrillic-path environment; verified commands used `uv run`.
