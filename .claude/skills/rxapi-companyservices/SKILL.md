---
name: rxapi-companyservices
description: Сервисные процессы CompanyServices в Directum RX / Aura — резервирование ресурсов и переговорных комнат, технологические документы, ДМО (документы материального обеспечения), константы системы Aura.
---

# CompanyServices — сервисные процессы и ресурсы

> Модуль покрывает резервирование объектов (переговорные, оборудование), работу с технологическими документами, согласование ДМО и системные константы Aura.

## Задачи резервирования объектов

```bash
# Активные задачи резервирования
python .claude/skills/rxapi-auth/scripts/query.py IReserveObjectsTasks \
  --filter "Status eq 'InProcess'" \
  --select "Id,Subject,Status,Started,MaxDeadline" \
  --expand "Author(\$select=Id,Name),ReserveEmployee(\$select=Id,Name)" \
  --orderby "Started desc" --top 20

# Задачи резервирования с объектами (временные слоты)
python .claude/skills/rxapi-auth/scripts/query.py IReserveObjectsTaskObjectss \
  --filter "ReserveObjectsTask/Id eq {taskId}" \
  --select "Id,Begin,End,Description,UsingFor"
```

## Задачи согласования резервирования

```bash
# Ожидают согласования
python .claude/skills/rxapi-auth/scripts/query.py IReserveObjectApprovalTasks \
  --filter "Status eq 'InProcess'" \
  --select "Id,Subject,Begin,End,RequestState,IsBusinessTrip,NewEndDate" \
  --expand "ReserveEmployee(\$select=Id,Name),Author(\$select=Id,Name)" \
  --orderby "Begin asc" --top 20
```

## Задачи продления резервирования

```bash
python .claude/skills/rxapi-auth/scripts/query.py IProlongReserveTasks \
  --filter "Status eq 'InProcess'" \
  --select "Id,Subject,ProlongTo,IsBusinessTrip,Status" \
  --expand "Author(\$select=Id,Name)" \
  --top 20
```

## Задания по резервированию (исполнители)

```bash
# Задания на получение объекта
python .claude/skills/rxapi-auth/scripts/query.py IReserveObjectGettingAssignments \
  --filter "Performer/Id eq {currentUserId} and Status eq 'InProcess'" \
  --select "Id,Subject,Deadline,HintText,InformationHintText" \
  --orderby "Deadline asc"

# Задания на выдачу объекта
python .claude/skills/rxapi-auth/scripts/query.py IReserveObjectGiveOutAssignments \
  --filter "Performer/Id eq {currentUserId} and Status eq 'InProcess'" \
  --select "Id,Subject,Deadline" \
  --orderby "Deadline asc"

# Задания на возврат объекта
python .claude/skills/rxapi-auth/scripts/query.py IReserveObjectTakeAssignments \
  --filter "Performer/Id eq {currentUserId} and Status eq 'InProcess'" \
  --select "Id,Subject,Deadline" \
  --orderby "Deadline asc"
```

## Технологические документы

```bash
# Действующие технологические документы
python .claude/skills/rxapi-auth/scripts/query.py ICompanyServicesTechnologyDocuments \
  --filter "LifeCycleState eq 'Active'" \
  --select "Id,Name,RegistrationNumber,RegistrationDate,Subject,IsSigned,LifeCycleState" \
  --expand "BusinessUnit(\$select=Id,Name),DocumentKind(\$select=Name)" \
  --orderby "RegistrationDate desc" --top 20

# Подписанные версии технологического документа
python .claude/skills/rxapi-auth/scripts/query.py ICompanyServicesTechnologyDocumentSignedVersionss \
  --filter "ElectronicDocument/Id eq {docId}" \
  --select "Id,Number,Created,Modified"
```

## Согласование ДМО (документы материального обеспечения)

```bash
# Активные задачи согласования ДМО
python .claude/skills/rxapi-auth/scripts/query.py IDMOApprovalTasks \
  --filter "Status eq 'InProcess'" \
  --select "Id,Subject,Status,Started,MaxDeadline" \
  --expand "Author(\$select=Id,Name)" \
  --orderby "Started desc" --top 20

# Задания согласования ДМО на мне
python .claude/skills/rxapi-auth/scripts/query.py IDMOApprovalAssignments \
  --filter "Performer/Id eq {currentUserId} and Status eq 'InProcess'" \
  --select "Id,Subject,Deadline" \
  --orderby "Deadline asc"
```

## Константы системы Aura

```bash
# Все константы
python .claude/skills/rxapi-auth/scripts/query.py IAuraConstants \
  --select "Id,Name,ConstantValue,Note,Status" \
  --filter "Status eq 'Active'" --top 100

# Поиск константы по имени
python .claude/skills/rxapi-auth/scripts/query.py IAuraConstants \
  --filter "contains(Name,'email')" \
  --select "Id,Name,ConstantValue,Note"
```

## Ключевые EntitySets

| EntitySet | Что содержит |
|-----------|-------------|
| `IReserveObjectsTasks` | Задачи резервирования ресурсов/комнат |
| `IReserveObjectsTaskObjectss` | Временные слоты резервирования (строки задачи) |
| `IReserveObjectsTaskObserverss` | Наблюдатели задач резервирования |
| `IReserveObjectApprovalTasks` | Задачи согласования резервирования |
| `IReserveObjectApprovalAssignments` | Задания согласования резервирования |
| `IReserveObjectApprovalTaskObserverss` | Наблюдатели задач согласования |
| `IProlongReserveTasks` | Задачи продления резервирования |
| `IProlongReserveApprovalAssignments` | Задания согласования продления |
| `IProlongReserveTaskConflictReserveTaskss` | Конфликты при продлении |
| `IReserveObjectGettingAssignments` | Задания: получить объект |
| `IReserveObjectGiveOutAssignments` | Задания: выдать объект |
| `IReserveObjectTakeAssignments` | Задания: принять возврат объекта |
| `IReserveObjectsNotices` | Уведомления по резервированию |
| `ICompanyServicesTechnologyDocuments` | Технологические документы |
| `ICompanyServicesTechnologyDocumentSignedVersionss` | Подписанные версии тех. документов |
| `ICompanyServicesTechnologyDocumentVersionss` | Все версии тех. документов |
| `ICompanyServicesTechnologyDocumentTrackings` | Трекинг тех. документов |
| `ICompanyServicesSimpleAcceptanceAssignments` | Задания простого принятия |
| `ICompanyServicesChangePersonalInfoTasks` | Задачи изменения персональных данных |
| `IDMOApprovalTasks` | Задачи согласования ДМО |
| `IDMOApprovalAssignments` | Задания согласования ДМО |
| `IDMOReworkAssignments` | Задания доработки ДМО |
| `IAuraConstants` | Константы системы Aura |

## Поля IReserveObjectsTaskDto

| Поле | Тип | Описание |
|------|-----|---------|
| `Subject` | String | Тема задачи |
| `Status` | String | Статус (InProcess, Completed, Aborted) |
| `Started` | DateTimeOffset | Дата запуска |
| `MaxDeadline` | DateTimeOffset | Крайний срок |
| `IsBusinessTrip` | Boolean | Связано с командировкой |
| `Author` | → IUser | Автор задачи |
| `ReserveEmployee` | → IEmployee | Сотрудник-резервирующий |
| `Objects` | →[IReserveObjectsTaskObjects] | Объекты резервирования (слоты) |

## Поля IReserveObjectApprovalTaskDto

| Поле | Тип | Описание |
|------|-----|---------|
| `Begin` | DateTimeOffset | Начало периода резервирования |
| `End` | DateTimeOffset | Конец периода резервирования |
| `IsBusinessTrip` | Boolean | Командировка |
| `NewEndDate` | DateTimeOffset | Новая дата окончания (при изменении) |
| `ReturnIteration` | Int32 | Итерация возврата на доработку |
| `RequestState` | String | Состояние заявки |
| `ReserveEmployee` | → IEmployee | Резервирующий сотрудник |

## Поля IAuraConstantDto

| Поле | Тип | Описание |
|------|-----|---------|
| `Name` | String | Имя константы |
| `ConstantValue` | String | Значение константы |
| `Note` | String | Примечание |
| `Status` | String | Статус (Active/Closed) |
| `Id` | Int64 | Идентификатор |

## Поля ITechnologyDocumentDto

| Поле | Тип | Описание |
|------|-----|---------|
| `Name` | String | Наименование документа |
| `Subject` | String | Содержание |
| `RegistrationNumber` | String | Регистрационный номер |
| `RegistrationDate` | DateTimeOffset | Дата регистрации |
| `LifeCycleState` | String | Состояние (Active, Obsolete, Draft) |
| `IsSigned` | Boolean | Подписан ЭП |
| `BusinessUnit` | → IBusinessUnit | Организация |
| `DocumentKind` | → IDocumentKind | Вид документа |
| `SignedVersions` | →[ITechnologyDocumentSignedVersions] | Подписанные версии |
