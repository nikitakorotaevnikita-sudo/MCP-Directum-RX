---
name: rxapi-crm
description: Контрагенты, контакты и сделки в Directum RX / Aura. Использовать когда нужно найти организацию, контрагента по ИНН, получить контакты компании или посмотреть сделки.
---

# CRM — контрагенты, контакты, сделки

## Найти контрагента

```bash
# По названию
python .claude/skills/rxapi-auth/scripts/query.py ICounterparties \
  --filter "contains(Name,'Ромашка') and Status eq 'Active'" \
  --select "Id,Name,TIN,TRRC,Status" --top 10

# По ИНН
python .claude/skills/rxapi-auth/scripts/query.py ICounterparties \
  --filter "TIN eq '7701234567'" \
  --select "Id,Name,TIN,TRRC,Status"
```

## Контакты контрагента

```bash
python .claude/skills/rxapi-auth/scripts/query.py IContacts \
  --filter "Company/Id eq {counterpartyId} and Status eq 'Active'" \
  --select "Id,Name,JobTitle,Phone,Email" --top 20
```

## Реквизиты контрагента (с телефонами)

```bash
python .claude/skills/rxapi-auth/scripts/query.py ICounterparties({counterpartyId}) \
  --expand "Phones(\$select=Number,Category),Emails(\$select=Address)"
```

## Сделки

```bash
# Активные сделки
python .claude/skills/rxapi-auth/scripts/query.py IPartnerDeals \
  --filter "Status eq 'Active'" \
  --select "Id,Name,Amount,Stage,CloseDate" \
  --expand "Counterparty(\$select=Id,Name),ResponsibleEmployee(\$select=Id,Name)" \
  --orderby "CloseDate asc" --top 20

# Мои сделки
python .claude/skills/rxapi-auth/scripts/query.py IPartnerDeals \
  --filter "ResponsibleEmployee/Id eq {currentUserId} and Status eq 'Active'" \
  --select "Id,Name,Amount,Stage,CloseDate"
```

## Ключевые EntitySets

| EntitySet | Что содержит |
|-----------|-------------|
| `ICounterparties` | Контрагенты (базовый) |
| `ICompanies` | Организации |
| `IPersons` | Физические лица |
| `IContacts` | Контактные лица |
| `IPartnerDeals` | Сделки |
