# Technical Documentation

**Version:** 1.0
**Status:** In development

## Architecture

FastAPI serves a Vanilla JS chat UI and backoffice. Python services implement Directum OData access, current-user resolution, assignment queries, safe action-item creation, LLM provider access, tool registry, and SQLite metrics.

## API Endpoints

| Method | Path | Description |
| --- | --- | --- |
| GET | / | Main UI |
| GET | /backoffice | Metrics page |
| GET | /health | App and LLM status |
| GET | /api/diagnostics/config | Sanitized config |
| GET | /api/diagnostics/current-user | Directum current user |
| GET | /api/diagnostics/odata | Directum diagnostics |
| POST | /api/chat | LLM chat |
| GET | /api/directum/assignments/my | My assignments |
| GET | /api/directum/assignments/overdue | Overdue assignments |
| GET | /api/directum/action-items/assigned-to-me | Action items assigned to me |
| GET | /api/directum/action-items/created-by-me | Action items created by me |
| GET | /api/directum/employees/search | Employee search |
| POST | /api/directum/action-items | Preview or create action item |
| POST | /api/feedback | Store feedback |
| GET | /api/metrics | Backoffice metrics |

## Safe Create

`POST /api/directum/action-items` returns a preview when `confirm=false`. It sends a Directum POST only when `confirm=true`.

LLM tool calls are preview-only: the tool registry does not expose `confirm` to the model and rejects direct confirmation attempts.

## LLM Profiles

The app uses the OpenAI-compatible client for Ollama, OpenRouter, Ario, and generic OpenAI-compatible endpoints. The built-in OpenRouter profile uses `https://openrouter.ai/api/v1` with `google/gemma-4-26b-a4b-it:free`; the API key must stay in local `.env` or runtime backoffice settings.

## Configuration

Secrets are loaded from `.env` and must not be logged or rendered in UI. Runtime diagnostics expose boolean `*_set` fields instead of secret values.
