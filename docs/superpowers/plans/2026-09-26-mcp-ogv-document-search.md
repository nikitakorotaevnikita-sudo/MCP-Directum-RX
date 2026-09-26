# mcpOGV: поиск документа по признакам, ссылки, текст — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** `find_documents` (несколько вариантов по известным признакам), ссылки на карточки документов во всех тулах, `get_document_text`.

**Architecture:** два новых сервиса (`document_search.py`, `document_text.py`) поверх `DirectumClient`, подключены в `DirectumServices`; тулы — в `tools/documents.py`. Ссылка на документ — веб-клиент с базовым GUID «Электронный документ».

**Спека:** `docs/superpowers/specs/2026-09-26-mcp-ogv-document-search-design.md` (все значения — оттуда).

## Global Constraints

- Только чтение Directum. Тексты для агента — по-русски. `.env` не трогать. Секреты не логировать.
- GUID базового типа документа: `030d8d67-9b94-4f0d-bcc6-691016eb70f3`.
- `find_documents`: `limit` 1–20 (по умолч. 5), пул `$top=50` на набор, ослабление: текст → период ±30 дней → без периода.
- `get_document_text`: `max_chars` 1000–50000 (по умолч. 20000), тело > 20 МБ — отказ, зависимость `pypdf`.
- Границы дней — в поясе стенда (`tz` из `MCP_UTC_OFFSET`, как у `AssignmentsService`).
- TDD: тест → падение → код → зелёный → коммит в `feature/mcp-ogv` (без push).

## Task 1: ссылки на карточки документов
- [ ] Тесты: `build_document_card_url`; `_document_url`/`search_documents`/`list_letters`/документы по контрагенту дают ссылку веб-клиента (обновить 3 старых ожидания в `tests/unit/test_action_items.py`).
- [ ] `DirectumClient.build_document_card_url`, `ELECTRONIC_DOCUMENT_CARD_GUID`; `ActionItemService._document_url` и `search_documents` через неё; правило markdown-ссылок в `INSTRUCTIONS`.
- [ ] Зелёный прогон `tests/unit`, коммит `feat(mcp): ссылки на карточки документов в веб-клиенте`.

## Task 2: `DocumentSearchService`
- [ ] Тесты `tests/unit/test_document_search.py`: токены/основы и стоп-слова; фильтр по каждому признаку; наборы по `kind`; контрагент/сотрудник → `or` по до 3 id; контрагент для `order` → `ValueError`; не найден контрагент/сотрудник → `message`; ранжирование и `match_reasons`; дедуп; ослабление условий и `relaxed`; пустые признаки → `ValueError`.
- [ ] Реализация `src/services/document_search.py`, модели `DocumentCandidate`, `DocumentSearchResult` в `schemas.py`, поле `document_search` в `DirectumServices` (+ `tz`).
- [ ] Зелёный прогон, коммит `feat(mcp): поиск документа по признакам с ранжированием`.

## Task 3: тул `find_documents`
- [ ] Тесты `tests/unit/test_mcp_tools_documents.py`: прокидывание параметров, парсинг дат, ошибки (нет признаков, плохая дата, контрагент к неподходящему виду), конверт.
- [ ] Тул в `tools/documents.py`.
- [ ] Коммит `feat(mcp): тул find_documents`.

## Task 4: `DocumentTextService` и тул `get_document_text`
- [ ] `pypdf` в `pyproject.toml`, установка в `.venv`.
- [ ] Тесты `tests/unit/test_document_text.py`: последняя версия; docx (сгенерированный zip), pdf (сгенерированный pypdf), txt utf-8/cp1251; фолбэк `PublicBody`; скан; обрезка; нет документа/версий; лимит размера. Тесты тула.
- [ ] Реализация `src/services/document_text.py`, модель `DocumentText`, поле `document_text` в `DirectumServices`, тул.
- [ ] Коммит `feat(mcp): тул get_document_text`.

## Task 5: live и документация
- [ ] Live-тесты (пропускаются без `MCP_LIVE_*`): `find_documents` по тексту, `get_document_text` для найденного документа.
- [ ] README (список тулов, `pypdf`), полный прогон, коммит; перезапуск сервера; live-проверка, когда стенд доступен.
