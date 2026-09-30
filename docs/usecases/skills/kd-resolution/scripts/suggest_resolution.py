"""Проект резолюции по входящему документу на основе истории поручений автора.

Вход: текст входящего документа (файл или «-»), история резолюций автора — ответ mcpOGV get_resolution_history
(--history), необязательно — название / реквизиты документа (--title) и дата (--today).

Что делает:
1. Находит в истории поручения, похожие на входящее по словам (TF-IDF по основам слов: название документа-основания,
   тема и текст поручения), с поправкой на давность.
2. Предлагает ответственного исполнителя и соисполнителей — кому этот автор поручал похожее, с доказательствами.
3. Предлагает, ставить ли на контроль: доля похожих поручений на контроле у этого автора + признаки в тексте
   (установленный срок, «доложить», «представить информацию», документ вышестоящего органа, «на контроль»).
4. Предлагает срок: установленный в тексте срок минус запас, иначе — типичный срок похожих поручений.
5. Собирает черновик резолюции и оценку уверенности.

Запуск:  python suggest_resolution.py ВХОДЯЩЕЕ.txt|- --history ИСТОРИЯ.json [--title "Вх. письмо …"]
                                      [--today ГГГГ-ММ-ДД] [--top 5] [--format json|md] [--debug]
Коды выхода: 0 — успех, 2 — ошибка входных данных, 1 — непредвиденная ошибка.
"""

import argparse
import json
import math
import re
import statistics
import sys
import traceback
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

MAX_TEXT_CHARS = 60_000
MAX_JSON_BYTES = 50_000_000
MAX_HISTORY = 5000
STEM = 6
MIN_SIMILARITY = 0.06
NEIGHBOURS = 20
HALF_LIFE_DAYS = 365
CO_MIN_SUPPORT = 2
CO_MIN_SHARE = 0.3
CONTROL_SHARE = 0.5
ENOUGH_HISTORY = 5
DEADLINE_RESERVE_WORKDAYS = 3
TITLE_WEIGHT = 3
DISCLAIMER = ("Проект резолюции — подсказка по истории поручений автора. Исполнителей, срок и контроль утверждает "
              "автор резолюции; проверьте, что исполнитель не в отпуске и тема по-прежнему за ним.")

WORD = re.compile(r"[а-яёa-z]{3,}", re.I)
DATE_NUM = re.compile(r"(?<!\d)(\d{1,2})\.(\d{1,2})\.(\d{4})(?!\d)")
MONTHS = {"января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6, "июля": 7, "августа": 8,
          "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12}
DATE_WORD = re.compile(r"(?<!\d)(\d{1,2})\s+(" + "|".join(MONTHS) + r")(?:\s+(\d{4}))?", re.I)
DEADLINE_CUE = re.compile(r"(?:в\s+срок\s+до|срок\s*[—–-]?\s*до|не\s+позднее|до)\s*$", re.I)
DATE_RU = re.compile(r"^\s*(\d{1,2})\.(\d{1,2})\.(\d{4})\s*$")

# Общие слова писем и поручений: они есть почти везде и не говорят о теме.
STOP = {stem for stem in (w[:STEM] for w in """
прошу просим просьба подготовить подготовка представить представление предоставить направить направляем направление
информацию информация информации письмо письма письмом ответ ответа ответное поручение поручения поручаю исполнение
исполнителем исполнитель соисполнителем результатах результатам результаты выполнить выполнение мероприятия мероприятий
необходимые необходимо соответствующую соответствующие рассмотреть рассмотрение вопрос вопросу вопросы вопросов
уважаемый уважаемая уважаемые также связи согласно соответствии рамках части пункт пункта статьи года году числе
которые который которая которых является являетесь этого этом этой настоящим настоящее просим приложение приложения
документ документа документы входящее входящего исходящее исходящего письмо сообщаем уведомляем данных данные
""".split())}
AUTHORITY = re.compile(
    r"правительств|президент|администраци\w*\s+президента|государственн\w+\s+дум|совет\w*\s+федерации|прокуратур|"
    r"счетн\w+\s+палат|счётн\w+\s+палат|губернатор|полномочн\w+\s+представител|следственн\w+\s+комитет|министерств|"
    r"федеральн\w+\s+(?:служб|агентств)", re.I)
CONTROL_CUES = [
    ("explicit_control", re.compile(r"на\s+контрол|поставить\s+на\s+контроль|взять\s+на\s+контроль", re.I),
     "В тексте прямо сказано о контроле."),
    ("report_required", re.compile(r"доложить|представить\s+(?:информаци|доклад|отч[её]т|сведени)|проинформировать\s+о\s+(?:ходе|результат)|"
                                   r"предоставить\s+(?:информаци|сведени|отч[её]т)", re.I),
     "Требуется доклад или информация о результатах."),
    ("periodic", re.compile(r"ежемесячно|ежеквартально|еженедельно|ежегодно|каждые\s+\d+", re.I),
     "Периодическое представление информации."),
]


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


def stems(text: str) -> list:
    out = []
    for w in WORD.findall((text or "").lower().replace("ё", "е")):
        s = w[:STEM]
        if len(w) >= 4 and s not in STOP:
            out.append(s)
    return out


def minus_workdays(day: date, n: int) -> date:
    while n > 0:
        day -= timedelta(days=1)
        if day.weekday() < 5:
            n -= 1
    return day


def find_deadlines(text: str, today: date) -> list:
    """Даты из текста, перед которыми стоит «в срок до», «до», «не позднее» — будущие установленные сроки."""
    found = []
    for m in DATE_NUM.finditer(text):
        if DEADLINE_CUE.search(text[max(0, m.start() - 25):m.start()]):
            try:
                found.append(date(int(m.group(3)), int(m.group(2)), int(m.group(1))))
            except ValueError:
                pass
    for m in DATE_WORD.finditer(text):
        if DEADLINE_CUE.search(text[max(0, m.start() - 25):m.start()]):
            year = int(m.group(3)) if m.group(3) else today.year
            try:
                d = date(year, MONTHS[m.group(2).lower()], int(m.group(1)))
            except ValueError:
                continue
            if not m.group(3) and d < today:
                d = date(year + 1, d.month, d.day)
            found.append(d)
    return sorted({d for d in found if d >= today})


def extract_history(data) -> list:
    if isinstance(data, dict):
        if not isinstance(data.get("items"), list):
            raise InputError("--history: ожидался ответ get_resolution_history (поле items)")
        data = data["items"]
    if not isinstance(data, list):
        raise InputError("--history: ожидался список поручений")
    items = [i for i in data if isinstance(i, dict) and i.get("id") is not None]
    if len(items) > MAX_HISTORY:
        raise InputError(f"--history: слишком много поручений ({len(items)}), допустимо не более {MAX_HISTORY}")
    return items


def _executor(item):
    if item.get("assignee"):
        return item["assignee"]
    parts = item.get("parts_assignees") or []
    return parts[0] if parts else None


def _others(item, main):
    # Основной исполнитель составного поручения — исполнитель первого пункта; он исключается как main.
    names = list(item.get("co_assignees") or []) + list(item.get("parts_assignees") or [])
    return [n for n in dict.fromkeys(names) if n and n != main]


def _doc_text(item):
    source = (item.get("source_document") or {}).get("name") or ""
    return " ".join([source] * TITLE_WEIGHT + [item.get("subject") or "", item.get("text") or ""])


SYSTEM_PREFIX = re.compile(r"^\s*Вы\s+являетесь\s+(?:со)?исполнителем\.?\s*(?:п\.\s*\d+\.?\s*)?", re.I)


def clean_text(text):
    """Текст поручения без служебного префикса RX («Вы являетесь исполнителем. п.1»)."""
    if not isinstance(text, str):
        return None
    text = SYSTEM_PREFIX.sub("", text).strip()
    return text[:1].upper() + text[1:] if text else None


def _fmt(day):
    return day.strftime("%d.%m.%Y") if day else None


def suggest(text: str, history, title: str = "", today: date | None = None, top: int = 5) -> dict:
    today = today or date.today()
    items = extract_history(history)
    text = (text or "")[:MAX_TEXT_CHARS]
    query_terms = Counter(stems(" ".join([title or ""] * TITLE_WEIGHT + [text])))
    notes = []
    if not query_terms:
        raise InputError("в тексте документа нет слов для сопоставления")
    if isinstance(history, dict) and history.get("truncated"):
        notes.append(f"История неполная: передано {len(items)} из {history.get('total')} поручений.")

    # TF-IDF по основам слов, косинусная близость.
    docs = [Counter(stems(_doc_text(i))) for i in items]
    df = Counter(term for d in docs for term in d)
    n = len(docs) + 1

    def weight(counter):
        return {t: (1 + math.log(c)) * math.log(n / (1 + df.get(t, 0)) + 1) for t, c in counter.items()}

    qw = weight(query_terms)
    qnorm = math.sqrt(sum(v * v for v in qw.values())) or 1.0
    scored = []
    for item, d in zip(items, docs):
        if not d:
            continue
        dw = weight(d)
        dot = sum(qw[t] * dw[t] for t in qw.keys() & dw.keys())
        if not dot:
            continue
        sim = dot / (qnorm * (math.sqrt(sum(v * v for v in dw.values())) or 1.0))
        if sim < MIN_SIMILARITY:
            continue
        created = parse_day(item.get("created"))
        age = (today - created).days if created else HALF_LIFE_DAYS
        recency = 0.5 ** (max(age, 0) / HALF_LIFE_DAYS)
        shared = sorted(qw.keys() & dw.keys(), key=lambda t: -(qw[t] * dw[t]))[:6]
        scored.append((sim, sim * recency, item, shared))
    scored.sort(key=lambda s: (-s[1], -s[0], str(s[2].get("id"))))
    neighbours = scored[:NEIGHBOURS]

    # Исполнитель.
    by_person = defaultdict(lambda: {"score": 0.0, "items": []})
    for sim, w, item, _ in neighbours:
        who = _executor(item)
        if who:
            by_person[who]["score"] += w
            by_person[who]["items"].append(item)
    total_w = sum(p["score"] for p in by_person.values()) or 1.0
    candidates = []
    for who, p in sorted(by_person.items(), key=lambda kv: -kv[1]["score"]):
        dates = [parse_day(i.get("created")) for i in p["items"]]
        candidates.append({"name": who, "share": round(p["score"] / total_w, 2), "support": len(p["items"]),
                           "last": max((d for d in dates if d), default=None),
                           "evidence": [i.get("id") for i in p["items"][:3]]})
    for c in candidates:
        c["last"] = c["last"].isoformat() if c["last"] else None
    main = candidates[0] if candidates else None
    top_sim = neighbours[0][0] if neighbours else 0.0
    if main and top_sim >= 0.25 and main["share"] >= 0.5 and main["support"] >= 2:
        confidence = "высокая"
    elif main and top_sim >= 0.12 and main["share"] >= 0.35:
        confidence = "средняя"
    else:
        confidence = "низкая"
    frequent = Counter(_executor(i) for i in items if _executor(i)).most_common(5)
    if confidence == "низкая":
        notes.append("Похожих поручений мало или они расходятся по исполнителям — исполнителя лучше выбрать автору; "
                     "ниже частые исполнители автора.")

    # Соисполнители — кто сопровождал похожие поручения основного исполнителя (или похожие вообще).
    co = []
    if main:
        pool = [i for _, _, i, _ in neighbours if _executor(i) == main["name"]] or [i for _, _, i, _ in neighbours]
        counts = Counter(n for i in pool for n in _others(i, main["name"]))
        for who, k in counts.most_common():
            share = k / len(pool)
            if k >= CO_MIN_SUPPORT and share >= CO_MIN_SHARE:
                co.append({"name": who, "support": k, "share": round(share, 2)})

    # Контроль.
    reasons = []
    signals = []
    lower = f"{title}\n{text}"
    for code, rx, label in CONTROL_CUES:
        if rx.search(lower):
            signals.append(code)
            reasons.append(label)
    deadlines = find_deadlines(lower, today)
    if deadlines:
        signals.append("external_deadline")
        reasons.append(f"В документе установлен срок: {_fmt(deadlines[0])}.")
    if AUTHORITY.search(lower):
        signals.append("authority")
        reasons.append("Документ от вышестоящего или федерального органа.")
    near = [(w, i) for _, w, i, _ in neighbours]
    weight_sum = sum(w for w, _ in near)
    hist_share = round(sum(w for w, i in near if i.get("under_control")) / weight_sum, 2) if weight_sum else None
    base_share = round(sum(1 for i in items if i.get("under_control")) / len(items), 2) if items else None
    if hist_share is not None:
        controlled = sum(1 for _, i in near if i.get("under_control"))
        reasons.append(f"Похожие поручения автора на контроле: {controlled} из {len(near)}.")
    # Приоритет: прямое указание → документ вышестоящего органа со сроком или докладом → привычка автора по похожим
    # (если похожих достаточно) → признаки текста.
    if "explicit_control" in signals:
        recommend = True
    elif "authority" in signals and ("external_deadline" in signals or "report_required" in signals):
        recommend = True
    elif hist_share is not None and len(near) >= ENOUGH_HISTORY:
        recommend = hist_share >= CONTROL_SHARE
        if not recommend and ("external_deadline" in signals or "report_required" in signals):
            reasons.append("Автор обычно не ставит такие поручения на контроль — срок проследите по сроку поручения.")
    elif hist_share is not None and len(near) >= 2 and hist_share >= CONTROL_SHARE:
        recommend = True
    else:
        recommend = "external_deadline" in signals and "report_required" in signals
    if not recommend and not reasons:
        reasons.append("Признаков контрольного документа нет, похожие поручения автора на контроль не ставились.")
    supervisors = Counter(i.get("supervisor") for _, i in near if i.get("under_control") and i.get("supervisor"))
    control = {"recommend": recommend, "signals": signals, "similar_under_control_share": hist_share,
               "author_under_control_share": base_share, "reasons": reasons,
               "supervisor": supervisors.most_common(1)[0][0] if recommend and supervisors else None}

    # Срок.
    terms = [i.get("term_days") for _, _, i, _ in neighbours if isinstance(i.get("term_days"), int) and i["term_days"] > 0]
    typical = int(statistics.median(terms)) if terms else None
    if deadlines:
        proposed = minus_workdays(deadlines[0], DEADLINE_RESERVE_WORKDAYS)
        if proposed <= today:
            proposed = deadlines[0]
        basis = (f"Срок документа {_fmt(deadlines[0])} минус {DEADLINE_RESERVE_WORKDAYS} рабочих дня на подготовку "
                 "ответа" if proposed != deadlines[0] else f"Срок документа {_fmt(deadlines[0])} — запаса нет")
    elif typical:
        proposed = today + timedelta(days=typical)
        while proposed.weekday() >= 5:
            proposed += timedelta(days=1)
        basis = f"Типичный срок похожих поручений автора — {typical} дн."
    else:
        proposed, basis = None, "Срок не установлен документом и не определяется по истории — укажите сами."
    deadline = {"proposed": proposed.isoformat() if proposed else None, "basis": basis,
                "document_deadlines": [d.isoformat() for d in deadlines], "typical_term_days": typical}

    # Черновик резолюции.
    best_text = next((clean_text(i.get("text")) for _, _, i, _ in neighbours
                      if main and _executor(i) == main["name"] and clean_text(i.get("text"))), None)
    lines = []
    if main:
        lines.append(f"{main['name']} (отв.)" + (", " + ", ".join(c["name"] for c in co) if co else ""))
    lines.append(best_text or "Прошу рассмотреть и подготовить предложения.")
    if proposed:
        lines.append(f"Срок — {_fmt(proposed)}.")
    lines.append("На контроль" + (f" (контролёр — {control['supervisor']})." if control["supervisor"] else ".") if recommend
                 else "Без постановки на контроль.")
    similar = [{"id": i.get("id"), "created": i.get("created"), "similarity": round(sim, 3), "assignee": _executor(i),
                "co_assignees": _others(i, _executor(i)), "under_control": bool(i.get("under_control")),
                "subject": i.get("subject"), "source_document": (i.get("source_document") or {}).get("name"),
                "shared_terms": shared, "url": i.get("url")}
               for sim, _, i, shared in neighbours[:top]]
    return {
        "today": today.isoformat(), "confidence": confidence,
        "assignee": main, "assignee_candidates": candidates[:top], "co_assignees": co,
        "control": control, "deadline": deadline,
        "resolution_draft": "\n".join(lines),
        "similar": similar, "similar_found": len(scored),
        "author_frequent_assignees": [{"name": w, "count": k} for w, k in frequent],
        "history_size": len(items), "notes": notes, "disclaimer": DISCLAIMER,
    }


def _cell(value):
    return ("—" if value in (None, "", []) else str(value)).replace("|", "\\|").replace("\n", " ")


def to_markdown(r) -> str:
    out = ["# Проект резолюции", "", f"Уверенность: **{r['confidence']}** (похожих поручений в истории: {r['similar_found']} "
           f"из {r['history_size']}).", "", "```", r["resolution_draft"], "```", ""]
    for n in r["notes"]:
        out += [f"> {n}", ""]
    if r["assignee_candidates"]:
        out += ["## Исполнитель", "", "| Кандидат | Доля похожих | Поручений | Последнее | Примеры |", "|---|---|---|---|---|"]
        for c in r["assignee_candidates"]:
            out.append(f"| {_cell(c['name'])} | {round(c['share'] * 100)} % | {c['support']} | {_cell(_fmt(parse_day(c['last'])))} | "
                       f"{', '.join('№ ' + str(e) for e in c['evidence'])} |")
        out.append("")
    elif r["author_frequent_assignees"]:
        out += ["Частые исполнители автора: " + ", ".join(f"{a['name']} ({a['count']})" for a in r["author_frequent_assignees"]), ""]
    if r["co_assignees"]:
        out += ["Соисполнители: " + ", ".join(f"{c['name']} (в {c['support']} похожих)" for c in r["co_assignees"]), ""]
    c = r["control"]
    out += ["## Контроль", "", f"**{'Поставить на контроль' if c['recommend'] else 'Не ставить на контроль'}**", ""]
    out += [f"- {x}" for x in c["reasons"]] + [""]
    out += ["## Срок", "", f"{_cell(r['deadline']['proposed'] and parse_day(r['deadline']['proposed']).strftime('%d.%m.%Y'))} — "
            f"{r['deadline']['basis']}", ""]
    if r["similar"]:
        out += ["## Похожие поручения автора", "", "| Поручение | Дата | Исполнитель | Контроль | Основание | Общие слова |",
                "|---|---|---|---|---|---|"]
        for s in r["similar"]:
            link = f"[№ {s['id']}]({s['url']})" if s.get("url") else f"№ {s['id']}"
            out.append(f"| {link} | {_cell(_fmt(parse_day(s['created'])))} | {_cell(s['assignee'])} | {'да' if s['under_control'] else 'нет'} | "
                       f"{_cell(s['source_document'] or s['subject'])} | {_cell(', '.join(s['shared_terms']))} |")
        out.append("")
    out.append(f"> {r['disclaimer']}")
    return "\n".join(out) + "\n"


# --- ввод-вывод ---

def read_bytes(path: str, label: str) -> bytes:
    if path == "-":
        return sys.stdin.buffer.read()
    p = Path(path)
    if not p.is_file():
        raise InputError(f"{label}: файл не найден: {path}")
    try:
        return p.read_bytes()
    except OSError as exc:
        raise InputError(f"{label}: не удалось прочитать {path}: {exc}") from None


def decode_text(data: bytes) -> str:
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode("cp1251", errors="replace")
    if not text.strip():
        raise InputError("текст документа пустой")
    return text


def read_history(path: str):
    data = read_bytes(path, "--history")
    if len(data) > MAX_JSON_BYTES:
        raise InputError("--history: файл слишком большой")
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    if not data.strip():
        raise InputError("--history: данные пустые")
    try:
        return json.loads(data.decode("utf-8", errors="replace"))
    except json.JSONDecodeError as exc:
        raise InputError(f"--history: это не JSON: {exc.msg} (строка {exc.lineno})") from None


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
    parser = _Parser(description="Проект резолюции по входящему документу на основе истории поручений автора.")
    parser.add_argument("input", help="текст входящего документа (файл) или «-»")
    parser.add_argument("--history", required=True, help="ответ get_resolution_history (JSON)")
    parser.add_argument("--title", default="", help="название / реквизиты входящего документа")
    parser.add_argument("--today", help="дата ГГГГ-ММ-ДД (по умолчанию — сегодня)")
    parser.add_argument("--top", type=int, default=5, help="сколько кандидатов и похожих поручений показать (1–20)")
    parser.add_argument("--format", choices=["json", "md"], default="json", help="формат вывода")
    parser.add_argument("--debug", action="store_true", help="выводить трассировку при ошибке")
    args = parser.parse_args(argv)
    try:
        if args.input == "-" and args.history == "-":
            raise InputError("stdin можно использовать только для одного из входов")
        if not 1 <= args.top <= 20:
            raise InputError("--top: допустимо от 1 до 20")
        today = date.today()
        if args.today:
            today = parse_day(args.today)
            if today is None:
                raise InputError(f"--today: ожидалась дата ГГГГ-ММ-ДД, получено «{args.today}»")
        history = read_history(args.history)
        result = suggest(decode_text(read_bytes(args.input, "документ")), history, args.title, today, args.top)
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
