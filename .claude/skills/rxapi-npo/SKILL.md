---
name: rxapi-npo
description: Корпоративный модуль AuraNPO для НПО Компас в Directum RX — официальные документы, шаблоны документов, входящие/исходящие письма, счета, акты сверки, договорные документы, учётные документы. Использовать когда нужны специфичные для НПО Компас документы, шаблоны, входящие счета или акты к договорам.
---

# AuraNPO — корпоративный модуль НПО Компас

Перед использованием: получить `currentUserId` через skill `rxapi-current-user`.

> **Важно:** модуль `AuraNPO` — корпоративное расширение, специфичное для компании **НПО Компас**. Сущности этого модуля отсутствуют в стандартной поставке Directum RX и доступны только в данной инсталляции. Все документы наследуют `IOfficialDocumentDto` — полный набор полей (статус ЖЦ, регистрационный номер, подразделение, подписант, проект и т.д.).

## Официальные документы

```bash
# Поиск официального документа по теме
python .claude/skills/rxapi-auth/scripts/query.py IOfficialDocuments \
  --filter "contains(Subject,'договор') and LifeCycleState ne 'Obsolete'" \
  --select "Id,Subject,RegistrationNumber,RegistrationDate,LifeCycleState,InternalApprovalState,ExternalApprovalState" \
  --expand "BusinessUnit(\$select=Name),Department(\$select=Name),ResponsibleEmployee(\$select=Id,Name)" \
  --orderby "RegistrationDate desc" --top 20

# Документы на исполнении у меня
python .claude/skills/rxapi-auth/scripts/query.py IOfficialDocuments \
  --filter "Assignee/Id eq {currentUserId} and ExecutionState ne 'Executed'" \
  --select "Id,Subject,RegistrationNumber,RegistrationDate,ExecutionState,ControlExecutionState" \
  --orderby "RegistrationDate desc" --top 20
```

## Шаблоны документов

```bash
# Активные шаблоны документов
python .claude/skills/rxapi-auth/scripts/query.py IDocumentTemplates \
  --filter "Status eq 'Active'" \
  --select "Id,Name,Status" \
  --expand "DocumentKinds(\$select=Id,Name)" \
  --orderby "Name asc" --top 50

# Шаблоны для подразделения
python .claude/skills/rxapi-auth/scripts/query.py IDocumentTemplates \
  --filter "Status eq 'Active'" \
  --select "Id,Name,Status" \
  --expand "BusinessUnits(\$select=Id,Name),Departments(\$select=Id,Name)" \
  --top 50
```

## Входящие письма

```bash
# Входящие письма мне как адресату
python .claude/skills/rxapi-auth/scripts/query.py IIncomingLetters \
  --filter "Addressee/Id eq {currentUserId} and LifeCycleState ne 'Obsolete'" \
  --select "Id,Name,RegistrationNumber,RegistrationDate,InNumber,Dated,LifeCycleState,ExecutionState" \
  --expand "Correspondent(\$select=Id,Name)" \
  --orderby "RegistrationDate desc" --top 20

# Входящие письма от контрагента
python .claude/skills/rxapi-auth/scripts/query.py IIncomingLetters \
  --filter "Correspondent/Id eq {counterpartyId}" \
  --select "Id,Name,RegistrationNumber,RegistrationDate,InNumber,Dated,LifeCycleState" \
  --orderby "RegistrationDate desc"
```

## Входящие счета

```bash
# Входящие счета по контрагенту
python .claude/skills/rxapi-auth/scripts/query.py IIncomingInvoices \
  --filter "Counterparty/Id eq {counterpartyId} and LifeCycleState ne 'Obsolete'" \
  --select "Id,Name,Number,Date,TotalAmount,VatAmount,PaymentDueDate,LifeCycleState,JournalEntryStatus" \
  --expand "Contract(\$select=Id,Name),Currency(\$select=Name)" \
  --orderby "Date desc"

# Счета к оплате (просроченные или в работе)
python .claude/skills/rxapi-auth/scripts/query.py IIncomingInvoices \
  --filter "PaymentDueDate le 2025-12-31T00:00:00Z and JournalEntryStatus ne 'Posted'" \
  --select "Id,Name,Number,Date,TotalAmount,PaymentDueDate" \
  --expand "Counterparty(\$select=Id,Name)" \
  --orderby "PaymentDueDate asc" --top 30
```

## Учётные документы (бухгалтерские)

```bash
# Учётные документы (базовый тип) по контрагенту
python .claude/skills/rxapi-auth/scripts/query.py IDocflowAccountingDocumentBases \
  --filter "Counterparty/Id eq {counterpartyId} and LifeCycleState ne 'Obsolete'" \
  --select "Id,Name,Number,Date,TotalAmount,VatAmount,LifeCycleState,IsFormalized" \
  --expand "Currency(\$select=Name),ResponsibleEmployee(\$select=Id,Name)" \
  --orderby "Date desc"
```

## Акты сверки с контрагентом

```bash
python .claude/skills/rxapi-auth/scripts/query.py IReconciliationStatements \
  --filter "Counterparty/Id eq {counterpartyId}" \
  --select "Id,Name,Number,Date,TotalAmount,LifeCycleState" \
  --expand "Currency(\$select=Name)" \
  --orderby "Date desc"
```

## Акты к договорам (ContractStatement)

```bash
python .claude/skills/rxapi-auth/scripts/query.py IContractStatements \
  --filter "Counterparty/Id eq {counterpartyId} and LifeCycleState ne 'Obsolete'" \
  --select "Id,Name,Number,Date,TotalAmount,VatAmount,LifeCycleState" \
  --expand "Currency(\$select=Name),Contract(\$select=Id,Name)" \
  --orderby "Date desc"
```

## Политики хранения документов

```bash
python .claude/skills/rxapi-auth/scripts/query.py IRetentionPolicies \
  --select "Id,Name,Status" --top 50

python .claude/skills/rxapi-auth/scripts/query.py IRetentionPolicyDocumentKindss \
  --filter "RetentionPolicy/Id eq {policyId}" \
  --select "Id,RowNumber" \
  --expand "DocumentKind(\$select=Id,Name)"
```

## Ключевые EntitySets

| EntitySet | Что содержит |
|-----------|-------------|
| `IOfficialDocuments` | Официальные документы (базовый тип всех документов AuraNPO) |
| `IDocumentTemplates` | Шаблоны документов с привязкой к видам, БЕ, подразделениям |
| `IDocumentTemplateVersionss` | Версии шаблонов |
| `IIncomingLetters` | Входящие письма |
| `IIncomingLetterTrackings` | Трекинг входящих писем |
| `IIncomingInvoices` | Входящие счета |
| `IIncomingInvoiceTrackings` | Трекинг входящих счетов |
| `IDocflowAccountingDocumentBases` | Учётные документы (бухгалтерские) |
| `IDocflowAccountingDocumentBaseTrackings` | Трекинг учётных документов |
| `IContractStatements` | Акты к договорам |
| `IContractStatementTrackings` | Трекинг актов |
| `IReconciliationStatements` | Акты сверки |
| `IReconciliationStatementTrackings` | Трекинг актов сверки |
| `IIncomingDocumentBases` | Входящие документы (база) |
| `IIncomingTaxInvoices` | Входящие счета-фактуры |
| `IRetentionPolicies` | Политики хранения документов |
| `IRetentionPolicyDocumentKindss` | Виды документов в политиках хранения |

## Поля IOfficialDocumentDto (основные)

| Поле | Тип | Описание |
|------|-----|---------|
| `Subject` | String | Тема / содержание |
| `RegistrationNumber` | String | Регистрационный номер |
| `RegistrationDate` | DateTimeOffset | Дата регистрации |
| `DocumentDate` | DateTimeOffset | Дата документа |
| `LifeCycleState` | String | Состояние ЖЦ (`Draft`, `Active`, `Obsolete`) |
| `RegistrationState` | String | Состояние регистрации |
| `InternalApprovalState` | String | Состояние внутреннего согласования |
| `ExternalApprovalState` | String | Состояние внешнего согласования |
| `ExecutionState` | String | Состояние исполнения |
| `ControlExecutionState` | String | Состояние контроля исполнения |
| `ExchangeState` | String | Состояние ЭДО |
| `Note` | String | Примечание |
| `PaperCount` | Int32 | Количество бумажных экземпляров |
| `StoredIn` | String | Место хранения |
| `LocationState` | String | Местонахождение |
| `IsReturnRequired` | Boolean | Требуется возврат |
| `ReturnDeadline` | DateTimeOffset | Срок возврата |
| `BusinessUnit` | → IBusinessUnit | Наша организация (БЕ) |
| `Department` | → IDepartment | Подразделение |
| `ResponsibleEmployee` | → IEmployee | Ответственный (из базового типа) |
| `OurSignatory` | → IEmployee | Подписант с нашей стороны |
| `Assignee` | → IEmployee | Исполнитель |
| `DocumentKind` | → IDocumentKind | Вид документа |
| `DocumentRegister` | → IDocumentRegister | Журнал регистрации |
| `CaseFile` | → ICaseFile | Дело (номенклатура) |
| `Project` | → IProjectBase | Проект |
| `LeadingDocument` | → IOfficialDocument | Ведущий документ |

## Поля IIncomingLetterDto (дополнительно к IOfficialDocumentDto)

| Поле | Тип | Описание |
|------|-----|---------|
| `InNumber` | String | Входящий номер от контрагента |
| `Dated` | DateTimeOffset | Дата документа контрагента |
| `Correspondent` | → ICounterparty | Корреспондент (от кого) |
| `Addressee` | → IEmployee | Адресат (кому) |
| `SignedBy` | → IContact | Подписан контактом контрагента |

## Поля IIncomingInvoiceDto (дополнительно к IAccountingDocumentBaseDto)

| Поле | Тип | Описание |
|------|-----|---------|
| `PaymentDueDate` | DateTimeOffset | Срок оплаты |
| `Contract` | → IContractualDocument | Договор |

## Типичные проблемы

| Ситуация | Действие |
|----------|----------|
| Сущность не найдена в стандартной UML | Это нормально — AuraNPO это company-specific модуль, используй `search_metadata.py` |
| Нужна структура документа | `python .claude/skills/rxapi-auth/scripts/search_metadata.py IOfficialDocumentDto` |
| Запрос без фильтра возвращает ошибку | Добавь фильтр по `Counterparty/Id`, `Assignee/Id` или диапазон дат |
| Нужен список всех типов AuraNPO | `python .claude/skills/rxapi-auth/scripts/query.py --list AuraNPO` не работает — ищи по части имени документа |
