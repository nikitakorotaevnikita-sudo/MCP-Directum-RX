# mcpOGV: тулы поверх прикладных методов RX — дизайн

Дата: 2026-09-26. Ветка: `feature/mcp-ogv`. Статус: согласован («делай все»).

Источник: разбор 181 метода сервиса интеграции (`Function` — GET `Модуль/Метод(парам=значение)`, `Action` — POST
`Модуль/Метод` с JSON) и исходники модулей в репозитории решения.

## Вызов методов

`DirectumClient.call_function(path, params)` — GET `{path}({k=литерал,...})`, литералы OData: строка `'…'` (кавычки
удваиваются), `true/false`, число, `DateTimeOffset` в ISO со смещением; путь URL-кодируется. Ответ: `value`, если есть,
иначе весь объект (комплексный тип). Действия — существующий `post`; ответ так же разворачивается из `value`.

## 1. `ask_documents` — вопросно-ответный поиск RX (QASearchCore)

- `QASearchEnabled`/`GetReadySearchAreas` (GET), `CreateSearchTask(question, searchAreaIds)` и
  `GetSearchTaskInfo(taskId)` (POST). Поиск асинхронный: задача в RAG-сервисе, затем опрос. Права учитываются RX
  (`recipientIdsOfCurrentUser` в исходнике).
- Тул `ask_documents(question, search_area_ids=None, wait_seconds=30)`: без областей — все готовые; опрос каждые 2 с
  до завершения или `wait_seconds` (5–45, чтобы уложиться в таймаут LibreChat 60 с). Не успел — `status=in_progress`
  и `task_id`; дочитать — `get_ask_documents_result(task_id)`. `list_qa_search_areas()` — список областей.
- Ответ: `status` (`completed` / `in_progress` / `error` / `unavailable`), `answer`, `score`, `search_area`, `sources`
  (`name`, `url` — `EntityLink` или ссылка на карточку по `EntityId`, `extension`, до 2 фрагментов по 500 символов), `message`.

## 2. `get_executive_summary` — сводка руководителя (GD.Dashboard)

`GetActionItemsMetric` (всего / в работе / просрочено по поручениям нашей организации текущего сотрудника) и
`GetRequestQuestionsMetric` (вопросы обращений: название, количество; отдаём топ-10 по убыванию и общее число).
Метрики считаются по организации **сотрудника**: у учётки без карточки сотрудника (например, Administrator) — нули;
тул в этом случае пишет пояснение в `message`.

## 3. `add_working_days` — рабочий календарь

`Docflow.AddWorkingDaysAndHours(date, days, hours)`: дата (начало дня в поясе стенда) + рабочие дни/часы по календарю RX.
Параметры: `date` (YYYY-MM-DD, по умолчанию сегодня), `days` (0–365), `hours` (0–24). Ответ: `date_from`, `days`, `hours`, `result`.

## 4. Проверка администратора через `Company.IsCurrentUserAdmin`

`Users.Current.IncludedIn(Roles.Administrators)` — учитывает вложенные группы. `AdminAccessService.is_admin` вызывает
метод; при ошибке вызова (метод недоступен на стенде) — прежний запрос к `IRoles` по Sid.

## Не делаем

`CitizenRequests.GetRequestStatus(regNumber, pin)` / `…ByKremlinId` — метод портала: нужен ПИН заявителя или id из ПОС,
у сотрудника их нет. Статус обращения для сотрудника — отдельной задачей через данные `IRequests`.

## Тестирование

Unit на фейковом клиенте: литералы и URL `call_function`, разворачивание ответа; QA — выбор областей, успешный опрос,
таймаут → `in_progress`, ошибки создания/выполнения, недоступность, маппинг источников; сводка и пустые метрики;
календарь; admin — метод, фолбэк на `IRoles`. Live — после восстановления стенда.
