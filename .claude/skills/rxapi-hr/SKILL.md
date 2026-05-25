---
name: rxapi-hr
description: HR-данные в Directum RX / Aura — отпуска, командировки, авансовые отчёты, кадровые документы. Использовать для запросов про отпуск сотрудника, командировки, кадровые приказы, авансовые отчёты.
---

# HR — отпуска, командировки, кадровые документы

Перед использованием: получить `currentUserId` через skill `rxapi-current-user`.

## Отпуска

**EntitySet:** `IHRVacations` (не `IVacations` — тот пустой).

**Поля дат:** `PlanBegin` / `PlanEnd` (плановые), `FactBegin` / `FactEnd` (фактические).

> ⚠️ `IHRVacations` **требует фильтр** — без него возвращает ошибку "превышено максимальное количество сущностей".

```bash
# Мои отпуска
python .claude/skills/rxapi-auth/scripts/query.py IHRVacations \
  --filter "Employee/Id eq {currentUserId}" \
  --select "Id,Name,Status,PlanBegin,PlanEnd,VacationKind" \
  --orderby "PlanBegin desc" --top 10

# Отпуска конкретного сотрудника
python .claude/skills/rxapi-auth/scripts/query.py IHRVacations \
  --filter "Employee/Id eq {employeeId}" \
  --select "Id,PlanBegin,PlanEnd,FactBegin,FactEnd,VacationKind,Status" \
  --orderby "PlanBegin desc"

# Отпуска в период (по списку сотрудников)
# Фильтр Employee/MainTeam/Id НЕ работает — нужно сначала получить Id сотрудников,
# потом фильтровать по Employee/Id через or (батчи до ~10 Id за раз)
python .claude/skills/rxapi-auth/scripts/query.py IHRVacations \
  --filter "(Employee/Id eq 62 or Employee/Id eq 1242 or Employee/Id eq 1450) and PlanBegin le 2025-06-12T00:00:00Z and PlanEnd ge 2025-04-06T00:00:00Z" \
  --select "Id,PlanBegin,PlanEnd,VacationKind,Status" \
  --expand "Employee(\$select=Id,Name)" \
  --orderby "PlanBegin asc"
```

### Поля IHRVacations

| Поле | Тип | Описание |
|------|-----|----------|
| `PlanBegin` | DateTimeOffset | Плановая дата начала |
| `PlanEnd` | DateTimeOffset | Плановая дата окончания |
| `FactBegin` | DateTimeOffset | Фактическая дата начала |
| `FactEnd` | DateTimeOffset | Фактическая дата окончания |
| `PlanDuration` | String | Плановая длительность (строка) |
| `FactDuration` | String | Фактическая длительность |
| `PlanAmountDays` | Int32 | Плановых дней |
| `FactAmountDays` | Int32 | Фактических дней |
| `VacationKind` | String | Тип: `Vacation`, ... |
| `Status` | String | `Active`, `Closed`, ... |
| `Compensation` | Boolean | С компенсацией |
| `WithoutSalary` | Boolean | Без сохранения з/п |
| `Year` | Int32 | Год отпуска |
| `Employee` (nav) | — | Сотрудник |

### Алгоритм: отпуска по команде / подразделению

Фильтр `Employee/MainTeam/Id` **не поддерживается** сервером. Правильный порядок:

1. Получить Id сотрудников команды:
```bash
python .claude/skills/rxapi-auth/scripts/query.py IEmployees \
  --filter "MainTeam/Id eq {teamId} and Status eq 'Active'" \
  --select "Id,Name"
```

2. Разбить на батчи по 8–10 сотрудников и для каждого батча:
```bash
python .claude/skills/rxapi-auth/scripts/query.py IHRVacations \
  --filter "(Employee/Id eq ... or ...) and PlanBegin le {dateTo} and PlanEnd ge {dateFrom}" \
  --select "Id,PlanBegin,PlanEnd,VacationKind,Status" \
  --expand "Employee(\$select=Id,Name)" \
  --orderby "PlanBegin asc"
```

> Лимит OData: не более ~10 условий `or` в одном фильтре (ограничение 100 узлов).

## Отсутствия (больничные и прочие)

**EntitySet:** `ICustomCompanyAbsences`
**Поля дат:** `Begin` / `End`

```bash
python .claude/skills/rxapi-auth/scripts/query.py ICustomCompanyAbsences \
  --filter "Employee/Id eq {employeeId} and Begin le 2025-06-12T00:00:00Z and End ge 2025-04-06T00:00:00Z" \
  --select "Id,Begin,End,Status" \
  --expand "Employee(\$select=Id,Name),AbsenceReason(\$select=Name)" \
  --orderby "Begin asc"
```

## Командировки

```bash
python .claude/skills/rxapi-auth/scripts/query.py IBusinessTrips \
  --filter "Employee/Id eq {currentUserId}" \
  --select "Id,Name,Status,DepartureDate,ArrivalDate,Destination" \
  --orderby "DepartureDate desc" --top 10
```

## Авансовые отчёты

```bash
python .claude/skills/rxapi-auth/scripts/query.py IExpenseReports \
  --filter "ResponsibleEmployee/Id eq {currentUserId}" \
  --select "Id,Name,Status,TotalAmount,RegistrationDate" \
  --orderby "RegistrationDate desc" --top 10
```

## Кадровые приказы

```bash
# Приказы о приёме
python .claude/skills/rxapi-auth/scripts/query.py IEmployeeHiringOrders \
  --filter "Status eq 'Active'" \
  --select "Id,Name,RegistrationDate" \
  --expand "Employee(\$select=Id,Name)" --top 20

# Приказы об увольнении
python .claude/skills/rxapi-auth/scripts/query.py IDismissalOrders \
  --filter "Status eq 'Active'" \
  --select "Id,Name,RegistrationDate" \
  --expand "Employee(\$select=Id,Name)" --top 20
```

## Ключевые EntitySets

| EntitySet | Что содержит | Обязателен фильтр |
|-----------|-------------|-------------------|
| `IHRVacations` | Отпуска (плановые и фактические даты) | ✅ да |
| `ICustomCompanyAbsences` | Больничные и прочие отсутствия | ✅ да |
| `IBusinessTrips` | Командировки | ✅ да |
| `IExpenseReports` | Авансовые отчёты | ✅ да |
| `IPersonalInformations` | Персональные данные сотрудников | ✅ да |
| `IEmployeeHiringOrders` | Приказы о приёме | — |
| `IDismissalOrders` | Приказы об увольнении | — |

## HRDocFlow — кадровый документооборот (44 EntitySets)

Модуль управления кадровыми документами с электронным подписанием.

### Расчётные листки

```bash
# Расчётные листки сотрудника
python .claude/skills/rxapi-auth/scripts/query.py IHRDocFlowSalarySlips \
  --filter "Person/Id eq {personId}" \
  --select "Id,Name,Period,LifeCycleState" \
  --expand "Person(\$select=Id,Name)" \
  --orderby "Period desc" --top 12

# Сотрудники в расчётном листке
python .claude/skills/rxapi-auth/scripts/query.py IHRDocFlowSalarySlipEmployeesInSlips \
  --filter "ElectronicDocument/Id eq {slipId}" \
  --select "Id,RowNumber" \
  --expand "Employee(\$select=Id,Name)"
```

### Документы, связанные с трудовой деятельностью

```bash
# Документы (база) по сотруднику
python .claude/skills/rxapi-auth/scripts/query.py IHRDocFlowDocRelatedToWorkBases \
  --filter "LifeCycleState ne 'Obsolete'" \
  --select "Id,Name,RegistrationDate,LifeCycleState" \
  --expand "Department(\$select=Name),BusinessUnit(\$select=Name)" \
  --orderby "RegistrationDate desc" --top 20

# Конкретные документы (расширенный тип)
python .claude/skills/rxapi-auth/scripts/query.py IHRDocFlowDocRelatedToWorks \
  --filter "LifeCycleState ne 'Obsolete'" \
  --select "Id,Name,RegistrationDate,LifeCycleState" \
  --orderby "RegistrationDate desc" --top 20
```

### Согласование и подписание кадровых документов

```bash
# Задачи HR-согласования
python .claude/skills/rxapi-auth/scripts/query.py IHRDocFlowHRApprovalTasks \
  --filter "Status eq 'InProcess'" \
  --select "Id,Subject,Status,Deadline" \
  --expand "Author(\$select=Id,Name)" --top 20

# Мои задания HR-согласования
python .claude/skills/rxapi-auth/scripts/query.py IHRDocFlowAgreementAssignments \
  --filter "Performer/Id eq {currentUserId} and Status eq 'InProcess'" \
  --select "Id,Subject,Status,Deadline" \
  --orderby "Deadline asc"

# Задания электронного подписания
python .claude/skills/rxapi-auth/scripts/query.py IHRDocFlowPSignAssignments \
  --filter "Performer/Id eq {currentUserId} and Status eq 'InProcess'" \
  --select "Id,Subject,Status,Deadline" \
  --orderby "Deadline asc"
```

### Ключевые EntitySets HRDocFlow

| EntitySet | Что содержит |
|-----------|-------------|
| `IHRDocFlowSalarySlips` | Расчётные листки |
| `IHRDocFlowSalarySlipEmployeesInSlips` | Сотрудники в расчётном листке |
| `IHRDocFlowDocRelatedToWorkBases` | Документы, связанные с работой (база) |
| `IHRDocFlowDocRelatedToWorks` | Документы, связанные с работой |
| `IHRDocFlowConsentDocuments` | Документы согласия |
| `IHRDocFlowStatementDocuments` | Заявления |
| `IHRDocFlowStatementTasks` | Задачи по заявлениям |
| `IHRDocFlowHRApprovalTasks` | Задачи HR-согласования |
| `IHRDocFlowHRProcessTasks` | Задачи HR-процессов |
| `IHRDocFlowAgreementTasks` | Задачи согласования |
| `IHRDocFlowAgreementAssignments` | Задания согласования |
| `IHRDocFlowEInteractionTasks` | Задачи электронного взаимодействия |
| `IHRDocFlowPSignAssignments` | Задания электронного подписания |
| `IHRDocFlowESignAssignments` | Задания ЭЦП |
| `IHRDocFlowPAcquaintanceAssignments` | Задания ознакомления |

### Поля ISalarySlipDto

| Поле | Тип | Описание |
|------|-----|---------|
| `Period` | DateTimeOffset | Расчётный период (месяц/год) |
| `LifeCycleState` | String | Состояние (`Draft`, `Active`, `Obsolete`) |
| `Person` | → IPerson | Физическое лицо |
| `EmployeesInSlip` | → [ISalarySlipEmployeesInSlip] | Сотрудники в листке |

## TimeTracker — учёт рабочего времени (3 EntitySets)

Модуль подтверждения табелей учёта рабочего времени.

```bash
# Задачи подтверждения табеля
python .claude/skills/rxapi-auth/scripts/query.py ITimeTrackerApproveTimeTasks \
  --filter "Status eq 'InProcess'" \
  --select "Id,Subject,Status,Deadline,PeriodStartDate,PeriodEndDate" \
  --expand "Author(\$select=Id,Name)" \
  --orderby "Deadline asc" --top 20

# Мои задания подтверждения табеля
python .claude/skills/rxapi-auth/scripts/query.py ITimeTrackerApproveTimeAssignments \
  --filter "Performer/Id eq {currentUserId} and Status eq 'InProcess'" \
  --select "Id,Subject,Status,Deadline" \
  --orderby "Deadline asc"
```

### Ключевые EntitySets TimeTracker

| EntitySet | Что содержит |
|-----------|-------------|
| `ITimeTrackerApproveTimeTasks` | Задачи подтверждения табеля |
| `ITimeTrackerApproveTimeAssignments` | Задания подтверждения табеля |
| `ITimeTrackerApproveTimeTaskObserverss` | Наблюдатели задач подтверждения |

### Поля IApproveTimeTaskDto

| Поле | Тип | Описание |
|------|-----|---------|
| `PeriodStartDate` | DateTimeOffset | Начало периода табеля |
| `PeriodEndDate` | DateTimeOffset | Конец периода табеля |
| `LeadObjectManager` | → IUser | Руководитель объекта |

## Типичные ошибки

| Ошибка | Причина | Решение |
|--------|---------|---------|
| "Превышено максимальное количество сущностей" | Запрос без фильтра | Добавить `--filter` |
| `StartDate` not found | Неверное имя поля | Использовать `PlanBegin`/`PlanEnd` |
| `Employee/MainTeam/Id` not supported | Глубокий путь не работает в filter | Сначала получить Id сотрудников, затем фильтровать по `Employee/Id` |
| Пустой результат у `IVacations` | Не тот EntitySet | Использовать `IHRVacations` |
