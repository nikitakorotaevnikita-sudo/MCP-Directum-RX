# MCP Directum Assignments Prototype Design

**Date:** 2026-05-22
**Status:** Approved
**Project:** MCP Directum RX

## Goal

Build a full prototype for working with Directum RX assignments and action items through an LLM chat, MCP-style tools, a Directum-focused UI, and a backoffice metrics page.

The first MVP covers:
- reading current-user assignments and action items;
- finding overdue assignments;
- finding action items assigned to the user and created by the user;
- searching employees for assignment creation;
- creating action items only through safe preview plus explicit confirmation;
- chatting with an LLM configured through OpenAI-compatible settings;
- supporting Ollama as a local LLM provider;
- tracking product and technical metrics in backoffice.

## User Experience

The main screen uses layout option A: a left Directum panel and a central LLM chat.

The left panel contains:
- current Directum user;
- quick actions: "Мои задания", "Просроченные", "Поручения мне", "Поручения от меня", "Создать поручение";
- Directum/OData connection status;
- LLM provider status;
- recent query results and selected entities.

The central area contains:
- chat with streaming LLM responses;
- readable tool-call states, for example "Получаю текущего пользователя", "Ищу просроченные задания", "Формирую preview поручения";
- result cards for assignments and action items;
- create-action-item preview cards;
- explicit confirmation UI before POST to Directum RX;
- feedback controls for answer rating.

The backoffice page is available at `/backoffice`.

## Architecture

The prototype uses:
- Python 3.10+;
- FastAPI for static UI, REST endpoints, streaming chat, diagnostics, and backoffice;
- Vanilla HTML/CSS/JS served from FastAPI;
- OpenAI Python SDK for OpenAI-compatible LLM providers;
- MCP-style tool layer implemented as Python services and exposed to the LLM orchestration layer;
- Directum RX OData service client;
- SQLite for metrics and event storage;
- Docker and docker-compose for local run and verification;
- pytest and Playwright for automated verification.

No frontend framework is used. React, Vue, Angular, Next.js, Svelte, Gradio, and Streamlit are out of scope.

## LLM Providers

The LLM layer is provider-aware but keeps one OpenAI-compatible client interface.

Environment variables:

```env
LLM_PROVIDER=ario
OPENAI_BASE_URL=https://example/v1
OPENAI_API_KEY=...
OPENAI_MODEL=...
LLM_TOOL_CALLING=auto
```

Supported provider profiles:

| Provider | Example base URL | Notes |
| --- | --- | --- |
| `ario` | ARIO OpenAI-compatible endpoint | Primary corporate profile from research notes |
| `openai-compatible` | Any `/v1` compatible endpoint | Generic external provider |
| `ollama` | `http://localhost:11434/v1` | Local model provider through Ollama OpenAI-compatible API |

Ollama example:

```env
LLM_PROVIDER=ollama
OPENAI_BASE_URL=http://localhost:11434/v1
OPENAI_API_KEY=ollama
OPENAI_MODEL=qwen3:8b
LLM_TOOL_CALLING=auto
```

If the selected model supports reliable tool calling, the chat can call assignment/action-item tools through the orchestration layer. If the selected local Ollama model does not support reliable tool calling, the chat still works, while Directum actions remain available through the left panel and server endpoints.

## Directum RX Integration

Use the project-local RX skill references:
- `Skills RX/.codex/skills/rxapi-auth/SKILL.md`;
- `Skills RX/.codex/skills/rxapi-current-user/SKILL.md`;
- `Skills RX/.codex/skills/rxapi-assignments/SKILL.md`;
- `Skills RX/.codex/skills/rxapi-employees/SKILL.md` when employee lookup is needed.

Any examples in copied RX skills that refer to `.claude/skills/...` must be translated to `Skills RX/.codex/skills/...`.

The production app must not shell out to RX skill scripts for runtime business logic. It should implement a reusable Directum OData client in Python. The scripts remain useful for exploration and metadata validation.

Directum settings are configurable through environment variables:

```env
DIRECTUM_BASE_URL=https://.../Integration/odata
DIRECTUM_AUTH_MODE=basic_token
DIRECTUM_AUTH_TOKEN=Basic ...
DIRECTUM_REQUEST_TIMEOUT_SECONDS=30
```

Secrets must not be hardcoded or printed in logs, chat responses, screenshots, or backoffice.

## MCP Tools

MVP tools:

| Tool | Purpose |
| --- | --- |
| `get_current_user` | Determine and cache current Directum user id |
| `get_my_assignments` | Return current user's in-process assignments |
| `get_overdue_assignments` | Return current user's overdue in-process assignments |
| `get_action_items_assigned_to_me` | Return action item execution assignments where current user is performer |
| `get_action_items_created_by_me` | Return action item execution tasks where current user is author |
| `search_employee` | Search employees by name to support action item creation |
| `create_action_item` | Build preview payload or create action item after explicit confirmation |

EntitySets and intent mapping:

| Intent | EntitySet |
| --- | --- |
| General assignments | `IAssignments` |
| Approval assignments | `IApprovalAssignments` |
| Action item assignments | `IActionItemExecutionAssignments` |
| Tasks created by current user | `ITasks` |
| Action item tasks created by current user | `IActionItemExecutionTasks` |
| Notices | `INotices` |
| Employees | `IEmployees` |

Current-user filters must be explicit. Directum OData does not automatically filter to the current user.

Common filters:

```text
Performer/Id eq {currentUserId} and Status eq 'InProcess'
Author/Id eq {currentUserId} and Status eq 'InProcess'
Deadline lt {nowIso}
```

## Safe Creation Flow

`create_action_item` is safe-first.

Input fields:
- `subject`;
- `performer_id` or employee search result reference;
- `action_text`;
- optional `deadline`;
- optional priority/importance if supported by metadata;
- `confirm`, default `false`.

Behavior:
- when `confirm=false`, validate input and return preview payload without POST;
- when `confirm=true`, POST to Directum RX;
- return normalized success/error data;
- record preview and confirmed creation events in metrics;
- never print credentials or raw auth headers.

The UI must show a preview card before confirmed creation. The confirmation action must be explicit.

## Backoffice

Backoffice path: `/backoffice`.

Required metrics:
- total chat requests;
- scenario distribution: assignments, overdue, assigned action items, created action items, create preview, create confirmed;
- number of `create_action_item` previews;
- number of confirmed action item creations;
- LLM errors;
- OData errors;
- LLM latency;
- Directum/OData latency;
- latest tool calls;
- answer feedback ratings.

Backoffice should also show:
- active LLM provider;
- selected model;
- provider health;
- Directum health;
- whether tool calling is enabled, disabled, or auto-detected.

Metrics storage: SQLite.

## API Endpoints

Initial FastAPI routes:

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/` | Main chat UI |
| `GET` | `/backoffice` | Backoffice metrics page |
| `GET` | `/health` | App health |
| `GET` | `/api/diagnostics/config` | Sanitized runtime config |
| `GET` | `/api/diagnostics/current-user` | Current user check |
| `GET` | `/api/diagnostics/odata` | Directum OData health |
| `POST` | `/api/chat` | Chat request with streaming response |
| `GET` | `/api/directum/assignments/my` | Current user's assignments |
| `GET` | `/api/directum/assignments/overdue` | Current user's overdue assignments |
| `GET` | `/api/directum/action-items/assigned-to-me` | Action items assigned to current user |
| `GET` | `/api/directum/action-items/created-by-me` | Action items created by current user |
| `GET` | `/api/directum/employees/search` | Employee lookup |
| `POST` | `/api/directum/action-items` | Preview or confirmed action item creation |
| `POST` | `/api/feedback` | Store chat feedback |
| `GET` | `/api/metrics` | Backoffice metrics data |

## Error Handling

Normalize errors into user-safe messages:
- invalid or missing LLM config;
- Ollama unavailable;
- selected model unavailable;
- LLM provider timeout;
- Directum unauthorized;
- Directum endpoint unavailable;
- OData validation failure;
- empty results;
- missing required fields for action item creation;
- POST rejected by Directum validation.

Internal logs may include diagnostic details, but must mask tokens and credentials.

## Testing

Required test layers:
- unit tests for config, OData URL/query building, current-user logic, metrics storage, safe create preview;
- integration tests for FastAPI endpoints with mocked Directum and mocked LLM provider;
- tests for `confirm=false` never POSTing to Directum;
- tests for `confirm=true` POSTing only after valid preview input;
- Playwright E2E for main UI chat, left panel quick actions, preview confirmation, and backoffice metrics;
- Docker smoke test.

Verification commands:

```powershell
docker-compose build
docker-compose up -d
curl http://localhost:[PORT]/
curl http://localhost:[PORT]/backoffice
python -m pytest tests/ -v --cov=src --cov-report=term-missing
python -m pytest tests/e2e/ -v
```

## Acceptance Criteria

- Main UI opens and shows left Directum panel plus central chat.
- LLM provider can be configured through `.env`.
- Ollama can be configured with `OPENAI_BASE_URL=http://localhost:11434/v1`.
- Health/diagnostics show sanitized LLM provider and Directum status.
- User can request current assignments through chat or left panel.
- User can request overdue assignments through chat or left panel.
- User can request action items assigned to them.
- User can request action items they created.
- User can search employees.
- Creating an action item with `confirm=false` returns preview and does not POST.
- Creating an action item with `confirm=true` performs POST only after valid input.
- Backoffice shows product and technical metrics.
- Metrics include chat count, scenario distribution, preview count, confirmed creation count, OData/LLM errors, latency, latest tool calls, and feedback.
- Tests and Docker verification produce real command evidence.

## Out Of Scope For MVP

- Full document search beyond fields needed for assignment context.
- Editing or completing assignments.
- Bulk creation of action items.
- Advanced audit log with full payload/response retention.
- Role-based access control beyond simple backoffice protection.
- Multi-tenant configuration UI.
- Production deployment hardening.

## Open Decisions For Implementation Plan

- Exact FastMCP transport mode: stdio, streamable HTTP, or app-internal tool registry first.
- Exact Directum POST payload fields for `IActionItemExecutionTasks`, to be validated against metadata and a safe test environment.
- Whether backoffice auth is Basic Auth in MVP or a simple environment-protected token.
