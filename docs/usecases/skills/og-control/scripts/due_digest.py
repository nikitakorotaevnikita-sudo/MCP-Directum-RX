"""Сводка контроля сроков обращений: группы по сроку, разрез по исполнителям, самые просроченные.

Вход — JSON из mcpOGV list_citizen_requests_due (объект с items) или список обращений из get_citizen_request_status.
Считаются только обращения «в работе» (без ответа, не перенаправленные и не прекращённые). Скрипт пересчитывает
дни до срока от даты «сегодня» и может сразу выдать спецификацию для навыка excel-report.

Группы: просрочено больше 10 дней, просрочено 1–10 дней, срок сегодня, 1–3 дня, 4–7 дней, позже 7 дней, без срока.

Запуск:  python due_digest.py ФАЙЛ|- [--today ГГГГ-ММ-ДД] [--top 10] [--format json|md|xlsx-spec] [--debug]
Коды выхода: 0 — успех, 2 — ошибка входных данных, 1 — непредвиденная ошибка.
"""

import argparse
import json
import re
import sys
import traceback
from datetime import date, datetime
from pathlib import Path

MAX_BYTES = 20_000_000
MAX_REQUESTS = 10000
DEFAULT_TOP = 10
BUCKETS = [
    ("overdue_10_plus", "просрочено больше 10 дней"),
    ("overdue_1_10", "просрочено на 1–10 дней"),
    ("today", "срок сегодня"),
    ("in_1_3", "срок через 1–3 дня"),
    ("in_4_7", "срок через 4–7 дней"),
    ("later", "срок позже 7 дней"),
    ("no_deadline", "без срока"),
]
ABORTED = {"прекращено", "aborted"}
ANSWERED = {"исполнено", "не требует исполнения", "executed", "withoutexecut"}
DATE_RU = re.compile(r"^\s*(\d{1,2})\.(\d{1,2})\.(\d{4})\s*$")
NO_ASSIGNEE = "Исполнитель не назначен"


class InputError(Exception):
    """Ошибка входных данных (код выхода 2)."""


def parse_day(value):
    if not isinstance(value, str) or not value.strip():
        return None
    m = DATE_RU.match(value)
    try:
        if m:
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        text = value.strip().replace("Z", "+00:00")
        return date.fromisoformat(text) if len(text) == 10 else datetime.fromisoformat(text).date()
    except ValueError:
        return None


def in_work(req) -> bool:
    state = str(req.get("execution_state") or "").strip().lower()
    if state in ABORTED or state in ANSWERED or req.get("transferred_to"):
        return False
    return not (req.get("answered") or req.get("response_date") or req.get("answer_letter"))


def bucket_of(days_left):
    if days_left is None:
        return "no_deadline"
    if days_left < -10:
        return "overdue_10_plus"
    if days_left < 0:
        return "overdue_1_10"
    if days_left == 0:
        return "today"
    if days_left <= 3:
        return "in_1_3"
    if days_left <= 7:
        return "in_4_7"
    return "later"


def extract(data):
    meta = {}
    if isinstance(data, dict):
        if not isinstance(data.get("items"), list):
            raise InputError("ожидался ответ list_citizen_requests_due (поле items) или список обращений")
        meta = {k: data.get(k) for k in ("total", "overdue_total", "due_soon_total", "within_days") if k in data}
        data = data["items"]
    if not isinstance(data, list):
        raise InputError("ожидался список обращений")
    items = [r for r in data if isinstance(r, dict)]
    if len(items) > MAX_REQUESTS:
        raise InputError(f"слишком много обращений: {len(items)} (допустимо не более {MAX_REQUESTS})")
    return items, meta


def digest(data, today: date, top: int = DEFAULT_TOP) -> dict:
    items, meta = extract(data)
    rows = []
    skipped = 0
    for req in items:
        if not in_work(req):
            skipped += 1
            continue
        deadline = parse_day(req.get("deadline"))
        left = (deadline - today).days if deadline else None
        registered = parse_day(req.get("registration_date"))
        questions = [q.get("question") for q in req.get("questions") or [] if isinstance(q, dict) and q.get("question")]
        rows.append({
            "id": req.get("id"), "registration_number": req.get("registration_number"), "url": req.get("url"),
            "registration_date": registered.isoformat() if registered else None,
            "deadline": deadline.isoformat() if deadline else None, "days_left": left,
            "days_overdue": -left if left is not None and left < 0 else 0,
            "assignee": (req.get("assignee") or "").strip() or NO_ASSIGNEE,
            "prolonged": bool(req.get("prolonged") or req.get("prolongations")),
            "questions": "; ".join(questions), "bucket": bucket_of(left),
        })
    rows.sort(key=lambda r: (r["days_left"] is None, r["days_left"] if r["days_left"] is not None else 0,
                             str(r["registration_number"] or "")))
    buckets = [{"key": key, "label": label, "count": sum(1 for r in rows if r["bucket"] == key)} for key, label in BUCKETS]
    by_assignee = {}
    for r in rows:
        a = by_assignee.setdefault(r["assignee"], {"assignee": r["assignee"], "in_work": 0, "overdue": 0,
                                                   "due_7_days": 0, "max_days_overdue": 0})
        a["in_work"] += 1
        if r["days_overdue"]:
            a["overdue"] += 1
            a["max_days_overdue"] = max(a["max_days_overdue"], r["days_overdue"])
        elif r["days_left"] is not None and r["days_left"] <= 7:
            a["due_7_days"] += 1
    assignees = sorted(by_assignee.values(), key=lambda a: (-a["overdue"], -a["max_days_overdue"], -a["due_7_days"],
                                                            a["assignee"]))
    summary = {
        "in_work": len(rows), "skipped_not_in_work": skipped,
        "overdue": sum(1 for r in rows if r["days_overdue"]),
        "due_today": sum(1 for r in rows if r["days_left"] == 0),
        "due_7_days": sum(1 for r in rows if r["days_left"] is not None and 0 <= r["days_left"] <= 7),
        "no_deadline": sum(1 for r in rows if r["days_left"] is None),
        "prolonged": sum(1 for r in rows if r["prolonged"]),
    }
    notes = []
    if meta.get("total") is not None and meta["total"] > len(items):
        notes.append(f"Инструмент вернул {len(items)} из {meta['total']} обращений в окне — сводка неполная; "
                     "увеличьте limit или сузьте по исполнителю.")
    return {"today": today.isoformat(), "summary": summary, "buckets": buckets, "by_assignee": assignees,
            "most_overdue": [r for r in rows if r["days_overdue"]][:top],
            "rows": rows, "source_totals": meta, "notes": notes}


def _ru(iso):
    day = parse_day(iso)
    return day.strftime("%d.%m.%Y") if day else "—"


def _cell(value):
    return ("—" if value in (None, "") else str(value)).replace("|", "\\|").replace("\n", " ")


def _link(r):
    num = r.get("registration_number") or f"id {r.get('id')}"
    return f"[{num}]({r['url']})" if r.get("url") else num


def to_markdown(result: dict) -> str:
    s = result["summary"]
    out = ["# Контроль сроков обращений", "",
           f"На {_ru(result['today'])}: в работе **{s['in_work']}**, просрочено **{s['overdue']}**, срок сегодня "
           f"**{s['due_today']}**, срок в ближайшие 7 дней (включая сегодня) **{s['due_7_days']}**, без срока **{s['no_deadline']}**.", ""]
    for n in result["notes"]:
        out += [f"> {n}", ""]
    out += ["| Группа | Обращений |", "|---|---|"]
    out += [f"| {b['label']} | {b['count']} |" for b in result["buckets"] if b["count"]]
    out += ["", "## По исполнителям", "", "| Исполнитель | В работе | Просрочено | Макс. дней просрочки | Срок ≤ 7 дней |",
            "|---|---|---|---|---|"]
    for a in result["by_assignee"]:
        out.append(f"| {_cell(a['assignee'])} | {a['in_work']} | {a['overdue']} | {a['max_days_overdue'] or '—'} | "
                   f"{a['due_7_days']} |")
    if result["most_overdue"]:
        out += ["", "## Самые просроченные", "", "| Обращение | Срок | Дней просрочки | Исполнитель | Вопросы |",
                "|---|---|---|---|---|"]
        for r in result["most_overdue"]:
            out.append(f"| {_cell(_link(r))} | {_ru(r['deadline'])} | {r['days_overdue']} | {_cell(r['assignee'])} | "
                       f"{_cell(r['questions'])} |")
    return "\n".join(out) + "\n"


def to_xlsx_spec(result: dict) -> dict:
    """Спецификация для excel-report/scripts/build_xlsx.py."""
    day = _ru(result["today"])
    return {
        "properties": {"title": f"Контроль сроков обращений на {day}"},
        "sheets": [
            {
                "name": "Обращения в работе",
                "title": f"Обращения граждан в работе на {day}",
                "columns": [
                    {"title": "Рег. №", "key": "registration_number", "type": "text"},
                    {"title": "Дата регистрации", "key": "registration_date", "type": "date"},
                    {"title": "Срок", "key": "deadline", "type": "date"},
                    {"title": "Дней до срока", "key": "days_left", "type": "integer"},
                    {"title": "Дней просрочки", "key": "days_overdue", "type": "integer"},
                    {"title": "Исполнитель", "key": "assignee", "type": "text", "width": 30},
                    {"title": "Вопросы", "key": "questions", "type": "text", "width": 60},
                    {"title": "Ссылка", "key": "url", "type": "url"},
                ],
                "rows": [{k: r[k] for k in ("registration_number", "registration_date", "deadline", "days_left",
                                            "days_overdue", "assignee", "questions", "url")} for r in result["rows"]],
                "totals": {"label": "Итого обращений", "columns": ["registration_number"], "func": "count"},
                "highlight": [{"column": "days_overdue", "op": ">", "value": 0, "color": "red", "row": True}],
            },
            {
                "name": "По исполнителям",
                "title": f"Обращения в работе по исполнителям на {day}",
                "columns": [
                    {"title": "Исполнитель", "key": "assignee", "type": "text", "width": 35},
                    {"title": "В работе", "key": "in_work", "type": "integer"},
                    {"title": "Просрочено", "key": "overdue", "type": "integer"},
                    {"title": "Макс. дней просрочки", "key": "max_days_overdue", "type": "integer"},
                    {"title": "Срок ≤ 7 дней", "key": "due_7_days", "type": "integer"},
                ],
                "rows": [dict(a) for a in result["by_assignee"]],
                "totals": {"label": "Итого", "columns": ["in_work", "overdue", "due_7_days"], "func": "sum"},
            },
        ],
    }


# --- ввод-вывод ---

def decode_json(data: bytes):
    if len(data) > MAX_BYTES:
        raise InputError(f"файл слишком большой: {len(data)} байт (допустимо не более {MAX_BYTES})")
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode("cp1251", errors="replace")
    if not text.strip():
        raise InputError("данные пустые")
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise InputError(f"это не JSON: {exc.msg} (строка {exc.lineno}, позиция {exc.colno})") from None


def read_input(path: str):
    if path == "-":
        return decode_json(sys.stdin.buffer.read())
    p = Path(path)
    if not p.is_file():
        raise InputError(f"файл не найден: {path}")
    try:
        return decode_json(p.read_bytes())
    except OSError as exc:
        raise InputError(f"не удалось прочитать файл {path}: {exc}") from None


def _write(stream, s: str):
    try:
        stream.buffer.write(s.encode("utf-8"))
        stream.buffer.flush()
    except AttributeError:
        stream.write(s)


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        _write(sys.stderr, f"Ошибка аргументов: {message}\n{self.format_usage()}")
        sys.exit(2)


def main(argv=None) -> int:
    parser = _Parser(description="Сводка контроля сроков обращений граждан.")
    parser.add_argument("input", help="JSON-файл из mcpOGV или «-» для чтения из stdin")
    parser.add_argument("--today", help="дата расчёта ГГГГ-ММ-ДД (по умолчанию — сегодня)")
    parser.add_argument("--top", type=int, default=DEFAULT_TOP, help="сколько самых просроченных показать (1–100)")
    parser.add_argument("--format", choices=["json", "md", "xlsx-spec"], default="json", help="формат вывода")
    parser.add_argument("--debug", action="store_true", help="выводить трассировку при ошибке")
    args = parser.parse_args(argv)
    try:
        today = date.today()
        if args.today:
            today = parse_day(args.today)
            if today is None:
                raise InputError(f"--today: ожидалась дата ГГГГ-ММ-ДД, получено «{args.today}»")
        if not 1 <= args.top <= 100:
            raise InputError("--top: допустимо от 1 до 100")
        result = digest(read_input(args.input), today, args.top)
        if args.format == "md":
            out = to_markdown(result)
        else:
            payload = to_xlsx_spec(result) if args.format == "xlsx-spec" else result
            out = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
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
