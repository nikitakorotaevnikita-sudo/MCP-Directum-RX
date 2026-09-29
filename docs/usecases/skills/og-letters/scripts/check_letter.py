"""Проверка черновика письма по обращению гражданина перед созданием документа в Directum RX.

Виды (--kind): answer — ответ заявителю; prolongation — уведомление о продлении срока; redirect — уведомление
заявителя о направлении по компетенции; cover — сопроводительное письмо в организацию по компетенции; agency —
запрос в другое ведомство; pos — ответ в ПОС.

Проверяет: незаполненные места («[…]», «___», «XX.XX.XXXX», строку «ЧЕРНОВИК»), персональные данные в тексте
(email, телефон, СНИЛС, паспорт), ссылку на обращение (номер и дату — если передан --request), освещение каждого
вопроса обращения (эвристика по словам названия вопроса), обязательные элементы вида письма, для уведомления
о продлении — новый срок в пределах 30 дней продления и 60 дней общего срока.

Запуск:  python check_letter.py ПИСЬМО.txt|- [--kind answer] [--request СТАТУС.json] [--format json|md] [--debug]
Коды выхода: 0 — успех, 2 — ошибка входных данных, 1 — непредвиденная ошибка.
"""

import argparse
import json
import re
import sys
import traceback
from bisect import bisect_right
from datetime import date, datetime
from pathlib import Path

MAX_CHARS = 500_000
MAX_JSON_BYTES = 5_000_000
MIN_ANSWER_WORDS = 40
COVERAGE_THRESHOLD = 0.5
PROLONGATION_DAYS = 30
MAX_TOTAL_DAYS = 60
KINDS = {
    "answer": "ответ заявителю",
    "prolongation": "уведомление о продлении срока",
    "redirect": "уведомление о направлении по компетенции",
    "cover": "сопроводительное письмо по компетенции",
    "agency": "запрос в другое ведомство",
    "pos": "ответ в ПОС",
}
DISCLAIMER = ("Проверка формальная и эвристическая: она не оценивает, дан ли ответ по существу. Решение о тексте "
              "принимает исполнитель, письмо подписывает уполномоченное лицо.")
MONTHS = {"января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6, "июля": 7, "августа": 8,
          "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12}
DATE_NUM = re.compile(r"(?<!\d)(\d{1,2})\.(\d{1,2})\.(\d{4})(?!\d)")
DATE_WORD = re.compile(r"(?<!\d)(\d{1,2})\s+(" + "|".join(MONTHS) + r")\s+(\d{4})", re.I)
DATE_RU = re.compile(r"^\s*(\d{1,2})\.(\d{1,2})\.(\d{4})\s*$")
WORD_RE = re.compile(r"[а-яёa-z0-9]+", re.I)
WS_RE = re.compile(r"\s+")

PLACEHOLDERS = [
    ("square_brackets", "поле в квадратных скобках", re.compile(r"\[[^\]\n]{1,200}\]")),
    ("underscores", "пропуск «___»", re.compile(r"_{3,}")),
    ("date_mask", "маска даты", re.compile(r"\b(?:XX|ХХ|ДД)\.(?:XX|ХХ|ММ)\.(?:XXXX|ХХХХ|ГГГГ)\b", re.I)),
    ("draft_mark", "пометка «ЧЕРНОВИК»", re.compile(r"^\s*ЧЕРНОВИК\b.*$", re.M)),
]
PII = [
    ("email", "адрес электронной почты", re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")),
    ("phone", "номер телефона", re.compile(r"(?<!\d)(?:\+7|8)[\s(-]*\d{3}[\s)-]*\d{3}[\s-]*\d{2}[\s-]*\d{2}(?!\d)")),
    ("snils", "СНИЛС", re.compile(r"(?<!\d)\d{3}-\d{3}-\d{3}[ -]\d{2}(?!\d)")),
    ("passport", "паспортные данные", re.compile(r"паспорт\w*[^\n]{0,20}?(?<!\d)\d{2}\s?\d{2}\s?\d{6}(?!\d)", re.I)),
]
# Обязательные по смыслу элементы: (код, описание, шаблон). Хотя бы одно совпадение шаблона.
REQUIRED = {
    "answer": [
        ("result", "результат рассмотрения (разъяснено, меры приняты, поддержано, отказано…)",
         r"разъясн|меры\s+(?:приняты|принимаются)|принят\w*\s+мер|поддерж|не\s+поддерж|отказ|рассмотрен|сообщаем|информируем"),
    ],
    "prolongation": [
        ("prolonged", "указание на продление срока", r"продл"),
        ("reason", "обоснование продления", r"в\s+связи\s+с|поскольку|так\s+как|в\s+целях|для\s+(?:получения|проведения|запроса)|необходимост"),
    ],
    "redirect": [
        ("redirected", "указание, что обращение направлено", r"направлен|переадресован|перенаправлен"),
        ("competence", "указание на компетенцию", r"компетенц"),
    ],
    "cover": [
        ("sending", "указание на направление обращения", r"направля\w*|направлен|пересыла\w*"),
        ("competence", "указание на компетенцию", r"компетенц"),
        ("attachment", "приложение (само обращение)", r"приложени"),
        ("notify", "просьба проинформировать заявителя или указание, что он уведомлён", r"заявител|гражданин|автор"),
    ],
    "agency": [
        ("request", "просьба представить сведения", r"прош|просим|просьб"),
        ("term", "срок представления сведений", r"срок|в\s+течение|до\s+\d"),
    ],
    "pos": [
        ("result", "результат рассмотрения", r"разъясн|меры|принят|поддерж|отказ|рассмотрен|сообщаем|информируем|отложен"),
    ],
}
STOP = {"вопрос", "вопросы", "вопросов", "другие", "другое", "прочие", "иные", "общие", "граждан", "гражданина",
        "связи", "также", "порядок", "деятельность", "работа", "работы", "сфере", "области", "органов"}


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


def _line_starts(text):
    starts = [0]
    idx = text.find("\n")
    while idx != -1:
        starts.append(idx + 1)
        idx = text.find("\n", idx + 1)
    return starts


def dates_in(text):
    found = []
    for m in DATE_NUM.finditer(text):
        try:
            found.append((m.start(), date(int(m.group(3)), int(m.group(2)), int(m.group(1)))))
        except ValueError:
            pass
    for m in DATE_WORD.finditer(text):
        try:
            found.append((m.start(), date(int(m.group(3)), MONTHS[m.group(2).lower()], int(m.group(1)))))
        except ValueError:
            pass
    return sorted(found)


def stem(word: str) -> str:
    return word.lower().replace("ё", "е")[:5]


def significant_stems(name: str) -> list:
    words = [w.lower().replace("ё", "е") for w in WORD_RE.findall(name or "")]
    return sorted({stem(w) for w in words if len(w) >= 4 and w not in STOP})


def _norm_number(value) -> str:
    return re.sub(r"[\s\-–—№]", "", str(value or "")).lower()


def _request_obj(data):
    if data is None:
        return None
    if isinstance(data, dict) and isinstance(data.get("items"), list):
        items = [r for r in data["items"] if isinstance(r, dict)]
        if len(items) != 1:
            raise InputError(f"--request: в items {len(items)} обращений — передайте одно обращение")
        return items[0]
    if isinstance(data, dict):
        return data
    raise InputError("--request: ожидалось обращение (ответ get_citizen_request_status)")


def check(text: str, kind: str = "answer", request=None) -> dict:
    if kind not in KINDS:
        raise InputError(f"неизвестный вид письма «{kind}»; допустимо: {', '.join(KINDS)}")
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n").replace(" ", " ")
    req = _request_obj(request)
    starts = _line_starts(text)
    issues = []

    def add(code, severity, message, pos=None):
        item = {"code": code, "severity": severity, "message": message}
        if pos is not None:
            item["line"] = bisect_right(starts, pos)
        issues.append(item)

    for code, label, rx in PLACEHOLDERS:
        for m in rx.finditer(text):
            add("placeholder", "violation", f"Незаполненное место ({label}): «{m.group(0).strip()[:80]}».", m.start())
    for code, label, rx in PII:
        for m in rx.finditer(text):
            add("personal_data", "warning", f"В тексте {label} — проверьте, нужно ли это в письме.", m.start())

    low = text.lower().replace("ё", "е")
    for code, label, pattern in REQUIRED[kind]:
        if not re.search(pattern, low):
            add("missing_" + code, "warning", f"Не найдено: {label}.")

    words = len(WORD_RE.findall(text))
    if kind in ("answer", "pos") and words < MIN_ANSWER_WORDS:
        add("too_short", "warning", f"Текст короткий ({words} слов) — проверьте, что ответ дан по существу каждого вопроса.")

    coverage = []
    found_dates = dates_in(text)
    if req:
        number = req.get("registration_number")
        if number and kind != "agency":
            if _norm_number(number) not in _norm_number(text):
                add("request_number_missing", "warning", f"Не указан регистрационный номер обращения «{number}».")
        reg = parse_day(req.get("registration_date"))
        if reg and kind in ("answer", "prolongation", "redirect") and all(d != reg for _, d in found_dates):
            add("request_date_missing", "info", f"Не указана дата регистрации обращения ({reg.strftime('%d.%m.%Y')}).")
        if kind in ("answer", "pos", "cover", "redirect"):
            stems_text = {stem(w) for w in WORD_RE.findall(low)}
            for q in req.get("questions") or []:
                if not isinstance(q, dict) or not q.get("question"):
                    continue
                stems = significant_stems(q["question"])
                hit = [s for s in stems if s in stems_text]
                share = round(len(hit) / len(stems), 2) if stems else None
                covered = share is None or share >= COVERAGE_THRESHOLD
                coverage.append({"question": q["question"], "code": q.get("code"), "matched_share": share,
                                 "covered": covered})
                if not covered:
                    add("question_not_addressed", "warning",
                        f"Похоже, не освещён вопрос «{q['question']}» (совпало слов: {len(hit)} из {len(stems)}).")
        if kind == "prolongation":
            initial = parse_day(req.get("initial_deadline")) or parse_day(req.get("deadline"))
            later = [d for _, d in found_dates if initial and d > initial]
            if not found_dates:
                add("new_deadline_missing", "violation", "Не указан новый срок рассмотрения.")
            elif initial and not later:
                add("new_deadline_missing", "warning",
                    f"Не найдена дата позже текущего срока {initial.strftime('%d.%m.%Y')} — укажите новый срок.")
            if later:
                new = max(later)
                if (new - initial).days > PROLONGATION_DAYS:
                    add("prolongation_over_30", "violation",
                        f"Новый срок {new.strftime('%d.%m.%Y')} — продление на {(new - initial).days} дн., больше 30.")
                if reg and (new - reg).days > MAX_TOTAL_DAYS:
                    add("total_over_60", "violation",
                        f"Новый срок {new.strftime('%d.%m.%Y')} — {(new - reg).days} дн. с регистрации, больше 60.")
            if len(req.get("prolongations") or []) >= 1:
                add("already_prolonged", "violation", "По обращению уже было продление — повторно продлевать нельзя.")
    elif kind == "prolongation" and not found_dates:
        add("new_deadline_missing", "violation", "Не указан новый срок рассмотрения.")

    order = {"violation": 0, "warning": 1, "info": 2}
    issues.sort(key=lambda i: (order[i["severity"]], i.get("line") or 0))
    return {
        "kind": kind, "kind_label": KINDS[kind], "words": words,
        "summary": {"violations": sum(1 for i in issues if i["severity"] == "violation"),
                    "warnings": sum(1 for i in issues if i["severity"] == "warning"),
                    "questions_total": len(coverage), "questions_covered": sum(1 for c in coverage if c["covered"])},
        "issues": issues, "question_coverage": coverage,
        "dates_found": sorted({d.isoformat() for _, d in found_dates}),
        "disclaimer": DISCLAIMER,
    }


LEVEL = {"violation": "исправить", "warning": "проверить", "info": "справка"}


def to_markdown(result: dict) -> str:
    s = result["summary"]
    out = [f"# Проверка письма: {result['kind_label']}", "",
           f"Исправить: **{s['violations']}**, проверить: **{s['warnings']}**. Слов в тексте: {result['words']}."]
    if s["questions_total"]:
        out.append(f"Вопросов обращения освещено: {s['questions_covered']} из {s['questions_total']}.")
    out.append("")
    if result["issues"]:
        out += ["| Строка | Уровень | Замечание |", "|---|---|---|"]
        for i in result["issues"]:
            out.append(f"| {i.get('line') or '—'} | {LEVEL[i['severity']]} | {i['message'].replace('|', '/')} |")
        out.append("")
    else:
        out += ["Формальных замечаний нет.", ""]
    out.append(f"> {result['disclaimer']}")
    return "\n".join(out) + "\n"


# --- ввод-вывод ---

def decode_text(data: bytes) -> str:
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        text = data.decode("utf-16", errors="replace")
    else:
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = data.decode("cp1251", errors="replace")
    if not text.strip():
        raise InputError("текст письма пустой — нечего проверять")
    if len(text) > MAX_CHARS:
        raise InputError(f"текст слишком большой: {len(text)} символов (допустимо не более {MAX_CHARS})")
    return text


def read_bytes(path: str) -> bytes:
    if path == "-":
        return sys.stdin.buffer.read()
    p = Path(path)
    if not p.is_file():
        raise InputError(f"файл не найден: {path}")
    try:
        return p.read_bytes()
    except OSError as exc:
        raise InputError(f"не удалось прочитать файл {path}: {exc}") from None


def read_request(path: str):
    data = read_bytes(path)
    if len(data) > MAX_JSON_BYTES:
        raise InputError("--request: файл слишком большой")
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    try:
        return json.loads(data.decode("utf-8", errors="replace"))
    except json.JSONDecodeError as exc:
        raise InputError(f"--request: это не JSON: {exc.msg}") from None


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
    parser = _Parser(description="Проверка черновика письма по обращению гражданина.")
    parser.add_argument("input", help="текст письма (файл) или «-» для чтения из stdin")
    parser.add_argument("--kind", choices=list(KINDS), default="answer", help="вид письма")
    parser.add_argument("--request", help="JSON обращения из get_citizen_request_status (номер, даты, вопросы)")
    parser.add_argument("--format", choices=["json", "md"], default="json", help="формат вывода")
    parser.add_argument("--debug", action="store_true", help="выводить трассировку при ошибке")
    args = parser.parse_args(argv)
    try:
        if args.request == "-" and args.input == "-":
            raise InputError("stdin можно использовать только для одного из входов")
        request = read_request(args.request) if args.request else None
        result = check(decode_text(read_bytes(args.input)), args.kind, request)
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
