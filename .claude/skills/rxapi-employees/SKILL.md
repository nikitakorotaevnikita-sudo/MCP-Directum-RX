---
name: rxapi-employees
description: Поиск сотрудников, должностей, подразделений и оргструктуры в Directum RX / Aura. Использовать когда нужно найти сотрудника, узнать его отдел, должность, контакты, руководителя или получить список подразделения.
---

# Сотрудники и организационная структура

## Найти сотрудника по имени

```bash
python .claude/skills/rxapi-auth/scripts/query.py IEmployees \
  --filter "contains(Name,'Иванов') and Status eq 'Active'" \
  --select "Id,Name,Phone,Email" \
  --expand "JobTitle(\$select=Name),Department(\$select=Id,Name)" \
  --top 10
```

## Получить данные сотрудника по Id

```bash
python .claude/skills/rxapi-auth/scripts/query.py IEmployees(1165) \
  --expand "JobTitle(\$select=Name),Department(\$select=Id,Name),BsnUnit(\$select=Id,Name)"
```

## Сотрудники подразделения

```bash
python .claude/skills/rxapi-auth/scripts/query.py IEmployees \
  --filter "Department/Id eq {deptId} and Status eq 'Active'" \
  --select "Id,Name,Phone,Email" \
  --expand "JobTitle(\$select=Name)" \
  --orderby "Name asc"
```

## Найти подразделение

```bash
python .claude/skills/rxapi-auth/scripts/query.py IDepartments \
  --filter "contains(Name,'разработка') and Status eq 'Active'" \
  --select "Id,Name" \
  --expand "BusinessUnit(\$select=Id,Name),Manager(\$select=Id,Name)"
```

## Найти бизнес-единицу / юрлицо

```bash
python .claude/skills/rxapi-auth/scripts/query.py IBusinessUnits \
  --filter "Status eq 'Active'" \
  --select "Id,Name,TIN,TRRC" --top 50
```

## Ключевые сущности

| EntitySet | Что содержит |
|-----------|-------------|
| `IEmployees` | Сотрудники |
| `IDepartments` | Подразделения |
| `IBusinessUnits` | Бизнес-единицы / юрлица |
| `IJobTitles` | Должности |
| `IPersons` | Физлица (ФИО, паспорт) |
| `IUsers` | Пользователи системы |
| `IGroups` | Группы пользователей |

## Статусы

`Active` — активный, `Closed` — уволен/закрыт. Всегда добавляй `Status eq 'Active'` для действующих записей.
