---
name: rxapi-esm
description: Сервисные запросы и обращения в Directum RX / Aura (ESM). Использовать для запросов про заявки в IT / АХО / HR, обращения, инциденты, мои заявки, обращения в работе.
---

# ESM — сервисные запросы и обращения

Перед использованием: получить `currentUserId` через skill `rxapi-current-user`.

## Мои обращения (я инициатор)

```bash
python .claude/skills/rxapi-auth/scripts/query.py IServiceRequests \
  --filter "Initiator/Id eq {currentUserId} and Status ne 'Closed'" \
  --select "Id,Name,Status,Created,Category" \
  --expand "Assignee(\$select=Id,Name)" \
  --orderby "Created desc" --top 20
```

## Обращения назначенные мне

```bash
python .claude/skills/rxapi-auth/scripts/query.py IServiceRequests \
  --filter "Assignee/Id eq {currentUserId} and Status eq 'InProcess'" \
  --select "Id,Name,Status,Created,Deadline" \
  --expand "Initiator(\$select=Id,Name)" \
  --orderby "Deadline asc"
```

## Найти обращение по теме

```bash
python .claude/skills/rxapi-auth/scripts/query.py IServiceRequests \
  --filter "contains(Name,'ноутбук') and Status ne 'Closed'" \
  --select "Id,Name,Status,Created" --top 10
```

## Категории сервисов

```bash
python .claude/skills/rxapi-auth/scripts/query.py IServiceCategories \
  --filter "Status eq 'Active'" \
  --select "Id,Name" --top 50
```

## Статусы обращений

| Значение | Смысл |
|----------|-------|
| `Draft` | Черновик |
| `Registered` | Зарегистрировано |
| `InProcess` | В работе |
| `PendingInitiator` | Ожидает инициатора |
| `Resolved` | Решено |
| `Closed` | Закрыто |

## Support — техническая поддержка (14 EntitySets)

Модуль управления сервисными запросами технической поддержки: SLA, исполнение работ, инфраструктурные задачи.

### Выполненные работы по заявкам

```bash
# Работы по конкретной заявке
python .claude/skills/rxapi-auth/scripts/query.py IRequestPerformedWorks \
  --filter "PlanStage/Id eq {stageId}" \
  --select "Id,Date,Duration,TimeSheetDuration,Comment" \
  --expand "Employee(\$select=Id,Name),JobKind(\$select=Name)" \
  --orderby "Date desc"
```

### SLA по зонам поддержки

```bash
# SLA для зоны поддержки
python .claude/skills/rxapi-auth/scripts/query.py ISupportAreaServiceLevelAgreements \
  --filter "SupportArea/Id eq {areaId}" \
  --select "Id,Deadline,ConfidenceThreshold" \
  --expand "SupportArea(\$select=Id,Name),Service(\$select=Id,Name),Responsible(\$select=Id,Name),City(\$select=Name)"

# Все SLA (без фильтра — возможна большая выборка)
python .claude/skills/rxapi-auth/scripts/query.py ISupportAreaServiceLevelAgreements \
  --select "Id,Deadline,ConfidenceThreshold" \
  --expand "SupportArea(\$select=Name),Service(\$select=Name)" \
  --top 50
```

### Зоны поддержки и услуги

```bash
# Зоны поддержки
python .claude/skills/rxapi-auth/scripts/query.py ISupportAreas \
  --select "Id,Name" --top 50

# Услуги поддержки
python .claude/skills/rxapi-auth/scripts/query.py ISupportServices \
  --select "Id,Name" --top 50

# Основные услуги
python .claude/skills/rxapi-auth/scripts/query.py ISupportMainServices \
  --select "Id,Name" --top 50
```

### Задачи по нестандартной инфраструктуре

```bash
# Инфраструктурные задачи в работе
python .claude/skills/rxapi-auth/scripts/query.py ISupportCustomProjectInfrastructureTasks \
  --filter "Status eq 'InProcess'" \
  --select "Id,Subject,Status,Deadline" \
  --expand "Author(\$select=Id,Name)" \
  --orderby "Deadline asc" --top 20

# Наблюдатели инфраструктурных задач
python .claude/skills/rxapi-auth/scripts/query.py ISupportCustomProjectInfrastructureTaskObserverss \
  --filter "Task/Id eq {taskId}" \
  --select "Id,RowNumber" \
  --expand "Observer(\$select=Id,Name)"
```

### Задания по решению обращений

```bash
# Мои задания по решению заявок
python .claude/skills/rxapi-auth/scripts/query.py ISupportRequestSolvingAssignments \
  --filter "Performer/Id eq {currentUserId} and Status eq 'InProcess'" \
  --select "Id,Subject,Status,Deadline" \
  --orderby "Deadline asc"

# Задачи решения заявок
python .claude/skills/rxapi-auth/scripts/query.py ISupportRequestSolvingTasks \
  --filter "Status eq 'InProcess'" \
  --select "Id,Subject,Status,Deadline" \
  --expand "Author(\$select=Id,Name)" \
  --orderby "Deadline asc" --top 20
```

### Поля IRequestPerformedWorkDto

| Поле | Тип | Описание |
|------|-----|---------|
| `Date` | DateTimeOffset | Дата выполнения работы |
| `Duration` | Double | Затраченное время (часы) |
| `TimeSheetDuration` | Double | Время для табеля (часы) |
| `Comment` | String | Комментарий |
| `PlanStage` | → IPlanStage | Этап плана |
| `JobKind` | → IJobKind | Вид работ |
| `Employee` | → IEmployee | Исполнитель |

### Поля ISupportAreaServiceLevelAgreementDto

| Поле | Тип | Описание |
|------|-----|---------|
| `Deadline` | Int32 | Срок реакции/решения (в часах) |
| `ConfidenceThreshold` | Double | Порог уверенности SLA (%) |
| `SupportArea` | → ISupportArea | Зона поддержки |
| `Service` | → IService | Услуга |
| `Responsible` | → IEmployee | Ответственный |
| `City` | → ICity | Город |

### Ключевые EntitySets Support

| EntitySet | Что содержит |
|-----------|-------------|
| `IRequestPerformedWorks` | Выполненные работы по заявкам |
| `ISupportAreaServiceLevelAgreements` | SLA по зонам поддержки |
| `ISupportAreas` | Зоны поддержки |
| `ISupportServices` | Услуги поддержки |
| `ISupportMainServices` | Основные услуги |
| `ISupportCustomProjectInfrastructureTasks` | Задачи по нестандартной инфраструктуре |
| `ISupportCustomProjectInfrastructureTaskObserverss` | Наблюдатели инфраструктурных задач |
| `ISupportRequestSolvingTasks` | Задачи решения заявок |
| `ISupportRequestSolvingAssignments` | Задания решения заявок |
| `ISupportRequestSolvingTaskObserverss` | Наблюдатели задач решения |
| `ISupportRequestAcceptanceAssignments` | Задания приёмки заявок |
| `ISupportRequestAgreementWithManagers` | Согласования заявок с руководителями |

## Ключевые EntitySets

| EntitySet | Что содержит |
|-----------|-------------|
| `IServiceRequests` | Сервисные запросы / обращения |
| `IServiceCategories` | Категории сервисов |
| `IServiceItems` | Услуги |
| `IConfigurationItems` | CMDB |
| `IInternalServiceRequests` | Внутренние запросы |
