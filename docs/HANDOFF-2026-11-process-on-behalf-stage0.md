# Handoff: документ из процесса RX — субъект из задачи и права (этап 0)

Для новой агентной сессии. Читать целиком перед началом работы. Общение с пользователем — по-русски.
Секреты — только плейсхолдеры, креды стенда — только в переменных окружения разовой команды.

## 1. Контекст

**Сценарий.** В Directum RX идёт no-code процесс «Создать документ». Блок схемы отправляет запрос ИИ-агенту в
ArioCowork по протоколу OpenAI под одним API-ключом. Агент через mcpOGV создаёт документ в RX.

**Проблемы:**
- mcpOGV работает под служебной учёткой Administrator. Автор документа — Administrator, реальный человек
  попадает только в «Подготовил» (параметр `on_behalf_of`, его выбирает модель).
- Права пользователю не выдаются: `MCP_WRITE_GRANT_RIGHTS_GUID` неизвестен.

**Спецификация (главный документ):** [`docs/rx-process-cowork-on-behalf-requirements.md`](rx-process-cowork-on-behalf-requirements.md).
В ней:
- целевая схема с подписанным контекстом процесса и `X-On-Behalf-Of`;
- требования к блоку RX, к Cowork, к сервису интеграции и к mcpOGV;
- этапы 0–2 и критерии приёмки.

**Задача этой сессии — этап 0 со стороны mcpOGV.** Он не требует доработок RX и Cowork. Сейчас не работают ни
модель, ни Cowork, поэтому всё проверяется тестами и прямыми вызовами MCP-клиентом (раздел 5).

Смежные документы:
- `docs/rx-integration-requirements.md`, часть 3 — метод «от имени» в сервисе интеграции (этап 1);
- `docs/mcp-ogv-overview.md` — обзор mcpOGV, ограничения API RX, эксплуатация;
- `docs/superpowers/specs/2026-10-14-mcp-ogv-write-tools-design.md` — тулы записи (двухшаговые, `confirm_token`).

## 2. Состояние репозитория

- Репозиторий: `https://github.com/nikitakorotaevnikita-sudo/MCP-Directum-RX`, ветка **`feature/mcp-ogv`**.
- Последние коммиты:
  - `3ef5298` feat(mcp): документ в рамках задачи процесса — вложение в задачу и права автору;
  - `42d4009` docs: требования к запросу ИИ-агента из процесса RX;
  - коммит с этим хэндоффом.
- Тесты: `.\.venv\Scripts\python.exe -m pytest tests/ --ignore=tests/e2e -q` → 1025 passed, 17 skipped.
  4 e2e чат-прототипа падают и на `main` — к задаче не относятся.
- Окружение: `uv pip install --python .venv\Scripts\python.exe -e ".[dev]"` (в venv нет pip).

### Что уже сделано в `3ef5298`

| Где | Что |
|---|---|
| `src/services/document_create.py` | `process_task(task_id)`: читает `ITasks(id)` (тип по `@odata.type` → набор, автор, вложения), определяет группу вложений: `TASK_DOCUMENT_GROUPS` (`ISimpleProcessTasks` → `0dbfb92e-d66b-4ada-9493-897a72d170df`), иначе — единственная группа текущих вложений, иначе `None` |
| там же | `plan_outgoing_letter` / `plan_legal_act` / `plan_document` принимают `process_task_id` → `plan["process_task"]` |
| там же | `_create_document`: после создания — `_attach_to_process` (POST `{набор}({id})/AttachmentDetails`), затем `_grant(document_id, [«Подготовил», автор задачи], attached_task_id)`. Если GUID не задан и документ приложен — предупреждение «доступ через вложение» |
| `src/mcp_server/tools/write.py` | Параметр `task_id` (тип `ProcessTask`) у `create_document`, `create_outgoing_letter`, `create_legal_act` |
| `tests/unit/test_document_create_process.py` | 10 тестов на сервис; `tests/unit/test_mcp_tools_write.py::test_document_tools_pass_process_task_id` |

**Проверено на стенде (только чтение):** задача процесса «Создать документ» — `DocflowApproval.ISimpleProcessTask`,
набор `ISimpleProcessTasks`, автор — инициатор (пример: задачи 86308–86315, автор — сотрудник id 396). Группа
вложений `0dbfb92e-…` — у всех 30 просмотренных задач этого типа.

**Не проверено:**
- даёт ли вложение в уже запущенную задачу доступ её участникам;
- срабатывает ли блок «Настройка прав доступа» на такой документ;
- живой вызов `GrantAccessRightsToDocument`. Автоклассификатор Claude Code заблокировал его как изменение прав,
  нужно явное разрешение пользователя.

## 3. Задачи этапа 0 (порядок выполнения)

Работать по TDD: сначала тест, убедиться, что он падает, потом код. После каждой задачи — полный прогон тестов.

### 3.1. «Подготовил» = автор задачи процесса (главное)

Сейчас «Подготовил» берётся из `on_behalf_of`, который выбирает модель. Нужно: если передан `task_id`, субъект
берётся из RX — автор задачи процесса.

- `write.py`: у трёх тулов `on_behalf_of` становится необязательным **только при** `task_id`. Без `task_id` и без
  `on_behalf_of` — прежняя ошибка «Укажите от чьего имени».
- `document_create.py`: общий шаг в начале трёх `plan_*`, например `_resolve_preparer(prepared_by_id, process_task_id)`:
  - задача есть → `prepared = task.author.id`;
  - `prepared_by_id` передан и не совпадает с автором → `CreateError`: «Документ в рамках задачи <id> создаётся от
    имени её автора (<ФИО>); on_behalf_of указывает другого сотрудника.»;
  - у задачи нет автора → `CreateError`.
- Описание `ProcessTask` и docstring тулов: при `task_id` `on_behalf_of` не нужен, «Подготовил» = автор задачи.
- **Поправить существующие тесты** `test_document_create_process.py`: сейчас там `prepared_by_id=53` при авторе
  396. В фейке нужен сотрудник 396 (Ким) с подразделением и организацией — `employee_card` требует их
  (`_require_org`).
- Тесты:
  - автор подставляется;
  - конфликт с `on_behalf_of` — ошибка;
  - совпадение — ок;
  - без автора — ошибка;
  - тул без `on_behalf_of`, но с `task_id`, — предпросмотр с автором задачи.

### 3.2. Принимать только подходящую задачу

- Настройка в `src/mcp_server/config.py`: `MCP_WRITE_PROCESS_TASK_TYPES: str = "ISimpleProcessTasks"` (через
  запятую). Пробросить в `DocumentCreateService` так же, как `grant_rights_guid`: `config.py` →
  `context.py:ServicesProvider.open` → `factory.build_directum_services` → конструктор сервиса.
- `process_task()`:
  - набор не из списка → `CreateError` «Задача <id> (<тип>) не из процесса, в рамках которого агенту разрешено
    создавать документы»;
  - `Status != 'InProcess'` → `CreateError` «Задача <id> не в работе (<статус>)». В `$select` добавить `Status` — он
    уже запрашивается.
- Тесты на оба отказа. Тесты групп вложений (`86400`, `86401`, тип `ICustomTasks`) — создать сервис со списком,
  включающим `ICustomTasks`.

### 3.3. Без повторного создания (идемпотентность до `request_id`)

Блок процесса может перезапуститься, агент — повторить вызов. Пока RX не передаёт `request_id`:

- В `_create_document` **перед** POST (только при `process_task`):
  1. перечитать вложения задачи (`ITasks(id)?$expand=AttachmentDetails`);
  2. по их id найти документы (`IOfficialDocuments`, фильтр `Id eq a or Id eq b …`, батчами ≤ 10 условий;
     `$select=Id,Name,Subject,Note`);
  3. если есть документ с той же темой (`Subject` без учёта регистра и пробелов по краям) и с пометкой mcpOGV в
     `Note` (`NOTE_TEMPLATE`, начало «Подготовлено ИИ-ассистентом (mcpOGV)») — не создавать; вернуть
     `{"document_id", "name", "url", "already_existed": True, "warnings": ["Документ уже создан в рамках этой задачи
     — возвращаю существующий."]}`.
- Тесты: повтор возвращает существующий и не делает POST; другая тема — создаёт; документ без пометки mcpOGV — создаёт.

### 3.4. Инструкция агенту

- В `src/mcp_server/app.py` (INSTRUCTIONS сервера) — правило: «Запрос пришёл из задачи RX (процесса) — передавай
  id этой задачи в `task_id` тулов создания документа; `on_behalf_of` тогда не нужен».
- Если в `docs/prompts/` есть промпт агента процесса — то же правило. Без фигурных скобок: они ломают промпты
  LibreChat и Cowork.

### 3.5. Документация

- `docs/mcp-ogv-overview.md`, раздел 3.2: отметить, что «Подготовил» в процессе берётся из задачи.
- `docs/rx-process-cowork-on-behalf-requirements.md`, раздел 5, этап 0: отметить, что сделано и что проверено.

## 4. Где что в коде

| Файл | Что |
|---|---|
| `src/services/document_create.py` | `DocumentCreateService`: `plan_*` / `create_*`, `process_task`, `_create_document`, `_attach`, `_attach_to_process`, `_grant`, `employee_card`, `_require_org`, `_note`, `NOTE_TEMPLATE`, `TASK_DOCUMENT_GROUPS` |
| `src/mcp_server/tools/write.py` | Тулы записи, `two_step` (предпросмотр → `confirm_token` → создание), `OnBehalf`, `ProcessTask`, `_required_person` |
| `src/mcp_server/tools/document_control.py` | `_person_id` (ФИО / id / «me»; «me» под служебной учёткой — ошибка) |
| `src/mcp_server/config.py` | `McpSettings` (`MCP_WRITE_*`) |
| `src/mcp_server/context.py` | `credentials_from_headers` (личный режим `X-Directum-Login/Password` или служебная учётка), `ServicesProvider.open` |
| `src/services/factory.py` | `build_directum_services` |
| `src/mcp_server/write_guard.py` | `ConfirmTokens` (HMAC-токен подтверждения), `WriteAudit` |
| `tests/unit/test_document_create.py` | `FakeClient`, `svc()`, `paths()` — переиспользуются в тестах процесса |
| `tests/unit/test_document_create_process.py` | `ProcessClient`, `TASKS` — тесты задачи процесса |
| `tests/unit/mcp_fakes.py` | `FakeProvider`, `call_tool`, `payload`, `error_text`, `run_async` |

## 5. Проверка без модели и Cowork

### 5.1. Автотесты

```powershell
& ".venv\Scripts\python.exe" -m pytest tests/ --ignore=tests/e2e -q -p no:cacheprovider
```

### 5.2. Живая проверка на стенде: скрипт вместо агента

Стенд 68: `https://governmentgenai.directum360.ru/` (172.16.104.68), сервис интеграции —
`https://governmentgenai.directum360.ru/Integration/odata` (без кредов отвечает 401, это нормально). Порт 8010 этого
хоста снаружи закрыт, поэтому mcpOGV для проверки запускается **локально**.

**Подготовка (делает пользователь):**
1. В веб-клиенте RX запустить тестовый процесс «Создать документ» от обычного пользователя (например, `nadya`).
   Сообщить id задачи.
2. Дать разрешение на запись в RX на стенде (создание тестового документа). Без явного «да» записи не делать.

**Запуск mcpOGV локально** (креды — только в переменных окружения этой консоли; `.env` не трогать):

```powershell
$env:DIRECTUM_BASE_URL = "https://governmentgenai.directum360.ru/Integration/odata"
$env:DIRECTUM_AUTH_TOKEN = "Basic <base64 логин:пароль служебной учётки>"
$env:MCP_ALLOW_ENV_CREDENTIALS = "true"
$env:MCP_HOST = "127.0.0.1"
$env:MCP_OGV_KEY = "<любой локальный ключ>"
$env:MCP_WRITE_ENABLED = "true"
$env:MCP_UTC_OFFSET = "+04:00"
& ".venv\Scripts\python.exe" -m src.mcp_server
```

**Вызов тула как агент** (в другой консоли, те же `MCP_OGV_KEY` в окружении):

```python
import os, json, anyio, httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

TASK_ID = 0  # id задачи процесса от пользователя

async def main():
    http = httpx2.AsyncClient(headers={"X-MCP-Key": os.environ["MCP_OGV_KEY"]}, timeout=120)
    async with Client(streamable_http_client("http://127.0.0.1:8010/mcp", http_client=http)) as c:
        args = {"document_type": "simple_document", "subject": "Тест этапа 0", "task_id": TASK_ID,
                "body_text": "Тестовый документ"}
        preview = json.loads((await c.call_tool("create_document", args)).content[0].text)
        print(json.dumps(preview["will_create"], ensure_ascii=False, indent=1))   # «Подготовил» = автор задачи?
        done = await c.call_tool("create_document", {**args, "confirm_token": preview["confirm_token"]})
        print(done.content[0].text)
        again = await c.call_tool("create_document", {**args, "confirm_token": preview["confirm_token"]})
        print(again.content[0].text)   # already_existed: true — нет второго документа

anyio.run(main)
```

Проверить по шагам:
1. **Предпросмотр.** «Подготовил» — инициатор процесса. `on_behalf_of` не передавался.
2. **Создание.** `process_task.attached = true`. Документ виден во вложениях задачи в веб-клиенте.
3. **Повторный вызов.** Возвращает тот же `document_id` с `already_existed`.
4. **Доступ инициатора.** Под учёткой инициатора (например, `nadya`, только чтение) запрос
   `GET IOfficialDocuments(<id>)?$select=Id,Name` отвечает 200 — документ доступен через вложение. Если 404/403 —
   вложение доступа не даёт; зафиксировать в спецификации и предложить пользователю блок «Настройка прав доступа».
5. **Блок прав (если пользователь добавил его в схему).** Проверить права исполнителя (изменение) и наблюдателя (просмотр).
6. **Отказы.** Передать `on_behalf_of` другого сотрудника → ошибка о конфликте. Передать id завершённой задачи →
   ошибка «не в работе».
7. **Уборка.** Тестовый документ после проверки удаляет пользователь в RX. Агент ничего не удаляет.

## 6. Правила (обязательны)

- **Файлы и секреты:**
  - не создавать, не редактировать и не перезаписывать `.env`;
  - секреты в коде, markdown, коммитах и памяти — только плейсхолдеры.
- **Git:** коммит и push — только по явной просьбе пользователя. Перед push — полный прогон тестов. Ветки и PR —
  только с согласия.
- **Запись в RX** (создание, права) — только с явного разрешения пользователя на конкретное действие. Отказ
  автоклассификатора не обходить: остановиться и объяснить.
- **Никогда не вызывать:** `CoreEntities.*`, `Company.CreateLogin` / `SetLoginPassword`,
  `Shell.AddUserToGroup` / `RemoveUserFromGroup`, `Docflow.GrantAccessRights*` без разрешения,
  `SmartProcessing.ElasticsearchReindex`, `Company.*TransferSubstitutedAccessRights`.
- **Зоны работ с сессией «Class OG»** (репозиторий Class-OG-Final). Её агент-классификатор читает
  `get_document`, `get_document_text`, `get_citizen_request_status` и навык `og-repeat`. Их имена, параметры и поля
  ответа меняются только после сообщения сессии «Class OG» (ListAgents); если её нет — сказать пользователю. Тексты
  обращений в сообщениях не пересылать.
- **Работа с HR Pro** (ветка `feature/mcp-hrpro`) **приостановлена** — не трогать.

## 7. Готово, когда

- [ ] 3.1–3.5 реализованы, тесты зелёные (ожидаемо около 1040+)
- [ ] Живая проверка 5.2 пройдена или зафиксировано, что именно не подтвердилось
- [ ] Спецификация и обзор обновлены
- [ ] Отчёт пользователю: что проверено на стенде, что нет, что передать командам RX и Cowork (этап 1)
