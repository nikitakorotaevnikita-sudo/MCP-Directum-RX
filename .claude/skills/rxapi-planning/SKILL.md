---
name: rxapi-planning
description: Планирование работ в Directum RX / Aura (модуль Planning) — планы проектов, этапы планов, трудозатраты, ключевые задачи, план-факт. Использовать когда нужно найти план по проекту, этапы плана, трудозатраты сотрудников по плану, ключевые задачи отчётного периода.
---

# Planning — планирование работ

Перед использованием: получить `currentUserId` через skill `rxapi-current-user`.

## Планы

```bash
# Активные планы по проекту
python .claude/skills/rxapi-auth/scripts/query.py IPlans \
  --filter "Project/Id eq {projectId} and Status eq 'Active'" \
  --select "Id,Name,Code,Status,StartDate,EndDate,Estimate,ActualEstimate,Duration,PlanState" \
  --expand "Manager(\$select=Id,Name),Project(\$select=Id,Name)" \
  --orderby "StartDate desc"

# Планы сотрудника (как менеджера плана)
python .claude/skills/rxapi-auth/scripts/query.py IPlans \
  --filter "Manager/Id eq {currentUserId} and Status eq 'Active'" \
  --select "Id,Name,Code,Status,StartDate,EndDate,Estimate,ActualEstimate,PlanState" \
  --orderby "StartDate desc" --top 20

# Планы за период
python .claude/skills/rxapi-auth/scripts/query.py IPlans \
  --filter "StartDate ge 2025-01-01T00:00:00Z and EndDate le 2025-12-31T00:00:00Z" \
  --select "Id,Name,Code,Status,StartDate,EndDate,Estimate,PlanState" \
  --expand "Project(\$select=Id,Name),Manager(\$select=Id,Name)" \
  --orderby "StartDate asc" --top 50
```

## Этапы планов

```bash
# Этапы конкретного плана
python .claude/skills/rxapi-auth/scripts/query.py IPlanStages \
  --filter "Plan/Id eq {planId}" \
  --select "Id,Name,Code,Number,StartDate,EndDate,Estimate,ActualStartDate,ActualEndDate,Status,StandardStageType" \
  --expand "JobKind(\$select=Name),CostsType(\$select=Name)" \
  --orderby "Number asc"

# Исполнители этапа
python .claude/skills/rxapi-auth/scripts/query.py IPlanStageEmployeess \
  --filter "PlanStage/Id eq {stageId}" \
  --select "Id,RowNumber" \
  --expand "Employee(\$select=Id,Name)"
```

## Сотрудники плана

```bash
# Сотрудники по плану
python .claude/skills/rxapi-auth/scripts/query.py IPlanEmployeess \
  --filter "Plan/Id eq {planId}" \
  --select "Id,RowNumber" \
  --expand "Employee(\$select=Id,Name)"
```

## Детализация этапов плана

```bash
python .claude/skills/rxapi-auth/scripts/query.py IPlanStagesDetailss \
  --filter "Plan/Id eq {planId}" \
  --select "Id,RowNumber" \
  --expand "PlanStage(\$select=Id,Name,StartDate,EndDate)"
```

## Ключевые задачи (KeyTasks)

```bash
# Ключевые задачи квартала
python .claude/skills/rxapi-auth/scripts/query.py IKeyTasksDocuments \
  --filter "Responsible/Id eq {currentUserId} and Quarter eq '1'" \
  --select "Id,Name,Year,Quarter,Month,LifeCycleState" \
  --expand "Responsible(\$select=Id,Name)" \
  --orderby "Year desc"

# Ключевые задачи подразделения за год
python .claude/skills/rxapi-auth/scripts/query.py IKeyTasksDocuments \
  --filter "Year ge 2025-01-01T00:00:00Z" \
  --select "Id,Name,Year,Quarter,LifeCycleState" \
  --expand "Responsible(\$select=Id,Name)" \
  --orderby "Year desc" --top 30

# Задания по согласованию ключевых задач
python .claude/skills/rxapi-auth/scripts/query.py IKeyTasksApprovalAssignments \
  --filter "Performer/Id eq {currentUserId} and Status eq 'InProcess'" \
  --select "Id,Subject,Status,Deadline" \
  --orderby "Deadline asc"

# Задачи согласования ключевых задач
python .claude/skills/rxapi-auth/scripts/query.py IKeyTasksApprovalTasks \
  --filter "Status eq 'InProcess'" \
  --select "Id,Subject,Status,Deadline" \
  --expand "Author(\$select=Id,Name)" --top 20
```

## Проекты (ProjectBase и Planning Projects)

```bash
# Базовые проекты
python .claude/skills/rxapi-auth/scripts/query.py IPlanningProjectBases \
  --filter "Status eq 'Active'" \
  --select "Id,Name,StartDate,EndDate,Stage" \
  --expand "Manager(\$select=Id,Name)" \
  --orderby "StartDate desc" --top 30

# Полные проекты (Planning module)
python .claude/skills/rxapi-auth/scripts/query.py IPlanningProjects \
  --filter "Manager/Id eq {currentUserId} and Status eq 'Active'" \
  --select "Id,Name,StartDate,EndDate,Stage" \
  --orderby "StartDate desc"

# Участники проекта
python .claude/skills/rxapi-auth/scripts/query.py IPlanningProjectProjectMemberss \
  --filter "Project/Id eq {projectId}" \
  --select "Id,RowNumber" \
  --expand "Employee(\$select=Id,Name)"
```

## Согласование и закрытие планов

```bash
# Задачи согласования планов
python .claude/skills/rxapi-auth/scripts/query.py IPlanApprovalTasks \
  --filter "Status eq 'InProcess'" \
  --select "Id,Subject,Status,Deadline" \
  --expand "Author(\$select=Id,Name)" --top 20

# Задачи закрытия планов
python .claude/skills/rxapi-auth/scripts/query.py IPlanCloseTasks \
  --filter "Status eq 'InProcess'" \
  --select "Id,Subject,Status,Deadline" \
  --expand "Author(\$select=Id,Name)" --top 20
```

## Ключевые EntitySets

| EntitySet | Что содержит | Модуль |
|-----------|-------------|--------|
| `IPlans` | Планы работ | Planning |
| `IPlanStages` | Этапы планов | Planning |
| `IPlanEmployeess` | Сотрудники плана | Planning |
| `IPlanStageEmployeess` | Исполнители этапа плана | Planning |
| `IPlanStagesDetailss` | Детализация этапов плана | Planning |
| `IKeyTasksDocuments` | Документы ключевых задач | Planning |
| `IKeyTasksApprovalTasks` | Задачи согласования ключевых задач | Planning |
| `IKeyTasksApprovalAssignments` | Задания согласования ключевых задач | Planning |
| `IKeyTasksFormationAssignments` | Задания формирования ключевых задач | Planning |
| `IKeyTasksReworkAssignments` | Задания доработки ключевых задач | Planning |
| `IPlanApprovalTasks` | Задачи согласования планов | Planning |
| `IPlanCloseTasks` | Задачи закрытия планов | Planning |
| `IPlanCloseNewsArticles` | Новостные статьи закрытия планов | Planning |
| `IPlanningProjectBases` | Базовые проекты | Planning |
| `IPlanningProjects` | Полные проекты | Planning |
| `IPlanningProjectProjectMemberss` | Участники проекта | Planning |
| `IPlanningProjectSubcontractorss` | Субподрядчики проекта | Planning |
| `IProjectPlanRXs` | Планы проектов (RX) | ProjectPlanner |
| `IProjectPlanRXTeamMemberss` | Участники плана RX | ProjectPlanner |
| `IProjectPlanObsoletes` | Устаревшие планы | ProjectPlanner |
| `IPerformerNoticePlanStagess` | Уведомления исполнителей по этапам | Planning |

## Поля IPlanDto

| Поле | Тип | Описание |
|------|-----|---------|
| `Name` | String | Наименование плана |
| `Code` | String | Код плана |
| `Status` | String | Статус (`Active`, `Closed`, ...) |
| `PlanState` | String | Состояние (`Draft`, `Approved`, ...) |
| `StartDate` / `EndDate` | DateTimeOffset | Плановые даты |
| `BaseStartDate` / `BaseEndDate` | DateTimeOffset | Базовые даты |
| `ActualStartDate` / `ActualEndDate` | DateTimeOffset | Фактические даты |
| `Estimate` | Double | Плановые трудозатраты (часы) |
| `BaseEstimate` | Double | Базовые трудозатраты |
| `ActualEstimate` | Double | Фактические трудозатраты |
| `Duration` | Int32 | Длительность (дни) |
| `ActualDuration` | Int32 | Фактическая длительность |
| `ApprovedEstimate` | Double | Утверждённые трудозатраты |
| `CooperationPercent` | Double | % кооперации |
| `UnforeseenPercent` | Double | % непредвиденных работ |
| `IsRegular` | String | Признак регулярности |
| `Calculate` | String | Способ расчёта |
| `Comment` | String | Комментарий |
| `Manager` | → IEmployee | Менеджер плана |
| `Project` | → IProjectBase | Проект |
| `ProjectStage` | → IProjectStage | Этап проекта |
| `CostsType` | → ICostsType | Тип затрат |

## Поля IPlanStageDto (основные)

| Поле | Тип | Описание |
|------|-----|---------|
| `Name` | String | Наименование этапа |
| `Number` | Double | Порядковый номер |
| `StartDate` / `EndDate` | DateTimeOffset | Плановые даты |
| `ActualStartDate` / `ActualEndDate` | DateTimeOffset | Фактические даты |
| `Estimate` | Double | Трудозатраты (часы) |
| `Status` | String | Статус |
| `StandardStageType` | String | Тип стандартного этапа |
| `IsTimeSheet` | Boolean | Учёт в табеле |
| `IsRequestOnly` | Boolean | Только по заявке |
| `Plan` | → IPlan | Родительский план |
| `JobKind` | → IJobKind | Вид работ |

## Поля IKeyTasksDocumentDto

| Поле | Тип | Описание |
|------|-----|---------|
| `Name` | String | Наименование документа ключевых задач |
| `Year` | DateTimeOffset | Год (дата) |
| `Quarter` | String | Квартал (`1`, `2`, `3`, `4`) |
| `Month` | String | Месяц |
| `LifeCycleState` | String | Состояние (`Draft`, `Active`, `Obsolete`) |
| `Responsible` | → IRecipient | Ответственный |

## Типичные проблемы

| Ситуация | Действие |
|----------|----------|
| Нужна структура сущности | `python .claude/skills/rxapi-auth/scripts/search_metadata.py IPlanDto` |
| Трудозатраты в часах | Поле `Estimate` — Double, в часах |
| Отчётный период ключевых задач | Используй `Quarter` (`'1'`..`'4'`) и `Year` (DateTimeOffset) |
| Фильтр по проекту | Для `IPlans` используй `Project/Id eq {projectId}` |
