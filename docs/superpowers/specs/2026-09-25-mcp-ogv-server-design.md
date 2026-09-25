# mcpOGV — MCP-сервер поверх API Directum RX

Дата: 2026-09-25
Статус: дизайн утверждён, ожидает ревью спеки

## 1. Цель

Дать агентной платформе на базе **LibreChat** доступ к Directum RX (DRX) через протокол
MCP. Нужен широкий набор операций, чтобы быстро проверять продуктовые гипотезы
(обращения граждан, исполнительская дисциплина, рассмотрение и согласование,
документы и НПА), и качественные курируемые операции для подтверждённых сценариев.

Текущий проект MCP-Directum-RX называется «MCP», но протокол MCP не реализует:
инструменты живут в `ToolRegistry` в формате OpenAI function calling и используются
только собственным чатом. mcpOGV — первый настоящий MCP-сервер проекта.

## 2. Ключевые решения

| Вопрос | Решение |
|---|---|
| Клиент | LibreChat (агентная платформа) |
| Транспорт | Streamable HTTP, stateless |
| Имя сервера | `mcpOGV` (ключ в `librechat.yaml`, имя MCP-сервера, сервис `mcp-ogv`) |
| Авторизация в DRX | Личные креды пользователя через `customUserVars` LibreChat |
| Подход | Гибрид: курируемое ядро + универсальное чтение + справочники доменов + прокси родного MCP DRX |
| Размещение | Этот репозиторий, пакет `src/mcp_server/`, отдельный процесс и Docker-сервис |

## 3. Результаты проверки стенда

Стенд: `https://governmentgenai.directum360.ru/Integration/odata` (DRX 26.2, ОГВ).
Проверено чтением от имени обычного пользователя и администратора.

- Обычный пользователь проходит Basic-авторизацию в Integration Service — схема с
  личными кредами работоспособна.
- Текущий пользователь определяется по логину: `IEmployees?$filter=Login/LoginName eq '<login>'`.
- Права DRX применяются: обычный пользователь видит меньше администратора
  (например, `IAssignments` в работе 5236 против 5507). Видимость на стенде широкая.
- **Запросы без `$filter` к большим наборам отклоняются**: «Превышено максимальное
  количество сущностей в запросе. Используйте фильтрацию» (даже с `$top=2`).
- Объёмы реальные: у одного пользователя 1191 задание в работе. Тяжёлые `$count`
  могут упираться в таймаут.
- Метаданные: 823 EntitySet, 101 Action, 80 Function.
- **Встроенный MCP-сервер DRX** `Sungero_MCPServer` v26.2.0.30 (протокол `2025-03-26`)
  доступен через bound action `IntegrationAIAgent/HandleMcpRequest(value: string) → string`
  (JSON-RPC строкой). Сейчас отдаёт 6 read-only тулов из реестра `IAIAgentTools`,
  2 ресурса (`config://tools`, `config://assistants`), 1 промпт. В реестре есть поле
  `IsExecConfirmRequired`.
  - Родной тул `gd_dashboard_ai_agent_get_action_items2_info` возвращает одинаковую
    нагрузку для всех исполнителей — вероятный баг агрегации на стороне DRX.
  - Родной тул `gd_dashboard_ai_agent_get_requests_info` возвращает только ссылку
    на список в веб-клиенте, без данных.

Наборы с данными (записи, видимые обычному пользователю):

| Домен | Наборы |
|---|---|
| Обращения граждан | `IRequests` 1761, `IQuestions` 1228, `ISubQuestions` 651, `IOutgoingRequestLetters` 1125, обработка 239, веб-приёмная 237, переадресация 145, ПОС 68 |
| Поручения | `IActionItemExecutionTasks` 1766, `IActionItemExecutionAssignments` 1214, контроль 36, продление срока 6 |
| Рассмотрение | `IDocumentReviewTasks` 1012, `IDocumentReviewAssignments` 1094, `IPreparingDraftResolutionAssignments` 237 |
| Документы | `IIncomingDocumentBases` 2751, `IOutgoingDocumentBases` 1521, `IIncomingLetters` 636, `IOutgoingLetters` 396 |
| Согласование | `IEntityApprovalAssignments` 605, `ISigningAssignments` 320, `IRegisterDocumentAssignments` 699, `IApprovalTasks` 113 |
| НПА | `ILegalActsLegalActs` 56, согласование 39 + 23 |
| Совещания | `IMinutesBases` 5 — данных мало |

Полезные функции и действия:
- `Dashboard/GetActionItemsMetric()` — всего / в работе / просрочено по поручениям.
- `Dashboard/GetRequestQuestionsMetric()` — обращения по вопросам классификатора.
- `CitizenRequests/GetRequestStatus`, `GetRequestStatusByKremlinId`.
- `RecordManagement/CreateActionItemExecution`, `CreateDocumentReviewTask`, `CreateAcquaintanceTask`.
- `QASearchCore/CreateSearchTask` + `GetSearchTaskInfo` (кандидат на следующий этап).

Запрещены к использованию в любом слое: `CoreEntities.*` (обслуживание, лицензии),
`Company.CreateLogin`, `Company.SetLoginPassword`, `Shell.AddUserToGroup`,
`Shell.RemoveUserFromGroup`, `Docflow.GrantAccessRights*`, `SmartProcessing.ElasticsearchReindex`,
`Company.*TransferSubstitutedAccessRights`.

## 4. Архитектура

```
src/
├── services/                  # существующий сервисный слой, переиспользуется
│   └── factory.py             # NEW: build_services(client, auth_token) — общая сборка
├── main.py                    # FastAPI-чат, переходит на factory, поведение не меняется
└── mcp_server/                # NEW, отдельный процесс
    ├── __main__.py            # точка входа: python -m src.mcp_server
    ├── app.py                 # MCP-сервер mcpOGV, Streamable HTTP, transport_security, /health
    ├── config.py              # настройки MCP (порт, хосты, секрет, TTL, флаги)
    ├── context.py             # креды из заголовков → DirectumClient → сервисы на запрос
    ├── envelope.py            # формат списков {items, total, returned, truncated}
    ├── errors.py              # маппинг ошибок в MCP tool error
    ├── audit.py               # аудит записи + метрики использования тулов (SQLite)
    ├── confirm_store.py       # хранилище preview-токенов
    ├── tools/
    │   ├── common.py          # get_current_user, search_employees
    │   ├── action_items.py    # задания, поручения, дисциплина, дашборд
    │   ├── citizen_requests.py
    │   ├── review_approval.py
    │   ├── documents.py       # документы, письма, НПА, совещания
    │   ├── write.py           # preview_* + confirm_operation
    │   ├── odata.py           # универсальный слой чтения
    │   └── native.py          # прокси родного MCP DRX
    └── resources.py           # drx://domains/*
```

- **Тулы — тонкие обёртки:** принять параметры → вызвать сервис → вернуть JSON.
  Бизнес-логика остаётся в `src/services/`.
- **Рефакторинг `factory`:** сейчас сервисы собираются один раз из токена `.env`.
  Нужно собирать их на каждый запрос из кредов пользователя. `factory.build_services`
  используется и чатом, и MCP.
- **Рефакторинг аналитики исходящих:** раскладка по срокам переезжает из
  `llm_service.py` в сервис `analytics`, чтобы ею пользовались чат и MCP.
- **Отчёт по поручению:** MCP-сервер не вызывает LLM. `get_action_item` отдаёт факты,
  текст отчёта пишет агент LibreChat.
- **Лимиты:** `limit` по умолчанию 20, максимум 100 (универсальный слой — 50).
  Ответ списка всегда содержит `total` и `truncated`.
- Даты — ISO 8601 с часовым поясом. У сущностей есть `url` на карточку в веб-клиенте DRX.

## 5. Авторизация и контекст запроса

Конфиг LibreChat:

```yaml
mcpServers:
  mcpOGV:
    type: streamable-http
    url: http://mcp-ogv:8010/mcp
    headers:
      X-Directum-Login: '{{DRX_LOGIN}}'
      X-Directum-Password: '{{DRX_PASSWORD}}'
      X-MCP-Key: '${MCP_OGV_KEY}'
    customUserVars:
      DRX_LOGIN:    { title: 'Логин Directum RX' }
      DRX_PASSWORD: { title: 'Пароль Directum RX' }
    serverInstructions: true
```

Обработка вызова:
1. Проверить `X-MCP-Key` — общий секрет LibreChat ↔ mcpOGV. Без него сервер не
   должен работать открытым прокси к DRX.
2. Взять логин и пароль из заголовков (`Context.headers`), собрать Basic в памяти.
   Нет заголовков → ошибка тула с инструкцией для пользователя.
3. Создать `DirectumClient` и сервисы через `factory` на время вызова, затем закрыть.
4. Текущий пользователь определяется по логину и кешируется в памяти на 10 минут.
   Ключ кеша — SHA-256 от логина и пароля; сам пароль не хранится.
5. 401 от DRX → «Неверный логин или пароль Directum». Остальные ошибки DRX — через
   `DirectumError.safe_message`.

Гарантии:
- Заголовки с кредами не логируются; креды не попадают в ответы, ошибки, метрики, аудит.
- Общего состояния между пользователями нет, кроме кеша «хеш кредов → сотрудник»
  (Id и ФИО) и кеша `$metadata` (одинаков для всех).
- `stateless_http`, `transport_security` со списком хостов из `MCP_ALLOWED_HOSTS`.

Режим отладки: `MCP_ALLOW_ENV_CREDENTIALS=true` — при отсутствии заголовков
используется `DIRECTUM_AUTH_TOKEN` из `.env` (для Claude Code / MCP Inspector без
LibreChat). По умолчанию выключен.

Новые настройки (добавляются в `.env` вручную, сам `.env` не редактируется агентами):
`MCP_PORT` (8010), `MCP_ALLOWED_HOSTS`, `MCP_OGV_KEY`, `MCP_ALLOW_ENV_CREDENTIALS`,
`MCP_CONFIRM_TOKEN_TTL` (600). `DIRECTUM_BASE_URL` указывает на стенд.

## 6. Каталог тулов v1

### Слой 1 — курируемые, чтение

| Домен | Тул | Параметры | Основа |
|---|---|---|---|
| Общие | `get_current_user` | — | `CurrentUserService` |
| | `search_employees` | `query`, `limit` | fuzzy-поиск |
| Поручения | `list_my_assignments` | `only_overdue`, `limit` | `AssignmentsService` |
| | `list_action_items` | `direction` (incoming\|outgoing), `on_control?`, `limit` | assigned_to_me / created_by_me |
| | `get_action_item` | `action_item_id` | `get_action_item_details` |
| | `get_action_items_dashboard` | — | `Dashboard/GetActionItemsMetric` |
| | `get_discipline_analytics` | `employee?`, `date_from?`, `date_to?` | `DisciplineAnalyticsService` |
| | `get_discipline_by_performers` | `performers?` (список ФИО) \| `department?`, `date_from?`, `date_to?` | `$count` по каждому исполнителю: в работе / просрочено / в срок / с опозданием / % в срок |
| | `get_outgoing_action_items_analytics` | `limit_per_category` | сервис `analytics` |
| Обращения | `get_requests_by_question_stats` | `top?` | `Dashboard/GetRequestQuestionsMetric` |
| | `list_citizen_requests` | `date_from?`, `date_to?`, `question?`, `in_work?`, `overdue?`, `limit` | `IRequests` |
| | `get_citizen_request` | `request_id` | карточка: номер, дата, тема, заявитель, вопросы, исполнитель, срок, состояние, ответы |
| | `get_citizen_requests_stats` | `date_from`, `date_to`, `group_by` (question\|month\|state) | `$count` |
| | `get_request_status` | `request_id` | `CitizenRequests/GetRequestStatus` |
| Рассмотрение | `list_my_reviews` | `status?`, `limit` | рассмотрение + проекты резолюций |
| Согласование | `list_my_approvals` | `kind` (approval\|signing\|all), `limit` | согласование, подписание |
| Документы | `search_documents` | `query`, `limit` | `search_documents` |
| | `get_document` | `document_id` | `get_document` |
| | `list_documents_by_counterparty` | `counterparty`, `limit` | стоп-листы + стемминг |
| | `list_letters` | `direction`, `date_from?`, `date_to?`, `limit` | `list_letters` |
| НПА | `list_legal_acts` | `state?`, `limit` | `ILegalActsLegalActs` |
| Совещания | `list_my_meetings` | `days` | `MeetingsService` |

Все тулы чтения помечаются `readOnlyHint=true`.

### Слой 1 — курируемые, запись

| Тул | Параметры | Действие DRX |
|---|---|---|
| `preview_action_item` | `document_id`, `performer`, `action_text`, `subject?`, `deadline?` | `RecordManagement/CreateActionItemExecution` |
| `preview_document_review` | `document_id`, `addressee`, `deadline?`, `comment?` | `RecordManagement/CreateDocumentReviewTask` |
| `preview_acquaintance` | `document_id`, `participants`, `deadline?` | `RecordManagement/CreateAcquaintanceTask` |
| `confirm_operation` | `preview_token` | выполняет сохранённую операцию |

Точные параметры действий `CreateDocumentReviewTask` и `CreateAcquaintanceTask`
уточняются по метаданным на этапе плана.

### Слой 2 — универсальное чтение

`odata_list_domains`, `odata_describe_entity(entity_set)`,
`odata_query(entity_set, filter, select?, expand?, orderby?, top?)`,
`odata_count(entity_set, filter)`, `odata_get(entity_set, id, expand?)`.

### Слой 3 — справочники доменов (MCP-ресурсы)

`drx://domains/{name}`: 17 скиллов `.claude/skills/rxapi-*` плюс новые справочники
для ОГВ-модулей (обращения граждан, рассмотрение, согласование, НПА), собранные из
метаданных стенда.

Инструкции сервера (`instructions`, на русском, подставляются LibreChat при
`serverInstructions: true`): назначение тулов, протокол preview → явное согласие
пользователя → confirm, правило «в `odata_query` всегда указывай фильтр».

### Слой 4 — прокси родного MCP DRX

Тулы из `Sungero_MCPServer` с префиксом `drx_native_`. См. раздел 9.

### Рекомендация для LibreChat

Тулов больше тридцати — для Qwen3 это много в одном контексте. Агентов собираем по
ролям с поштучным выбором тулов в Agent Builder: «Помощник по обращениям»,
«Контроль исполнения», «Секретарь руководителя». Тулы записи — только там, где нужны.

## 7. Безопасная запись

**`preview_*`** ничего не пишет в DRX:
- документ существует и виден пользователю (`get_document` под его кредами);
- исполнитель/адресат разрешён поиском и подтверждён `get_employee` — выдуманный id не проходит;
- срок разобран с часовым поясом, текст не пустой и не шаблонный.

Операция сохраняется в `confirm_store` под токеном `secrets.token_urlsafe(16)` и
привязывается к хешу кредов, типу операции и полностью разрешённому payload.
TTL — `MCP_CONFIRM_TOKEN_TTL`. Агенту возвращаются токен, человекочитаемая сводка
и указание получить явное согласие пользователя.

**`confirm_operation(token)`**:
- токен существует, не просрочен, принадлежит тому же пользователю;
- токен удаляется до выполнения (одноразовость, без двойного создания);
- выполняется сохранённый payload без повторной интерпретации;
- ответ — id и ссылка на созданную сущность; запись в аудит.

Ограничение: сервер не может доказать, что подтвердил человек. Снижение риска —
инструкции и описания тулов, тулы записи только у нужных агентов, запись невозможна
одним вызовом, действия выполняются под личными кредами, аудит каждой записи.

Хранилище в памяти процесса → одна реплика в v1.

## 8. Защита универсального слоя

- Только GET.
- `entity_set` есть в метаданных и не в чёрном списке (логины, пользователи,
  сертификаты, лицензии, права доступа, аудит, системные настройки).
- `filter` обязателен; длина ограничена; запрещены символы `&`, `?`, `#`;
  значения URL-кодируются.
- `select`, `expand`, `orderby` сверяются с метаданными; при неизвестном поле —
  ошибка со списком допустимых полей.
- `top`: по умолчанию 20, максимум 50. Таймаут запроса 20 секунд.
- `$metadata` кешируется в памяти при первом обращении.

## 9. Прокси родного MCP DRX

- `tools/list`: родные тулы запрашиваются из DRX кредами вызывающего, кешируются на
  10 минут по хешу кредов, получают префикс `drx_native_`; описание и схема — как есть.
- `tools/call drx_native_*`: префикс снимается, `tools/call` упаковывается в JSON-RPC
  и отправляется в `IntegrationAIAgent/HandleMcpRequest` под кредами пользователя;
  результат (включая `isError`) возвращается как есть.
- В v1 проксируются только тулы с `readOnlyHint=true`.
- Переводятся только `tools/list` и `tools/call`, не весь сеанс.
- **Технический риск:** список тулов зависит от пользователя, а высокоуровневый API
  SDK регистрирует тулы статически. Первый шаг реализации — спайк с низкоуровневым
  обработчиком `list_tools`. Запасной вариант: статические `drx_native_list` и
  `drx_native_call(tool, arguments)`.

## 10. Ошибки и наблюдаемость

| Ситуация | Ответ тула (`isError=true`) |
|---|---|
| нет кредов | «Укажите логин и пароль Directum в настройках mcpOGV в LibreChat» |
| неверный `X-MCP-Key` | отказ без деталей |
| DRX 401 / 403 | «Неверный логин или пароль» / «Недостаточно прав в Directum» |
| «Превышено… Используйте фильтрацию» | «Слишком широкий запрос — добавь фильтр» |
| таймаут | «Запрос слишком тяжёлый — сузь условия» |
| прочие ошибки DRX | `DirectumError.safe_message` |
| непредвиденная ошибка | «Внутренняя ошибка mcpOGV, код `<correlation-id>`» |

- Структурированный лог каждого вызова: тул, длительность, статус, логин, correlation-id.
- Метрики использования тулов в SQLite (тул, результат, длительность, хеш пользователя) —
  для анализа гипотез: какие тулы агенты реально используют и где ошибаются.
- Аудит записи: логин, операция, id сущностей, время.
- `/health` без авторизации.

## 11. Тестирование

- **unit (TDD):** `context` (заголовки, отсутствие кредов, хеш, кеш), `envelope`,
  `errors`, каждый курируемый тул на фейковых сервисах, защита универсального слоя,
  `confirm_store` (TTL, одноразовость, привязка к пользователю), прокси родного MCP
  (префиксы, фильтр read-only, JSON-RPC).
- **интеграционные:** MCP-клиент из SDK поверх настоящего протокола, DRX подменён
  `httpx.MockTransport`.
- **live-smoke** (маркер, по умолчанию пропускается): read-only тулы на стенде,
  креды из переменных окружения. Запись на стенде — только вручную с явного согласия.
- **ручной e2e в LibreChat:** подключение, ввод кредов, сценарии трёх агентов.
- Покрытие ≥ 70%.

## 12. Деплой

- Зависимость: MCP Python SDK с закреплённой версией (ставится при сборке образа).
- `docker-compose.yml`: сервис `mcp-ogv` на том же образе, команда
  `python -m src.mcp_server`, порт 8010, `env_file: .env`, healthcheck `/health`.
- LibreChat в той же Docker-сети; `MCP_ALLOWED_HOSTS` включает `mcp-ogv`.
- README: раздел про mcpOGV и пример `librechat.yaml` без секретов.

## 13. Порядок поставки

1. **Ядро:** `factory`, `context`, `envelope`, `errors`, `app`, Docker, пример
   конфига LibreChat; спайк динамического `list_tools`; прокси родного MCP;
   универсальный слой; перенос существующих тулов (поручения, документы, письма,
   совещания, дисциплина, аналитика исходящих).
2. **Обращения граждан.**
3. **Дисциплина по исполнителям, дашборд, рассмотрение и согласование.**
4. **Документы и НПА.**
5. **Запись:** `confirm_store`, аудит, три операции preview + `confirm_operation`.

## 14. Вне рамок v1

- Redis и горизонтальное масштабирование.
- OAuth / SSO / OBO.
- Пишущие родные тулы DRX.
- Расширение записи: выполнить задание, согласовать, продлить срок, переадресовать
  (подпроект 3).
- QA-поиск по базе знаний DRX, досье контрагента — кандидаты в подпроект 2.

## 15. Открытые вопросы

- Точные параметры `CreateDocumentReviewTask`, `CreateAcquaintanceTask`,
  `CitizenRequests/GetRequestStatus` — уточняются по метаданным в плане.
- Поле состояния и срока обращения в `IRequests` для фильтров `in_work` / `overdue` —
  уточняется по данным стенда в плане.
- Баг агрегации родного тула `get_action_items2_info` — сообщить команде DRX.
