"""Шаблон скрипта навыка: читает ответ тула mcpOGV (JSON) и считает результат.

Сейчас пример считает просроченные записи из списка `items` (поле `deadline`) — замените на свою логику в check().

Запуск:  python skill_script.py ВХОД.json|- [--format json|md] [--only-overdue] [--today ГГГГ-ММ-ДД] [--debug]
Коды выхода: 0 — успех, 2 — ошибка входных данных, 1 — непредвиденная ошибка.
Только стандартная библиотека Python 3.10+ (внешние пакеты — в requirements.txt).
"""

import argparse
import json
import sys
import traceback
from datetime import date
from pathlib import Path

MAX_BYTES = 20_000_000


class InputError(Exception):
    """Ошибка входных данных (код выхода 2): понятное сообщение для агента."""


def read_json(source: str):
    """Файл или «-» (stdin). Принимает ответ тула как есть."""
    try:
        raw = sys.stdin.buffer.read() if source == "-" else Path(source).read_bytes()
    except OSError as exc:
        raise InputError(f"не удалось прочитать {source}: {exc}") from None
    if len(raw) > MAX_BYTES:
        raise InputError(f"вход больше {MAX_BYTES // 1_000_000} МБ")
    try:
        return json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InputError(f"вход — не JSON: {exc}") from None


def parse_day(value):
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return date.fromisoformat(value.strip()[:10])
    except ValueError:
        return None


def check(data, today: date, only_overdue: bool = False) -> dict:
    """Основная логика. Возвращает словарь — он же JSON-результат."""
    items = data.get("items") if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise InputError("ожидался список записей или объект с полем items")
    rows, warnings = [], []
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            raise InputError(f"items[{i}]: ожидался объект")
        deadline = parse_day(item.get("deadline"))
        if deadline is None:
            warnings.append(f"items[{i}]: нет срока или срок не распознан — пропущено")
            continue
        days_overdue = (today - deadline).days
        if only_overdue and days_overdue <= 0:
            continue
        rows.append({"id": item.get("id"), "subject": item.get("subject", ""), "url": item.get("url"),
                     "deadline": deadline.isoformat(), "days_overdue": max(days_overdue, 0)})
    rows.sort(key=lambda r: -r["days_overdue"])
    overdue = sum(1 for r in rows if r["days_overdue"] > 0)
    return {"total": len(rows), "overdue": overdue, "rows": rows, "warnings": warnings}


def to_markdown(result: dict) -> str:
    lines = [f"**Просрочено: {result['overdue']} из {result['total']}**", ""]
    if result["rows"]:
        lines += ["| Запись | Срок | Дней просрочки |", "|---|---|---|"]
        for r in result["rows"]:
            title = f"[{r['subject'] or r['id']}]({r['url']})" if r.get("url") else (r["subject"] or str(r["id"]))
            d = r["deadline"]
            lines.append(f"| {title} | {d[8:10]}.{d[5:7]}.{d[:4]} | {r['days_overdue']} |")
    for w in result["warnings"]:
        lines.append(f"- ⚠ {w}")
    return "\n".join(lines) + "\n"


class _Parser(argparse.ArgumentParser):
    def error(self, message):  # ошибка параметров — тоже код 2
        _write(sys.stderr, f"Ошибка входных данных: {message}\n")
        sys.exit(2)


def _write(stream, text: str) -> None:
    stream.buffer.write(text.encode("utf-8"))
    stream.flush()


def main(argv=None) -> int:
    parser = _Parser(description="Шаблон скрипта навыка.")
    parser.add_argument("input", help="ответ тула (JSON) или «-» для stdin")
    parser.add_argument("--format", choices=["json", "md"], default="json", help="формат вывода")
    parser.add_argument("--only-overdue", action="store_true", help="только просроченные")
    parser.add_argument("--today", help="дата отсчёта ГГГГ-ММ-ДД (для тестов)")
    parser.add_argument("--debug", action="store_true", help="трассировка при ошибке")
    args = parser.parse_args(argv)
    try:
        today = parse_day(args.today) if args.today else date.today()
        if today is None:
            raise InputError("--today: ожидалась дата ГГГГ-ММ-ДД")
        result = check(read_json(args.input), today, args.only_overdue)
        out = to_markdown(result) if args.format == "md" else json.dumps(result, ensure_ascii=False, indent=2) + "\n"
        _write(sys.stdout, out)
        return 0
    except InputError as exc:
        _write(sys.stderr, f"Ошибка входных данных: {exc}\n")
        return 2
    except Exception as exc:  # noqa: BLE001
        if args.debug:
            traceback.print_exc()
        _write(sys.stderr, f"Непредвиденная ошибка: {exc}\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
