# mcpOGV: поиск документа по признакам, ссылки на карточки, текст документа — дизайн

Дата: 2026-09-26. Ветка: `feature/mcp-ogv`. Статус: согласован с пользователем.

## Юзкейс

Сотрудник не может найти документ в Directum RX. Он пишет в чат то, что помнит («письмо из Минфина про ремонт
дорог, в августе»), и получает несколько вариантов с пояснением, почему каждый подошёл, и кликабельной ссылкой
на карточку. Выбранный документ агент может прочитать (текст файла) и пересказать.

Поиск — только по карточке документа (реквизиты). Полнотекстовый поиск по содержимому файлов и смысловой поиск —
вне рамок.

## 1. Ссылки на карточки документов

- `DirectumClient.build_document_card_url(document_id) -> str`:
  `{хост}/Client/#/card/030d8d67-9b94-4f0d-bcc6-691016eb70f3/{id}`, где GUID — платформенный базовый тип
  «Электронный документ» (`BaseGuid` у `OfficialDocument` в метаданных решения). Одна ссылка работает для любого вида.
- `ActionItemService._document_url` возвращает эту ссылку (раньше — адрес OData API, пользователю бесполезный).
  `search_documents` тоже заполняет `url` (раньше `null`). Затрагивает `search_documents`, `get_document`,
  `list_documents_by_counterparty`, `list_letters` и чат-прототип (там ссылка просто становится рабочей).
- В `INSTRUCTIONS` сервера: «Ссылки на карточки выводи markdown-ссылками `[название](url)`».

## 2. Тул `find_documents`

Сервис `src/services/document_search.py` (`DocumentSearchService`), тул в `src/mcp_server/tools/documents.py`.

| Параметр | Значения | Условие |
|---|---|---|
| `text` | строка | слова из названия/темы: токены ≥ 3 символов без стоп-слов, основа слова (срез окончаний); в OData — `contains(Name,'осн') or contains(Subject,'осн')` по всем основам через `or` |
| `kind` | `any` (по умолч.), `incoming_letter`, `outgoing_letter`, `order`, `memo`, `contract`, `citizen_request` | набор: `IOfficialDocuments`, `IIncomingLetters`, `IOutgoingLetters`, `IOrderBases`, `IMemos`, `IContractualDocuments`, `IRequests` |
| `counterparty` | строка | до 3 совпадений `search_counterparty`; навигация `Correspondent` (письма) / `Counterparty` (договорные). При `kind=any` — поиск по письмам и договорным; для `order`/`memo`/`citizen_request` — ошибка «контрагент к этому виду не применим» |
| `employee` | строка | до 3 совпадений `search_employee`; `PreparedBy`, `OurSignatory` или `Assignee` |
| `date_from`, `date_to` | `YYYY-MM-DD` | `RegistrationDate` в периоде, а для незарегистрированных — `Created`; границы дней в поясе стенда |
| `registration_number` | строка | `contains(RegistrationNumber,'…')` |
| `limit` | 1–20, по умолч. 5 | сколько вариантов вернуть |

Нужен хотя бы один признак. Контрагент/сотрудник не найдены → ответ с `message`, без запроса документов.

**Пул и ранжирование.** По каждому целевому набору один запрос (`$top=50`, `$orderby=RegistrationDate desc`,
`$expand=DocumentKind($select=Name)`), объединение без дублей по `Id`. Очки: +10 за каждую совпавшую основу слова
в названии/теме (без учёта регистра), +5 — номер совпал точно, +2 — дата в исходном периоде. При равенстве — новее выше.
`match_reasons` — человекочитаемые причины («слова: ремонт, дорог», «контрагент: …», «номер: …», «дата в периоде»,
«сотрудник: …», «вид: …»).

**Ослабление условий**, если пул пуст (по порядку, до первого непустого): убрать `text` (если есть другие признаки) →
расширить период на ±30 дней → убрать период. Что ослаблено — в `relaxed` (список строк).

**Ответ:** `items` (кандидаты: `id`, `name`, `kind`, `registration_number`, `registration_date`, `created`, `url`,
`score`, `match_reasons`), `returned`, `candidates_total`, `relaxed`, `message`.

## 3. Тул `get_document_text`

Сервис `src/services/document_text.py` (`DocumentTextService`), параметры `document_id`, `max_chars`
(1000–50000, по умолч. 20000).

1. `IOfficialDocuments?$filter=Id eq N&$expand=Versions($select=Id,Number;$expand=AssociatedApplication($select=Extension))` —
   последняя версия по `Number`. Нет документа → «не найден или нет доступа»; нет версий → «у документа нет файла».
2. Тело: `IOfficialDocuments(N)/Versions(V)/Body` → `Value` (base64). Больше 20 МБ — отказ.
3. Извлечение: `docx` — `zipfile` + `word/document.xml` (абзацы `w:p`, текст `w:t`); `pdf` — `pypdf`;
   `txt`/`csv`/`md`/`xml`/`html` — декодирование utf-8, иначе cp1251. Иной формат → пробуем `PublicBody` (PDF-представление);
   нет — «формат не поддерживается». Пустой текст у PDF → «похоже на скан без текстового слоя».
4. Ответ: `document_id`, `name`, `version`, `extension`, `text` (обрезан до `max_chars`), `chars_total`, `truncated`, `url`, `message`.

Новая зависимость: `pypdf` (чистый Python, работает офлайн в Docker).

## Тестирование

Unit на фейковом клиенте: ссылки; построение фильтров `find_documents` (каждый признак, наборы по `kind`,
контрагент для неподходящего вида, пояс стенда), ранжирование и `match_reasons`, ослабление условий, пустые признаки;
`get_document_text` — выбор последней версии, docx/pdf/txt, `PublicBody`-фолбэк, скан, обрезка, отсутствие версий,
лимит размера; тулы — конверты и ошибки.

Live — когда стенд доступен: `find_documents` по реальному письму, `get_document_text` для документа с docx/pdf,
ручная проверка ссылки в браузере.

## Риски

- Стенд RX на момент дизайна недоступен (БД `192.168.52.18` не отвечает) — формат ответа `Body` и открытие ссылки
  с базовым GUID проверяются после восстановления; при расхождении правится в рамках этой же фичи.
- `contains` в OData стенда предполагается нечувствительным к регистру (так работает текущий поиск сотрудников);
  проверить live.
- Пул ограничен 50 записями на набор, упорядоченными по дате: при очень общих словах лучший кандидат может не попасть в пул.
