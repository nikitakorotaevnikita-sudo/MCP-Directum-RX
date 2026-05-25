# MCP Directum RX — CLAUDE.md

## Что это

Прототип чат-ассистента для работы с заданиями и поручениями Directum RX через LLM.
FastAPI backend + Vanilla JS frontend. Всё общение с пользователем — на русском.

## Запуск

```powershell
# Windows (двойной клик или из терминала)
.\launch.bat

# Или напрямую
cd "C:\Users\Korotaev_NO\Desktop\Проекты\MCP-Directum-RX"
& "C:\Users\Korotaev_NO\Desktop\Проекты\MCP-Directum-RX\.venv\Scripts\python.exe" -m uvicorn src.main:app --host 0.0.0.0 --port 8005 --reload
```

Открыть: http://localhost:8005/
Backoffice: http://localhost:8005/backoffice

## Тесты

```powershell
# Все тесты с покрытием
& ".venv\Scripts\python.exe" -m pytest tests/ -v --cov=src --cov-report=term-missing

# Только unit
& ".venv\Scripts\python.exe" -m pytest tests/unit/ -v

# E2E (Playwright)
& ".venv\Scripts\python.exe" -m pytest tests/e2e/ -v
```

Целевое покрытие: ≥70%. Скриншоты Playwright → `tmp/screenshots/`.

## Стек

- **Python 3.10+**, FastAPI, Uvicorn
- **Frontend**: Vanilla JS, plain HTML/CSS — никаких React/Vue/Angular
- **LLM**: OpenAI Python SDK (работает с Ollama, OpenRouter, любым OpenAI-совместимым)
- **Тесты**: pytest + Playwright E2E
- **HTTP**: httpx (OData-клиент)
- **Config**: pydantic-settings, `.env`

## Архитектура

```
src/
├── main.py              # FastAPI app, все endpoints
├── config.py            # Settings (pydantic-settings, .env)
├── models/schemas.py    # Pydantic модели
└── services/
    ├── llm_service.py       # LLM streaming, tool calling, preview markers
    ├── tool_registry.py     # 9 инструментов для LLM
    ├── directum_client.py   # OData HTTP клиент
    ├── action_items.py      # Создание поручений/задач, поиск
    ├── assignments.py       # Получение заданий
    ├── current_user.py      # Текущий пользователь Directum
    └── metrics_storage.py   # SQLite метрики
```

## Ключевые паттерны

### Safe-create (preview → confirm)
LLM вызывает `create_action_item(confirm=False)` → возвращает маркер `[[DIRECTUM_ACTION_ITEM_PREVIEW:{...}]]` → frontend рендерит карточку с кнопкой → пользователь нажимает → `POST /api/directum/action-items` с `confirm=true`. LLM **не может** создать без подтверждения пользователя.

### Tool routing (русский язык)
- «поручение» → `create_action_item`
- «задача» → `create_task`

### Preview markers
`[[DIRECTUM_ACTION_ITEM_PREVIEW:{...}]]` — парсит `app.js`, рендерит карточку подтверждения.

### Fuzzy search
`search_employee` пробует полный запрос, потом разбивает на токены — ищет по каждому отдельно.

## Конфигурация (.env — не трогать!)

```env
LLM_PROVIDER=ollama
OPENAI_BASE_URL=http://localhost:11434/v1
OPENAI_API_KEY=ollama
OPENAI_MODEL=llama3.1:latest
LLM_TOOL_CALLING=auto
DIRECTUM_BASE_URL=https://your-directum/Integration/odata
DIRECTUM_AUTH_TOKEN=Basic <base64>
APP_HOST=0.0.0.0
APP_PORT=8000
METRICS_DB_PATH=data/metrics.db
```

**Никогда не создавать, не редактировать, не перезаписывать `.env`.**

## Известные проблемы

### Создание поручений/задач заблокировано
`POST /RecordManagement/CreateActionItemExecutionTask` → 400 `Не указан обязательный параметр "Кем"`.
**Причина**: API требует автора (`AssignedBy`) через OData navigation property `$ref`, не простое поле.
**Файл анализа**: `handoff.md`

### LLM Tool Calling
- Ollama gemma4: не генерирует tool calls → использовать `llama3.1:latest`
- Groq LLaMA 3.3: кракозябры в кириллических аргументах tool call

## Правила работы

### Обязательно
- Общение с пользователем — **по-русски**
- Инструкции агентам — на английском
- Тесты писать **до** реализации (TDD)
- Перед любым `git push` — прогнать тесты
- JS-библиотеки хранить **локально** (не CDN) — Docker без интернета
- `tmp/` в `.gitignore`, туда идут скриншоты Playwright

### Запрещено
- Секреты (API ключи, токены, пароли) в markdown или код — только плейсхолдеры
- Редактировать `.env`
- JS-фреймворки (React/Vue/Angular/Next.js/Svelte)
- Создавать ветки и PR без явного согласования

### Git
«Закоммить и запушить» без уточнений = `git add` нужных файлов + `git commit` + `git push origin HEAD`.

## Пайплайн агентов

```
/pm → ba → architect → developer → qa → GO/NO-GO
```

Артефакты в `.hypothesis/`:
- `00_HYPOTHESIS.md`
- `01_REQUIREMENTS.md`
- `02_ARCHITECTURE.md`
- `04_IMPLEMENTATION.md`
- `05_TEST_RESULTS.md`
- `BUILD_LOG.md`

## Безопасность

- Не логировать `SecretStr` поля
- `public_config()` возвращает только `*_set` флаги для секретов
- `DirectumError.safe_message` — никогда не пропускать внутренние детали наружу
- Basic Auth токен никогда не попадает в ответы API или логи
