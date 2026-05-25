---
name: rxapi-auth
description: Авторизация и базовые OData-запросы к Directum RX / Aura. Использовать при любой работе с данными системы Directum RX, Aura, OData-интеграцией.
---

# Авторизация и базовые запросы к Directum RX (Aura)

## Подключение

**Base URL:** `https://aura.npo-comp.ru/Integration/odata`

**Токен:** читать из `.env` в корне проекта — строка вида `Basic <base64>`.

**Обязательные заголовки:**
```
Authorization: <значение из .env целиком>
Accept: application/json
```

## Файлы конфигурации (корень проекта)

| Файл | Назначение | Как получить |
|------|-----------|--------------|
| `.env` | Токен авторизации (`Basic <base64>`) | Выдаётся администратором системы |
| `metadata.xml` | Кэш схемы OData (~2MB, 1540 EntitySets) | Скачать один раз командой ниже |

**Скачать/обновить metadata.xml:**
```bash
# Windows (PowerShell)
$token = Get-Content .env
Invoke-WebRequest -Uri "https://aura.npo-comp.ru/Integration/odata/`$metadata" `
  -Headers @{Authorization=$token} -OutFile metadata.xml

# Linux/Mac
curl -H "Authorization: $(cat .env)" \
  "https://aura.npo-comp.ru/Integration/odata/\$metadata" -o metadata.xml
```

> `metadata.xml` — это кэш. Скрипты читают его локально для поиска полей и типов сущностей.
> Обновлять при обновлении версии системы.

## Как делать запросы

### Через скрипт (предпочтительно)
```bash
python .claude/skills/rxapi-auth/scripts/query.py IEmployees --filter "contains(Name,'Иванов')" --select "Id,Name" --top 5
python .claude/skills/rxapi-auth/scripts/query.py IAssignments --filter "Status eq 'InProcess'" --count --top 10
python .claude/skills/rxapi-auth/scripts/query.py IEmployees(1165)        # одна запись по Id
python .claude/skills/rxapi-auth/scripts/query.py --list                  # все доступные EntitySets
python .claude/skills/rxapi-auth/scripts/query.py --list assignment       # EntitySets с 'assignment' в имени
```

### Прямой HTTP GET
```
GET {base_url}/{EntitySet}?{параметры}
```

## OData параметры

| Параметр | Пример | Что делает |
|----------|--------|------------|
| `$filter` | `Status eq 'Active'` | Фильтр записей |
| `$select` | `Id,Name,Status` | Выбрать только эти поля |
| `$expand` | `Department($select=Id,Name)` | Раскрыть связанный объект |
| `$top` | `50` | Первые N записей |
| `$skip` | `100` | Пропустить N (пагинация) |
| `$orderby` | `Deadline asc` | Сортировка |
| `$count=true` | — | Вернуть общее кол-во в `@odata.count` |

## Операторы фильтра

```
eq, ne, gt, lt, ge, le              — сравнение
and, or, not                        — логика
contains(Name,'текст')              — содержит
startswith(Name,'А')                — начинается с
Name eq null                        — проверка null
Department/Name eq 'ИТ'             — фильтр по связанному полю
Performer/Id eq 1165                — фильтр по Id связанного объекта
```

## Формат ответа

```json
{
  "@odata.count": 42,
  "value": [ {"Id": 1, "Name": "..."}, ... ]
}
```
Для одной записи `/IEmployees(1165)` — объект напрямую, без `value`.

## Разрешение русских терминов в API-имена

**Проблема:** пользователь говорит "команда" — это `IDepartments` или `ITeamsCommonAPITeams`?
Используй `find_term.py` перед построением запроса, если термин неоднозначен.

```bash
# Что такое "команда" в системе?
python .claude/skills/rxapi-auth/scripts/find_term.py команда
# → Entity: Team → ITeamsCommonAPITeams "Производственная команда"
# → Поле Employee.MainTeam = "Команда"

# Что такое "отпуск"?
python .claude/skills/rxapi-auth/scripts/find_term.py отпуск

# Что означает API-поле MainTeam?
python .claude/skills/rxapi-auth/scripts/find_term.py --prop MainTeam
# → [Employee] IEmployees  →  MainTeam = "Команда"

# Все поля сущности IEmployees на русском
python .claude/skills/rxapi-auth/scripts/find_term.py --eset IEmployees
```

**Ключевые термины и их отображение:**

| Пользователь говорит | API EntitySet | Поле / примечание |
|---------------------|--------------|-------------------|
| "команда", "моя команда" | `ITeamsCommonAPITeams` | `Employee.MainTeam` — команда сотрудника |
| "подразделение", "отдел" | `IDepartments` | `Employee.Department` |
| "отсутствие", "в отпуске" | `IAbsences` | `Begin`, `End`, `AbsenceReason` |
| "задания", "задачи" | `IAssignments` | `Performer/Id` — исполнитель |
| "сотрудник" | `IEmployees` | `Status eq 'Active'` — только активные |

> Файл словаря: `.claude/skills/rxapi-auth/entity_terms.json` (931 сущность, 1320 полей)
> Пересобрать: `python .claude/skills/rxapi-auth/scripts/build_terms.py`

## Типичные проблемы

| Ситуация | Действие |
|----------|----------|
| `value: []` — пусто | Ослабить фильтр, проверить значения |
| 401 Unauthorized | Проверить токен в .env |
| Непонятный русский термин | `python .claude/skills/rxapi-auth/scripts/find_term.py <слово>` |
| Нужно узнать поля сущности | `python .claude/skills/rxapi-auth/scripts/search_metadata.py IEmployeeDto` |
| Нужно найти EntitySet | `python .claude/skills/rxapi-auth/scripts/query.py --list <ключевое слово>` |

## Дата и время в фильтрах

Формат ISO 8601: `2025-03-18T00:00:00Z`

```
RegistrationDate ge 2025-01-01T00:00:00Z and RegistrationDate lt 2025-04-01T00:00:00Z
Deadline lt 2025-03-18T00:00:00Z
```
