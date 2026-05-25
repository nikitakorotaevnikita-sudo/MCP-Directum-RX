# Design: Meetings List + Action Item Report

**Date:** 2026-05-25  
**Status:** Approved  
**Features:** UC-1 Meetings in Chat, UC-2 Action Item Report

---

## 1. Architecture

### New components

**`src/services/meetings.py` — `MeetingsService`**
- `get_my_meetings(days: int = 7) -> list[MeetingSummary]`
- OData: `GET IMeetings?$filter=...&$expand=Members($expand=Member),Minutes&$orderby=StartDate asc&$top=20`
- Filter: `StartDate ge <today> and StartDate le <today+days> and Members/any(m: m/Member/Id eq {current_user_id})`
- Зависимость: `CurrentUserService` (уже есть в `src/services/current_user.py`) — вызывается один раз для получения `current_user_id`

**New Pydantic models (`src/models/schemas.py`)**
```
MeetingSummary:
  id: int
  subject: str
  start_date: datetime
  end_date: datetime | None
  place: str | None
  agenda_summary: str | None      # из Minutes[0].Description или Subject, max 200 chars
  client_card_url: str

ActionItemDetail:
  id: int
  subject: str
  text: str | None
  performer: str
  author: str
  deadline: date | None
  status: str
  created_date: date
  client_card_url: str
  narrative: str                  # LLM-нарратив (2-4 предложения)
```

**ToolRegistry — два новых инструмента**
- `get_my_meetings` — без обязательных аргументов; параметр `days: int = 7`
- `get_action_item_details` — аргумент `action_item_id: int` (обязательный)

**New endpoint**
- `GET /api/directum/meetings/upcoming?days=7` → `list[MeetingSummary]` (для Sidebar)

**LLMService — прямая маршрутизация**
- Ключевые слова: `совещани`, `встреч`, `заседани` → `_direct_meetings_response()`
- Ключевые слова: `отчёт поручени`, `отчет поручени`, `расскажи о поручении`, `детали поручения` → `_direct_action_item_report_response(action_item_id)`
- Если ID не указан — просим уточнить

---

## 2. Data & API

### Meetings (UC-1)

**OData query:**
```
GET /IMeetings
  ?$filter=StartDate ge {today}T00:00:00Z and StartDate le {today+7}T23:59:59Z
         and Members/any(m: m/Member/Id eq {current_user_id})
  &$expand=Members($expand=Member($select=Id,Name)),
           Minutes($select=Description,Subject;$orderby=Created asc;$top=1)
  &$select=Id,Subject,StartDate,EndDate,Place
  &$orderby=StartDate asc
  &$top=20
```

**Поля:**
- `Subject` → тема
- `StartDate` / `EndDate` → дата/время форматируются как `ДД.ММ.ГГГГ ЧЧ:ММ`
- `Place` → место (если пусто — «не указано»)
- Повестка: берётся `Minutes[0].Description` (первый протокол), усекается до 200 символов. Если протокола нет — `Subject`.

**Клиентская карточка:**
```python
DirectumClient.build_client_card_url("IMeetings", meeting_id)
```

### Action Item Report (UC-2)

**OData query:**
```
GET /IActionItemExecutionTasks({id})
  ?$expand=
    Performer($select=Id,Name,JobTitle),
    Author($select=Id,Name),
    ActionItemExecutionAssignments($select=Status,DeadLine,Note,ActualExecutionDate)
  &$select=Id,Subject,Text,Status,DeadLine,Created
```

**LLM-нарратив:** После получения данных — один вызов LLM (не streaming):
```
"Напиши краткий отчёт (2-4 предложения) о поручении для руководителя. 
Тема: {subject}. Исполнитель: {performer}. Срок: {deadline}. 
Статус: {status}. Текст задания: {text}."
```

**Фильтрация доступа:** Показываем поручения только если текущий пользователь — автор (`ICurrentEmployee.Id == Author.Id`). Если нет — сообщение «Поручение найдено, но вы не являетесь его автором».

---

## 3. UI & Formatting

### Chat output — Meetings (UC-1)

```
📅 Ваши совещания на ближайшие 7 дней

**27.05.2026 10:00** — Еженедельная планёрка отдела
📍 Конференц-зал А · [Открыть карточку](https://...)
> Повестка: Обсуждение итогов квартала, распределение задач на май...

**28.05.2026 14:30** — Совещание по проекту ЭДО
📍 Zoom · [Открыть карточку](https://...)
> Повестка: Демонстрация прототипа, согласование сроков...
```

- Нет совещаний → «На ближайшие 7 дней совещаний не запланировано.»
- Markdown рендерится через существующий marked.js

### Chat output — Action Item Report (UC-2)

```
📋 Отчёт по поручению #42

**Тема:** Подготовка аналитической записки
**Исполнитель:** Иванова Мария Петровна (Главный специалист)
**Срок:** 30.05.2026 · **Статус:** В работе
[Открыть карточку](https://...)

---

Поручение находится в работе. Иванова М.П. приняла задание 23 мая. 
Срок исполнения — 30 мая, до дедлайна 5 дней. 
Промежуточных отчётов пока не поступало.
```

### Sidebar — кнопка «Совещания»

В `app.js` в панели Directum добавляется кнопка:
```
📅 Мои совещания
```
При клике — GET `/api/directum/meetings/upcoming` → список совещаний в боковой панели (аналогично заданиям).

Формат карточки в Sidebar:
```
📅 Еженедельная планёрка
   27.05 10:00 · Конференц-зал А
```

### Clickable action items → report trigger

В существующем output аналитики исходящих поручений каждая строка-поручение делается ссылкой:
```javascript
// app.js: при клике на поручение с ID → подставляем в input и отправляем
"отчёт поручение #42"
```
Эта строка попадает в LLMService, детектируется прямым роутингом, вызывает `get_action_item_details(42)`.

---

## 4. Error handling

- OData 404 для `IActionItemExecutionTasks({id})` → «Поручение #{id} не найдено.»
- OData timeout (> 10 сек) → «Directum не отвечает, попробуйте позже.»
- LLM нарратив недоступен (timeout / error) → показываем данные без нарратива (нарратив заменяется пустой строкой, блок `---` убирается)
- Пустой список совещаний → дружественное сообщение, не ошибка

---

## 5. Out of scope

- Создание/редактирование совещаний через чат
- Сохранение транскриптов и расшифровка речи
- Push-уведомления о предстоящих совещаниях
- Массовые отчёты по нескольким поручениям сразу
- Фильтрация совещаний по участнику/теме через чат
