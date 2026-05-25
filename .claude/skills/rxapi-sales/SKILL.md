---
name: rxapi-sales
description: Модуль Sales в Directum RX / Aura — коммерческие предложения, пресейл-документы, документы сделок, активности по продажам. Использовать когда нужно найти КП, пресейл, сделки с контрагентами или активности менеджеров по продажам.
---

# Sales — коммерческие предложения, пресейл и сделки

Перед использованием: получить `currentUserId` через skill `rxapi-current-user`.

> **Важно:** модуль Sales — часть расширения Aura. Все документы наследуют поля из `IOfficialDocumentDto` (регистрационный номер, дата, статус, контрагент и т.д.).

## Коммерческие предложения

```bash
# Активные КП текущего пользователя
python .claude/skills/rxapi-auth/scripts/query.py ICommercialOffers \
  --filter "ResponsibleEmployee/Id eq {currentUserId} and LifeCycleState ne 'Obsolete'" \
  --select "Id,Name,RegistrationNumber,RegistrationDate,SendingDate,LifeCycleState" \
  --expand "Counterparty(\$select=Id,Name)" \
  --orderby "RegistrationDate desc" --top 20

# КП по контрагенту
python .claude/skills/rxapi-auth/scripts/query.py ICommercialOffers \
  --filter "Counterparty/Id eq {counterpartyId}" \
  --select "Id,Name,RegistrationNumber,RegistrationDate,SendingDate,LifeCycleState" \
  --orderby "RegistrationDate desc"

# Версии КП
python .claude/skills/rxapi-auth/scripts/query.py ICommercialOfferVersionss \
  --filter "ElectronicDocument/Id eq {offerId}" \
  --select "Id,Number,Created,Modified"
```

## Адресаты коммерческого предложения

```bash
python .claude/skills/rxapi-auth/scripts/query.py ICommercialOfferAddresseess \
  --filter "ElectronicDocument/Id eq {offerId}" \
  --select "Id,Correspondent,Addressee" \
  --expand "Correspondent(\$select=Id,Name),Addressee(\$select=Id,Name)"
```

## Пресейл-документы

```bash
# Мои пресейл-документы
python .claude/skills/rxapi-auth/scripts/query.py IPresaleDocuments \
  --filter "ResponsibleEmployee/Id eq {currentUserId} and LifeCycleState ne 'Obsolete'" \
  --select "Id,Name,RegistrationDate,LifeCycleState" \
  --expand "Company(\$select=Id,Name)" \
  --orderby "RegistrationDate desc" --top 20

# Статус пресейл-заявки
python .claude/skills/rxapi-auth/scripts/query.py IPresaleRequestTasks \
  --filter "Status eq 'InProcess'" \
  --select "Id,Subject,Status,Deadline" \
  --expand "Author(\$select=Id,Name)" \
  --orderby "Deadline asc" --top 20
```

## Документы сделок

```bash
# Документы по сделке (к контрагенту)
python .claude/skills/rxapi-auth/scripts/query.py IDealDocuments \
  --filter "Company/Id eq {companyId} and LifeCycleState ne 'Obsolete'" \
  --select "Id,Name,RegistrationDate,LifeCycleState" \
  --orderby "RegistrationDate desc"

# Согласование сделки с партнёром
python .claude/skills/rxapi-auth/scripts/query.py IPartnerDealTasks \
  --filter "Status eq 'InProcess'" \
  --select "Id,Subject,Status,Deadline" \
  --expand "Author(\$select=Id,Name)" --top 20
```

## Активности (Activity Documents)

```bash
# Активности по компании
python .claude/skills/rxapi-auth/scripts/query.py IActivityDocuments \
  --filter "Company/Id eq {companyId}" \
  --select "Id,Name,RegistrationDate,LifeCycleState" \
  --expand "ResponsibleEmployee(\$select=Id,Name)" \
  --orderby "RegistrationDate desc" --top 20
```

## Задания Sales

```bash
# Задания по обработке лидов
python .claude/skills/rxapi-auth/scripts/query.py ISalesAssignments \
  --filter "Performer/Id eq {currentUserId} and Status eq 'InProcess'" \
  --select "Id,Subject,Status,Deadline" \
  --orderby "Deadline asc"

# Задания по обработке отчётов
python .claude/skills/rxapi-auth/scripts/query.py ISalesReportProcessAssignments \
  --filter "Performer/Id eq {currentUserId} and Status eq 'InProcess'" \
  --select "Id,Subject,Status,Deadline" \
  --orderby "Deadline asc"
```

## Ключевые EntitySets

| EntitySet | Что содержит |
|-----------|-------------|
| `ICommercialOffers` | Коммерческие предложения |
| `ICommercialOfferVersionss` | Версии коммерческих предложений |
| `ICommercialOfferAddresseess` | Адресаты КП |
| `ICommercialOfferTrackings` | Трекинг КП (бумажный экземпляр) |
| `IPresaleDocuments` | Пресейл-документы |
| `IPresaleRequestTasks` | Задачи по пресейл-заявкам |
| `IPresaleAcceptWorkAssignments` | Задания приёмки работ по пресейлу |
| `IDealDocuments` | Документы сделок |
| `IPartnerDealTasks` | Задачи согласования сделок с партнёром |
| `IActivityDocuments` | Активности (коммуникации с контрагентами) |
| `ISalesAssignments` | Задания по обработке лидов |
| `ISalesReportProcessAssignments` | Задания по обработке отчётов |
| `ISalesSetReasonClosedDealTasks` | Задачи указания причины закрытия сделки |

## Поля ICommercialOfferDto (наследует IOutgoingDocumentBaseDto -> IOfficialDocumentDto)

| Поле | Тип | Описание |
|------|-----|---------|
| `SendingDate` | DateTimeOffset | Дата отправки КП |
| `PostId` | String | Идентификатор почтового сообщения |
| `RegistrationNumber` | String | Регистрационный номер (из IOfficialDocumentDto) |
| `RegistrationDate` | DateTimeOffset | Дата регистрации |
| `Subject` | String | Тема |
| `LifeCycleState` | String | Состояние: `Draft`, `Active`, `Obsolete` |
| `IsManyAddressees` | Boolean | Множество адресатов (из IOutgoingDocumentBaseDto) |
| `SentDate` | DateTimeOffset | Дата отправки (из IOutgoingDocumentBaseDto) |
| `Counterparty` | → ICounterparty | Контрагент |
| `ResponsibleEmployee` | → IEmployee | Ответственный |
| `Addressee` | → IContact | Адресат |

## Поля IActivityDocumentDto / IPresaleDocumentDto (наследуют IOfficialDocumentDto)

| Поле | Тип | Описание |
|------|-----|---------|
| `Name` | String | Наименование |
| `RegistrationDate` | DateTimeOffset | Дата регистрации |
| `LifeCycleState` | String | Состояние жизненного цикла |
| `Company` | → ICompany | Связанная компания |
| `ResponsibleEmployee` | → IEmployee | Ответственный |

## Типичные проблемы

| Ситуация | Действие |
|----------|----------|
| Нужна структура сущности | `python .claude/skills/rxapi-auth/scripts/search_metadata.py ICommercialOfferDto` |
| Не видно поля суммы в КП | КП наследует только `SendingDate` и `PostId` — сумма не хранится в КП |
| Поиск по контрагенту | Используй `Company/Id` (для ActivityDocument) или `Counterparty/Id` (для CommercialOffer) |
