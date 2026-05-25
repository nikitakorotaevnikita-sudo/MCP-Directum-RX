---
name: rxapi-assignments
description: Получение заданий, задач, поручений и согласований в Directum RX / Aura. Использовать когда нужно узнать мои задания в работе, просроченные дедлайны, задачи на согласование, поручения, что я отправил или получил.
---

# Задания и задачи

Перед использованием: получить `currentUserId` через skill `rxapi-current-user`.

## Мои задания в работе

```bash
python .claude/skills/rxapi-auth/scripts/query.py IAssignments \
  --filter "Performer/Id eq {currentUserId} and Status eq 'InProcess'" \
  --select "Id,Subject,Deadline,Status" \
  --expand "Task(\$select=Id,Subject)" \
  --orderby "Deadline asc" --count
```

## Просроченные задания

```bash
python .claude/skills/rxapi-auth/scripts/query.py IAssignments \
  --filter "Performer/Id eq {currentUserId} and Status eq 'InProcess' and Deadline lt 2025-03-18T00:00:00Z" \
  --select "Id,Subject,Deadline" --orderby "Deadline asc"
```

## Задачи которые я создал

```bash
python .claude/skills/rxapi-auth/scripts/query.py ITasks \
  --filter "Author/Id eq {currentUserId} and Status eq 'InProcess'" \
  --select "Id,Subject,Status,Started,Deadline" \
  --orderby "Started desc" --top 20
```

## Задания на согласование (мне)

```bash
python .claude/skills/rxapi-auth/scripts/query.py IApprovalAssignments \
  --filter "Performer/Id eq {currentUserId} and Status eq 'InProcess'" \
  --select "Id,Subject,Deadline" \
  --expand "MainTask(\$select=Id,Subject)"
```

## Поручения — мне выданы

```bash
python .claude/skills/rxapi-auth/scripts/query.py IActionItemExecutionAssignments \
  --filter "Performer/Id eq {currentUserId} and Status eq 'InProcess'" \
  --select "Id,Subject,Deadline" --orderby "Deadline asc"
```

## Поручения — я выдал

```bash
python .claude/skills/rxapi-auth/scripts/query.py IActionItemExecutionTasks \
  --filter "Author/Id eq {currentUserId} and Status eq 'InProcess'" \
  --select "Id,Subject,Deadline,Status" --top 20
```

## Статусы

| Значение | Смысл |
|----------|-------|
| `InProcess` | В работе |
| `Completed` | Выполнено |
| `Aborted` | Прервано |
| `Draft` | Черновик |

## Ключевые EntitySets

| EntitySet | Что содержит |
|-----------|-------------|
| `IAssignments` | Все задания (базовый) |
| `IApprovalAssignments` | Задания согласования |
| `IReviewAssignments` | Задания рассмотрения |
| `IActionItemExecutionAssignments` | Задания исполнения поручений |
| `ITasks` | Все задачи (базовый) |
| `IApprovalTasks` | Задачи согласования |
| `IActionItemExecutionTasks` | Задачи поручений |
| `INotices` | Уведомления |

## Полезные expand

```
Performer         — исполнитель
Author            — автор
MainTask          — родительская задача
AttachmentDetails — вложенные документы
Texts             — комментарии к заданию
```

## Задачи и задания Teams (TeamsCommonAPI)

```bash
# Активные задачи Teams
python .claude/skills/rxapi-auth/scripts/query.py ITeamsTasks \
  --filter "Status eq 'InProcess'" \
  --select "Id,Subject,Status,Started,MaxDeadline,IsDelayed,IsManuallyStarted,EntityId,EntityHyperlink" \
  --expand "Author(\$select=Id,Name)" \
  --orderby "Started desc" --top 20

# Мои задания Teams
python .claude/skills/rxapi-auth/scripts/query.py ITeamsAssignments \
  --filter "Performer/Id eq {currentUserId} and Status eq 'InProcess'" \
  --select "Id,Subject,Deadline,Status" \
  --expand "MainTask(\$select=Id,Subject)" \
  --orderby "Deadline asc"

# Уведомления Teams
python .claude/skills/rxapi-auth/scripts/query.py ITeamsNotices \
  --filter "Performer/Id eq {currentUserId}" \
  --select "Id,Subject,IsImportant,EntityName,EntityId,EntityHyperlink,IsCollectiveNotice" \
  --orderby "Id desc" --top 30
```

### Ключевые EntitySets TeamsCommonAPI

| EntitySet | Что содержит |
|-----------|-------------|
| `ITeamsTasks` | Задачи TeamsCommonAPI |
| `ITeamsAssignments` | Задания TeamsCommonAPI |
| `ITeamsNotices` | Уведомления Teams |
| `ITeamsTaskObserverss` | Наблюдатели задач Teams |
| `ITeamsTaskResponsibless` | Ответственные по задачам Teams |
| `ITeamsTaskNotifiesCollections` | Список уведомляемых в задаче |
| `ITeamsCommonAPITeams` | Команды (Team) |
| `ITeamsCommonAPIComments` | Комментарии Teams |
| `ITeamsCommonAPITeamRecipientLinkss` | Получатели команды |

### Поля ITeamsTaskDto

| Поле | Тип | Описание |
|------|-----|---------|
| `Subject` | String | Тема задачи |
| `Status` | String | Статус (InProcess, Completed, Aborted) |
| `Started` | DateTimeOffset | Дата запуска |
| `MaxDeadline` | DateTimeOffset | Крайний срок |
| `IsDelayed` | Boolean | Задача отложена |
| `IsManuallyStarted` | Boolean | Запущена вручную |
| `IsRootTaskForConsolidated` | Boolean | Корневая для консолидированной задачи |
| `EntityId` | Int64 | ID связанной сущности |
| `RootEntityId` | Int64 | ID корневой сущности |
| `EntityHyperlink` | String | Ссылка на сущность в RX |
| `ClientHyperlink` | String | Ссылка для клиента |
| `SettingsId` | Int64 | ID настроек задачи |
| `SolutionGuid` | String | GUID решения |
| `Author` | → IUser | Автор задачи |
| `Responsibles` | →[ITeamsTaskResponsibles] | Ответственные |
| `NotifiesCollection` | →[ITeamsTaskNotifiesCollection] | Список уведомляемых |
