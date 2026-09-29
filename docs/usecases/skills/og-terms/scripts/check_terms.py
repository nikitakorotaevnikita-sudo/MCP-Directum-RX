"""Проверка сроков обращений граждан по 59-ФЗ и описанию решения Directum RX «Обращения граждан».

Вход — JSON из mcpOGV: ответ get_citizen_request_status или list_citizen_requests_due (объект с items), список
обращений или одно обращение. Скрипт пересчитывает сроки от даты «сегодня» и возвращает нарушения и предупреждения.

Проверки (календарные дни, если не сказано иное):
- регистрация — не позднее 3 дней с поступления (ч. 2 ст. 8; в RX по умолчанию 3 рабочих дня);
- исходный срок рассмотрения — не более 30 дней с регистрации (ч. 1 ст. 12);
- продление — один раз и не более чем на 30 дней, общий срок — не более 60 дней с регистрации (ч. 2 ст. 12);
- ответ дан позже срока; срок в работе нарушен или истекает в ближайшие 7 дней;
- у обращения в работе нет срока; вопросы без кода классификатора; рассмотрено, но нет результата по вопросу.

Запуск:  python check_terms.py ФАЙЛ|- [--today ГГГГ-ММ-ДД] [--soon-days 7] [--format json|md] [--debug]
Коды выхода: 0 — успех, 2 — ошибка входных данных, 1 — непредвиденная ошибка.
"""

import argparse
import json
import re
import sys
import traceback
from datetime import date, datetime, timedelta
from pathlib import Path

MAX_BYTES = 20_000_000
MAX_REQUESTS = 5000
REGISTRATION_DAYS = 3
REVIEW_DAYS = 30
PROLONGATION_DAYS = 30
MAX_TOTAL_DAYS = 60
DEFAULT_SOON_DAYS = 7
DISCLAIMER = ("Проверка по общим нормам 59-ФЗ в календарных днях (регистрация — также в рабочих днях без учёта "
              "праздников). Особые сроки (установленные законом для отдельных категорий обращений, сокращённые сроки "
              "по регламенту органа, отметки «Оперативно» и «Срочно») скрипт не знает — сверяйтесь с регламентом.")

SEVERITY_ORDER = {"violation": 0, "warning": 1, "info": 2}
ABORTED = {"прекращено", "aborted"}
ANSWERED = {"исполнено", "не требует исполнения", "executed", "withoutexecut"}
DATE_RU = re.compile(r"^\s*(\d{1,2})\.(\d{1,2})\.(\d{4})\s*$")


class InputError(Exception):
    """Ошибка входных данных (код выхода 2)."""


def parse_day(value):
    """ISO-дата, ISO-дата-время или ДД.ММ.ГГГГ → date; пусто или мусор → None."""
    if value is None or value == "":
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if not isinstance(value, str):
        return None
    m = DATE_RU.match(value)
    try:
        if m:
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        text = value.strip().replace("Z", "+00:00")
        if len(text) == 10:
            return date.fromisoformat(text)
        return datetime.fromisoformat(text).date()
    except ValueError:
        return None


def working_days_between(start: date, end: date) -> int:
    """Рабочие дни (пн–пт) после start по end включительно; праздники не учитываются."""
    if end <= start:
        return 0
    days = (end - start).days
    weeks, rest = divmod(days, 7)
    count = weeks * 5
    for i in range(1, rest + 1):
        if (start + timedelta(days=weeks * 7 + i)).weekday() < 5:
            count += 1
    return count


def fmt(day):
    return day.strftime("%d.%m.%Y") if day else None


def _issue(issues, code, severity, message):
    issues.append({"code": code, "severity": severity, "message": message})


def _state(req):
    state = str(req.get("execution_state") or "").strip().lower()
    if state in ABORTED:
        return "прекращено"
    if req.get("transferred_to"):
        return "перенаправлено"
    if req.get("answered") or req.get("response_date") or req.get("answer_letter") or state in ANSWERED:
        return "рассмотрено"
    return "в работе"


def check_request(req: dict, today: date, soon_days: int = DEFAULT_SOON_DAYS) -> dict:
    issues = []
    received = parse_day(req.get("request_date"))
    registered = parse_day(req.get("registration_date"))
    deadline = parse_day(req.get("deadline"))
    initial = parse_day(req.get("initial_deadline")) or deadline
    response = parse_day(req.get("response_date"))
    letter = req.get("answer_letter") if isinstance(req.get("answer_letter"), dict) else None
    if response is None and letter:
        response = parse_day(letter.get("registration_date"))
    prolongations = [p for p in (req.get("prolongations") or []) if isinstance(p, dict)]
    status = _state(req)
    metrics = {"registration_lag_days": None, "registration_lag_working_days": None, "review_term_days": None,
               "total_term_days": None, "prolongation_days": None, "prolongations": len(prolongations),
               "days_left": None, "days_overdue": None, "answered_late_days": None}

    # Регистрация.
    if registered is None:
        if received and status == "в работе":
            lag = working_days_between(received, today)
            if lag > REGISTRATION_DAYS:
                _issue(issues, "not_registered", "violation",
                       f"Не зарегистрировано: поступило {fmt(received)}, прошло {lag} раб. дн. "
                       f"(регистрация — в течение {REGISTRATION_DAYS} дней).")
            else:
                _issue(issues, "not_registered", "info", f"Ещё не зарегистрировано (поступило {fmt(received)}).")
    elif received:
        lag = (registered - received).days
        wlag = working_days_between(received, registered)
        metrics["registration_lag_days"] = lag
        metrics["registration_lag_working_days"] = wlag
        if lag < 0:
            _issue(issues, "registration_before_receipt", "warning",
                   f"Дата регистрации {fmt(registered)} раньше даты поступления {fmt(received)} — проверьте карточку.")
        elif wlag > REGISTRATION_DAYS:
            _issue(issues, "registration_late", "violation",
                   f"Зарегистрировано через {wlag} раб. дн. ({lag} календ.) после поступления — больше "
                   f"{REGISTRATION_DAYS} дней.")
        elif lag > REGISTRATION_DAYS:
            _issue(issues, "registration_late_calendar", "warning",
                   f"Зарегистрировано через {lag} календ. дн. (рабочих — {wlag}): по 59-ФЗ срок 3 дня, в RX по "
                   "умолчанию считаются рабочие дни — сверьте с регламентом.")

    # Сроки рассмотрения.
    if registered and initial:
        term = (initial - registered).days
        metrics["review_term_days"] = term
        if term > REVIEW_DAYS:
            _issue(issues, "initial_deadline_over_30", "violation",
                   f"Исходный срок {fmt(initial)} — {term} дн. с регистрации, больше {REVIEW_DAYS}.")
        elif term < 0:
            _issue(issues, "deadline_before_registration", "warning",
                   f"Срок {fmt(initial)} раньше даты регистрации {fmt(registered)} — проверьте карточку.")
    if len(prolongations) > 1:
        _issue(issues, "multiple_prolongations", "violation",
               f"Срок продлевался несколько раз ({len(prolongations)}) — по 59-ФЗ продлить можно один раз.")
    if deadline and initial and deadline > initial:
        extra = (deadline - initial).days
        metrics["prolongation_days"] = extra
        if extra > PROLONGATION_DAYS:
            _issue(issues, "prolongation_over_30", "violation",
                   f"Срок продлён на {extra} дн. ({fmt(initial)} → {fmt(deadline)}) — больше {PROLONGATION_DAYS}.")
        if not prolongations:
            _issue(issues, "prolongation_not_recorded", "warning",
                   "Срок больше исходного, но записи о продлении (с причиной) нет.")
    for p in prolongations:
        if not str(p.get("reason") or "").strip():
            _issue(issues, "prolongation_without_reason", "warning",
                   f"Продление № {p.get('number') or '?'} без указанной причины.")
    if registered and deadline:
        total = (deadline - registered).days
        metrics["total_term_days"] = total
        if total > MAX_TOTAL_DAYS:
            _issue(issues, "total_over_60", "violation",
                   f"Общий срок {total} дн. с регистрации — больше {MAX_TOTAL_DAYS}.")

    # Исполнение.
    if status == "рассмотрено":
        if response and deadline and response > deadline:
            late = (response - deadline).days
            metrics["answered_late_days"] = late
            _issue(issues, "answered_late", "violation",
                   f"Ответ от {fmt(response)} — позже срока {fmt(deadline)} на {late} дн.")
        for q in req.get("questions") or []:
            if isinstance(q, dict) and q.get("question") and not (q.get("review_result") or q.get("review_result_label")):
                _issue(issues, "review_result_missing", "warning",
                       f"Нет результата рассмотрения по вопросу «{q['question']}».")
    elif status == "в работе" and registered:
        if deadline is None:
            _issue(issues, "no_deadline", "violation", "Обращение в работе, но срок не указан.")
        else:
            left = (deadline - today).days
            metrics["days_left"] = left
            if left < 0:
                metrics["days_overdue"] = -left
                _issue(issues, "overdue", "violation", f"Срок {fmt(deadline)} нарушен на {-left} дн.")
            elif left == 0:
                _issue(issues, "due_today", "warning", f"Срок истекает сегодня ({fmt(deadline)}).")
            elif left <= soon_days:
                _issue(issues, "due_soon", "warning", f"До срока {fmt(deadline)} осталось {left} дн.")
            if left >= 0 and not prolongations and deadline == initial and left <= soon_days:
                _issue(issues, "prolongation_possible", "info",
                       "Если ответ не успевают подготовить — продлить срок можно один раз до 30 дней, "
                       "до истечения срока, с уведомлением заявителя.")
    for q in req.get("questions") or []:
        if isinstance(q, dict) and q.get("question") and not q.get("code"):
            _issue(issues, "question_not_classified", "info",
                   f"Вопрос «{q['question']}» без кода классификатора — не попадёт в отчёты по ТОТК.")

    issues.sort(key=lambda i: SEVERITY_ORDER[i["severity"]])
    return {
        "id": req.get("id"),
        "registration_number": req.get("registration_number"),
        "url": req.get("url"),
        "assignee": req.get("assignee"),
        "status": status,
        "request_date": received.isoformat() if received else None,
        "registration_date": registered.isoformat() if registered else None,
        "initial_deadline": initial.isoformat() if initial else None,
        "deadline": deadline.isoformat() if deadline else None,
        "response_date": response.isoformat() if response else None,
        "metrics": metrics,
        "issues": issues,
    }


def extract_requests(data) -> list:
    if isinstance(data, dict):
        if isinstance(data.get("items"), list):
            data = data["items"]
        elif "id" in data or "registration_number" in data:
            data = [data]
        else:
            raise InputError("ожидался ответ get_citizen_request_status / list_citizen_requests_due (поле items), "
                             "список обращений или одно обращение")
    if not isinstance(data, list):
        raise InputError("ожидался список обращений")
    requests = [r for r in data if isinstance(r, dict)]
    if not requests:
        raise InputError("в данных нет ни одного обращения")
    if len(requests) > MAX_REQUESTS:
        raise InputError(f"слишком много обращений: {len(requests)} (допустимо не более {MAX_REQUESTS})")
    return requests


def check(data, today: date, soon_days: int = DEFAULT_SOON_DAYS) -> dict:
    results = [check_request(r, today, soon_days) for r in extract_requests(data)]
    by_code = {}
    for r in results:
        for i in r["issues"]:
            by_code[i["code"]] = by_code.get(i["code"], 0) + 1
    summary = {
        "requests": len(results),
        "with_violations": sum(1 for r in results if any(i["severity"] == "violation" for i in r["issues"])),
        "with_warnings": sum(1 for r in results if any(i["severity"] == "warning" for i in r["issues"])),
        "in_work": sum(1 for r in results if r["status"] == "в работе"),
        "overdue": sum(1 for r in results if r["metrics"]["days_overdue"]),
        "by_code": dict(sorted(by_code.items())),
    }
    return {"today": today.isoformat(), "soon_days": soon_days, "summary": summary, "requests": results,
            "disclaimer": DISCLAIMER}


SEVERITY_LABEL = {"violation": "нарушение", "warning": "внимание", "info": "справка"}


def _cell(value):
    return ("—" if value in (None, "") else str(value)).replace("|", "\\|").replace("\n", " ")


def to_markdown(result: dict) -> str:
    s = result["summary"]
    out = ["# Проверка сроков обращений", "",
           f"На {fmt(parse_day(result['today']))}: обращений **{s['requests']}**, с нарушениями **{s['with_violations']}**, "
           f"с предупреждениями **{s['with_warnings']}**, в работе **{s['in_work']}**, просрочено **{s['overdue']}**.", ""]
    rows = [(r, i) for r in result["requests"] for i in r["issues"] if i["severity"] != "info"]
    if rows:
        out += ["| Обращение | Статус | Срок | Уровень | Замечание |", "|---|---|---|---|---|"]
        for r, i in rows:
            num = r["registration_number"] or f"id {r['id']}"
            link = f"[{num}]({r['url']})" if r.get("url") else num
            out.append(f"| {_cell(link)} | {_cell(r['status'])} | {_cell(fmt(parse_day(r['deadline'])))} | "
                       f"{SEVERITY_LABEL[i['severity']]} | {_cell(i['message'])} |")
        out.append("")
    else:
        out += ["Нарушений и предупреждений нет.", ""]
    infos = [(r, i) for r in result["requests"] for i in r["issues"] if i["severity"] == "info"]
    if infos:
        out += ["Справочно:", ""]
        for r, i in infos:
            out.append(f"- {r['registration_number'] or 'id ' + str(r['id'])}: {i['message']}")
        out.append("")
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
    parser = _Parser(description="Проверка сроков обращений граждан по 59-ФЗ.")
    parser.add_argument("input", help="JSON-файл из mcpOGV или «-» для чтения из stdin")
    parser.add_argument("--today", help="дата проверки ГГГГ-ММ-ДД (по умолчанию — сегодня)")
    parser.add_argument("--soon-days", type=int, default=DEFAULT_SOON_DAYS, help="окно «срок истекает», дней (0–60)")
    parser.add_argument("--format", choices=["json", "md"], default="json", help="формат вывода")
    parser.add_argument("--debug", action="store_true", help="выводить трассировку при ошибке")
    args = parser.parse_args(argv)
    try:
        today = date.today()
        if args.today:
            today = parse_day(args.today)
            if today is None:
                raise InputError(f"--today: ожидалась дата ГГГГ-ММ-ДД, получено «{args.today}»")
        if not 0 <= args.soon_days <= 60:
            raise InputError("--soon-days: допустимо от 0 до 60")
        result = check(read_input(args.input), today, args.soon_days)
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
