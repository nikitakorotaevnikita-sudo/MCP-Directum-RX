---
name: rxapi-finance
description: Финансовые и учётные документы в Directum RX / Aura (модуль FinancialArchive) — входящие и исходящие платёжные поручения, общехозяйственные документы, акты инвентаризации, документы по основным средствам. Использовать когда нужно найти платёжное поручение, акт инвентаризации, документ по ОС или финансовый учётный документ.
---

# FinancialArchive — финансовые учётные документы

Перед использованием: получить `currentUserId` через skill `rxapi-current-user`.

> **Важно:** модуль FinancialArchive — часть расширения Aura. Все документы наследуют поля из `IAccountingDocumentBaseDto` (сумма, валюта, контрагент, дата, номер, НДС и т.д.).

## Входящие платёжные поручения

```bash
# Входящие платёжные поручения по контрагенту
python .claude/skills/rxapi-auth/scripts/query.py IIncomingPaymentOrders \
  --filter "Counterparty/Id eq {counterpartyId}" \
  --select "Id,Name,Number,Date,TotalAmount,VatAmount,NetAmount,LifeCycleState" \
  --expand "Currency(\$select=Name),ResponsibleEmployee(\$select=Id,Name)" \
  --orderby "Date desc"

# Входящие платёжные поручения за период
python .claude/skills/rxapi-auth/scripts/query.py IIncomingPaymentOrders \
  --filter "Date ge 2025-01-01T00:00:00Z and Date le 2025-03-31T00:00:00Z" \
  --select "Id,Name,Number,Date,TotalAmount,LifeCycleState" \
  --expand "Counterparty(\$select=Id,Name)" \
  --orderby "Date desc" --top 50
```

## Исходящие платёжные поручения

```bash
# Исходящие платёжные поручения по контрагенту
python .claude/skills/rxapi-auth/scripts/query.py IFinancialArchiveOutgoingPaymentOrders \
  --filter "Counterparty/Id eq {counterpartyId}" \
  --select "Id,Name,Number,Date,TotalAmount,VatAmount,LifeCycleState" \
  --expand "Currency(\$select=Name)" \
  --orderby "Date desc"
```

## Общехозяйственные документы (General Accounting)

```bash
# Документы за период
python .claude/skills/rxapi-auth/scripts/query.py IGeneralAccountingDocuments \
  --filter "Date ge 2025-01-01T00:00:00Z and PeriodFrom ge 2025-01-01T00:00:00Z" \
  --select "Id,Name,Number,Date,TotalAmount,PeriodFrom,PeriodTo,LifeCycleState" \
  --expand "Counterparty(\$select=Id,Name),ResponsibleEmployee(\$select=Id,Name)" \
  --orderby "Date desc" --top 50

# Члены комиссии по документу
python .claude/skills/rxapi-auth/scripts/query.py IGeneralAccountingDocumentCommissionMemberss \
  --filter "ElectronicDocument/Id eq {docId}" \
  --select "Id,RowNumber" \
  --expand "Employee(\$select=Id,Name)"
```

## Внутренние учётные документы (база)

```bash
python .claude/skills/rxapi-auth/scripts/query.py IInternalAccountingDocumentBases \
  --filter "ResponsibleEmployee/Id eq {currentUserId}" \
  --select "Id,Name,Number,Date,TotalAmount,PeriodFrom,PeriodTo,LifeCycleState" \
  --expand "Counterparty(\$select=Id,Name)" \
  --orderby "Date desc" --top 20
```

## Документы по основным средствам

```bash
# Документы по ОС за период
python .claude/skills/rxapi-auth/scripts/query.py IFixedAssetDocuments \
  --filter "Date ge 2025-01-01T00:00:00Z" \
  --select "Id,Name,Number,Date,TotalAmount,PeriodFrom,PeriodTo,InventoryNumber,LifeCycleState" \
  --expand "Counterparty(\$select=Id,Name),ResponsibleEmployee(\$select=Id,Name)" \
  --orderby "Date desc" --top 20

# Члены комиссии
python .claude/skills/rxapi-auth/scripts/query.py IFixedAssetDocumentCommissionMemberss \
  --filter "ElectronicDocument/Id eq {docId}" \
  --select "Id,RowNumber" \
  --expand "Employee(\$select=Id,Name)"
```

## Акты инвентаризации

```bash
python .claude/skills/rxapi-auth/scripts/query.py IFinancialArchiveInventoryDocuments \
  --filter "Date ge 2025-01-01T00:00:00Z" \
  --select "Id,Name,Number,Date,TotalAmount,PeriodFrom,PeriodTo,LifeCycleState" \
  --expand "ResponsibleEmployee(\$select=Id,Name)" \
  --orderby "Date desc" --top 20
```

## Акты приёма-передачи основных средств

```bash
python .claude/skills/rxapi-auth/scripts/query.py IFinancialArchiveAssetTransferDocuments \
  --filter "Date ge 2025-01-01T00:00:00Z" \
  --select "Id,Name,Number,Date,TotalAmount,PeriodFrom,PeriodTo,LifeCycleState" \
  --expand "Counterparty(\$select=Id,Name),ResponsibleEmployee(\$select=Id,Name)" \
  --orderby "Date desc" --top 20
```

## Учётные документы Docflow (базовый тип)

```bash
python .claude/skills/rxapi-auth/scripts/query.py IDocflowAccountingDocumentBases \
  --filter "Counterparty/Id eq {counterpartyId}" \
  --select "Id,Name,Number,Date,TotalAmount,LifeCycleState" \
  --orderby "Date desc" --top 20
```

## Центры финансовой ответственности (справочник)

```bash
python .claude/skills/rxapi-auth/scripts/query.py IFinancialResponsibilityCenters \
  --select "Id,Name,Status" --top 50
```

## Ключевые EntitySets

| EntitySet | Что содержит |
|-----------|-------------|
| `IIncomingPaymentOrders` | Входящие платёжные поручения |
| `IIncomingPaymentOrderVersionss` | Версии входящих ПП |
| `IIncomingPaymentOrderTrackings` | Трекинг входящих ПП |
| `IFinancialArchiveOutgoingPaymentOrders` | Исходящие платёжные поручения |
| `IFinancialArchiveOutgoingPaymentOrderVersionss` | Версии исходящих ПП |
| `IGeneralAccountingDocuments` | Общехозяйственные учётные документы |
| `IGeneralAccountingDocumentCommissionMemberss` | Члены комиссии по общехоз. документам |
| `IInternalAccountingDocumentBases` | Внутренние учётные документы (база) |
| `IInternalAccountingDocumentBaseCommissionMemberss` | Члены комиссии |
| `IFixedAssetDocuments` | Документы по основным средствам |
| `IFixedAssetDocumentCommissionMemberss` | Члены комиссии по ОС |
| `IFinancialArchiveInventoryDocuments` | Акты инвентаризации |
| `IFinancialArchiveInventoryDocumentCommissionMemberss` | Комиссия по инвентаризации |
| `IFinancialArchiveAssetTransferDocuments` | Акты приёма-передачи ОС |
| `IFinancialArchiveAssetTransferDocumentCommissionMemberss` | Комиссия по актам ПП ОС |
| `IDocflowAccountingDocumentBases` | Базовый тип учётных документов (Docflow) |
| `IFinancialResponsibilityCenters` | Центры финансовой ответственности |

## Поля IAccountingDocumentBaseDto (общие для всех финансовых документов)

| Поле | Тип | Описание |
|------|-----|---------|
| `Number` | String | Номер документа |
| `Date` | DateTimeOffset | Дата документа |
| `TotalAmount` | Double | Сумма с НДС |
| `VatAmount` | Double | Сумма НДС |
| `NetAmount` | Double | Сумма без НДС |
| `IsFormalized` | Boolean | Формализованный документ (ЭДО) |
| `JournalEntryNumber` | String | Номер в журнале учёта |
| `JournalEntryStatus` | String | Статус журнальной записи |
| `FormalizedServiceType` | String | Тип услуги (для формализованных) |
| `PurchaseOrderNumber` | String | Номер заказа на закупку |
| `Currency` | → ICurrency | Валюта |
| `Counterparty` | → ICounterparty | Контрагент |
| `ResponsibleEmployee` | → IEmployee | Ответственный |
| `Accountant` | → IEmployee | Бухгалтер |
| `VatRate` | → IVatRate | Ставка НДС |
| `Bank` | → IBank | Банк |

## Поля IInternalAccountingDocumentBaseDto (дополнительно к IAccountingDocumentBaseDto)

| Поле | Тип | Описание |
|------|-----|---------|
| `PeriodFrom` | DateTimeOffset | Начало периода |
| `PeriodTo` | DateTimeOffset | Конец периода |
| `InventoryNumber` | String | Инвентарный номер |
| `CommissionPresident` | → IEmployee | Председатель комиссии |
| `Custodian` | → IEmployee | МОЛ (материально ответственное лицо) |

## Интеграция 1С (AuraIntegration1C)

```bash
# Исходящие счета (синхронизированные с 1С)
python .claude/skills/rxapi-auth/scripts/query.py IOutgoingInvoices \
  --filter "LifeCycleState eq 'Active'" \
  --select "Id,Name,Number,Date,TotalAmount,VatAmount,NetAmount,JournalEntryNumber,JournalEntryStatus,IsFormalized" \
  --expand "Counterparty(\$select=Id,Name),Currency(\$select=Name),ResponsibleEmployee(\$select=Id,Name)" \
  --orderby "Date desc" --top 20

# Ссылки на внешние сущности 1С
python .claude/skills/rxapi-auth/scripts/query.py IExternalEntityLinks \
  --filter "ExtSystemId eq '1C'" \
  --select "Id,Name,EntityType,EntityId,ExtEntityType,ExtEntityId,SyncDate,IsDeleted" \
  --orderby "SyncDate desc" --top 50

# Ссылки по конкретной внутренней сущности
python .claude/skills/rxapi-auth/scripts/query.py IExternalEntityLinks \
  --filter "EntityType eq 'IOutgoingInvoice' and EntityId eq {invoiceId}" \
  --select "Id,ExtEntityType,ExtEntityId,ExtSystemId,SyncDate,IsDeleted"
```

### Ключевые EntitySets AuraIntegration1C

| EntitySet | Что содержит |
|-----------|-------------|
| `IOutgoingInvoices` | Исходящие счета (интеграция с 1С) |
| `IExternalEntityLinks` | Связи внутренних сущностей с объектами 1С |

### Поля IExternalEntityLinkDto

| Поле | Тип | Описание |
|------|-----|---------|
| `EntityType` | String | Тип внутренней сущности RX |
| `EntityId` | Int64 | ID внутренней сущности |
| `ExtEntityType` | String | Тип объекта в 1С |
| `ExtEntityId` | String | ID объекта в 1С |
| `ExtSystemId` | String | Идентификатор внешней системы |
| `SyncDate` | DateTimeOffset | Дата последней синхронизации |
| `IsDeleted` | Boolean | Помечено как удалённое в 1С |
| `Name` | String | Наименование |
| `Status` | String | Статус записи |

## Типичные проблемы

| Ситуация | Действие |
|----------|----------|
| Нужна структура сущности | `python .claude/skills/rxapi-auth/scripts/search_metadata.py IIncomingPaymentOrderDto` |
| Фильтр по периоду документа | Для внутренних документов используй `PeriodFrom`/`PeriodTo`, для остальных — `Date` |
| Не находит документ по сумме | Поля `TotalAmount` — тип Double, сравнивай точно: `TotalAmount eq 10000.00` |
