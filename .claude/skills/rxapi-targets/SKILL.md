---
name: rxapi-targets
description: Цели, KPI и показатели в Directum RX / Aura (модули Targets и KPI). Использовать когда нужно узнать цели сотрудника или подразделения, карты KPI, показатели, прогресс по целям, OKR-карты, ключевые результаты.
---

# Цели и KPI в Directum RX (Aura)

Перед использованием: получить `currentUserId` через skill `rxapi-current-user`.

> **Важно:** модуль Targets — часть расширения Aura, его сущности отсутствуют
> в базовой UML-схеме Directum RX. Используй `search_metadata.py` для изучения полей.

## Модуль Targets

### Цели сотрудника или подразделения

```bash
# Мои цели
python .claude/skills/rxapi-auth/scripts/query.py ITargetsTargets \
  --filter "Responsible/Id eq {currentUserId} and Status eq 'Active'" \
  --select "Id,Name,Priority,Status,AchievementPercentage,PeriodStart,PeriodEnd" \
  --expand "AchievementStatus(\$select=Name)" \
  --orderby "PeriodStart desc"

# Цели подразделения / бизнес-единицы
python .claude/skills/rxapi-auth/scripts/query.py ITargetsTargets \
  --filter "StructuralUnit/Id eq {unitId} and Status eq 'Active'" \
  --select "Id,Name,Priority,AchievementPercentage,PeriodStart,PeriodEnd" \
  --expand "Responsible(\$select=Id,Name),AchievementStatus(\$select=Name)"

# Цели за период
python .claude/skills/rxapi-auth/scripts/query.py ITargetsTargets \
  --filter "Responsible/Id eq {employeeId} and PeriodStart ge 2025-01-01T00:00:00Z" \
  --select "Id,Name,AchievementPercentage,Priority,Status,PeriodLabel" \
  --orderby "PeriodStart asc"

# Личные цели (IsPersonal = true)
python .claude/skills/rxapi-auth/scripts/query.py ITargetsTargets \
  --filter "Responsible/Id eq {currentUserId} and IsPersonal eq true" \
  --select "Id,Name,AchievementPercentage,Status"
```

### Ключевые результаты (Key Results)

```bash
# Ключевые результаты по цели
python .claude/skills/rxapi-auth/scripts/query.py ITargetsTargetKeyResultss \
  --filter "Target/Id eq {targetId}" \
  --select "Id,Name,Status"
```

### Карты целей (OKR-карты)

```bash
# Карты целей подразделения
python .claude/skills/rxapi-auth/scripts/query.py ITargetsTargetsMaps \
  --filter "Status eq 'Active'" \
  --select "Id,Name,Code,KPI,IsMain,ModuleName" \
  --orderby "CodeIndex asc" --top 20

# Показатели карты
python .claude/skills/rxapi-auth/scripts/query.py ITargetsTargetsMapIndicatorss \
  --filter "TargetsMap/Id eq {mapId}" \
  --select "Id,Name"
```

### Статусы достижения

```bash
# Справочник статусов достижения
python .claude/skills/rxapi-auth/scripts/query.py ITargetsAchievementStatuss \
  --select "Id,Name"
```

## Модуль KPI

### Метрики (показатели KPI)

```bash
# Мои метрики KPI
python .claude/skills/rxapi-auth/scripts/query.py IKPIMetrics \
  --filter "Responsible/Id eq {currentUserId}" \
  --select "Id,Name,TargetValue,ActualValue,AchievementPercentage,Status,PeriodLabel,MetricType" \
  --expand "MetricGroup(\$select=Name),MeasurementUnit(\$select=Name)" \
  --orderby "Name asc"

# Метрики подразделения
python .claude/skills/rxapi-auth/scripts/query.py IKPIMetrics \
  --filter "StructuralUnit/Id eq {unitId} and Status eq 'Active'" \
  --select "Id,Name,TargetValue,ActualValue,AchievementPercentage,KeyIndicator" \
  --expand "Responsible(\$select=Id,Name)"

# Только ключевые показатели
python .claude/skills/rxapi-auth/scripts/query.py IKPIMetrics \
  --filter "Responsible/Id eq {currentUserId} and KeyIndicator eq true" \
  --select "Id,Name,TargetValue,ActualValue,AchievementPercentage"
```

### Фактические значения

```bash
python .claude/skills/rxapi-auth/scripts/query.py IKPIActualValues \
  --filter "Metric/Id eq {metricId}" \
  --select "Id,Value,Date" \
  --orderby "Date desc" --top 12
```

### Плановые значения

```bash
python .claude/skills/rxapi-auth/scripts/query.py IKPITargetValues \
  --filter "Metric/Id eq {metricId}" \
  --select "Id,Value,PeriodStart,PeriodEnd" \
  --orderby "PeriodStart asc"
```

### Личные карты KPI

```bash
python .claude/skills/rxapi-auth/scripts/query.py ITargetsKPIMaps \
  --filter "Owner/Id eq {currentUserId}" \
  --select "Id,Name,ModuleName" \
  --expand "Indicators(\$select=Id,Name)"
```

## Ключевые EntitySets

| EntitySet | Что содержит | Модуль |
|-----------|-------------|--------|
| `ITargetsTargets` | Цели (OKR) | Targets |
| `ITargetsTargetsMaps` | Карты целей | Targets |
| `ITargetsTargetKeyResultss` | Ключевые результаты | Targets |
| `ITargetsAchievementStatuss` | Статусы достижения (справочник) | Targets |
| `ITargetsMethodologies` | Методологии | Targets |
| `ITargetsWorkRules` | Правила работы | Targets |
| `ITargetsKPIMaps` | Личные KPI-карты | Targets |
| `IKPIMetrics` | Метрики / показатели | KPI |
| `IKPIActualValues` | Фактические значения метрик | KPI |
| `IKPITargetValues` | Плановые значения метрик | KPI |
| `IKPIMetricGroups` | Группы метрик | KPI |
| `IKPIMeasurementUnits` | Единицы измерения | KPI |

## Поля ITargetsTargets (ITargetDto)

| Поле | Тип | Описание |
|------|-----|----------|
| `Name` | String | Наименование цели |
| `Code` | String | Код |
| `Priority` | String | Приоритет |
| `Status` | String | Статус |
| `AchievementPercentage` | Double | % достижения |
| `PeriodStart` / `PeriodEnd` | DateTimeOffset | Период |
| `PeriodLabel` | String | Метка периода (напр. "Q1 2025") |
| `IsPersonal` | Boolean | Личная цель |
| `Description` | String | Описание |
| `Responsible` | → IEmployee | Ответственный |
| `StructuralUnit` | → IGroup | Подразделение / БЕ |
| `AchievementStatus` | → IAchievementStatus | Статус достижения |
| `KeyResults` | → [ITargetKeyResults] | Ключевые результаты |

## Поля IKPIMetrics (IMetricDto)

| Поле | Тип | Описание |
|------|-----|----------|
| `Name` | String | Наименование показателя |
| `TargetValue` | Double | Плановое значение |
| `ActualValue` | Double | Фактическое значение |
| `AchievementPercentage` | Double | % выполнения |
| `KeyIndicator` | Boolean | Ключевой показатель |
| `MetricType` | String | Тип метрики |
| `PeriodLabel` | String | Период |
| `Status` | String | Статус |
| `Responsible` | → IEmployee | Ответственный |
| `StructuralUnit` | → IGroup | Подразделение / БЕ |
| `MetricGroup` | → IMetricGroup | Группа |

## Типичные проблемы

| Ситуация | Действие |
|----------|----------|
| Нет resx-переводов для Targets | Модуль Aura — в resx не включён. Используй поля из таблиц выше |
| `IWorkRules` ≠ цели | `ITargetsWorkRules` — правила работы, не цели. Цели — `ITargetsTargets` |
| Нужна структура сущности | `python .claude/skills/rxapi-auth/scripts/search_metadata.py ITargetDto` |
