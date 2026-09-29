# Навыки агентов

Два переиспользуемых навыка для LLM-агента (например, в LibreChat с mcpOGV): агент берёт данные из тулов
Directum RX, собирает JSON-спецификацию и вызывает скрипт. Вёрстку, типы ячеек, ссылки и защиту от ошибок
делает скрипт.

| Навык | Когда | Скрипт | Зависимость |
|---|---|---|---|
| [`pdf-report`](pdf-report/SKILL.md) | «сделай отчёт в PDF», справка для руководителя, печать | `pdf-report/scripts/build_pdf.py` | reportlab ≥ 4.0 |
| [`excel-report`](excel-report/SKILL.md) | «выгрузи в Excel», списки для фильтрации и подсчётов | `excel-report/scripts/build_xlsx.py` | openpyxl ≥ 3.1 |

Общее для обоих:

- Вызов: `python build_xxx.py SPEC.json OUTPUT` (`SPEC` = `-` — чтение из stdin) или `build(spec, output_path)` из Python.
- Коды выхода: `0` — успех (в stdout путь к файлу), `2` — ошибка спецификации (в stderr — какое поле и что не так),
  `1` — непредвиденная ошибка.
- Строгая проверка спецификации, лимиты на объём, атомарная запись, без сети.
- Даты ISO → ДД.ММ.ГГГГ, ссылки `{"text", "url"}` и `[текст](url)`, только http/https.
- Правила: не выдумывать данные, сохранять ссылки на карточки RX, не включать лишние персональные данные граждан.

Примеры спецификаций — в `*/examples/example_spec.json`. Тесты — `tests/skills/`:

```bash
.venv/Scripts/python.exe -m pytest tests/skills -q
```

Шрифты DejaVu Sans для кириллицы в PDF лежат в `pdf-report/fonts/` (лицензия — `LICENSE-DejaVu.txt`).

## Навыки правового отдела (правовые акты)

Набор по описанию решения Directum RX 26.2 «Правовые акты органов власти». Данные о проектах и актах — из mcpOGV
(`find_legal_acts`, `get_legal_act`, `list_legal_acts_on_approval`, `get_document_text`), сведения о
законодательстве — из MCP «Гарант» (`garant_search`, `garant_document_info`, `garant_document_redactions`,
`garant_block_changed`, `garant_find_hyperlinks`, `garant_snippets`, `garant_export_*`, `garant_sutyazhnik`, …).

| Навык | Когда | Скрипт |
|---|---|---|
| [`pa-route`](pa-route/SKILL.md) | на каком этапе проект, что дальше, кто следующий, почему застрял | — |
| [`pa-legal-basis`](pa-legal-basis/SKILL.md) | действуют ли акты-основания, редакции, изменения норм, практика (ГАРАНТ) | `extract_legal_refs.py` |
| [`pa-normcontrol`](pa-normcontrol/SKILL.md) | предварительный нормоконтроль и лингвистическая проверка | `check_structure.py` |
| [`pa-anticorruption`](pa-anticorruption/SKILL.md) | предварительная АКЭ по Методике № 96 (текст Методики — из ГАРАНТа) | `flag_markers.py` |
| [`pa-orv`](pa-orv/SKILL.md) | нужна ли ОРВ, материалы к обсуждениям, черновик заключения | — |
| [`pa-compare`](pa-compare/SKILL.md) | сравнение проекта и акта, версий, акта с нормой из ГАРАНТа | `compare_texts.py` |
| [`pa-conclusions`](pa-conclusions/SKILL.md) | черновики заключений ГПУ, ведомства, нормоконтроля, ОРВ, текста задания, сопроводительного письма | шаблоны `templates/` |

Скрипты — только стандартная библиотека Python 3.10+, без сети; вход — текст (файл или `-` для stdin, UTF-8 или
cp1251), выход — JSON или `--format md`; коды выхода 0 / 2 (плохой вход) / 1. Результаты скриптов эвристические —
агент перепроверяет их по тексту. Все навыки дают предварительный анализ для юриста, а не экспертизу.

## Упаковка для загрузки на платформу

Готовые архивы лежат в `dist/`: `pdf-report.zip` / `.skill`, `excel-report.zip` / `.skill`. `SKILL.md` — в корне архива.
Пересобрать после правок:

```bash
python docs/usecases/skills/package_skills.py            # все навыки
python docs/usecases/skills/package_skills.py pdf-report # один
```

Скрипт проверяет требования платформы и **не создаёт архив**, если хоть одно не выполнено (код выхода 2):

- `SKILL.md` начинается с YAML-заголовка между строками `---` с полями `name` (строчные латинские буквы, цифры,
  дефисы, до 64 символов, совпадает с именем каталога) и `description` (до 1024 символов);
- заголовок — валидный YAML (частая ошибка: «: » внутри значения без кавычек — заключите значение в кавычки);
- в архиве есть `SKILL.md`, архив не повреждён, размер ≤ 50 МБ.

В архив не попадают `__pycache__`, `*.pyc` и служебные каталоги. Сборка воспроизводимая: одинаковые исходники дают
побайтно одинаковый архив.
