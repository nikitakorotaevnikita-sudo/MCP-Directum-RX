---
name: rxapi-documents
description: Поиск и получение документов в Directum RX / Aura — договоры, счета, накладные, УПД, входящие и исходящие документы. Использовать когда нужно найти документ по названию, номеру, дате или контрагенту.
---

# Документы

## Найти документ по названию

```bash
python .claude/skills/rxapi-auth/scripts/query.py IOfficialDocuments \
  --filter "contains(Name,'акт') and Status ne 'Obsolete'" \
  --select "Id,Name,RegistrationNumber,RegistrationDate" \
  --orderby "RegistrationDate desc" --top 20
```

## По регистрационному номеру

```bash
python .claude/skills/rxapi-auth/scripts/query.py IOfficialDocuments \
  --filter "RegistrationNumber eq '123/2025'" \
  --select "Id,Name,RegistrationNumber,RegistrationDate"
```

## Входящие документы

```bash
python .claude/skills/rxapi-auth/scripts/query.py IIncomingDocuments \
  --filter "Status eq 'Active'" \
  --select "Id,Name,RegistrationNumber,RegistrationDate,InNumber" \
  --expand "Correspondent(\$select=Id,Name)" \
  --orderby "RegistrationDate desc" --top 20
```

## Исходящие документы

```bash
python .claude/skills/rxapi-auth/scripts/query.py IOutgoingDocuments \
  --filter "Status eq 'Active'" \
  --select "Id,Name,RegistrationNumber,RegistrationDate" \
  --expand "Addressee(\$select=Id,Name)" \
  --orderby "RegistrationDate desc" --top 20
```

## Договоры

```bash
python .claude/skills/rxapi-auth/scripts/query.py IContracts \
  --filter "contains(Name,'поставка') and Status ne 'Obsolete'" \
  --select "Id,Name,RegistrationNumber,TotalAmount,ValidFrom,ValidTill" \
  --expand "Counterparty(\$select=Id,Name)" --top 20
```

## Первичные документы (Aura)

```bash
# Входящие счета
python .claude/skills/rxapi-auth/scripts/query.py IIncomingInvoices \
  --select "Id,Name,RegistrationNumber,TotalAmount" \
  --expand "Counterparty(\$select=Id,Name)" --top 20

# УПД
python .claude/skills/rxapi-auth/scripts/query.py IUniversalTransferDocuments \
  --select "Id,Name,RegistrationNumber,TotalAmount,DocumentDate" --top 20

# Накладные
python .claude/skills/rxapi-auth/scripts/query.py IWaybills \
  --select "Id,Name,RegistrationNumber,DocumentDate,TotalAmount" --top 20

# Акты выполненных работ
python .claude/skills/rxapi-auth/scripts/query.py IContractStatements \
  --select "Id,Name,RegistrationNumber,TotalAmount,DocumentDate" --top 20
```

## Документы за период

```bash
python .claude/skills/rxapi-auth/scripts/query.py IOfficialDocuments \
  --filter "RegistrationDate ge 2025-01-01T00:00:00Z and RegistrationDate lt 2025-04-01T00:00:00Z" \
  --select "Id,Name,RegistrationNumber,RegistrationDate" \
  --orderby "RegistrationDate desc"
```

## Статусы документов

`Active` — действует, `Obsolete` — аннулирован, `Draft` — черновик.

## Ключевые EntitySets

| EntitySet | Что содержит |
|-----------|-------------|
| `IOfficialDocuments` | Все официальные документы (базовый) |
| `IIncomingDocuments` | Входящие |
| `IOutgoingDocuments` | Исходящие |
| `IInternalDocuments` | Внутренние |
| `IContracts` | Договоры |
| `ISupAgreements` | Доп. соглашения |
| `IIncomingInvoices` | Входящие счета |
| `IOutgoingInvoices` | Исходящие счета |
| `IUniversalTransferDocuments` | УПД |
| `IWaybills` | Накладные |
| `IIncomingTaxInvoices` | Входящие счета-фактуры |
| `IOutgoingTaxInvoices` | Исходящие счета-фактуры |
| `IContractStatements` | Акты выполненных работ |

## Внутренние политики (InternalPolicies)

```bash
# Действующие внутренние политики
python .claude/skills/rxapi-auth/scripts/query.py IInternalPoliciesInternalPolicies \
  --filter "LifeCycleState eq 'Active'" \
  --select "Id,Name,RegistrationNumber,RegistrationDate,ValidFrom,ValidTill,EditionNumber,IsInitialEdition,LifeCycleState" \
  --expand "Curator(\$select=Id,Name),Responsible(\$select=Id,Name),BusinessUnit(\$select=Name)" \
  --orderby "RegistrationDate desc" --top 20

# Категории внутренних политик
python .claude/skills/rxapi-auth/scripts/query.py IInternalPoliciesInternalPolicyCategories \
  --select "Id,Name,Status" --top 50

# Задачи изменения политики
python .claude/skills/rxapi-auth/scripts/query.py IInternalPoliciesChangeRequestTasks \
  --filter "Status eq 'InProcess'" \
  --select "Id,Subject,Status,Started" \
  --expand "Author(\$select=Id,Name)" \
  --orderby "Started desc" --top 20
```

### Ключевые EntitySets InternalPolicies

| EntitySet | Что содержит |
|-----------|-------------|
| `IInternalPoliciesInternalPolicies` | Внутренние политики (ЛНА) |
| `IInternalPoliciesInternalPolicyCategories` | Категории политик |
| `IInternalPoliciesChangeRequestTasks` | Задачи изменения политики |

### Поля IInternalPolicyBaseDto

| Поле | Тип | Описание |
|------|-----|---------|
| `ValidFrom` | DateTimeOffset | Дата начала действия |
| `ValidTill` | DateTimeOffset | Дата окончания действия |
| `IsInitialEdition` | Boolean | Первоначальная редакция |
| `EditionNumber` | Int32 | Номер редакции |
| `SerialNumberOfEdition` | Int32 | Порядковый номер редакции |
| `ExpirationDays` | Int32 | Дней до истечения срока |
| `Curator` | → IEmployee | Куратор политики |
| `Responsible` | → IEmployee | Ответственный |
| `EnactingOrder` | → IOrder | Вводящий приказ |
| `CanceledBy` | → IOrder | Отменяющий приказ |
| `PrevEdition` | → IInternalPolicyBase | Предыдущая редакция |
| `Participants` | →[IInternalPolicyBaseParticipants] | Участники ознакомления |

## ЛНД — списки нормативных документов (LRDManagement)

```bash
# Действующие списки ЛНД
python .claude/skills/rxapi-auth/scripts/query.py ILRDManagementLocalRegulationDocumentLists \
  --filter "Status eq 'Active'" \
  --select "Id,Name,Subject,JobNames,DepatmentNames,Status" \
  --expand "BusinessUnit(\$select=Id,Name)" \
  --orderby "Name asc" --top 50

# Группы пользователей ЛНД
python .claude/skills/rxapi-auth/scripts/query.py ILRDManagementUserGroups \
  --select "Id,Name,Status" --top 50

# Документы в конкретном списке ЛНД
python .claude/skills/rxapi-auth/scripts/query.py ILRDManagementLocalRegulationDocumentLists \
  --filter "Id eq {listId}" \
  --expand "LRDDocuments(\$select=Id),UserGroups(\$select=Id),Links(\$select=Id)"
```

### Ключевые EntitySets LRDManagement

| EntitySet | Что содержит |
|-----------|-------------|
| `ILRDManagementLocalRegulationDocumentLists` | Списки нормативных документов |
| `ILRDManagementUserGroups` | Группы пользователей ЛНД |

### Поля ILocalRegulationDocumentListDto

| Поле | Тип | Описание |
|------|-----|---------|
| `Name` | String | Название списка |
| `Subject` | String | Предмет регулирования |
| `JobNames` | String | Должности (текст) |
| `DepatmentNames` | String | Подразделения (текст) |
| `Status` | String | Статус (Active/Closed) |
| `BusinessUnit` | → IBusinessUnit | Организация |
| `Links` | →[ILocalRegulationDocumentListLinks] | Ссылки на документы |
| `LRDDocuments` | →[ILocalRegulationDocumentListLRDDocuments] | Документы списка |
| `UserGroups` | →[ILocalRegulationDocumentListUserGroups] | Группы пользователей |

## Доверенности (PowerOfAttorneyCore)

```bash
# Классификаторы доверенностей
python .claude/skills/rxapi-auth/scripts/query.py IPowerOfAttorneyCorePowerOfAttorneyClassifiers \
  --filter "Status eq 'Active'" \
  --select "Id,Name,Code,Mnemonic,Description,Started,Expiring,PwrVisibility,PwrRead,PwrIssuer,Status" \
  --expand "Group(\$select=Id,Name)" \
  --orderby "Name asc" --top 50

# Заявки на доверенности
python .claude/skills/rxapi-auth/scripts/query.py IPowerOfAttorneyServiceApps \
  --filter "Status eq 'Active'" \
  --select "Id,Name,Status" \
  --top 20
```

### Ключевые EntitySets PowerOfAttorneyCore

| EntitySet | Что содержит |
|-----------|-------------|
| `IPowerOfAttorneyCorePowerOfAttorneyClassifiers` | Классификаторы доверенностей |
| `IPowerOfAttorneyServiceApps` | Заявки на оформление доверенностей |

### Поля IPowerOfAttorneyClassifierDto

| Поле | Тип | Описание |
|------|-----|---------|
| `Name` | String | Наименование классификатора |
| `Code` | String | Код |
| `Mnemonic` | String | Мнемоника |
| `Description` | String | Описание |
| `Started` | DateTimeOffset | Дата начала действия |
| `Revoked` | DateTimeOffset | Дата отзыва |
| `NsiId` | String | Идентификатор в НСИ |
| `Expiring` | Int32 | Срок действия (дней) |
| `PwrVisibility` | String | Видимость полномочий |
| `PwrRead` | String | Полномочия на чтение |
| `PwrIssuer` | String | Полномочия выдающего |
| `LegalRelations` | String | Правоотношения |
| `LawDetails` | String | Реквизиты закона |
| `Status` | String | Статус (Active/Closed) |
| `Group` | → IPowerOfAttorneyClassifierGroup | Группа классификаторов |
