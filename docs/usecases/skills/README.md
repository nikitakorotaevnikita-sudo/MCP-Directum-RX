# Навыки формирования отчётов

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
