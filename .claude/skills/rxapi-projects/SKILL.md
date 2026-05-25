---
name: rxapi-projects
description: Проекты, планы и Agile-доски в Directum RX / Aura. Использовать для запросов про проекты, задачи по проекту, планы подразделения, Agile-доски. Для целей и KPI — используй skill rxapi-targets.
---

# Проекты и планы

Перед использованием: получить `currentUserId` через skill `rxapi-current-user`.

## Проекты

```bash
# Активные проекты
python .claude/skills/rxapi-auth/scripts/query.py IProjects \
  --filter "Status eq 'Active'" \
  --select "Id,Name,StartDate,EndDate" \
  --expand "Manager(\$select=Id,Name)" \
  --orderby "StartDate desc" --top 20

# Мои проекты
python .claude/skills/rxapi-auth/scripts/query.py IProjects \
  --filter "Manager/Id eq {currentUserId} and Status eq 'Active'" \
  --select "Id,Name,StartDate,EndDate"
```

## Задачи проекта

```bash
python .claude/skills/rxapi-auth/scripts/query.py IProjectPlanActivities \
  --filter "Project/Id eq {projectId} and Status eq 'InProcess'" \
  --select "Id,Name,Deadline,Status" \
  --expand "Performer(\$select=Id,Name)" \
  --orderby "Deadline asc"
```

## Планы (модуль Planning)

```bash
python .claude/skills/rxapi-auth/scripts/query.py IPlans \
  --filter "Department/Id eq {deptId} and Status eq 'Active'" \
  --select "Id,Name,Status,Period,StartDate,EndDate" --top 20
```

## Цели и KPI

> Используй skill **`rxapi-targets`** — там `ITargetsTargets`, `IKPIMetrics` и карты KPI.
> `ITargetsWorkRules` — правила работы, **не цели**.

## Agile-доски

```bash
python .claude/skills/rxapi-auth/scripts/query.py IAgileBoards \
  --filter "Status eq 'Active'" --select "Id,Name" --top 20

python .claude/skills/rxapi-auth/scripts/query.py IAgileBoardTasks \
  --filter "Board/Id eq {boardId} and Status ne 'Completed'" \
  --select "Id,Name,Priority,Status" \
  --expand "Assignee(\$select=Id,Name)"
```

## Portfolio & Programs — портфели и программы (19 EntitySets)

Модуль управления портфелями проектов и программами (инициативами).

```bash
# Активные портфели
python .claude/skills/rxapi-auth/scripts/query.py IPortfolios \
  --filter "Stage ne 'Closed'" \
  --select "Id,Name,ShortName,StartDate,EndDate,Stage,Priority,ExecutionPercent" \
  --expand "Manager(\$select=Id,Name)" \
  --orderby "StartDate desc" --top 20

# Участники портфеля
python .claude/skills/rxapi-auth/scripts/query.py IPortfolioTeamMemberss \
  --filter "Portfolio/Id eq {portfolioId}" \
  --select "Id,RowNumber" \
  --expand "Employee(\$select=Id,Name)"

# Ворота (Gates) портфеля
python .claude/skills/rxapi-auth/scripts/query.py IPortfolioGatesDirRXs \
  --filter "Project/Id eq {portfolioId}" \
  --select "Id,Name" --top 20

# Программы / инициативы
python .claude/skills/rxapi-auth/scripts/query.py IPortfolioProgramInitiatives \
  --filter "Stage ne 'Closed'" \
  --select "Id,Name,ShortName,StartDate,EndDate,InitiativeStage,PlannedWorkload,PlannedCosts" \
  --expand "Manager(\$select=Id,Name),ImplManager(\$select=Id,Name)" \
  --orderby "StartDate desc" --top 20

# Риски портфеля/программы
python .claude/skills/rxapi-auth/scripts/query.py IPortfolioProgramRisks \
  --filter "Project/Id eq {portfolioId}" \
  --select "Id,Name" --top 30
```

### Поля IPortfolioDto (наследует IProjectCoreDto)

| Поле | Тип | Описание |
|------|-----|---------|
| `Name` / `ShortName` | String | Наименование / краткое |
| `StartDate` / `EndDate` | DateTimeOffset | Даты портфеля |
| `Stage` | String | Стадия (`Active`, `Closed`, ...) |
| `Priority` | String | Приоритет |
| `ExecutionPercent` | Int32 | % исполнения |
| `Description` | String | Описание |
| `Manager` | → IEmployee | Менеджер |

### Ключевые EntitySets Portfolio & Programs

| EntitySet | Что содержит |
|-----------|-------------|
| `IPortfolios` | Портфели проектов |
| `IPortfolioTeamMemberss` | Участники портфеля |
| `IPortfolioGatesDirRXs` | Ворота контроля портфеля |
| `IPortfolioRiskNoticesDirRXs` | Уведомления о рисках портфеля |
| `IPortfolioClassifiers` | Классификаторы портфелей |
| `IPortfolioProgramInitiatives` | Инициативы / программы |
| `IPortfolioProgramInitiativeTeamMemberss` | Участники инициативы |
| `IPortfolioProgramInitiativeGatesDirRXs` | Ворота инициативы |
| `IPortfolioProgramInitiativeImplementationProjectss` | Проекты реализации инициативы |
| `IPortfolioProgramRisks` | Риски |
| `IPortfolioProgramRiskCategories` | Категории рисков |

## ProjectDocuments — проектные документы (39 EntitySets)

Модуль документации по проектам.

```bash
# Проектная документация
python .claude/skills/rxapi-auth/scripts/query.py IProductDocumentations \
  --filter "LifeCycleState ne 'Obsolete'" \
  --select "Id,Name,RegistrationDate,LifeCycleState" \
  --expand "BusinessUnit(\$select=Name),Department(\$select=Name)" \
  --orderby "RegistrationDate desc" --top 20

# Фиктивные документы (технический тип-контейнер)
python .claude/skills/rxapi-auth/scripts/query.py IFakeDocuments \
  --select "Id,Name" --top 20
```

### Ключевые EntitySets ProjectDocuments

| EntitySet | Что содержит |
|-----------|-------------|
| `IProductDocumentations` | Документация по продукту/проекту |
| `IProductDocumentationVersionss` | Версии документации |
| `IProductDocumentationTrackings` | Трекинг документации |
| `IFakeDocuments` | Фиктивные документы (технический тип) |
| `IFakeDocumentVersionss` | Версии фиктивных документов |

> **Примечание:** `IFakeDocuments` — технический тип-контейнер для служебных целей. Не является реальным видом документа.

## Ключевые EntitySets

| EntitySet | Что содержит |
|-----------|-------------|
| `IProjects` | Проекты |
| `IProjectPlanActivities` | Задачи проекта |
| `IPlans` | Планы |
| `IPlanStages` | Этапы планов |
| `IKPICards` | Карточки KPI (см. rxapi-targets) |
| `IAgileBoards` | Agile-доски |
| `IAgileBoardTasks` | Задачи на досках |
| `IPortfolios` | Портфели проектов |
