"""Прогноз срыва поручений на контроле: группы риска с объяснением причин.

Вход — ответ mcpOGV list_controlled_action_items (объект с items) или список поручений; необязательно — досье
get_control_dossier по части поручений (--dossiers: один объект, список или объект с dossiers) для признаков
«продления», «просрочены подпоручения», «нет движения».

Группы: «просрочено», «ждёт приёмки» (исполнено, решение за контролёром), «сорвётся», «под угрозой», «в норме»,
«без срока». Балл риска складывается из прозрачных признаков, у каждого — фраза-причина.

Запуск:  python risk_score.py РЕЕСТР.json|- [--dossiers ДОСЬЕ.json] [--today ГГГГ-ММ-ДД] [--format json|md] [--debug]
Коды выхода: 0 — успех, 2 — ошибка входных данных, 1 — непредвиденная ошибка.
"""

import argparse
import json
import re
import sys
import traceback
from datetime import date, datetime
from pathlib import Path

MAX_BYTES = 30_000_000
MAX_ITEMS = 10000
HIGH = 3
MEDIUM = 1
LOAD_WINDOW_DAYS = 7
LOAD_THRESHOLD = 5
STALE_DAYS = 14
GROUPS = [
    ("overdue", "просрочено"),
    ("on_acceptance", "ждёт приёмки"),
    ("high", "сорвётся"),
    ("medium", "под угрозой"),
    ("normal", "в норме"),
    ("no_deadline", "без срока"),
]
DATE_RU = re.compile(r"^\s*(\d{1,2})\.(\d{1,2})\.(\d{4})\s*$")
ACCEPTANCE_STATES = {"на приёмке у контролёра", "oncontrol"}
DISCLAIMER = ("Прогноз — эвристика по данным RX: он подсказывает, кому позвонить первым, а не оценивает работу "
              "исполнителя. Проверьте досье перед выводами.")


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


def extract_items(data):
    if isinstance(data, dict):
        if not isinstance(data.get("items"), list):
            raise InputError("ожидался ответ list_controlled_action_items (поле items) или список поручений")
        meta = {k: data.get(k) for k in ("total", "overdue_total", "on_acceptance_total", "state", "due") if k in data}
        data = data["items"]
    else:
        meta = {}
    if not isinstance(data, list):
        raise InputError("ожидался список поручений")
    items = [i for i in data if isinstance(i, dict) and i.get("id") is not None]
    if len(items) > MAX_ITEMS:
        raise InputError(f"слишком много поручений: {len(items)} (допустимо не более {MAX_ITEMS})")
    return items, meta


def extract_dossiers(data) -> dict:
    if data is None:
        return {}
    if isinstance(data, dict) and isinstance(data.get("dossiers"), list):
        data = data["dossiers"]
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        raise InputError("--dossiers: ожидалось досье get_control_dossier, список досье или объект с dossiers")
    found = {}
    for d in data:
        if isinstance(d, dict) and isinstance(d.get("action_item"), dict) and d["action_item"].get("id") is not None:
            found[d["action_item"]["id"]] = d
    return found


def _last_activity(dossier) -> date | None:
    days = []
    for c in dossier.get("correspondence") or []:
        days.append(parse_day(c.get("created")))
    for h in dossier.get("deadline_history") or []:
        days.append(parse_day(h.get("requested")))
    for a in dossier.get("acceptance") or []:
        days.append(parse_day(a.get("created")))
    for n in dossier.get("tree") or []:
        days.append(parse_day(n.get("actual_date")))
        if (n.get("level") or 0) > 0:
            days.append(parse_day(n.get("created")))
    days = [d for d in days if d]
    return max(days) if days else None


def assess(item, today: date, dossier=None, load=0) -> dict:
    deadline = parse_day(item.get("deadline"))
    created = parse_day(item.get("created"))
    state = str(item.get("execution_state_label") or item.get("execution_state") or "").strip().lower()
    reasons, score = [], 0
    in_work = (item.get("status") in (None, "InProcess"))
    left = (deadline - today).days if deadline else None

    def add(points, text):
        nonlocal score
        score += points
        reasons.append(text)

    if not in_work:
        group = "normal"
        reasons.append("Поручение не в работе (завершено или прекращено).")
    elif state in ACCEPTANCE_STATES:
        group = "on_acceptance"
        reasons.append("Исполнено и ждёт приёмки — решение за контролёром.")
    elif deadline is None:
        group = "no_deadline"
        reasons.append("У поручения нет срока — проверьте, нужен ли контроль по сроку.")
    elif left < 0:
        group = "overdue"
        reasons.append(f"Срок нарушен на {-left} дн.")
    else:
        if left <= 1:
            add(2, "Срок сегодня." if left == 0 else "Срок завтра.")
        elif left <= 3:
            add(1, f"До срока {left} дн.")
        if created and deadline > created:
            share = (today - created).days / (deadline - created).days
            if share >= 0.8:
                add(1, f"Прошло {round(share * 100)} % срока.")
        if load >= LOAD_THRESHOLD:
            add(1, f"У исполнителя {load} поручений на контроле со сроком в ближайшие {LOAD_WINDOW_DAYS} дн.")
        if dossier:
            s = dossier.get("summary") or {}
            accepted = s.get("extensions_accepted") or 0
            if accepted >= 2:
                add(2, f"Срок уже переносился {accepted} раза.")
            elif accepted == 1:
                add(1, "Срок уже переносился.")
            if s.get("extensions_pending"):
                add(1, "Есть запрос на продление, который ещё не рассмотрен.")
            late_reports = [r for r in dossier.get("report_requests") or [] if r.get("days_overdue")]
            if late_reports:
                add(1, f"Запрошенный отчёт не представлен (просрочен на {max(r['days_overdue'] for r in late_reports)} дн.).")
            if s.get("nodes_overdue"):
                add(2, f"Просрочены подпоручения: {s['nodes_overdue']}.")
            last = _last_activity(dossier)
            if last is None or (today - last).days >= STALE_DAYS:
                if created and (today - created).days >= STALE_DAYS:
                    add(1, "Нет движения больше двух недель (отчётов, подпоручений, переписки)." if last
                        else "Нет следов движения (отчётов, подпоручений, переписки).")
        group = "high" if score >= HIGH else ("medium" if score >= MEDIUM else "normal")
    return {
        "id": item.get("id"), "subject": item.get("subject"), "url": item.get("url"), "assignee": item.get("assignee"),
        "supervisor": item.get("supervisor"), "deadline": deadline.isoformat() if deadline else None,
        "days_left": left if left is not None and left >= 0 else None,
        "days_overdue": -left if left is not None and left < 0 else None,
        "group": group, "score": score, "reasons": [r for r in reasons if r], "dossier_used": dossier is not None,
    }


def score_all(data, today: date, dossiers=None) -> dict:
    items, meta = extract_items(data)
    dossiers = extract_dossiers(dossiers)
    load = {}
    for i in items:
        d = parse_day(i.get("deadline"))
        if d and 0 <= (d - today).days <= LOAD_WINDOW_DAYS and i.get("assignee"):
            load[i["assignee"]] = load.get(i["assignee"], 0) + 1
    results = [assess(i, today, dossiers.get(i.get("id")), load.get(i.get("assignee"), 0)) for i in items]
    order = {key: n for n, (key, _) in enumerate(GROUPS)}
    results.sort(key=lambda r: (order[r["group"]], -(r["days_overdue"] or 0), -r["score"],
                                r["days_left"] if r["days_left"] is not None else 10**6))
    groups = [{"key": k, "label": label, "count": sum(1 for r in results if r["group"] == k)} for k, label in GROUPS]
    people = {}
    for r in results:
        who = r["assignee"] or "Исполнитель не указан"
        p = people.setdefault(who, {"assignee": who, "total": 0, "overdue": 0, "high": 0, "medium": 0})
        p["total"] += 1
        if r["group"] in ("overdue", "high", "medium"):
            p[r["group"]] += 1
    bottlenecks = sorted((p for p in people.values() if p["overdue"] or p["high"]),
                         key=lambda p: (-p["overdue"], -p["high"], -p["medium"], p["assignee"]))
    notes = []
    if meta.get("total") is not None and meta["total"] > len(items):
        notes.append(f"В реестре {meta['total']} поручений, передано {len(items)} — прогноз неполный; увеличьте limit.")
    if dossiers and len(dossiers) < len(items):
        notes.append(f"Досье передано для {len(dossiers)} из {len(items)} поручений — у остальных оценка только по срокам.")
    return {"today": today.isoformat(), "groups": groups, "items": results, "bottlenecks": bottlenecks,
            "source": meta, "notes": notes, "disclaimer": DISCLAIMER}


def _cell(value):
    return ("—" if value in (None, "") else str(value)).replace("|", "\\|").replace("\n", " ")


def _link(r):
    title = f"№ {r['id']}"
    return f"[{title}]({r['url']})" if r.get("url") else title


def _ru(iso):
    d = parse_day(iso)
    return d.strftime("%d.%m.%Y") if d else "—"


def to_markdown(result) -> str:
    counts = {g["key"]: g["count"] for g in result["groups"]}
    out = ["# Контроль исполнения: прогноз срыва", "",
           f"На {_ru(result['today'])}: просрочено **{counts['overdue']}**, ждёт приёмки **{counts['on_acceptance']}**, "
           f"сорвётся **{counts['high']}**, под угрозой **{counts['medium']}**, в норме **{counts['normal']}**"
           + (f", без срока **{counts['no_deadline']}**" if counts["no_deadline"] else "") + ".", ""]
    for n in result["notes"]:
        out += [f"> {n}", ""]
    for key, label in GROUPS:
        rows = [r for r in result["items"] if r["group"] == key]
        if not rows or key == "normal":
            continue
        out += [f"## {label.capitalize()} ({len(rows)})", "", "| Поручение | Исполнитель | Срок | Почему |", "|---|---|---|---|"]
        for r in rows:
            out.append(f"| {_cell(_link(r))} {_cell(r['subject'])} | {_cell(r['assignee'])} | {_ru(r['deadline'])} | "
                       f"{_cell(' '.join(r['reasons']))} |")
        out.append("")
    if result["bottlenecks"]:
        out += ["## Узкие места", "", "| Исполнитель | На контроле | Просрочено | Сорвётся | Под угрозой |", "|---|---|---|---|---|"]
        for p in result["bottlenecks"]:
            out.append(f"| {_cell(p['assignee'])} | {p['total']} | {p['overdue']} | {p['high']} | {p['medium']} |")
        out.append("")
    out.append(f"> {result['disclaimer']}")
    return "\n".join(out) + "\n"


# --- ввод-вывод ---

def decode_json(data: bytes, label: str):
    if len(data) > MAX_BYTES:
        raise InputError(f"{label}: файл слишком большой")
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode("cp1251", errors="replace")
    if not text.strip():
        raise InputError(f"{label}: данные пустые")
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise InputError(f"{label}: это не JSON: {exc.msg} (строка {exc.lineno})") from None


def read_json(path: str, label: str):
    if path == "-":
        return decode_json(sys.stdin.buffer.read(), label)
    p = Path(path)
    if not p.is_file():
        raise InputError(f"{label}: файл не найден: {path}")
    try:
        return decode_json(p.read_bytes(), label)
    except OSError as exc:
        raise InputError(f"{label}: не удалось прочитать {path}: {exc}") from None


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
    parser = _Parser(description="Прогноз срыва поручений на контроле.")
    parser.add_argument("input", help="ответ list_controlled_action_items (JSON) или «-»")
    parser.add_argument("--dossiers", help="досье get_control_dossier (JSON: одно, список или объект с dossiers)")
    parser.add_argument("--today", help="дата расчёта ГГГГ-ММ-ДД (по умолчанию — сегодня)")
    parser.add_argument("--format", choices=["json", "md"], default="json", help="формат вывода")
    parser.add_argument("--debug", action="store_true", help="выводить трассировку при ошибке")
    args = parser.parse_args(argv)
    try:
        if args.dossiers == "-" and args.input == "-":
            raise InputError("stdin можно использовать только для одного из входов")
        today = date.today()
        if args.today:
            today = parse_day(args.today)
            if today is None:
                raise InputError(f"--today: ожидалась дата ГГГГ-ММ-ДД, получено «{args.today}»")
        dossiers = read_json(args.dossiers, "--dossiers") if args.dossiers else None
        result = score_all(read_json(args.input, "реестр"), today, dossiers)
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
