# mcpOGV: просмотр поручений сотрудников администратором — дизайн

Дата: 2026-09-25. Ветка: `feature/mcp-ogv`. Статус: согласован с пользователем.

## Цель

Дать агенту LibreChat, работающему под учёткой администратора Directum RX, инструмент для просмотра
поручений любого сотрудника: входящих (сотрудник — исполнитель) и исходящих (сотрудник — автор),
с фильтрами по статусу, просрочке и периоду. Обычные пользователи инструмент не видят и вызвать не могут.

Вне рамок: запись/изменение поручений, вложенные группы в «Администраторах», сводная аналитика
(её закрывает `get_discipline_analytics`).

## Инструмент `admin_list_employee_action_items`

Read-only (`READ_ONLY`), файл `src/mcp_server/tools/admin.py`.

| Параметр | Тип / значения | По умолчанию | Смысл |
|---|---|---|---|
| `employee` | строка: ФИО или id | — (обязателен) | чьи поручения |
| `direction` | `incoming` / `outgoing` | — (обязателен) | сотрудник исполнитель / автор |
| `status` | `in_process` / `completed` / `aborted` / `all` | `in_process` | статус поручения |
| `only_overdue` | bool | `false` | срок прошёл и поручение в работе |
| `date_field` | `deadline` / `created` | `deadline` | к какой дате применять период |
| `date_from`, `date_to` | `YYYY-MM-DD` | нет | период, обе границы включительно |
| `limit` | 1–100 | 20 | размер выдачи |

Ответ — конверт `list_envelope` (`items`, `total`, `returned`, `truncated`) плюс
`employee: {id, name}` и `filters` (фактически применённые значения параметров).
Элементы `items` — `AssignmentSummary`, как у `list_action_items`
(`entity_type`: `action_item_assignment` для входящих, `action_item_task` для исходящих).

### Выбор сотрудника

- `employee` из одних цифр → `action_items.get_employee(id)`; нет записи → ошибка «Сотрудник с id N не найден».
- Иначе → `action_items.search_employee(query)` (тот же fuzzy-поиск, что у `search_employees`):
  - 0 совпадений → «Сотрудник «…» не найден»;
  - ровно 1 → он;
  - несколько, но одно имя совпадает с запросом без учёта регистра → оно;
  - иначе → ошибка со списком кандидатов `id — ФИО` (до 10), чтобы агент переспросил пользователя.

### Источники и фильтры OData

| `direction` | Набор | Роль в фильтре | `$expand` |
|---|---|---|---|
| `incoming` | `IActionItemExecutionAssignments` | `Performer/Id eq <id>` | — |
| `outgoing` | `IActionItemExecutionTasks` | `Author/Id eq <id>` | `Assignee($select=Name)` |

Условия объединяются через `and`:

- `status`: `in_process` → `Status eq 'InProcess'`, `completed` → `'Completed'`, `aborted` → `'Aborted'`, `all` → без условия.
- `only_overdue=true` → `Status eq 'InProcess' and Deadline lt <now UTC>`; при `status` ≠ `in_process`/`all` — ошибка
  «Просроченными бывают только поручения в работе» (не молча подменяем статус).
- Период: поле `Deadline` или `Created`; `date_from` → `ge <date>T00:00:00Z`, `date_to` → `lt <date+1>T00:00:00Z`.
  Неверный формат даты или `date_from > date_to` → ошибка с понятным текстом.
- Сортировка `Deadline asc`, как у существующих списков. `total` — `client.count` с тем же фильтром.

## Проверка «администратор»

Новый `src/services/admin_access.py`, класс `AdminAccessService(client, current_user_service)`:

- `is_admin() -> bool`: один запрос
  `IRoles?$filter=Sid eq 9cc6ea59-cd05-4c8e-b041-abefe9432e20 and RecipientLinks/any(l: l/Member/Id eq <мой id>)&$select=Id&$top=1`.
  Sid — платформенный идентификатор роли «Администраторы», не зависит от названия.
  Проверено на стенде: администратор получает строку, обычный пользователь — 204 (пусто), запрос коллекции ему разрешён.
- Учитывается только прямое членство.
- Сервис добавляется в `DirectumServices` (`factory.py`) полем `admin_access`.

### Скрытие и защита вызова

Новый `src/mcp_server/admin_gate.py`, `AdminGateMiddleware(provider, cache)` — по образцу `NativeProxyMiddleware`:

- `tools/list`: если пользователь не администратор — убрать из ответа тулы с префиксом `admin_`.
- `tools/call` тула `admin_*`: если не администратор — ошибка тула «Инструмент доступен только администраторам Directum RX.»
  (вызов не доходит до Directum). Защищает от вызова по известному имени.
- Результат `is_admin` кешируется `TtlCache` по `credentials.fingerprint` на 600 с.
- Любая ошибка при проверке (нет кредов, 401, 403, сеть) → «не администратор» (fail closed); в лог — только тип исключения.
- Порядок middleware: gate регистрируется так, чтобы видеть итоговый список, включая `drx_native_*`
  (на них фильтр не влияет — у них другой префикс).

Сам тул дополнительно вызывает `admin_access.is_admin()` в своём действии — вторая линия защиты,
если middleware будет отключён или обойдён.

## Изменения в существующем коде

`AssignmentsService`:

- новый метод `action_items_filter(direction, employee_id, status="in_process", only_overdue=False, date_field="deadline", date_from=None, date_to=None) -> tuple[str, str]`
  возвращает `(entity_set, filter)`;
- новые `list_employee_action_items(...)` и `count_employee_action_items(...)` поверх него;
- `_action_items_source(direction)` (мои поручения) переписывается как частный случай с id текущего пользователя
  и `status="in_process"` — поведение и фильтр прежние, существующие тесты должны пройти без изменений.

## Ошибки

Все отказы — `ToolError` с русским текстом через существующий `to_tool_error`. Внутренние детали OData наружу
не выходят (как сейчас). Метрики вызова пишет `ToolRunner` как обычно.

## Тестирование (TDD)

Unit (`tests/unit/`):

- `test_admin_access.py`: `is_admin` true/false по ответу клиента; фильтр содержит Sid и id пользователя; ошибка клиента → исключение пробрасывается (решение fail-closed — в gate).
- `test_mcp_admin_gate.py`: администратор видит `admin_list_employee_action_items`; не-администратор не видит; вызов не-администратором → ошибка, сервис не вызывался; ошибка проверки → скрыт; кеш — повторный `tools/list` не повторяет проверку.
- `test_mcp_tools_admin.py`: выбор сотрудника (id, одно совпадение, точное имя среди нескольких, неоднозначность, не найден); прокидывание параметров; конверт с `employee` и `filters`; `only_overdue` с `completed` → ошибка; неверная дата → ошибка.
- `test_assignments_filters.py` (или расширение существующего): фильтры для всех `status`, `only_overdue`, периода по `deadline`/`created`, обоих `direction`; фильтр «моих поручений» не изменился.

Live (`tests/live/test_mcp_live.py`, пропускается без `MCP_LIVE_*`): под текущей live-учёткой
`is_admin` возвращает bool без ошибки; если учётка — администратор, `list_employee_action_items` по самому себе
возвращает конверт.

Критерий готовности: `pytest tests/ --ignore=tests/e2e` зелёный; ручная проверка на стенде по HTTP:
под Administrator тул виден и возвращает поручения nadya, под nadya тул не виден и вызов отклоняется.
