# MCP Directum RX Assistant

Прототип чат-ассистента для работы с заданиями, поручениями, документами и
аналитикой Directum RX через LLM с MCP-стильными инструментами.

FastAPI backend + Vanilla JS frontend. Всё общение с пользователем — на русском.

## Статус проекта

**Работает:**
- Просмотр заданий, просроченных заданий, поручений (входящих и исходящих), совещаний
- Поиск сотрудников, документов и документов по контрагенту (fuzzy-fallback, стемминг, стоп-слова)
- Реестр входящих/исходящих писем за период (`list_letters`)
- **Создание поручений по документу** — `RecordManagement/CreateActionItemExecution` + автостарт `Docflow/StartTask` (проверено на стенде, поручение создаётся)
- **Создание задач** — `Docflow/CreateSimpleTask`
- Preview-карточки с подтверждением перед созданием (LLM не может создать без кнопки)
- **Аналитика исполнительской дисциплины** — детерминированные метрики через OData `$count`
  (в работе / просрочено / завершено / в срок / с опозданием / % в срок), фильтры по сотруднику и периоду
- **Аналитика исходящих поручений** по категориям срочности
- **Визуализация аналитики** — inline-SVG гистограммы и круговой gauge (без фреймворков/CDN)
- **Drill-down модалка** — клик по колонке графика открывает список поручений
  (статус, срок, ответственный) с кнопкой «Отчёт» (рендерится в модалке)
- Кликабельные ссылки на карточки Directum; ссылка «Выдать поручение» у найденных документов
- Автокоррекция текста поручения через LLM (повелительное наклонение, отглагольная тема)
- Контекстный диалог: разрешение местоимений, повтор предыдущего исполнителя/текста
- Панель быстрых промптов: сворачивание (память в localStorage), клик = отправка
- Backoffice: метрики чата, последние вызовы инструментов, настройка LLM/Directum в UI

**Ограничения / зависит от стенда:**
- Документ привязывается к поручению **только по явному id** (через ссылку «Выдать поручение»
  `#document-<id>`); угадывание по тексту намеренно отключено во избежание чужих вложений
- Модуль «Обращения граждан» в Directum присутствует (виды «Жалоба/Заявление/Предложение»),
  но на проверенном стенде данных нет — запрос готов, аналитику подключим при наличии данных
- Управление наблюдателями/соисполнителями, отзыв/завершение через чат — не реализовано

## Возможности

### Чат с LLM
- Сообщения на русском, рендеринг Markdown, потоковая передача ответов
- Быстрые детерминированные маршруты без LLM: «мои задания», «просроченные»,
  «поручения мне/от меня», «исполнительская дисциплина», «аналитика исходящих», «совещания»
- Контекстный диалог (местоимения, повтор исполнителя/текста)

### Инструменты LLM (14)
`get_current_user`, `get_my_assignments`, `get_overdue_assignments`,
`get_action_items_assigned_to_me`, `get_action_items_created_by_me`,
`search_employee`, `search_documents`, `search_documents_by_counterparty`,
`list_letters`, `create_action_item`, `create_task`, `get_my_meetings`,
`get_action_item_details`, `get_discipline_analytics`.
Внутренние (не выдаются модели): `get_document`, `get_employee`.

### Preview-карточки
- Перед созданием поручения/задачи — preview-карточка с реквизитами (включая «Документ»)
- Кнопки **«Создать поручение/задачу»** и **«Отмена»**; создание только после подтверждения
- Логика: `confirm=false` → preview, `confirm=true` → POST в Directum

### Аналитика и визуализация
- **Дисциплина** (`get_discipline_analytics`): `$count`-метрики, фильтры по сотруднику/периоду,
  гистограмма + gauge «% в срок»
- **Исходящие поручения**: распределение по срокам + drill-down модалка по колонке
  (список поручений, ответственный, кнопка «Отчёт» → отчёт LLM в модалке)
- Рендер маркеров `[[DIRECTUM_ANALYTICS:{...}]]` и `[[DIRECTUM_ACTION_ITEM_PREVIEW:{...}]]` во фронте

## Архитектура

```
src/
├── main.py                  # FastAPI app, все endpoints
├── config.py                # Settings (pydantic-settings, .env)
├── models/schemas.py        # Pydantic модели
└── services/
    ├── llm_service.py        # LLM streaming, tool calling, маркеры, санитайз истории
    ├── tool_registry.py      # 14 инструментов LLM (+2 внутренних)
    ├── directum_client.py    # OData HTTP-клиент ($count, $expand, navigation)
    ├── action_items.py       # Создание поручений/задач, поиск (сотрудники/документы/контрагенты)
    ├── assignments.py        # Получение заданий/поручений
    ├── discipline_analytics.py # Метрики исполнительской дисциплины через $count
    ├── current_user.py       # Текущий пользователь Directum
    ├── meetings.py           # Совещания
    └── metrics_storage.py    # SQLite метрики
```

Принцип ADR-004: метрики считает детерминированный код, LLM только объясняет/формулирует;
визуализацию рисует фронт.

## Конфигурация (.env)

```env
LLM_PROVIDER=ollama
OPENAI_BASE_URL=http://localhost:11434/v1
OPENAI_API_KEY=ollama
OPENAI_MODEL=llama3.1:latest
LLM_TOOL_CALLING=auto

DIRECTUM_BASE_URL=https://your-directum/Integration/odata
DIRECTUM_AUTH_TOKEN=Basic <base64 user:password>

APP_HOST=0.0.0.0
APP_PORT=8000
METRICS_DB_PATH=data/metrics.db
```

**`.env` хранит боевые креды стенда — не коммитить, не редактировать в репозитории.**

## Запуск

### Локально (Windows, без Docker)

```powershell
& ".venv\Scripts\python.exe" -m uvicorn src.main:app --host 0.0.0.0 --port 8005 --reload
```
Открыть: http://localhost:8005/ · Backoffice: http://localhost:8005/backoffice

### Docker

```powershell
docker compose up -d --build
```
Открыть: http://localhost:8000/ · Backoffice: http://localhost:8000/backoffice

(`docker-compose.yml` пробрасывает `.env` через `env_file` и том `./data` для метрик.)

## API Endpoints

| Method | Path | Описание |
|--------|------|----------|
| GET | `/` | Главная страница (чат) |
| GET | `/backoffice` | Метрики и настройки |
| GET | `/health` | Статус приложения и LLM |
| POST | `/api/chat` | LLM-чат |
| GET | `/api/directum/assignments/my` | Мои задания |
| GET | `/api/directum/assignments/overdue` | Просроченные задания |
| GET | `/api/directum/action-items/assigned-to-me` | Поручения мне |
| GET | `/api/directum/action-items/created-by-me` | Поручения от меня |
| GET | `/api/directum/employees/search?query=` | Поиск сотрудника |
| GET | `/api/directum/documents/search?query=` | Поиск документа |
| GET | `/api/directum/documents/by-counterparty?query=` | Документы по контрагенту |
| GET | `/api/directum/letters?direction=&date_from=&date_to=` | Письма (incoming/outgoing) за период |
| GET | `/api/directum/discipline?employee=&date_from=&date_to=` | Аналитика исполнительской дисциплины |
| GET | `/api/directum/meetings/upcoming?days=` | Ближайшие совещания |
| POST | `/api/directum/action-items` | Preview (`confirm=false`) / создание (`confirm=true`) поручения |
| POST | `/api/directum/tasks` | Создание задачи |
| GET | `/api/metrics` | Метрики (backoffice) |
| POST | `/api/feedback` | Обратная связь |
| GET | `/api/diagnostics/*` | Диагностика (config, current-user, odata) |
| GET/POST | `/api/directum/connection/*`, `/api/llm/connection/*` | Проверка/применение настроек подключения |

## Поток «поручение по документу»

```
1. «Найди документы от <организация>» → список документов
   + блок «Выдать поручение по документу» со ссылками #document-<id>
2. Клик «Выдать поручение» → в чат подставляется «Выдай поручение по документу #<id>: …»
3. Пользователь дописывает текст и исполнителя (по ФИО)
4. Детерминированный маршрут: резолв документа (get_document) и исполнителя,
   preview-карточка со строкой «Документ»
5. Кнопка «Создать поручение» → POST /api/directum/action-items {confirm:true, document_id}
6. CreateActionItemExecution → Docflow/StartTask → документ в области вложения
```

## Известные проблемы и нюансы

### Совместимость моделей с tool calling
- **Qwen3-32B-AWQ, llama3.1** — отдают нативные `tool_calls`, работают штатно.
- **Qwen3.6-35B-A3B (MoE)** — отдаёт вызовы в Hermes-XML (`<tool_call>…</tool_call>`)
  в тексте. Если сервинг (vLLM/ario) не настроен с tool-парсером, в стриме вызов теряется →
  пустые ответы. Лечится на стороне сервинга: `--enable-auto-tool-choice --tool-call-parser hermes`.
- **Ollama gemma**: не генерирует tool calls. **Groq LLaMA 3.3**: кракозябры в кириллице tool call.

### Санитайз истории
Внутренние маркеры (`[[DIRECTUM_…]]`) и блок ссылок «Выдать поручение по документу»
вырезаются из истории перед отправкой модели — иначе слабые модели имитируют разметку
(битый/выдуманный markup). Direct-route видит исходную историю (переиспользует preview).

### Поиск контрагента
Fuzzy-fallback использует стемминг словоформ и стоп-лист шумовых токенов
(«РФ», орг-правовые формы, родовые слова госорганов: «министерство», «федеральн*» и т.п.),
чтобы домен («культуры/финансов»), а не родовое слово, определял совпадение.

## mcpOGV — MCP-сервер для LibreChat

Отдельный процесс, который даёт агентной платформе на базе LibreChat доступ к Directum RX по протоколу MCP (Streamable HTTP). Работает **от имени пользователя**: логин и пароль Directum пользователь вводит в LibreChat (`customUserVars`), они приходят в заголовках и в mcpOGV не сохраняются.

**Состав (этап 1):**
- курируемые тулы: `get_current_user`, `search_employees`, `list_my_assignments`, `list_action_items`, `get_action_item`, `get_discipline_analytics`, `get_outgoing_action_items_analytics`, `search_documents`, `get_document`, `list_documents_by_counterparty`, `list_letters`, `list_my_meetings`;
- только для администраторов DRX (роль «Администраторы», прямое членство): `admin_list_employee_action_items` — поручения любого сотрудника (входящие/исходящие, статус, просрочка, период, срок сегодня/7 дней — `due`). Не-администраторам тул не показывается и не вызывается;
- универсальное чтение: `odata_list_domains`, `odata_describe_entity`, `odata_query`, `odata_count`, `odata_get` (фильтр обязателен, чувствительные наборы закрыты);
- справочники доменов — ресурсы `drx://domains/*`;
- тулы встроенного MCP Directum (`drx_native_*`, только read-only).

**Локальный запуск:**

```powershell
& ".venv\Scripts\python.exe" -m src.mcp_server
```

Проверка: `http://localhost:8010/health`. Нужные переменные — в `.env.example` (раздел mcpOGV).

`list_action_items` принимает `due` (`today` — срок сегодня, `week` — ближайшие 7 дней): «мои поручения со сроком на неделе».
Границы дней считаются в часовом поясе стенда: `MCP_UTC_OFFSET` (например `+04:00`), по умолчанию — пояс машины с сервером. В Docker-контейнере пояс UTC, поэтому там переменную нужно задать явно.

**Локальная отладка без LibreChat:** `MCP_ALLOW_ENV_CREDENTIALS=true` — запросы без заголовков с кредами идут под `DIRECTUM_AUTH_TOKEN`. Без `MCP_OGV_KEY` сервер в этом режиме стартует только на loopback:

```powershell
$env:MCP_ALLOW_ENV_CREDENTIALS = "true"
$env:MCP_HOST = "127.0.0.1"
& ".venv\Scripts\python.exe" -m src.mcp_server
```

**Docker:** сервис `mcp-ogv` в `docker-compose.yml`. Порт 8010 опубликован только на loopback хоста (`127.0.0.1:8010`) — для проверки `/health` с самой машины. LibreChat обращается к `mcp-ogv` не через порт хоста, а по общей Docker-сети: `http://mcp-ogv:8010/mcp` (хост `mcp-ogv:8010` должен быть в `MCP_ALLOWED_HOSTS`). Подключение к LibreChat — `docs/mcp-ogv/librechat.example.yaml`.

Общая сеть (один раз): `docker network create mcp-net`. Затем в `docker-compose.yml` этого проекта (через `docker-compose.override.yml`, чтобы чат не зависел от внешней сети):

```yaml
services:
  mcp-ogv:
    networks:
      - default
      - mcp-net
networks:
  mcp-net:
    external: true
```

и в compose-проекте LibreChat (сервис `api`):

```yaml
services:
  api:
    networks:
      - default
      - mcp-net
networks:
  mcp-net:
    external: true
```

Важно для compose-развёртываний:
- **не включайте** `MCP_ALLOW_ENV_CREDENTIALS` — иначе запросы без кредов пользователя пойдут под сервисной учёткой;
- по умолчанию `mcp-ogv` читает общий `.env` вместе с `DIRECTUM_AUTH_TOKEN` чата. Лучше дать сервису отдельный env-файл (например, `env_file: [.env.mcp-ogv]`) без `DIRECTUM_AUTH_TOKEN` — mcpOGV он не нужен.

**Метрики использования тулов:** SQLite `MCP_USAGE_DB_PATH` (тул, успех, длительность, обезличенный id пользователя — HMAC логина с солью `MCP_METRICS_SALT`, без кредов и без производных пароля).

**Live-проверка на стенде** (только чтение, креды из окружения):

```powershell
$env:MCP_LIVE_BASE_URL = "https://<стенд>/Integration/odata"
$env:MCP_LIVE_LOGIN = "<логин>"
$env:MCP_LIVE_PASSWORD = "<пароль>"
& ".venv\Scripts\python.exe" -m pytest tests/live -v
```

## Тестирование

```powershell
# Все unit-тесты с покрытием
& ".venv\Scripts\python.exe" -m pytest tests/unit/ -v --cov=src --cov-report=term-missing

# E2E (Playwright)
& ".venv\Scripts\python.exe" -m pytest tests/e2e/ -v
```
Целевое покрытие ≥70%. Скриншоты Playwright → `tmp/screenshots/`.

## Стек

Python 3.10+, FastAPI, Uvicorn, httpx (OData), OpenAI Python SDK (Ollama/OpenRouter/любой
OpenAI-совместимый), pydantic-settings, SQLite (метрики). Frontend — Vanilla JS / HTML / CSS,
без фреймворков; JS-библиотеки локально (Docker без интернета).
