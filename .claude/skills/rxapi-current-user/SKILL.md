---
name: rxapi-current-user
description: Определить ID текущего пользователя в Directum RX / Aura. Использовать в начале каждой сессии перед фильтрацией по пользователю — мои задания, мои документы, моё расписание и т.п.
---

# Определение текущего пользователя

## Правило

**Всегда** выполнять в начале сессии. Кешировать `currentUserId` — не запрашивать повторно.

OData-сервис **не фильтрует автоматически** по текущему пользователю. Без явного фильтра вернутся все записи, доступные по правам (тысячи строк чужих данных).

## Быстрый способ — скрипт

```bash
python .claude/skills/rxapi-current-user/.claude/skills/rxapi-current-user/scripts/whoami.py
```

Вернёт:
```json
{"id": 1165, "name": "Беляк Игорь Сергеевич", "login": "nt_work\\belyak_is"}
```

Сохрани `id` как `currentUserId`.

## Ручной способ (два шага)

### Шаг 1 — извлечь логин из токена

Токен в `.env` имеет вид `Basic <base64>`. Декодировать base64 → строка `логин:пароль` → взять до `:`.

```python
import base64, os
token = open('.env').read().strip()
encoded = token.replace('Basic ', '')
login = base64.b64decode(encoded).decode().split(':')[0]
# → 'nt_work\\belyak_is'
```

### Шаг 2 — запросить Id по логину

```bash
python .claude/skills/rxapi-auth/scripts/query.py IUsers \
  --filter "Login/LoginName eq 'nt_work\belyak_is'" \
  --select "Id,Name" --top 1
```

Взять `Id` из ответа → это `currentUserId`.

## Использование currentUserId в фильтрах

```
Performer/Id eq {currentUserId}              — я исполнитель задания
Author/Id eq {currentUserId}                 — я автор задачи
Employee/Id eq {currentUserId}               — мои HR-данные
Initiator/Id eq {currentUserId}              — мои сервисные запросы
ResponsibleEmployee/Id eq {currentUserId}    — я ответственный
Manager/Id eq {currentUserId}                — мои проекты (я менеджер)
```

## Важно

- `IUsers` и `IEmployees` возвращают **одинаковый Id** для одного человека
- **Не использовать** `IPersonalSettings?$top=1` — вернёт первую запись по Id, не текущего пользователя
- `ILogins` может быть недоступен — не полагаться на него
