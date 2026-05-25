---
name: rxapi-genai
description: ИИ-функциональность Directum RX / Aura — запросы к языковым моделям, управление моделями и сервисами GenAI, промпты, QA-поиск по базе знаний, ИИ-ассистенты менеджеров.
---

# GenAI — искусственный интеллект в Directum Aura

> Модуль объединяет четыре компонента: GenAI (запросы, модели, промпты), GenAISolutions (промпты для действий с документами), QASearch (поиск по базе знаний) и Intelligence (ИИ-ассистенты менеджеров).

## Запросы к ИИ (IGenAIRequests)

```bash
# История запросов к ИИ — последние
python .claude/skills/rxapi-auth/scripts/query.py IGenAIRequests \
  --filter "Status eq 'Completed'" \
  --select "Id,Name,Status,ProcessStatus,ArioTaskStatus,PromptTokensCount,CompletionTokensCount,TotalTokensCount" \
  --expand "Author(\$select=Id,Name),GenAIPrompt(\$select=Id,Name)" \
  --orderby "Id desc" --top 20

# Запросы в обработке
python .claude/skills/rxapi-auth/scripts/query.py IGenAIRequests \
  --filter "ProcessStatus eq 'InProcess'" \
  --select "Id,Name,ProcessStatus,ArioTaskStatus,EntityType,EntityId" \
  --top 20

# Запросы по документу
python .claude/skills/rxapi-auth/scripts/query.py IGenAIRequests \
  --filter "EntityType eq 'IOfficialDocument' and EntityId eq {docId}" \
  --select "Id,Name,UserInput,Result,Status,TotalTokensCount" \
  --orderby "Id desc"
```

## Модели GenAI

```bash
# Доступные модели
python .claude/skills/rxapi-auth/scripts/query.py IGenAIModels \
  --filter "Status eq 'Active'" \
  --select "Id,Name,TokensRatio,ContextSize,Status" \
  --top 50

# Модели конкретного сервиса
python .claude/skills/rxapi-auth/scripts/query.py IGenAIServiceModelss \
  --filter "GenAIService/Id eq {serviceId}" \
  --select "Id,Name,TokensRatio,ContextSize"
```

## Сервисы GenAI

```bash
# Настроенные сервисы ИИ
python .claude/skills/rxapi-auth/scripts/query.py IGenAIServices \
  --filter "Status eq 'Active'" \
  --select "Id,Name,ServiceUrl,ApiType,Status" \
  --expand "Models(\$select=Id,Name,ContextSize)" \
  --top 20
```

## Промпты GenAI

```bash
# Базовые промпты
python .claude/skills/rxapi-auth/scripts/query.py IGenAIPromptBases \
  --filter "Status eq 'Active'" \
  --select "Id,Name,Temperature,IsSystem,Status" \
  --expand "GenAIModel(\$select=Id,Name),Service(\$select=Id,Name)" \
  --top 50

# Промпты для генерации текстов
python .claude/skills/rxapi-auth/scripts/query.py IGenAIPromptGenerations \
  --filter "Status eq 'Active'" \
  --select "Id,Name,PromptType,Placeholder,Temperature" \
  --expand "GenAIModel(\$select=Id,Name)" \
  --top 50

# Промпты для суммаризации
python .claude/skills/rxapi-auth/scripts/query.py IGenAIPromptSummaries \
  --filter "Status eq 'Active'" \
  --select "Id,Name,TaskType,Temperature" \
  --expand "GenAIModel(\$select=Id,Name)" \
  --top 50
```

## QA-поиск по базе знаний

```bash
# Зоны поиска (области знаний)
python .claude/skills/rxapi-auth/scripts/query.py IQASearchAreas \
  --filter "Status eq 'Active'" \
  --select "Id,Name,Note,IndexName,Status" \
  --expand "Prompt(\$select=Id,Name),Responsible(\$select=Id,Name)" \
  --top 50

# Промпты QA-поиска
python .claude/skills/rxapi-auth/scripts/query.py IQASearchPrompts \
  --filter "Status eq 'Active'" \
  --select "Id,Name,SearchType,Temperature" \
  --expand "GenAIModel(\$select=Id,Name)" \
  --top 20

# Проиндексированные сущности в зоне поиска
python .claude/skills/rxapi-auth/scripts/query.py IQASearchIndexedEntities \
  --filter "contains(Name,'{keyword}')" \
  --select "Id,Name,Status" --top 20

# Очередь индексации
python .claude/skills/rxapi-auth/scripts/query.py IQASearchIndexQueueItems \
  --select "Id,Name,Status" --top 50
```

## ИИ-ассистенты менеджеров (Intelligence)

```bash
# Список ИИ-ассистентов менеджеров
python .claude/skills/rxapi-auth/scripts/query.py IIntelligenceAIManagersAssistants \
  --filter "Status eq 'Active'" \
  --select "Id,Status,PreparesActionItemDrafts,PreparesResolution" \
  --expand "Manager(\$select=Id,Name)" \
  --top 50

# Классификаторы ИИ-ассистентов
python .claude/skills/rxapi-auth/scripts/query.py IIntelligenceAIManagersAssistantClassifierss \
  --select "Id,Name,Status" --top 50
```

## Ключевые EntitySets

| EntitySet | Что содержит |
|-----------|-------------|
| `IGenAIRequests` | Запросы к ИИ (история, статусы, результаты) |
| `IGenAIModels` | Языковые модели (размер контекста, токен-ратио) |
| `IGenAIServices` | Сервисы ИИ (URL, тип API, ключи) |
| `IGenAIServiceModelss` | Связь сервисов с моделями |
| `IGenAIPromptBases` | Базовые промпты |
| `IGenAIPromptGenerations` | Промпты для генерации текстов |
| `IGenAIPromptSummaries` | Промпты для суммаризации |
| `IGenAIPromptDocActionBases` | Базовые промпты для действий с документами |
| `IGenAIPromptDocActionBaseDocumentKindss` | Виды документов для промптов |
| `IGenAIPromptGenerationDocumentKindss` | Виды документов для генерации |
| `IGenAIPromptSummaryDocumentKindss` | Виды документов для суммаризации |
| `IGenAIPromptProcessKinds` | Промпты для видов процессов |
| `IQASearchAreas` | Зоны QA-поиска (области знаний) |
| `IQASearchAreaBases` | Базовые зоны поиска |
| `IQASearchAreaHelps` | Справочные зоны поиска |
| `IQASearchPrompts` | Промпты QA-поиска |
| `IQASearchIndexedEntities` | Проиндексированные документы/сущности |
| `IQASearchIndexQueueItems` | Очередь индексации |
| `IIntelligenceAIManagersAssistants` | ИИ-ассистенты менеджеров |
| `IIntelligenceAIManagersAssistantClassifierss` | Классификаторы ассистентов |

## Поля IGenAIRequestDto

| Поле | Тип | Описание |
|------|-----|---------|
| `Name` | String | Наименование запроса |
| `EntityId` | Int64 | ID сущности, к которой привязан запрос |
| `EntityType` | String | Тип сущности (напр. `IOfficialDocument`) |
| `UserInput` | String | Пользовательский ввод |
| `Result` | String | Результат (ответ ИИ) |
| `Status` | String | Итоговый статус |
| `ProcessStatus` | String | Статус обработки (InProcess, Completed, Error) |
| `ArioTaskStatus` | String | Статус задачи Ario |
| `ArioTaskId` | Int32 | ID задачи в Ario |
| `PromptTokensCount` | Int32 | Токены промпта |
| `CompletionTokensCount` | Int32 | Токены ответа |
| `TotalTokensCount` | Int32 | Итого токенов |
| `ReduceIterationsCount` | Int32 | Кол-во итераций сокращения контекста |
| `Updated` | DateTimeOffset | Дата обновления |
| `Author` | → IUser | Автор запроса |
| `GenAIPrompt` | → IGenAIPromptBase | Использованный промпт |
| `ExtractedText` | → IBinaryData | Извлечённый текст (исходный) |

## Поля IGenAIModelDto

| Поле | Тип | Описание |
|------|-----|---------|
| `Name` | String | Название модели |
| `TokensRatio` | Double | Коэффициент токенов (стоимость) |
| `ContextSize` | Int32 | Размер контекста (токены) |
| `Status` | String | Статус (Active/Closed) |
| `Id` | Int64 | Идентификатор |

## Поля IGenAIPromptBaseDto

| Поле | Тип | Описание |
|------|-----|---------|
| `Name` | String | Название промпта |
| `Temperature` | Double | Температура генерации (0.0–1.0) |
| `IsSystem` | Boolean | Системный промпт |
| `UserTemplate` | String | Шаблон пользовательского сообщения |
| `SystemTemplate` | String | Системный шаблон |
| `Status` | String | Статус (Active/Closed) |
| `GenAIModel` | → IGenAIModel | Модель по умолчанию |
| `Service` | → IGenAIService | Сервис ИИ |

## Поля IQASearchAreaDto

| Поле | Тип | Описание |
|------|-----|---------|
| `Name` | String | Название зоны поиска |
| `Note` | String | Описание |
| `IndexName` | String | Имя индекса в поисковом движке |
| `Status` | String | Статус (Active/Closed) |
| `Prompt` | → IQASearchPrompt | Промпт для поиска |
| `Responsible` | → IRecipient | Ответственный |
