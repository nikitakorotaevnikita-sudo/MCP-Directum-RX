---
name: rxapi-exchange
description: Электронный документооборот (ЭДО) с контрагентами в Directum RX / Aura — обмен документами, ящики обмена, статусы ЭДО, подписание, отзывы. Использовать когда нужно найти документы ЭДО, статус обмена с контрагентом, ящики ЭДО, задания по обработке входящих документов ЭДО.
---

# Exchange — электронный документооборот (ЭДО)

Перед использованием: получить `currentUserId` через skill `rxapi-current-user`.

## Документы ЭДО

```bash
# Входящие документы ЭДО в работе
python .claude/skills/rxapi-auth/scripts/query.py IExchangeDocuments \
  --filter "ExchangeState ne 'Terminated' and ExchangeState ne 'Completed'" \
  --select "Id,Name,ExchangeState,MessageDate" \
  --expand "Counterparty(\$select=Id,Name),Box(\$select=Id,Name)" \
  --orderby "MessageDate desc" --top 20

# Документы ЭДО по контрагенту
python .claude/skills/rxapi-auth/scripts/query.py IExchangeDocuments \
  --filter "Counterparty/Id eq {counterpartyId}" \
  --select "Id,Name,ExchangeState,InvoiceState,MessageDate,OutgoingStatus" \
  --expand "Box(\$select=Id,Name)" \
  --orderby "MessageDate desc"
```

## Информация об обмене (ExchangeDocumentInfo)

```bash
# Статус обмена конкретного документа
python .claude/skills/rxapi-auth/scripts/query.py IExchangeDocumentInfos \
  --filter "Document/Id eq {documentId}" \
  --select "Id,ExchangeState,InvoiceState,OutgoingStatus,BuyerAcceptanceStatus,NeedSign,MessageDate,MessageType" \
  --expand "Counterparty(\$select=Id,Name),Box(\$select=Id,Name)"

# Ожидают подписания (NeedSign)
python .claude/skills/rxapi-auth/scripts/query.py IExchangeDocumentInfos \
  --filter "NeedSign eq true and Status ne 'Closed'" \
  --select "Id,ExchangeState,NeedSign,MessageDate" \
  --expand "Document(\$select=Id,Name),Counterparty(\$select=Id,Name)" \
  --orderby "MessageDate asc" --top 20

# Сервисные документы обмена
python .claude/skills/rxapi-auth/scripts/query.py IExchangeDocumentInfoServiceDocumentss \
  --filter "ExchangeDocumentInfo/Id eq {infoId}" \
  --select "Id,Name"
```

## Ящики обмена (Boxes)

```bash
# Все активные ящики ЭДО
python .claude/skills/rxapi-auth/scripts/query.py IBoxBases \
  --filter "Status eq 'Active'" \
  --select "Id,Name,ConnectionStatus,Routing,DeadlineInDays" \
  --expand "Responsible(\$select=Id,Name)" --top 50

# Ящики компании (бизнес-единицы)
python .claude/skills/rxapi-auth/scripts/query.py ICompanyBaseExchangeBoxess \
  --select "Id,Name" --top 50

# Ящики контрагентов
python .claude/skills/rxapi-auth/scripts/query.py ICounterpartyExchangeBoxess \
  --filter "Counterparty/Id eq {counterpartyId}" \
  --select "Id,Name" --top 20
```

## Сервисы ЭДО

```bash
# Доступные сервисы ЭДО
python .claude/skills/rxapi-auth/scripts/query.py IExchangeServices \
  --select "Id,Name" --top 20
```

## Задания по обработке документов ЭДО

```bash
# Задания в работе у меня
python .claude/skills/rxapi-auth/scripts/query.py IExchangeDocumentProcessingAssignments \
  --filter "Performer/Id eq {currentUserId} and Status eq 'InProcess'" \
  --select "Id,Subject,Status,Deadline" \
  --orderby "Deadline asc" --top 20

# Задачи обработки ЭДО
python .claude/skills/rxapi-auth/scripts/query.py IExchangeDocumentProcessingTasks \
  --filter "Status eq 'InProcess'" \
  --select "Id,Subject,Status,Deadline" \
  --expand "Author(\$select=Id,Name)" \
  --orderby "Deadline asc" --top 20
```

## Типы документов ЭДО

```bash
python .claude/skills/rxapi-auth/scripts/query.py IExchangeCoreExchangeDocumentTypes \
  --select "Id,Name" --top 50
```

## Ключевые EntitySets

| EntitySet | Что содержит | Модуль |
|-----------|-------------|--------|
| `IExchangeDocuments` | Электронные документы ЭДО | Exchange |
| `IExchangeDocumentInfos` | Статус и метаданные обмена | Exchange |
| `IExchangeDocumentInfoServiceDocumentss` | Сервисные документы (квитанции, подтверждения) | Exchange |
| `IExchangeDocumentVersionss` | Версии документов ЭДО | Exchange |
| `IExchangeDocumentTrackings` | Трекинг бумажного экземпляра | Exchange |
| `IExchangeDocumentProcessingTasks` | Задачи обработки входящих ЭДО | Exchange |
| `IExchangeDocumentProcessingAssignments` | Задания обработки ЭДО | Exchange |
| `IExchangeDocumentProcessingTaskObserverss` | Наблюдатели задач обработки | Exchange |
| `IBoxBases` | Базовые ящики ЭДО | ExchangeCore |
| `IExchangeServices` | Сервисы ЭДО (Диадок, СБИС и т.д.) | Exchange |
| `IExchangeCoreExchangeDocumentTypes` | Типы документов ЭДО | ExchangeCore |
| `ICompanyBaseExchangeBoxess` | Ящики компаний-бизнес-единиц | ExchangeCore |
| `ICompanyExchangeBoxess` | Ящики юрлиц | ExchangeCore |
| `ICounterpartyExchangeBoxess` | Ящики контрагентов | ExchangeCore |
| `IPersonExchangeBoxess` | Личные ящики сотрудников | ExchangeCore |
| `IBankExchangeBoxess` | Ящики банков | ExchangeCore |
| `IBusinessUnitBoxExchangeServiceCertificatess` | Сертификаты ящиков | ExchangeCore |

## Поля IExchangeDocumentInfoDto

| Поле | Тип | Описание |
|------|-----|---------|
| `ExchangeState` | String | Состояние обмена (`Sent`, `Received`, `Signed`, `Rejected`, `Terminated`, `Completed`) |
| `InvoiceState` | String | Состояние счёта-фактуры (`None`, `Sent`, `CorrectionWait`, `Completed`) |
| `OutgoingStatus` | String | Статус исходящего (`None`, `Sent`, `DeliveryConfirmed`) |
| `BuyerAcceptanceStatus` | String | Статус подписания покупателем |
| `BuyerDeliveryConfirmationStatus` | String | Статус подтверждения доставки |
| `NeedSign` | Boolean | Требует подписания |
| `MessageType` | String | Тип сообщения (`Outgoing`, `Incoming`) |
| `RevocationStatus` | String | Статус отзыва |
| `MessageDate` | DateTimeOffset | Дата сообщения в ЭДО |
| `ServiceDocumentId` | String | ID документа в сервисе ЭДО |
| `ServiceMessageId` | String | ID сообщения в сервисе ЭДО |
| `ServiceCounterpartyId` | String | ID контрагента в сервисе ЭДО |
| `Document` | → IOfficialDocument | Связанный документ в системе |
| `Box` | → IBoxBase | Ящик ЭДО |
| `Counterparty` | → ICounterparty | Контрагент |
| `RootBox` | → IBusinessUnitBox | Ящик бизнес-единицы |

## Поля IBoxBaseDto

| Поле | Тип | Описание |
|------|-----|---------|
| `Name` | String | Наименование ящика |
| `Note` | String | Примечание |
| `Status` | String | Статус (`Active`, `Closed`) |
| `ConnectionStatus` | String | Статус подключения |
| `Routing` | String | Режим маршрутизации |
| `DeadlineInDays` | Int32 | Срок обработки в днях |
| `DeadlineInHours` | Int32 | Срок обработки в часах |
| `Responsible` | → IEmployee | Ответственный |

## Типичные проблемы

| Ситуация | Действие |
|----------|----------|
| Нужно найти документ по ID сообщения в Диадоке | Фильтр по `ServiceMessageId` в `IExchangeDocumentInfos` |
| Документ не подписан контрагентом | Проверить `BuyerAcceptanceStatus` и `NeedSign` |
| Структура сущности | `python .claude/skills/rxapi-auth/scripts/search_metadata.py IBoxBaseDto` |
