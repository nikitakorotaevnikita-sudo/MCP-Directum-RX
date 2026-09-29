"""Поиск ссылок на правовые акты в тексте проекта правового акта.

Находит упоминания Конституции РФ, ФКЗ, федеральных законов, кодексов, указов Президента,
постановлений и распоряжений Правительства РФ, приказов федеральных органов, законов субъектов,
актов органов власти субъекта / муниципалитета и иных актов вида «<вид акта> от <дата> № <номер>».

Запуск:  python extract_legal_refs.py ФАЙЛ|- [--format json|md] [--debug]
Коды выхода: 0 — успех, 2 — ошибка входных данных, 1 — непредвиденная ошибка.
Позиции: line — номер строки (с 1), start/end — смещения символов от начала текста (с 0, end не включается).
"""

import argparse
import json
import re
import sys
import traceback
from bisect import bisect_right
from pathlib import Path

MAX_CHARS = 2_000_000
DISCLAIMER = (
    "Список получен эвристически по шаблонам написания реквизитов. Возможны пропуски и лишние срабатывания; "
    "действие и редакцию каждого акта проверьте в правовой базе."
)

KIND_LABELS = {
    "constitution": "Конституция Российской Федерации",
    "federal_constitutional_law": "Федеральный конституционный закон",
    "federal_law": "Федеральный закон",
    "code": "Кодекс Российской Федерации",
    "presidential_decree": "Указ Президента Российской Федерации",
    "government_resolution": "Постановление Правительства Российской Федерации",
    "government_order": "Распоряжение Правительства Российской Федерации",
    "ministry_order": "Приказ федерального органа исполнительной власти",
    "regional_law": "Закон субъекта Российской Федерации",
    "regional_act": "Акт органа власти субъекта или муниципального образования",
    "other_act": "Иной правовой акт",
}

# Однозначная замена символов (длина текста не меняется, смещения сохраняются).
_TRANSLATE = {ord(c): " " for c in "       \t\f\v"}
_TRANSLATE.update({ord(c): "-" for c in "‐‑‒–—―−"})
_TRANSLATE.update({ord(c): '"' for c in "“”„‟″"})

RF = r"(?:Российской\s+Федерации|РФ)"
# Окончания существительных, чтобы «указ» не ловил «указанный», а «закон» — «законодательство».
END_M = r"(?:а|у|ом|е|ы|ов|ам|ами|ах)?"  # закон, приказ, указ
END_N = r"(?:е|я|ю|ем|и|й|ям|ями|ях)"  # постановление, распоряжение, решение
ZAKON = r"закон" + END_M
POST = r"постановлени" + END_N
RASP = r"распоряжени" + END_N
PRIKAZ = r"приказ" + END_M
UKAZ = r"указ" + END_M
RESH = r"решени" + END_N

CODE_NAMES = [
    (r"арбитражн\w*\s+процессуальн", "Арбитражный процессуальный кодекс Российской Федерации"),
    (r"гражданск\w*\s+процессуальн", "Гражданский процессуальный кодекс Российской Федерации"),
    (r"уголовно-процессуальн", "Уголовно-процессуальный кодекс Российской Федерации"),
    (r"уголовно-исполнительн", "Уголовно-исполнительный кодекс Российской Федерации"),
    (r"гражданск", "Гражданский кодекс Российской Федерации"),
    (r"бюджетн", "Бюджетный кодекс Российской Федерации"),
    (r"налогов", "Налоговый кодекс Российской Федерации"),
    (r"трудов", "Трудовой кодекс Российской Федерации"),
    (r"земельн", "Земельный кодекс Российской Федерации"),
    (r"градостроительн", "Градостроительный кодекс Российской Федерации"),
    (r"жилищн", "Жилищный кодекс Российской Федерации"),
    (r"уголовн", "Уголовный кодекс Российской Федерации"),
    (r"семейн", "Семейный кодекс Российской Федерации"),
    (r"лесн", "Лесной кодекс Российской Федерации"),
    (r"водн", "Водный кодекс Российской Федерации"),
    (r"воздушн", "Воздушный кодекс Российской Федерации"),
]
CODE_ABBR = {
    "ГК": "Гражданский кодекс Российской Федерации",
    "БК": "Бюджетный кодекс Российской Федерации",
    "НК": "Налоговый кодекс Российской Федерации",
    "ТК": "Трудовой кодекс Российской Федерации",
    "ЗК": "Земельный кодекс Российской Федерации",
    "ГрК": "Градостроительный кодекс Российской Федерации",
    "ЖК": "Жилищный кодекс Российской Федерации",
    "УК": "Уголовный кодекс Российской Федерации",
    "АПК": "Арбитражный процессуальный кодекс Российской Федерации",
    "ГПК": "Гражданский процессуальный кодекс Российской Федерации",
    "УПК": "Уголовно-процессуальный кодекс Российской Федерации",
    "КоАП": "Кодекс Российской Федерации об административных правонарушениях",
}
KOAP = "Кодекс Российской Федерации об административных правонарушениях"
KAS = "Кодекс административного судопроизводства Российской Федерации"
CODE_NAME_RES = [(re.compile(p, re.I), name) for p, name in CODE_NAMES]

_code_named = r"(?:" + "|".join(p for p, _ in CODE_NAMES) + r")\w*\s+кодекс\w*(?:\s+" + RF + r")?"
_code_koap = r"кодекс\w*\s+(?:" + RF + r"\s+)?об\s+административных\s+правонарушениях"
_code_kas = r"кодекс\w*\s+административного\s+судопроизводства(?:\s+" + RF + r")?"
_code_abbr = r"(?-i:(?:ГК|БК|НК|ТК|ЗК|ГрК|ЖК|УК|АПК|ГПК|УПК)\s+РФ|КоАП(?:\s+РФ)?)(?!\w)"

ORGANS = (
    r"(?:губернатор|правительств|администраци|глав[аыеуо](?!\w)|министерств|департамент|управлени|комитет|"
    r"агентств|инспекци|дум[аыеу](?!\w)|законодательн\w*\s+собрани|собрани|совет|мэр|префектур|службы)"
)

ORGAN_RE = re.compile(r"(?<!\w)" + ORGANS, re.I)

ANCHORS = [
    ("constitution", r"конституци[яиюей]{1,2}\s+" + RF),
    ("federal_constitutional_law", r"федеральн\w*\s+конституционн\w*\s+" + ZAKON),
    ("federal_law", r"(?:федеральн\w*\s+" + ZAKON + r"|" + ZAKON + r"\s+" + RF + r")"),
    ("code", r"(?:" + _code_named + r"|" + _code_koap + r"|" + _code_kas + r"|" + _code_abbr + r")"),
    ("presidential_decree", UKAZ + r"\s+президента\s+" + RF),
    ("government_resolution", POST + r"\s+правительства\s+" + RF),
    ("government_order", RASP + r"\s+правительства\s+" + RF),
    ("ministry_order", PRIKAZ + r"\s+(?:(?:министерств\w*|федеральн\w*\s+(?:служб|агентств)\w*)"
     r"(?:\s+[а-яё-]+){0,8}?\s+" + RF + r"|мин(?!истерств)[а-яё]{3,}(?:\s+(?:России|РФ))?"
     r"|(?:рос[а-яё]{3,}|ФНС|ФАС|ФСБ|МВД|МЧС|ФСТЭК|Казначейства)\s+России)(?!\w)"),
    ("regional_law", ZAKON + r"\s+(?:[а-яё-]+\s+(?:края|области|автономного\s+округа|автономной\s+области)"
     r"|республики\s+[а-яё-]+(?:\s+(?!от(?!\w))[а-яё-]+)?|города\s+(?:москвы|санкт-петербурга|севастополя)"
     r"|края|области)(?!\w)"),
    ("regional_act", r"(?:" + POST + "|" + RASP + "|" + UKAZ + "|" + PRIKAZ + "|" + RESH + r")\s+" + ORGANS + r"\w*"),
    ("other_act", r"(?:" + POST + "|" + RASP + "|" + UKAZ + "|" + PRIKAZ + "|" + RESH + "|" + ZAKON
     + r"|положени" + END_N + r"|письм(?:о|а|у|ом|е)|определени" + END_N + r"|протокол" + END_M
     + r"|соглашени" + END_N + r")"),
]
ANCHOR_RE = re.compile(
    r"(?<!\w)(?:" + "|".join(f"(?P<k{i}>{p})" for i, (_, p) in enumerate(ANCHORS)) + r")(?!\w)",
    re.I,
)

MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа",
          "сентября", "октября", "ноября", "декабря"]
DATE_CORE = (
    r"(?:(?P<d1>\d{1,2})\.(?P<m1>\d{1,2})\.(?P<y1>\d{4})"
    r"|[«\"]?(?P<d2>\d{1,2})[»\"]?\s+(?P<m2>" + "|".join(MONTHS) + r")\s+(?P<y2>\d{4}))"
    r"(?:\s*(?:года(?!\w)|г\.|г(?!\w)))?"
)
OT_DATE_RE = re.compile(r"от\s+" + DATE_CORE, re.I)
DATE_RE = re.compile(DATE_CORE, re.I)
NUM_RE = re.compile(
    r"(?:№|(?<!\w)N(?:o\.?|º|°)?)\s*"
    r"(?P<num>\d[0-9A-Za-zА-Яа-яЁё]*(?:-[0-9A-Za-zА-Яа-яЁё]+|/[0-9A-Za-zА-Яа-яЁё]+|\.\d+)*)"
)
TITLE_RE = re.compile(
    r"«(?P<t1>[^«»]{1,500}(?:«[^«»]{0,300}»[^«»]{0,500}){0,3})»|\"(?P<t2>[^\"\n]{1,500})\""
)
SEP_RE = re.compile(r"[\s,]{0,10}")
SPACE_RE = re.compile(r"[ \n]{0,10}")
REDACTION_RE = re.compile(r"\((?:в\s+)?ред(?:\.|акци[ия])(?!\w)[^()]{0,250}\)", re.I)
ISSUER_WORD_RE = re.compile(r"\s+(г\.\s*[А-ЯЁ][а-яё-]+|[А-Яа-яЁё][А-Яа-яЁё-]*|«[^«»\n]{1,60}»|\"[^\"\n]{1,60}\")")
ISSUER_STOP = {"от", "и", "или", "в", "во", "на", "по", "с", "со", "о", "об", "к", "для", "а", "также", "не", "что",
               "который", "которым", "которая", "которое", "утвержденный", "утвержденным", "утвержденного"}
ISSUER_DOC_RE = re.compile(r"(?:постановлени|распоряжени|приказ|указ|решени|закон)", re.I)
WS_RE = re.compile(r"\s+")

DOC_NOMINATIVE = [
    ("постановлени", "Постановление"), ("распоряжени", "Распоряжение"), ("приказ", "Приказ"),
    ("указ", "Указ"), ("решени", "Решение"), ("закон", "Закон"), ("положени", "Положение"),
    ("письм", "Письмо"), ("определени", "Определение"), ("протокол", "Протокол"), ("соглашени", "Соглашение"),
]
BASE_QUERY = {
    "federal_constitutional_law": "Федеральный конституционный закон",
    "federal_law": "Федеральный закон",
    "presidential_decree": "Указ Президента Российской Федерации",
    "government_resolution": "Постановление Правительства Российской Федерации",
    "government_order": "Распоряжение Правительства Российской Федерации",
}
# Реквизиты обязательны: без них «федеральным законом» — общая фраза, а не ссылка.
NEEDS_DETAILS = {"federal_constitutional_law", "federal_law", "presidential_decree", "government_resolution",
                 "government_order", "ministry_order", "regional_law", "regional_act"}


class InputError(Exception):
    """Ошибка входных данных (код выхода 2)."""


def normalize(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n").translate(_TRANSLATE)


def collapse(s: str) -> str:
    return WS_RE.sub(" ", s).strip()


def _parse_date(m):
    try:
        if m.group("d1"):
            d, mo, y = int(m.group("d1")), int(m.group("m1")), int(m.group("y1"))
        else:
            d, mo, y = int(m.group("d2")), MONTHS.index(m.group("m2").lower()) + 1, int(m.group("y2"))
    except (TypeError, ValueError):
        return None
    if not (1 <= d <= 31 and 1 <= mo <= 12 and 1800 <= y <= 2100):
        return None
    return f"{y:04d}-{mo:02d}-{d:02d}"


def _normalize_number(num: str) -> str:
    num = num.strip()
    return re.sub(r"(?i)-(фкз|фз)$", lambda m: "-" + m.group(1).upper(), num)


def _collect_issuer(text, pos):
    """Слова органа после вида акта (до «от», «№», знака препинания), не больше 12."""
    words = []
    end = pos
    while len(words) < 12:
        m = ISSUER_WORD_RE.match(text, end)
        if not m:
            break
        word = m.group(1)
        low = word.lower()
        if low in ISSUER_STOP or (word[0] not in "«\"" and ISSUER_DOC_RE.match(word)):
            break
        if word[0] in "«\"":
            # Кавычки в составе органа («Город Пермь»), только если дальше идут реквизиты.
            nxt = SPACE_RE.match(text, m.end()).end()
            if not (OT_DATE_RE.match(text, nxt) or NUM_RE.match(text, nxt)):
                break
        words.append(word)
        end = m.end()
    return (collapse(" ".join(words)) if words else ""), end


def _parse_tail(text, pos):
    """Реквизиты после вида акта: дата, номер (в любом порядке), название, редакция."""
    info = {"date": None, "number": None, "title": None, "redaction_date": None}
    end = pos
    p = SEP_RE.match(text, pos).end()
    m = OT_DATE_RE.match(text, p)
    if m:
        info["date"] = _parse_date(m)
        end = m.end()
        p = SEP_RE.match(text, end).end()
    m = NUM_RE.match(text, p)
    if m:
        info["number"] = _normalize_number(m.group("num"))
        end = m.end()
        p = SEP_RE.match(text, end).end()
        if info["date"] is None:
            m = OT_DATE_RE.match(text, p)
            if m:
                info["date"] = _parse_date(m)
                end = m.end()
    p = SPACE_RE.match(text, end).end()
    m = TITLE_RE.match(text, p)
    if m:
        info["title"] = collapse(m.group("t1") or m.group("t2") or "") or None
        end = m.end()
    p = SPACE_RE.match(text, end).end()
    m = REDACTION_RE.match(text, p)
    if m:
        dm = DATE_RE.search(m.group(0))
        if dm:
            info["redaction_date"] = _parse_date(dm)
    return info, end


def _code_name(anchor: str) -> str:
    low = anchor.lower()
    if "административных правонарушениях" in collapse(low):
        return KOAP
    if "административного судопроизводства" in collapse(low):
        return KAS
    abbr = anchor.split()[0]
    if abbr in CODE_ABBR:
        return CODE_ABBR[abbr]
    for rx, name in CODE_NAME_RES:
        if rx.match(low):
            return name
    return collapse(anchor)


def _doc_nominative(anchor: str) -> str:
    low = anchor.lower()
    for stem, nom in DOC_NOMINATIVE:
        if low.startswith(stem):
            return nom
    return collapse(anchor.split()[0]).capitalize()


def _search_query(ref):
    kind = ref["kind"]
    if kind == "constitution":
        return "Конституция Российской Федерации"
    if kind == "code":
        return ref["name"]
    if kind == "federal_law" and ref["doc_type"] == "Закон Российской Федерации":
        parts = [ref["doc_type"]]
    elif kind in BASE_QUERY:
        parts = [BASE_QUERY[kind]]
    else:
        parts = [ref["doc_type"] + (" " + ref["issuer"] if ref["issuer"] else "")]
    if ref["date"]:
        y, mo, d = ref["date"].split("-")
        parts.append(f"от {d}.{mo}.{y}")
    if ref["number"]:
        parts.append(f"№ {ref['number']}")
    if not ref["number"] and ref["title"]:
        parts.append(f"«{ref['title']}»")
    return " ".join(parts)


def _line_starts(text):
    starts = [0]
    idx = text.find("\n")
    while idx != -1:
        starts.append(idx + 1)
        idx = text.find("\n", idx + 1)
    return starts


def _find_raw(text):
    """Все совпадения в порядке текста (до слияния дублей)."""
    found = []
    pos = 0
    n = len(text)
    while pos < n:
        m = ANCHOR_RE.search(text, pos)
        if not m:
            break
        kind = ANCHORS[int(m.lastgroup[1:])][0]
        anchor = m.group(0)
        issuer = None
        doc_type = _doc_nominative(anchor)
        if kind == "federal_law" and not anchor.lower().startswith("федеральн"):
            doc_type = "Закон Российской Федерации"
        tail_pos = m.end()
        if kind == "ministry_order":
            issuer = collapse(anchor.split(None, 1)[1])
        elif kind == "regional_law":
            issuer = collapse(anchor.split(None, 1)[1])
        elif kind in ("regional_act", "other_act"):
            extra, tail_pos = _collect_issuer(text, m.end())
            base = anchor.split(None, 1)[1] if kind == "regional_act" else ""
            issuer = collapse(base + " " + extra) or None
        elif kind in ("federal_law", "federal_constitutional_law", "constitution", "code"):
            rf = re.match(r"\s+" + RF + r"(?!\w)", text[tail_pos:tail_pos + 40])
            if rf and kind in ("federal_law", "federal_constitutional_law"):
                tail_pos += rf.end()
        info, end = _parse_tail(text, tail_pos)
        if end == tail_pos:
            end = m.end()
        has_details = info["date"] or info["number"] or info["title"]
        number = info["number"] or ""
        # Уточнение вида по суффиксу номера.
        if kind not in ("code", "constitution") and number:
            if number.endswith("-ФКЗ"):
                kind = "federal_constitutional_law"
            elif number.endswith("-ФЗ"):
                kind = "federal_law"
        accept = True
        if kind in NEEDS_DETAILS and not has_details:
            accept = False
        if kind == "other_act" and not (info["date"] and info["number"]):
            accept = False
        if not accept:
            pos = m.end()
            continue
        if kind in ("federal_law", "federal_constitutional_law") and ANCHORS[int(m.lastgroup[1:])][0] == "other_act":
            issuer = None
        elif kind == "other_act" and issuer and ORGAN_RE.search(issuer):
            kind = "regional_act"
        ref = {
            "kind": kind,
            "doc_type": doc_type,
            "issuer": issuer or None,
            "date": info["date"],
            "number": info["number"],
            "title": info["title"],
            "redaction_date": info["redaction_date"],
            "name": None,
            "start": m.start(),
            "end": end,
        }
        if kind == "constitution":
            ref["name"] = "Конституция Российской Федерации"
        elif kind == "code":
            ref["name"] = _code_name(anchor)
        found.append(ref)
        pos = max(end, m.end())
    return found


def _key(ref):
    kind = ref["kind"]
    if kind in ("constitution", "code"):
        return (kind, ref["name"])
    if ref["date"] or ref["number"]:
        return (kind, ref["date"], ref["number"])
    return (kind, "title", (ref["title"] or "").lower())


def extract(text: str) -> dict:
    """Находит ссылки на правовые акты. Возвращает словарь references/total/disclaimer."""
    text = normalize(text or "")
    starts = _line_starts(text)
    raw = _find_raw(text)
    groups = {}
    order = []
    for ref in raw:
        key = _key(ref)
        if key not in groups:
            groups[key] = {"refs": []}
            order.append(key)
        groups[key]["refs"].append(ref)

    # Слияние неполных упоминаний (без даты / только название) с полными.
    by_number = {}
    by_title = {}
    for key in order:
        first = groups[key]["refs"][0]
        if first["kind"] in ("constitution", "code"):
            continue
        if first["date"] and first["number"]:
            by_number.setdefault((first["kind"], first["number"]), key)
        for r in groups[key]["refs"]:
            if r["title"] and (first["date"] or first["number"]):
                by_title.setdefault((first["kind"], r["title"].lower()), key)
    merged_into = {}
    for key in order:
        first = groups[key]["refs"][0]
        if first["kind"] in ("constitution", "code"):
            continue
        target = None
        if first["date"] is None and first["number"]:
            target = by_number.get((first["kind"], first["number"]))
        elif first["date"] is None and first["number"] is None and first["title"]:
            target = by_title.get((first["kind"], first["title"].lower()))
        if target and target != key:
            merged_into[key] = target
    for key, target in merged_into.items():
        groups[target]["refs"].extend(groups[key]["refs"])

    references = []
    for key in order:
        if key in merged_into:
            continue
        refs = sorted(groups[key]["refs"], key=lambda r: r["start"])
        main = next((r for r in refs if r["date"] and r["number"]), refs[0])
        first = refs[0]
        out = {
            "kind": main["kind"],
            "kind_label": KIND_LABELS[main["kind"]],
            "raw": collapse(text[main["start"]:main["end"]]),
            "issuer": main["issuer"],
            "date": main["date"],
            "number": main["number"],
            "title": main["title"] or next((r["title"] for r in refs if r["title"]), None),
            "name": main["name"],
            "redaction_date": next((r["redaction_date"] for r in refs if r["redaction_date"]), None),
            "occurrences": [
                {"line": bisect_right(starts, r["start"]), "start": r["start"], "end": r["end"]} for r in refs
            ],
            "doc_type": main["doc_type"],
            "_first": first["start"],
        }
        out["search_query"] = _search_query(out)
        references.append(out)
    references.sort(key=lambda r: r["_first"])
    for r in references:
        del r["_first"]
        del r["doc_type"]
    return {"references": references, "total": len(references), "disclaimer": DISCLAIMER}


def to_markdown(result: dict) -> str:
    lines = ["# Ссылки на правовые акты", "", f"Найдено актов: **{result['total']}**", ""]
    if not result["references"]:
        lines.append("Ссылки на правовые акты не найдены.")
    for i, r in enumerate(result["references"], 1):
        lines.append(f"## {i}. {r['search_query']}")
        lines.append(f"- Вид: {r['kind_label']}")
        if r["issuer"]:
            lines.append(f"- Орган: {r['issuer']}")
        if r["date"]:
            lines.append(f"- Дата: {r['date']}")
        if r["number"]:
            lines.append(f"- Номер: {r['number']}")
        if r["title"]:
            lines.append(f"- Название: «{r['title']}»")
        if r["redaction_date"]:
            lines.append(f"- Редакция от: {r['redaction_date']}")
        lines.append(f"- Как написано: {r['raw']}")
        lines.append("- Строки: " + ", ".join(str(o["line"]) for o in r["occurrences"]))
        lines.append(f"- Запрос для поиска: `{r['search_query']}`")
        lines.append("")
    lines.append(f"> {result['disclaimer']}")
    return "\n".join(lines) + "\n"


# --- ввод-вывод ---

def decode_bytes(data: bytes) -> str:
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        text = data.decode("utf-16", errors="replace")
    else:
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = data.decode("cp1251", errors="replace")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if not text.strip():
        raise InputError("текст пустой — нечего проверять")
    if len(text) > MAX_CHARS:
        raise InputError(f"текст слишком большой: {len(text)} символов (допустимо не более {MAX_CHARS})")
    return text


def read_input(path: str) -> str:
    if path == "-":
        return decode_bytes(sys.stdin.buffer.read())
    p = Path(path)
    if not p.is_file():
        raise InputError(f"файл не найден: {path}")
    try:
        data = p.read_bytes()
    except OSError as exc:
        raise InputError(f"не удалось прочитать файл {path}: {exc}") from None
    return decode_bytes(data)


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
    parser = _Parser(description="Поиск ссылок на правовые акты в тексте проекта.")
    parser.add_argument("input", help="путь к текстовому файлу или «-» для чтения из stdin")
    parser.add_argument("--format", choices=["json", "md"], default="json", help="формат вывода")
    parser.add_argument("--debug", action="store_true", help="выводить трассировку при ошибке")
    args = parser.parse_args(argv)
    try:
        result = extract(read_input(args.input))
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
