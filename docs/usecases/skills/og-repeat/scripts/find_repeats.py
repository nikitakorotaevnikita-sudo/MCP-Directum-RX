"""Повторные обращения: у одного заявителя — более ранние обращения по тем же вопросам классификатора.

Вход — JSON из mcpOGV get_citizen_request_status(applicant=…) (объект с items) или список обращений. Для каждого
обращения скрипт находит предыдущие обращения того же заявителя в периоде повторности, у которых совпадает хотя бы
один вопрос (по коду ТОТК, а без кода — по названию вопроса), сверяет с флажком «Повторное» в RX и отмечает случаи,
когда по тому же вопросу уже неоднократно давались ответы (повод проверить основания ст. 11 59-ФЗ).

Запуск:  python find_repeats.py ФАЙЛ|- [--period-days 180] [--answered-threshold 2] [--format json|md] [--debug]
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
MAX_REQUESTS = 5000
DEFAULT_PERIOD_DAYS = 180
DEFAULT_ANSWERED_THRESHOLD = 2
DISCLAIMER = ("Совпадение вопросов — повод проверить повторность, а не вывод. Прекратить переписку по ст. 11 "
              "59-ФЗ может только руководитель (уполномоченное лицо), если в обращении нет новых доводов и "
              "ранее давались ответы по существу; заявитель уведомляется.")
DATE_RU = re.compile(r"^\s*(\d{1,2})\.(\d{1,2})\.(\d{4})\s*$")
WS_RE = re.compile(r"\s+")
ANSWERED = {"исполнено", "не требует исполнения", "executed", "withoutexecut"}


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


def norm(text) -> str:
    return WS_RE.sub(" ", str(text or "").lower().replace("ё", "е")).strip(" .,;")


def question_keys(req) -> dict:
    """Ключ вопроса → название. Ключ — код ТОТК, а без кода — нормализованное название."""
    keys = {}
    for q in req.get("questions") or []:
        if not isinstance(q, dict):
            continue
        name = str(q.get("question") or "").strip()
        code = str(q.get("code") or "").strip()
        if code:
            keys["code:" + code] = name or code
        elif name:
            keys["name:" + norm(name)] = name
    return keys


def is_answered(req) -> bool:
    state = norm(req.get("execution_state"))
    return bool(req.get("answered") or req.get("response_date") or req.get("answer_letter") or state in ANSWERED)


def extract_requests(data) -> list:
    if isinstance(data, dict):
        if isinstance(data.get("items"), list):
            data = data["items"]
        else:
            raise InputError("ожидался ответ get_citizen_request_status (поле items) или список обращений")
    if not isinstance(data, list):
        raise InputError("ожидался список обращений")
    requests = [r for r in data if isinstance(r, dict)]
    if not requests:
        raise InputError("в данных нет ни одного обращения")
    if len(requests) > MAX_REQUESTS:
        raise InputError(f"слишком много обращений: {len(requests)} (допустимо не более {MAX_REQUESTS})")
    return requests


def _day(req):
    return parse_day(req.get("registration_date")) or parse_day(req.get("request_date"))


def _brief(req):
    letter = req.get("answer_letter") if isinstance(req.get("answer_letter"), dict) else None
    day = _day(req)
    return {"id": req.get("id"), "registration_number": req.get("registration_number"),
            "date": day.isoformat() if day else None, "url": req.get("url"), "answered": is_answered(req),
            "answer_letter": {"registration_number": letter.get("registration_number"), "url": letter.get("url")}
            if letter else None}


def find(data, period_days: int = DEFAULT_PERIOD_DAYS, answered_threshold: int = DEFAULT_ANSWERED_THRESHOLD) -> dict:
    requests = extract_requests(data)
    by_applicant = {}
    no_applicant = 0
    for r in requests:
        key = norm(r.get("applicant"))
        if not key:
            no_applicant += 1
            continue
        by_applicant.setdefault(key, []).append(r)

    results = []
    for key, items in by_applicant.items():
        # Сначала более ранние; без даты — в конец, сравнивать их по периоду нельзя.
        items.sort(key=lambda r: (_day(r) is None, _day(r) or date.max, str(r.get("id"))))
        days = [_day(r) for r in items]
        keys = [question_keys(r) for r in items]
        for idx, req in enumerate(items):
            day = days[idx]
            qkeys = keys[idx]
            earlier = []
            for pidx in range(idx):
                prev, pday = items[pidx], days[pidx]
                if day is None or pday is None or not qkeys:
                    continue
                gap = (day - pday).days
                if gap < 0 or gap > period_days:
                    continue
                shared = sorted(set(qkeys) & set(keys[pidx]))
                if shared:
                    earlier.append((prev, shared, gap))
            shared_questions = []
            for qk in sorted({k for _, shared, _ in earlier for k in shared}):
                prevs = [p for p, shared, _ in earlier if qk in shared]
                answered = [p for p in prevs if is_answered(p)]
                shared_questions.append({
                    "key": qk, "question": qkeys[qk], "code": qk[5:] if qk.startswith("code:") else None,
                    "earlier_requests": len(prevs), "earlier_answered": len(answered),
                    "article_11_check": len(answered) >= answered_threshold,
                })
            computed = bool(earlier)
            flagged = bool(req.get("is_repeated"))
            notes = []
            if computed and not flagged:
                notes.append("Есть предыдущее обращение по тому же вопросу, но в RX не отмечено «Повторное».")
            if flagged and not computed:
                notes.append("В RX отмечено «Повторное», но предыдущее обращение по тем же вопросам в данных за "
                             f"{period_days} дн. не найдено (возможно, оно раньше периода или не попало в выборку).")
            if not qkeys:
                notes.append("У обращения нет вопросов — повторность по вопросам не проверить.")
            if day is None:
                notes.append("Нет даты регистрации и поступления — повторность по периоду не проверить.")
            results.append({
                **_brief(req), "applicant": req.get("applicant"), "is_repeated_in_rx": flagged,
                "repeat_found": computed, "flag_mismatch": computed != flagged,
                "earlier": [dict(_brief(p), shared_questions=[qkeys[k] for k in shared], days_before=gap)
                            for p, shared, gap in earlier],
                "shared_questions": shared_questions,
                "article_11_check": any(q["article_11_check"] for q in shared_questions),
                "notes": notes,
            })
    summary = {
        "requests": len(requests), "applicants": len(by_applicant), "without_applicant": no_applicant,
        "repeats_found": sum(1 for r in results if r["repeat_found"]),
        "flag_mismatches": sum(1 for r in results if r["flag_mismatch"]),
        "article_11_check": sum(1 for r in results if r["article_11_check"]),
    }
    return {"period_days": period_days, "answered_threshold": answered_threshold, "summary": summary,
            "requests": results, "disclaimer": DISCLAIMER}


def _cell(value):
    return ("—" if value in (None, "") else str(value)).replace("|", "\\|").replace("\n", " ")


def _link(item):
    num = item.get("registration_number") or f"id {item.get('id')}"
    return f"[{num}]({item['url']})" if item.get("url") else num


def _ru(iso):
    day = parse_day(iso)
    return day.strftime("%d.%m.%Y") if day else "—"


def to_markdown(result: dict) -> str:
    s = result["summary"]
    out = ["# Повторные обращения", "",
           f"Обращений: **{s['requests']}**, заявителей: **{s['applicants']}**, найдено повторных: "
           f"**{s['repeats_found']}**, расхождений с флажком «Повторное»: **{s['flag_mismatches']}**, "
           f"проверить по ст. 11: **{s['article_11_check']}**. Период повторности — {result['period_days']} дн.", ""]
    if s["without_applicant"]:
        out += [f"Без заявителя (не сравнивались): {s['without_applicant']}.", ""]
    rows = [r for r in result["requests"] if r["repeat_found"] or r["flag_mismatch"] or r["notes"]]
    if rows:
        out += ["| Обращение | Дата | «Повторное» в RX | Предыдущие по тем же вопросам | Ст. 11 | Примечание |",
                "|---|---|---|---|---|---|"]
        for r in rows:
            prev = "; ".join(f"{_link(p)} от {_ru(p['date'])}" + (" (отвечено)" if p["answered"] else "")
                             for p in r["earlier"]) or "—"
            out.append(f"| {_cell(_link(r))} | {_ru(r['date'])} | {'да' if r['is_repeated_in_rx'] else 'нет'} | "
                       f"{_cell(prev)} | {'проверить' if r['article_11_check'] else '—'} | {_cell(' '.join(r['notes']))} |")
        out.append("")
    else:
        out += ["Повторных обращений по совпадающим вопросам не найдено.", ""]
    out.append(f"> {result['disclaimer']}")
    return "\n".join(out) + "\n"


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
    parser = _Parser(description="Поиск повторных обращений заявителя по совпадающим вопросам.")
    parser.add_argument("input", help="JSON-файл из mcpOGV или «-» для чтения из stdin")
    parser.add_argument("--period-days", type=int, default=DEFAULT_PERIOD_DAYS, help="период повторности, дней (1–3650)")
    parser.add_argument("--answered-threshold", type=int, default=DEFAULT_ANSWERED_THRESHOLD,
                        help="сколько ранее отвеченных обращений по вопросу дают повод проверить ст. 11 (1–20)")
    parser.add_argument("--format", choices=["json", "md"], default="json", help="формат вывода")
    parser.add_argument("--debug", action="store_true", help="выводить трассировку при ошибке")
    args = parser.parse_args(argv)
    try:
        if not 1 <= args.period_days <= 3650:
            raise InputError("--period-days: допустимо от 1 до 3650")
        if not 1 <= args.answered_threshold <= 20:
            raise InputError("--answered-threshold: допустимо от 1 до 20")
        result = find(read_input(args.input), args.period_days, args.answered_threshold)
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
