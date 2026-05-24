# MCP Directum RX

Prototype for working with Directum RX assignments and action items through an LLM chat, MCP-style tools, and a backoffice metrics page.

## Run

```powershell
copy .env.example .env
docker-compose build
docker-compose up -d
```

Open:

- Main UI: http://localhost:8000/
- Backoffice: http://localhost:8000/backoffice

## Ollama Profile

For local development outside Docker:

```env
LLM_PROVIDER=ollama
OPENAI_BASE_URL=http://localhost:11434/v1
OPENAI_API_KEY=ollama
OPENAI_MODEL=qwen3:8b
LLM_TOOL_CALLING=auto
```

When the app runs in Docker Desktop and Ollama runs on the Windows host, use:

```env
OPENAI_BASE_URL=http://host.docker.internal:11434/v1
```

## OpenRouter Profile

Use an OpenRouter API key in `.env` and keep it out of Git:

```env
LLM_PROVIDER=openrouter
OPENAI_BASE_URL=https://openrouter.ai/api/v1
OPENAI_API_KEY=sk-or-v1-change-me
OPENAI_MODEL=google/gemma-4-26b-a4b-it:free
LLM_TOOL_CALLING=auto
```

## Tests

```powershell
python -m pytest tests/ -v --cov=src --cov-report=term-missing
python -m pytest tests/e2e/ -v
```
