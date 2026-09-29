"""Формальная проверка структуры проекта правового акта (предварительный нормоконтроль).

Проверяет нумерацию пунктов, внутренние отсылки к пунктам и приложениям, наличие положения
о вступлении в силу, неопределённые выражения, незаполненные места, повторы слов и форматы дат.

Запуск:  python check_structure.py ФАЙЛ|- [--format json|md] [--debug]
Коды выхода: 0 — успех, 2 — ошибка входных данных, 1 — непредвиденная ошибка.
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
    "Проверка формальная и эвристическая: нумерация и отсылки распознаются по шаблонам, возможны ложные "
    "срабатывания и пропуски. Каждое замечание проверьте по тексту проекта."
)

TYPE_LABELS = {
    "numbering_gap": "Пропуск в нумерации",
    "numbering_duplicate": "Повтор номера",
    "numbering_restart": "Нарушение последовательности нумерации",
    "broken_reference": "Отсылка к несуществующему пункту",
    "missing_appendix": "Несоответствие приложений",
    "vague_term": "Неопределённое выражение",
    "no_entry_into_force": "Нет положения о вступлении в силу",
    "empty_placeholder": "Незаполненный фрагмент",
    "repeated_word": "Повтор слова",
    "date_format": "Нестандартный формат даты",
}
SEVERITY_ORDER = {"error": 0, "warning": 1, "info": 2}

# Неопределённые выражения: (как показать, шаблон, важность).
VAGUE_TERMS = [
    ("своевременно", r"своевременно", "warning"),
    ("в разумный срок", r"в\s+разумн\w+\s+срок\w*", "warning"),
    ("при необходимости", r"при\s+необходимости", "warning"),
    ("в случае необходимости", r"в\s+случае\s+необходимости", "warning"),
    ("надлежащим образом", r"надлежащим\s+образом", "warning"),
    ("в установленном порядке", r"в\s+установленном\s+порядке", "warning"),
    ("соответствующий орган", r"соответствующ\w*\s+орган\w*", "warning"),
    ("иные документы", r"ин(?:ые|ых|ыми|ым)\s+документ\w*", "warning"),
    ("и т.д.", r"и\s+т\.\s*д\.?", "warning"),
    ("и т.п.", r"и\s+т\.\s*п\.?", "warning"),
    ("и другие", r"и\s+други[ехм]\w*", "warning"),
    ("как правило", r"как\s+правило", "warning"),
    ("по возможности", r"по\s+возможности", "warning"),
    ("в кратчайшие сроки", r"в\s+кратчайши[ех]\s+срок\w*", "warning"),
    ("незамедлительно", r"незамедлительно", "info"),
]
VAGUE_RES = [(label, re.compile(r"(?<!\w)" + p + r"(?!\w)", re.I), sev) for label, p, sev in VAGUE_TERMS]

ENTRY_RE = re.compile(
    r"вступа(?:ет|ют)\s+в\s+(?:законную\s+)?силу|вступ(?:ил|ило|или|ит|ят)\s+в\s+(?:законную\s+)?силу"
    r"|со\s+дня\s+(?:его\s+|их\s+)?официального\s+опубликования|с\s+момента\s+(?:его\s+|их\s+)?подписания"
    r"|со\s+дня\s+(?:его\s+|их\s+)?подписания",
    re.I,
)

_TRANSLATE = {ord(c): " " for c in "       \t\f\v"}
_TRANSLATE.update({ord(c): "-" for c in "‐‑‒–—―−"})
_TRANSLATE.update({ord(c): '"' for c in "“”„‟"})

LETTERS = "абвгдежзиклмнопрстуфхцчшщэюя"  # ё, й, ъ, ы, ь для подпунктов не используются
DOTTED_RE = re.compile(r"[ ]*([1-9]\d{0,3}(?:\.[1-9]\d{0,2}|\.0){0,5})(\.?)(?=[ ]|$)")
PAREN_RE = re.compile(r"[ ]*(\d{1,3})\)(?=[ ]|$)")
LETTER_RE = re.compile(r"[ ]*([а-яё])\)(?=[ ]|$)")
ROMAN_RE = re.compile(r"[ ]*([IVXL]{1,6})\.(?=[ ])")
APPENDIX_HEAD_RE = re.compile(r"[ ]*(?:Приложение|ПРИЛОЖЕНИЕ)(?:[ ]*(?:№|N|No)?[ ]*(\d{1,3}))?(?![\w:])(.*)$")

ITEM = r"(?:\d{1,3}(?:\.\d{1,3}){0,4}(?![\d])|«[а-яё]»|\"[а-яё]\"|[а-яё]\)|\d{1,3}\))"
REF_RE = re.compile(
    r"(?<!\w)(?P<kw>подпункт\w*|пункт\w*|пп\.|п\.)[ \n]*(?P<list>" + ITEM
    + r"(?:(?:[ \n]*(?:,|-)[ \n]*|[ \n]+(?:и|или)[ \n]+)" + ITEM + r"){0,20})",
    re.I,
)
ITEM_RE = re.compile(ITEM, re.I)
LIST_SEP_RE = re.compile(r"[ \n]*(?:(,)|(-)|(и|или)(?!\w))[ \n]*", re.I)
PARENT_RE = re.compile(r"[ \n]+(?:подпункта|пункта|пп\.|п\.)[ \n]*(" + ITEM + r")", re.I)
EXT_RE = re.compile(
    r"(?<!\w)(?:стать[яиеюй]\w*|ст\.\s*\d|ч\.\s*\d|част[иьея](?!\w)|главы\s+\d|федеральн\w*|закон\w*|кодекс\w*|постановлени\w*"
    r"|распоряжени\w*|приказ\w*|указ\w*|решени\w*|методик\w*|конституци\w*|устав\w*|регламент\w*|соглашени\w*"
    r"|договор\w*|(?-i:ГК|НК|БК|ТК|ЗК|ЖК|ГрК|КоАП)(?!\w))",
    re.I,
)
SENTENCE_END_RE = re.compile(r";|\.[ ]*\n|\n[ ]*\n|\.[ ]+(?=[А-ЯЁA-Z])")
PRECEDING_WORD_RE = re.compile(r"(\w+)\W*$")
APP_REF_RE = re.compile(r"(?<!\w)приложени(?:е|я|ю|ем|и|ях|ями|ям)(?!\w)", re.I)
APP_NUM_RE = re.compile(r"[ \n]*(?:№|N|No)?[ \n]*(\d{1,3})(?!\d)")
APP_NEXT_RE = re.compile(r"[ \n]*(?:,|и|-)[ \n]*(?:№|N|No)?[ \n]*(\d{1,3})(?!\d)")
APP_UNNUMBERED_RE = re.compile(
    r"(?<!\w)(?:согласно|в\s+соответствии\s+с|приведенн\w*\s+в|указанн\w*\s+в|установленн\w*\s+в)\s+"
    r"приложени(?:ю|ем|и)(?!\s*(?:№|N|No)?\s*\d)|\(приложение\)",
    re.I,
)
APP_EXTERNAL_RE = re.compile(
    r"[ \n]*к[ \n]+(?!настоящ)(?:[\w-]+[ \n]+){0,2}?(?:постановлени|приказ|распоряжени|закон|решени|указ|методик"
    r"|регламент|соглашени|договор)",
    re.I,
)
REQUISITE_BLANK_RE = re.compile(r"^\s*(?:от\s*)?_+\s*(?:г\.)?\s*(?:№|N)\s*_+\s*$")
PLACEHOLDERS = [
    (re.compile(r"_{3,}"), "незаполненное поле «___»"),
    (re.compile(r"…|\.{3,}"), "многоточие — возможно, пропущен текст"),
    (re.compile(r"\[|\]"), "квадратные скобки — возможно, шаблонная вставка"),
    (re.compile(r"[XxХх]{2}\.[XxХх]{2}\.[XxХх]{2,4}"), "шаблон даты вместо даты"),
    (re.compile(r"<\s*(?:…|\.{2,}|_*|дата|номер|фио|наименование[^<>\n]{0,40})\s*>", re.I), "шаблон «<…>»"),
    (re.compile(r"\{[^{}\n]{0,50}\}"), "шаблон в фигурных скобках"),
]
REPEAT_RE = re.compile(r"(?<![\w-])([^\W\d_]{1,40})(?:[ ]+|[ ]*\n[ ]*)(\1)(?![\w-])", re.I)
ISO_DATE_RE = re.compile(r"(?<![\d-])\d{4}-\d{1,2}-\d{1,2}(?![\d-])")
SLASH_DATE_RE = re.compile(r"(?<![\d/])\d{1,2}/\d{1,2}/\d{2,4}(?![\d/])")
DOT_DATE_RE = re.compile(r"(?<![\d.])(\d{1,2})\.(\d{1,2})\.(\d{4})(?!\d|\.\d)")
SHORT_YEAR_RE = re.compile(r"(?<!\w)от\s+(\d{1,2}\.\d{1,2}\.\d{2})(?!\d|\.\d)", re.I)


class InputError(Exception):
    """Ошибка входных данных (код выхода 2)."""


def normalize(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n").translate(_TRANSLATE)


def _line_starts(text):
    starts = [0]
    idx = text.find("\n")
    while idx != -1:
        starts.append(idx + 1)
        idx = text.find("\n", idx + 1)
    return starts


class _Checker:
    def __init__(self, text):
        self.text = normalize(text or "")
        self.lines = self.text.split("\n")
        self.starts = _line_starts(self.text)
        self.issues = []
        self.points = []
        self.dotted = set()
        self.sub_by_parent = {}
        self.appendix_lines = set()
        self.declared = []

    def line_of(self, offset):
        return bisect_right(self.starts, offset)

    def add(self, type_, severity, line, detail):
        text = self.lines[line - 1].strip()[:200] if line else ""
        self.issues.append({"type": type_, "type_label": TYPE_LABELS[type_], "severity": severity,
                            "line": line, "text": text, "detail": detail})

    # --- нумерация ---
    def scan_points(self):
        section = 0
        section_name = "основной текст"
        last_dotted = None
        last_paren = None
        groups = {}
        for idx, line in enumerate(self.lines, 1):
            m = APPENDIX_HEAD_RE.match(line)
            if m and (not m.group(2).strip() or re.match(r"\s*к\s", m.group(2))):
                section += 1
                num = m.group(1) or ""
                section_name = f"Приложение № {num}" if num else "Приложение"
                self.declared.append((num, idx))
                self.appendix_lines.add(idx)
                last_dotted = last_paren = None
                continue
            kind = None
            m = DOTTED_RE.match(line)
            if m and (m.group(2) or "." in m.group(1)) and line[m.end():].strip():
                kind = "dotted"
                parts = tuple(int(x) for x in m.group(1).split("."))
                number = m.group(1)
                parent = ".".join(map(str, parts[:-1])) or None
                key = (section, "d", parts[:-1])
                ordinal = parts[-1]
                depth = len(parts)
                last_dotted, last_paren = number, None
                self.dotted.add(number)
            else:
                m = PAREN_RE.match(line)
                if m:
                    kind = "paren"
                    number = m.group(1) + ")"
                    ordinal = int(m.group(1))
                    parent = last_dotted
                    key = (section, "p", last_dotted)
                    depth = (last_dotted.count(".") + 2) if last_dotted else 1
                    last_paren = m.group(1)
                    self._register_sub(last_dotted, m.group(1))
                else:
                    m = LETTER_RE.match(line)
                    if m:
                        kind = "letter"
                        letter = m.group(1)
                        number = letter + ")"
                        ordinal = LETTERS.index(letter) + 1 if letter in LETTERS else None
                        parent = (f"{last_dotted} {last_paren})" if last_dotted else f"{last_paren})") \
                            if last_paren else last_dotted
                        key = (section, "l", last_dotted, last_paren)
                        depth = (last_dotted.count(".") + 2 if last_dotted else 1) + (1 if last_paren else 0)
                        self._register_sub(last_dotted, letter)
                    else:
                        m = ROMAN_RE.match(line)
                        if m and _roman(m.group(1)):
                            kind = "roman"
                            number = m.group(1)
                            ordinal = _roman(m.group(1))
                            parent = None
                            key = (section, "r")
                            depth = 0
            if not kind:
                continue
            self.points.append({"number": number, "kind": kind, "line": idx, "depth": depth,
                                "parent": parent, "section": section_name})
            if ordinal is None:
                continue
            g = groups.setdefault(key, {"prev": None, "seen": set()})
            prefix = (".".join(number.split(".")[:-1]) + ".") if kind == "dotted" and "." in number else ""
            self._check_sequence(g, ordinal, number, idx, kind, prefix)
            g["prev"] = ordinal
            g["seen"].add(ordinal)

    def _register_sub(self, dotted, label):
        if not dotted:
            return
        parts = dotted.split(".")
        for i in range(1, len(parts) + 1):
            self.sub_by_parent.setdefault(".".join(parts[:i]), set()).add(label)

    def _check_sequence(self, g, n, number, line, kind, prefix=""):
        prev = g["prev"]
        if kind == "letter":
            show = lambda k: LETTERS[k - 1] + ")"  # noqa: E731
        elif kind == "paren":
            show = lambda k: f"{k})"  # noqa: E731
        else:
            show = lambda k: f"{prefix}{k}"  # noqa: E731
        if prev is None:
            if n != 1:
                self.add("numbering_gap", "error", line,
                         f"Нумерация уровня начинается с «{number}», ожидался номер {show(1)}")
            return
        if n == prev + 1:
            return
        if n == 1:
            self.add("numbering_restart", "warning", line,
                     f"Нумерация начата заново («{number}») после номера {show(prev)}")
        elif n in g["seen"]:
            self.add("numbering_duplicate", "error", line, f"Номер «{number}» уже встречался на этом уровне")
        elif n > prev + 1:
            missing = ", ".join(show(k) for k in range(prev + 1, min(n, prev + 6)))
            more = " и др." if n - prev - 1 > 5 else ""
            self.add("numbering_gap", "error", line,
                     f"После номера {show(prev)} идёт «{number}»: пропущено {missing}{more}")
        else:
            self.add("numbering_restart", "warning", line,
                     f"Номер «{number}» идёт после {show(prev)} — нарушен порядок нумерации")

    # --- отсылки ---
    def _is_external(self, end):
        window = self.text[end:end + 60]
        cut = SENTENCE_END_RE.search(window)
        if cut:
            window = window[:cut.start()]
        m = EXT_RE.search(window)
        if not m:
            return False
        before = PRECEDING_WORD_RE.search(window[:m.start()])
        return not (before and before.group(1).lower().startswith("настоящ"))

    def check_references(self):
        if not self.dotted:
            return
        for m in REF_RE.finditer(self.text):
            items = self._parse_list(m.group("list"))
            chain = []
            pos = m.end()
            for _ in range(3):
                pm = PARENT_RE.match(self.text, pos)
                if not pm:
                    break
                chain.append(_clean_item(pm.group(1)))
                pos = pm.end()
            if self._is_external(pos):
                continue
            kw = m.group("kw").lower()
            is_sub = kw.startswith("подпункт") or kw == "пп."
            line = self.line_of(m.start())
            missing = []
            parent = next((c for c in reversed(chain) if _is_num(c)), None)
            if parent is not None:
                if parent not in self.dotted:
                    missing.append(f"пункт {parent}")
                else:
                    subs = self.sub_by_parent.get(parent, set())
                    for it in items:
                        if "." in it:
                            if it not in self.dotted:
                                missing.append(f"подпункт {it}")
                        elif it not in subs and not (f"{parent}.{it}" in self.dotted):
                            missing.append(f"подпункт {_show_item(it)} пункта {parent}")
            else:
                for it in items:
                    if not _is_num(it):
                        continue
                    if is_sub and "." not in it:
                        continue  # «подпункт 2» без пункта — не проверить
                    if it not in self.dotted:
                        missing.append(("подпункт " if is_sub else "пункт ") + it)
            if missing:
                self.add("broken_reference", "error", line,
                         f"В тексте нет: {', '.join(dict.fromkeys(missing))} (отсылка: {_collapse(self.text[m.start():pos])})")

    def _parse_list(self, s):
        items = []
        pos = 0
        prev = None
        range_next = False
        while pos < len(s):
            im = ITEM_RE.match(s, pos)
            if not im:
                break
            it = _clean_item(im.group(0))
            if range_next and prev is not None:
                items.extend(_expand_range(prev, it))
            else:
                items.append(it)
            prev = it
            pos = im.end()
            sm = LIST_SEP_RE.match(s, pos)
            if not sm or sm.end() == pos:
                break
            range_next = bool(sm.group(2))
            pos = sm.end()
        return list(dict.fromkeys(items))

    # --- приложения ---
    def check_appendices(self):
        referenced = []
        for m in APP_REF_RE.finditer(self.text):
            line = self.line_of(m.start())
            if line in self.appendix_lines:
                continue
            nm = APP_NUM_RE.match(self.text, m.end())
            if not nm:
                continue
            nums = [nm.group(1)]
            pos = nm.end()
            while True:
                nx = APP_NEXT_RE.match(self.text, pos)
                if not nx:
                    break
                nums.append(nx.group(1))
                pos = nx.end()
            if APP_EXTERNAL_RE.match(self.text, pos) or self._is_external(pos):
                continue
            for n in nums:
                referenced.append((n, line))
        for m in APP_UNNUMBERED_RE.finditer(self.text):
            end = m.end()
            if APP_EXTERNAL_RE.match(self.text, end):
                continue
            referenced.append(("", self.line_of(m.start())))
        declared_nums = {n for n, _ in self.declared}
        if "" in declared_nums:
            declared_nums.add("1")
        seen = set()
        for n, line in referenced:
            if n in seen:
                continue
            ok = (n == "" and self.declared) or n in declared_nums
            if not ok:
                seen.add(n)
                what = f"приложение № {n}" if n else "приложение"
                self.add("missing_appendix", "error", line,
                         f"Есть отсылка на {what}, но такого приложения в тексте нет")
        ref_nums = {n for n, _ in referenced}
        for n, line in self.declared:
            used = n in ref_nums or (n in ("", "1") and "" in ref_nums) or (n == "" and "1" in ref_nums)
            if not used:
                what = f"Приложение № {n}" if n else "Приложение"
                self.add("missing_appendix", "info", line, f"{what} есть в тексте, но отсылки на него не найдено")
        self.referenced = sorted({n for n, _ in referenced if n}, key=lambda x: int(x))

    # --- лексика и оформление ---
    def check_lines(self):
        entry_found = bool(ENTRY_RE.search(self.text))
        if not entry_found:
            self.add("no_entry_into_force", "warning", None,
                     "Не найдено положение о вступлении в силу («вступает в силу…», «со дня официального опубликования»)")
        for label, rx, sev in VAGUE_RES:
            for m in rx.finditer(self.text):
                self.add("vague_term", sev, self.line_of(m.start()),
                         f"Неопределённое выражение «{label}» — уточните срок, порядок или субъект")
        for idx, line in enumerate(self.lines, 1):
            if not line.strip():
                continue
            if not REQUISITE_BLANK_RE.match(line):
                for rx, what in PLACEHOLDERS:
                    m = rx.search(line)
                    if m:
                        self.add("empty_placeholder", "error", idx, f"Найдено: {what} («{m.group(0)}»)")
            for m in ISO_DATE_RE.finditer(line):
                self.add("date_format", "info", idx, f"Дата «{m.group(0)}» не в формате ДД.ММ.ГГГГ")
            for m in SLASH_DATE_RE.finditer(line):
                if "№" in line[max(0, m.start() - 3):m.start()]:
                    continue
                self.add("date_format", "info", idx, f"Дата «{m.group(0)}» не в формате ДД.ММ.ГГГГ")
            for m in DOT_DATE_RE.finditer(line):
                d, mo, y = m.group(1), m.group(2), m.group(3)
                if (len(d) < 2 or len(mo) < 2) and 1 <= int(d) <= 31 and 1 <= int(mo) <= 12:
                    self.add("date_format", "info", idx,
                             f"Дата «{m.group(0)}» без ведущих нулей — рекомендуется ДД.ММ.ГГГГ")
            for m in SHORT_YEAR_RE.finditer(line):
                self.add("date_format", "info", idx, f"Дата «{m.group(1)}» с двузначным годом")
        for m in REPEAT_RE.finditer(self.text):
            self.add("repeated_word", "info", self.line_of(m.start()),
                     f"Слово «{m.group(1)}» повторено подряд")

    def result(self):
        self.scan_points()
        self.check_references()
        self.check_appendices()
        self.check_lines()
        self.issues.sort(key=lambda i: (i["line"] if i["line"] is not None else float("inf"),
                                        SEVERITY_ORDER[i["severity"]], i["type"]))
        levels = {}
        for p in self.points:
            levels[str(p["depth"])] = levels.get(str(p["depth"]), 0) + 1
        by_type = {}
        for i in self.issues:
            by_type[i["type"]] = by_type.get(i["type"], 0) + 1
        return {
            "structure": {
                "points": self.points,
                "levels": dict(sorted(levels.items(), key=lambda kv: int(kv[0]))),
                "appendices_declared": [n or "без номера" for n, _ in self.declared],
                "appendices_referenced": self.referenced,
            },
            "issues": self.issues,
            "stats": {"lines": len(self.lines) if self.text else 0, "points": len(self.points),
                      "issues_by_type": by_type},
            "disclaimer": DISCLAIMER,
        }


def _roman(s):
    vals = {"I": 1, "V": 5, "X": 10, "L": 50}
    total = 0
    for i, ch in enumerate(s):
        v = vals[ch]
        total += -v if i + 1 < len(s) and vals[s[i + 1]] > v else v
    return total if total > 0 else None


def _clean_item(it):
    it = it.strip().strip("«»\"").rstrip(")")
    return it.lower()


def _is_num(it):
    return bool(re.fullmatch(r"\d{1,3}(?:\.\d{1,3})*", it))


def _show_item(it):
    return f"«{it}»" if not it[:1].isdigit() else f"{it})"


def _expand_range(a, b):
    if a.isdigit() and b.isdigit() and 0 < int(b) - int(a) <= 50:
        return [str(k) for k in range(int(a) + 1, int(b) + 1)]
    if _is_num(a) and _is_num(b) and "." in a:
        pa, pb = a.rsplit(".", 1), b.rsplit(".", 1)
        if len(pb) == 2 and pa[0] == pb[0] and 0 < int(pb[1]) - int(pa[1]) <= 50:
            return [f"{pa[0]}.{k}" for k in range(int(pa[1]) + 1, int(pb[1]) + 1)]
    if len(a) == 1 and len(b) == 1 and a in LETTERS and b in LETTERS and LETTERS.index(b) > LETTERS.index(a):
        return list(LETTERS[LETTERS.index(a) + 1:LETTERS.index(b) + 1])
    return [b]


def _collapse(s):
    return re.sub(r"\s+", " ", s).strip()


def check(text: str) -> dict:
    """Проверяет структуру текста проекта. Возвращает structure/issues/stats/disclaimer."""
    return _Checker(text).result()


SEV_TITLES = [("error", "Ошибки"), ("warning", "Предупреждения"), ("info", "Сведения")]


def to_markdown(result: dict) -> str:
    st = result["structure"]
    out = ["# Проверка структуры проекта", ""]
    out.append(f"Строк: {result['stats']['lines']}, пунктов: {result['stats']['points']}, "
               f"замечаний: {len(result['issues'])}")
    out += ["", "## Структура", ""]
    if st["levels"]:
        out.append("- Пунктов по уровням: " + ", ".join(f"уровень {k} — {v}" for k, v in st["levels"].items()))
    else:
        out.append("- Нумерованные пункты не найдены")
    out.append("- Приложения в тексте: " + (", ".join(st["appendices_declared"]) or "нет"))
    out.append("- Отсылки на приложения: " + (", ".join(st["appendices_referenced"]) or "нет"))
    out += ["", "## Замечания", ""]
    if not result["issues"]:
        out.append("Замечаний не найдено.")
    for sev, title in SEV_TITLES:
        items = [i for i in result["issues"] if i["severity"] == sev]
        if not items:
            continue
        out += [f"### {title} ({len(items)})", "", "| Строка | Тип | Пояснение | Фрагмент |", "|---|---|---|---|"]
        for i in items:
            frag = i["text"].replace("|", "\\|")
            detail = i["detail"].replace("|", "\\|")
            out.append(f"| {i['line'] if i['line'] else '—'} | {i['type_label']} | {detail} | {frag} |")
        out.append("")
    out.append(f"> {result['disclaimer']}")
    return "\n".join(out) + "\n"


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
    parser = _Parser(description="Формальная проверка структуры проекта правового акта.")
    parser.add_argument("input", help="путь к текстовому файлу или «-» для чтения из stdin")
    parser.add_argument("--format", choices=["json", "md"], default="json", help="формат вывода")
    parser.add_argument("--debug", action="store_true", help="выводить трассировку при ошибке")
    args = parser.parse_args(argv)
    try:
        result = check(read_input(args.input))
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
