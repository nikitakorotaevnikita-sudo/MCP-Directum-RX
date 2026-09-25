# Handoff: деплой mcpOGV на отдельный стенд

Документ для новой агентной сессии, которая будет разворачивать mcpOGV рядом с LibreChat на другом стенде.
Читать целиком перед началом. Общение с пользователем — по-русски; секреты — только плейсхолдеры.

## 1. Что это

**mcpOGV** — MCP-сервер (Streamable HTTP) поверх OData API Directum RX для агентной платформы на базе **LibreChat**.
Работает **от имени пользователя**: логин/пароль DRX пользователь вводит в LibreChat (`customUserVars`),
они приходят в каждом запросе в заголовках `X-Directum-Login` / `X-Directum-Password`, сервер их не хранит и не логирует.
Доступ к самому серверу закрыт общим секретом `X-MCP-Key` (LibreChat ↔ mcpOGV).

- Код: `src/mcp_server/` (отдельный процесс, `python -m src.mcp_server`), живёт в одном репо с чат-прототипом.
- Спека (источник требований): `docs/superpowers/specs/2026-09-25-mcp-ogv-server-design.md`.
- План этапа 1: `docs/superpowers/plans/2026-09-25-mcp-ogv-phase1.md`. Этапы 2–5 не начаты.
- Ветка: `feature/mcp-ogv` (от `main` e554ba9). **В `main` не влита** — деплоить из ветки, мёрж решает пользователь.

## 2. Состояние на момент передачи

- Этап 1 (12 задач) + волна исправлений финального ревью (F1–F12) закрыты.
- Тесты: `pytest tests/` → 362 passed, 5 skipped (live), 4 failed — **4 e2e Playwright чат-прототипа падают и на `main`**
  (устаревшие селекторы UI), к mcpOGV не относятся. Без e2e: `pytest tests/ --ignore=tests/e2e` → всё зелёное.
- Покрытие `src/mcp_server` ≈ 97%.
- Live-smoke на стенде разработки (`governmentgenai.directum360.ru`) — 5/5.
- **Не проверялось:** `docker build` / `docker compose up` (оставлено на сессию деплоя), реальная связка с LibreChat.

## 3. Состав сервера (этап 1)

| Группа | Тулы |
|---|---|
| Пользователь/оргструктура | `get_current_user`, `search_employees` |
| Задания и поручения | `list_my_assignments`, `list_action_items`, `get_action_item` |
| Аналитика | `get_discipline_analytics`, `get_outgoing_action_items_analytics` |
| Документы | `search_documents`, `get_document`, `list_documents_by_counterparty`, `list_letters`, `list_my_meetings` |
| Универсальное чтение OData | `odata_list_domains`, `odata_describe_entity`, `odata_query`, `odata_count`, `odata_get` |
| Встроенный MCP DRX (прокси) | `drx_native_*` — только read-only тулы модуля Sungero_MCPServer |
| Ресурсы | `drx://domains/*` — справочники из `.claude/skills/rxapi-*/SKILL.md` (кроме auth, current-user) |

Все тулы только читают. Записи (создание поручений) в этапе 1 нет.

Ключевые файлы:
- `src/mcp_server/config.py` — все настройки (см. §5);
- `src/mcp_server/app.py` — сборка сервера, `McpKeyMiddleware`, `/health`, правила старта;
- `src/mcp_server/context.py` — креды из заголовков (latin-1 → utf-8), кеш сервисов на пользователя;
- `src/mcp_server/odata_meta.py` — запреты универсального слоя (наборы, типы, навигации, секретные свойства);
- `src/mcp_server/native.py` — прокси встроенного MCP через `IntegrationAIAgent/HandleMcpRequest`;
- `src/mcp_server/audit.py` — SQLite-метрики вызовов тулов.

## 4. Архитектура деплоя

```
Пользователь ─► LibreChat (api) ──HTTP, сеть docker mcp-net──► mcp-ogv:8010/mcp ──HTTPS OData──► Directum RX
                 headers: X-Directum-Login/Password (customUserVars), X-MCP-Key
```

- `mcp-ogv` публикует порт только на loopback хоста: `127.0.0.1:8010:8010` (для `/health` с самой машины).
- LibreChat ходит по имени сервиса `http://mcp-ogv:8010/mcp` через внешнюю docker-сеть `mcp-net`.
- `/health` — публичный, `/mcp` — только с правильным `X-MCP-Key` (иначе 401).
- Host-заголовок проверяется (`MCP_ALLOWED_HOSTS`, защита от DNS rebinding): `mcp-ogv:8010` обязан быть в списке.

## 5. Переменные окружения mcpOGV

| Переменная | По умолчанию | Для стенда |
|---|---|---|
| `DIRECTUM_BASE_URL` | — (обязательна) | `https://<новый-стенд>/Integration/odata` |
| `MCP_OGV_KEY` | нет | **обязателен**, сгенерировать: `python -c "import secrets;print(secrets.token_hex(32))"` |
| `MCP_METRICS_SALT` | = `MCP_OGV_KEY` | **задать своё** случайное значение (не `change-me`) |
| `MCP_HOST` | `0.0.0.0` | `0.0.0.0` в контейнере |
| `MCP_PORT` | `8010` | `8010` |
| `MCP_ALLOWED_HOSTS` | `localhost:8010,127.0.0.1:8010,mcp-ogv:8010` | добавить свои host:port, если имя сервиса другое |
| `MCP_DIRECTUM_TIMEOUT_SECONDS` | `20` | по ситуации |
| `MCP_USAGE_DB_PATH` | `data/mcp_usage.db` | оставить (volume `./data`) |
| `MCP_ALLOW_ENV_CREDENTIALS` | `false` | **НЕ включать** на стенде |
| `DIRECTUM_AUTH_TOKEN` | нет | **не нужен** mcpOGV; не класть в env сервиса |

Правила старта (`build_asgi_app`): без `MCP_OGV_KEY` сервер падает с `RuntimeError`, если только не
`MCP_ALLOW_ENV_CREDENTIALS=true` **и** loopback `MCP_HOST` (режим локальной отладки).

**Рекомендация:** отдельный env-файл `.env.mcp-ogv` только с переменными выше (без `DIRECTUM_AUTH_TOKEN` чата).
`.env` / `.env.mcp-ogv` агент **не создаёт и не редактирует** — готовит содержимое с плейсхолдерами и отдаёт пользователю.

## 6. Шаги деплоя (чек-лист)

1. **Код на стенде:** `git clone https://github.com/nikitakorotaevnikita-sudo/MCP-Directum-RX.git && git checkout feature/mcp-ogv`.
   Убедиться, что в клоне есть `.claude/skills/rxapi-*/SKILL.md` (они в git; Dockerfile их копирует — без них не будет ресурсов `drx://domains/*`).
2. **Проверка стенда DRX** (с машины деплоя, креды обычного пользователя через переменные окружения, не в файлы):
   - `GET <DIRECTUM_BASE_URL>/$metadata` с Basic → 200;
   - есть ли модуль встроенного MCP (`IntegrationAIAgent/HandleMcpRequest`). Если нет — `drx_native_*` просто не появятся, остальное работает.
3. **Env-файл** `.env.mcp-ogv` (пользователь заполняет сам) — по §5.
4. **Docker-сеть:** `docker network create mcp-net` (один раз).
5. **`docker-compose.override.yml`** в этом репо (не коммитить стендовые значения):
   ```yaml
   services:
     mcp-ogv:
       env_file: [.env.mcp-ogv]
       networks: [default, mcp-net]
   networks:
     mcp-net:
       external: true
   ```
   Внимание: как `env_file` из override сливается с базовым (заменяет или дополняет `.env`), зависит от версии compose —
   проверить итог через `docker compose config mcp-ogv` (значения секретов в выводе не показывать пользователю в чат).
   Если `.env` на стенде содержит `DIRECTUM_AUTH_TOKEN` — либо не держать его там, либо поправить `docker-compose.yml`
   (по согласованию с пользователем). Сервис чата `mcp-directum-rx` на стенде, вероятно, не нужен: `docker compose up -d mcp-ogv`.
6. **Сборка и запуск:** `docker compose build mcp-ogv && docker compose up -d mcp-ogv`;
   `docker compose ps` → healthy; `curl http://127.0.0.1:8010/health`.
   Возможные грабли Dockerfile: `pip install -e ".[dev]"` выполняется до `COPY src` (editable без исходников) и тянет dev-зависимости
   (playwright и пр.). Если сборка падает/тяжёлая — заменить на обычную установку после `COPY src` без `[dev]`; это правка в репо → коммит в ветку.
   Также проверить, что в образе есть `httpx2` (транзитивно от `mcp==2.2.0`; явно в зависимостях не объявлен).
7. **LibreChat:** в `librechat.yaml` добавить блок из `docs/mcp-ogv/librechat.example.yaml`;
   в окружение LibreChat — `MCP_OGV_KEY=<тот же ключ>`; сервис `api` LibreChat подключить к `mcp-net`:
   ```yaml
   services:
     api:
       networks: [default, mcp-net]
   networks:
     mcp-net:
       external: true
   ```
   Перезапустить LibreChat. Пользователь вводит `DRX_LOGIN` / `DRX_PASSWORD` в настройках MCP-сервера в UI.
8. **Проверки** (§7). Сменить `MCP_METRICS_SALT` с плейсхолдера — обязательно.

## 7. Проверка после деплоя

- `curl -i http://127.0.0.1:8010/health` → 200.
- `curl -i -X POST http://127.0.0.1:8010/mcp` без ключа → **401** (`{"error":"unauthorized"}`).
- Изнутри контейнера LibreChat: `curl -i http://mcp-ogv:8010/health` → 200 (сеть и Host в allowed_hosts).
- Полный MCP-вызов по HTTP (скрипт на машине деплоя, venv проекта; креды — из переменных окружения):
  ```python
  import os, anyio, httpx2
  from mcp import Client
  from mcp.client.streamable_http import streamable_http_client

  async def main():
      http = httpx2.AsyncClient(headers={
          "X-MCP-Key": os.environ["MCP_OGV_KEY"],
          "X-Directum-Login": os.environ["DRX_LOGIN"],
          "X-Directum-Password": os.environ["DRX_PASSWORD"],
      })
      async with Client(streamable_http_client("http://127.0.0.1:8010/mcp", http_client=http)) as c:
          print([t.name for t in (await c.list_tools()).tools])
          print((await c.call_tool("get_current_user", {})).content[0].text)

  anyio.run(main)
  ```
- Live-smoke прямо против OData нового стенда (без HTTP-слоя):
  `MCP_LIVE_BASE_URL`, `MCP_LIVE_LOGIN`, `MCP_LIVE_PASSWORD` в окружении → `pytest tests/live -v` (5 тестов, только чтение).
- В LibreChat: агент с mcpOGV — «кто я?», «мои задания», «дисциплина по поручениям», «найди документ …».
- Метрики: `data/mcp_usage.db`, таблица с вызовами — без логинов/паролей, только HMAC-id.

## 8. Известные риски и отложенное

- **Персональные данные:** через `odata_*` агент видит ПДн заявителей (ФИО, email, телефоны, адрес, ИНН). Политику решает пользователь — поднять вопрос до показа.
- Стенд DRX может требовать `$filter` на любом списке (так на стенде разработки) — тулы это учитывают, `odata_query` требует фильтр.
- Явный `$select` по умолчанию на очень больших типах может упереться в длину URL → план Б: вырезать поля из ответа без `$select`.
- Отложено на этап 2+: блокировка `MetadataCache` на время загрузки; пагинация встроенного MCP; ограничение объёма в
  `get_outgoing_action_items_analytics`; очистка встроенных результатов с `isError`; редактирование `Bearer` в ошибках;
  `IBusinessUnitBoxs.Login` читаем; `httpx2` не объявлен в зависимостях.
- Таймаут LibreChat в примере — 60 с, таймаут DRX — 20 с; на медленном стенде поднять оба согласованно.

## 9. Правила (обязательны)

- Никогда не создавать/не редактировать/не перезаписывать `.env` (и стендовые env-файлы) — только готовить текст с плейсхолдерами.
- Секреты в код, markdown, коммиты, память — нельзя. Креды стенда — только в разовых командах через переменные окружения.
- Коммит/пуш/ветки/PR — только с явного согласия пользователя; перед push — `pytest tests/ --ignore=tests/e2e`.
- Не включать `MCP_ALLOW_ENV_CREDENTIALS` на стенде; не публиковать 8010 наружу (только loopback + docker-сеть).
- Запрещённые действия DRX (никогда не вызывать): `CoreEntities.*`, `Company.CreateLogin` / `SetLoginPassword`,
  `Shell.AddUserToGroup` / `RemoveUserFromGroup`, `Docflow.GrantAccessRights*`, `SmartProcessing.ElasticsearchReindex`,
  `Company.*TransferSubstitutedAccessRights`.
- Obsidian-заметка `Прототипы/MCP сервер/01 - Research.md` содержит живые секреты — не копировать оттуда ничего в репо.

## 10. Что сделать по итогам деплоя

- Отчитаться пользователю: URL LibreChat, результаты проверок §7, что пришлось поменять.
- Правки Dockerfile/compose — коммитом в `feature/mcp-ogv` (с согласия), стендовые значения — только в override/env вне git.
- Следующие этапы (по спеке): 2 — обращения граждан; 3 — дисциплина по исполнителям, дашборд, рассмотрение/согласование;
  4 — НПА; 5 — запись через preview/confirm. Для каждого — отдельный план.
