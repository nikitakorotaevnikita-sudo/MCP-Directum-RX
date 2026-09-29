"""Сравнение двух редакций правового акта по пунктам.

Делит каждый текст на единицы (преамбула, пункты «1.», «1.1.», подпункты «2)», «а)», приложения),
сопоставляет их по номерам и по тексту и показывает добавленные, исключённые, изменённые
и перенумерованные единицы с пословной разницей. Если нумерации нет — сравнивает абзацы.

Запуск:  python compare_texts.py СТАРАЯ НОВАЯ [--format json|md] [--context N] [--debug]
         (вместо одного из файлов можно указать «-» — текст читается из stdin)
Коды выхода: 0 — успех, 2 — ошибка входных данных, 1 — непредвиденная ошибка.
"""

import argparse
import difflib
import json
import re
import sys
import traceback
from pathlib import Path

MAX_CHARS = 2_000_000
RENUMBER_THRESHOLD = 0.9
DISCLAIMER = (
    "Сопоставление выполнено автоматически по номерам пунктов и сходству текста. Перенумерация и границы "
    "пунктов определяются эвристически; различия только в пробелах, виде кавычек и тире не учитываются."
)

# Однозначные замены 1:1 (длина строки не меняется); re.sub быстрее str.translate на кириллице.
_SPACES_RE = re.compile("[\u00a0\u2007\u202f\u2009\u200a\u2002\u2003\t\f\v]")
_DASHES_RE = re.compile("[\u2010\u2011\u2012\u2013\u2014\u2015\u2212]")
_QUOTES_RE = re.compile("[«»\u201c\u201d\u201e\u201f\u2033]")


def _line_norm(s):
    return _DASHES_RE.sub("-", _SPACES_RE.sub(" ", s))


def _token_norm(s):
    return _QUOTES_RE.sub('"', _DASHES_RE.sub("-", s))


DOTTED_RE = re.compile(r"[ ]*([1-9]\d{0,3}(?:\.[1-9]\d{0,2}|\.0){0,5})(\.?)(?=[ ]|$)")
PAREN_RE = re.compile(r"[ ]*(\d{1,3})\)(?=[ ]|$)")
LETTER_RE = re.compile(r"[ ]*([а-яё])\)(?=[ ]|$)")
APPENDIX_HEAD_RE = re.compile(r"[ ]*(?:Приложение|ПРИЛОЖЕНИЕ)(?:[ ]*(?:№|N|No)?[ ]*(\d{1,3}))?(?![\w:])(.*)$")
TOKEN_RE = re.compile(r"\w+|[^\w\s]")
WS_RE = re.compile(r"\s+")
PREAMBLE = "преамбула"


class InputError(Exception):
    """Ошибка входных данных (код выхода 2)."""


def _norm_text(s):
    return WS_RE.sub(" ", _token_norm(s)).strip()


class Unit:
    __slots__ = ("key", "lines", "text", "tokens", "norm_tokens", "norm")

    def __init__(self, key):
        self.key = key
        self.lines = []

    def finish(self):
        text = self.text = "\n".join(self.lines).strip()
        self.tokens = [(m.start(), m.end()) for m in TOKEN_RE.finditer(text)]
        mapped = _token_norm(text)  # замена 1:1, позиции совпадают
        self.norm_tokens = [mapped[a:b] for a, b in self.tokens]
        self.norm = _norm_text(text)


def split_units(text):
    """Делит текст на единицы. Возвращает (список Unit, есть ли нумерация)."""
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    units = []
    current = Unit(PREAMBLE)
    appendix = ""
    last_dotted = None
    last_paren = None
    numbered = False
    for raw in lines:
        line = _line_norm(raw)
        key = None
        body = raw
        m = APPENDIX_HEAD_RE.match(line)
        if m and (not m.group(2).strip() or re.match(r"\s*к\s", m.group(2))):
            appendix = f"Приложение № {m.group(1)}" if m.group(1) else "Приложение"
            key = appendix
            last_dotted = last_paren = None
        else:
            m = DOTTED_RE.match(line)
            if m and (m.group(2) or "." in m.group(1)) and line[m.end():].strip():
                last_dotted, last_paren = m.group(1), None
                key = f"п. {m.group(1)}"
                body = raw[m.end():]
            else:
                m = PAREN_RE.match(line) or LETTER_RE.match(line)
                if m:
                    label = m.group(1) + ")"
                    if m.re is PAREN_RE:
                        last_paren = label
                        parts = [f"п. {last_dotted}"] if last_dotted else []
                    else:
                        parts = ([f"п. {last_dotted}"] if last_dotted else []) + ([f"пп. {last_paren}"] if last_paren else [])
                    key = ", ".join(parts + [f"пп. {label}"])
                    body = raw[m.end():]
            if key and appendix:
                key = f"{appendix}, {key}"
            if key:
                numbered = True
        if key:
            if current.lines or current.key != PREAMBLE:
                units.append(current)
            current = Unit(key)
            if body.strip() and key != appendix:
                current.lines.append(body.strip())
            elif key == appendix:
                current.lines.append(raw.strip())
        else:
            current.lines.append(raw.rstrip())
    if current.lines or current.key != PREAMBLE:
        units.append(current)
    units = [u for u in units if "".join(u.lines).strip() or u.key != PREAMBLE]
    # Повторяющиеся номера делаем уникальными.
    seen = {}
    for u in units:
        if u.key in seen:
            seen[u.key] += 1
            u.key = f"{u.key} (#{seen[u.key]})"
        else:
            seen[u.key] = 1
    for u in units:
        u.finish()
    return units, numbered


def split_paragraphs(text):
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    paras = [p for p in re.split(r"\n[ \t ]*\n", text) if p.strip()]
    units = []
    for i, p in enumerate(paras, 1):
        u = Unit(f"абзац {i}")
        u.lines = [p.strip()]
        u.finish()
        units.append(u)
    return units


def _matcher(a, b):
    # На длинных единицах включаем autojunk, иначе сравнение становится квадратичным.
    return difflib.SequenceMatcher(None, a, b, autojunk=len(b) > 2000)


class _Sim:
    """Сходство единиц по словам (0..1) с кэшем: SequenceMatcher на повторяющихся словах бывает дорогим."""

    def __init__(self):
        self.cache = {}
        self.matchers = {}

    def matcher(self, a, b):
        key = (id(a), id(b))
        sm = self.matchers.get(key)
        if sm is None:
            sm = self.matchers[key] = _matcher(a.norm_tokens, b.norm_tokens)
        return sm

    def ratio(self, a, b):
        key = (id(a), id(b))
        r = self.cache.get(key)
        if r is None:
            if a.norm == b.norm:
                r = 1.0
            elif not a.norm_tokens or not b.norm_tokens:
                r = 0.0
            else:
                sm = self.matcher(a, b)
                rq = sm.real_quick_ratio()
                r = rq * 0.5 if rq < 0.3 else sm.ratio()
            r = round(r, 3)
            self.cache[key] = r
        return r

    @staticmethod
    def length_ok(a, b, threshold):
        la, lb = len(a.norm_tokens), len(b.norm_tokens)
        return la + lb > 0 and 2 * min(la, lb) / (la + lb) >= threshold

    @staticmethod
    def quick(a, b):
        return _matcher(a.norm_tokens, b.norm_tokens).quick_ratio()

    def at_least(self, a, b, threshold):
        return self.length_ok(a, b, threshold) and self.quick(a, b) >= threshold and self.ratio(a, b) >= threshold


def _slice(unit, i1, i2):
    if i1 >= i2:
        return ""
    text = unit.text
    return text[unit.tokens[i1][0]:unit.tokens[i2 - 1][1]]


def word_diff(a, b, context=8, sm=None):
    """Пословная разница двух единиц. Длинные неизменённые участки сворачиваются в «…»."""
    sm = sm or _matcher(a.norm_tokens, b.norm_tokens)
    ops = sm.get_opcodes()
    out = []
    for n, (tag, i1, i2, j1, j2) in enumerate(ops):
        if tag == "equal":
            length = i2 - i1
            first, last = n == 0, n == len(ops) - 1
            if first and last:
                limit = length
            elif first or last:
                limit = context
            else:
                limit = 2 * context
            if length > limit:
                if first:
                    before = "… " + _slice(a, i2 - context, i2) if context else "…"
                    after = "… " + _slice(b, j2 - context, j2) if context else "…"
                elif last:
                    before = _slice(a, i1, i1 + context) + " …" if context else "…"
                    after = _slice(b, j1, j1 + context) + " …" if context else "…"
                else:
                    before = (_slice(a, i1, i1 + context) + " … " + _slice(a, i2 - context, i2)) if context else "…"
                    after = (_slice(b, j1, j1 + context) + " … " + _slice(b, j2 - context, j2)) if context else "…"
            else:
                before, after = _slice(a, i1, i2), _slice(b, j1, j2)
            out.append({"op": "equal", "before": before, "after": after})
        else:
            op = {"replace": "replace", "delete": "delete", "insert": "insert"}[tag]
            out.append({"op": op, "before": _slice(a, i1, i2), "after": _slice(b, j1, j2)})
    return out


def _align_numbered(old, new, sim):
    pairs = []  # (old_idx, new_idx)
    used_o, used_n = set(), set()
    # 1) Полностью совпадающий текст (с учётом порядка).
    sm = difflib.SequenceMatcher(None, [u.norm for u in old], [u.norm for u in new], autojunk=False)
    for blk in sm.get_matching_blocks():
        for k in range(blk.size):
            if old[blk.a + k].norm:
                pairs.append((blk.a + k, blk.b + k))
                used_o.add(blk.a + k)
                used_n.add(blk.b + k)
    by_norm = {}
    for j, u in enumerate(new):
        if j not in used_n and u.norm:
            by_norm.setdefault(u.norm, []).append(j)
    for i, u in enumerate(old):
        if i in used_o or not u.norm:
            continue
        cand = by_norm.get(u.norm)
        if cand:
            j = cand.pop(0)
            pairs.append((i, j))
            used_o.add(i)
            used_n.add(j)
    new_by_key = {u.key: j for j, u in enumerate(new)}
    # 2) Очень похожий текст: сначала тот же номер, затем другой (перенумерация).
    for i, u in enumerate(old):
        j = new_by_key.get(u.key)
        if i in used_o or j is None or j in used_n:
            continue
        if sim.at_least(u, new[j], RENUMBER_THRESHOLD):
            pairs.append((i, j))
            used_o.add(i)
            used_n.add(j)
    rest_o = [i for i in range(len(old)) if i not in used_o]
    rest_n = [j for j in range(len(new)) if j not in used_n]
    # Для больших текстов сравниваем только соседей по порядку (окно), чтобы не уйти в квадрат.
    window = None if len(rest_o) * len(rest_n) <= 40_000 else 6
    quick = {}
    sm = difflib.SequenceMatcher(None, autojunk=False)
    for rj, j in enumerate(rest_n):
        sm.set_seq2(new[j].norm_tokens)
        lo, hi = (0, len(rest_o)) if window is None else (max(0, rj - window), min(len(rest_o), rj + window + 1))
        for i in rest_o[lo:hi]:
            if not sim.length_ok(old[i], new[j], RENUMBER_THRESHOLD):
                continue
            sm.set_seq1(old[i].norm_tokens)
            q = sm.quick_ratio()
            if q >= RENUMBER_THRESHOLD:
                quick.setdefault(i, []).append((-q, abs(i - j), j))
    cands = []
    for i, lst in quick.items():
        # Точное сходство считаем только для трёх лучших по быстрой оценке.
        for _, dist, j in sorted(lst)[:3]:
            r = sim.ratio(old[i], new[j])
            if r >= RENUMBER_THRESHOLD:
                cands.append((-r, dist, i, j))
    for _, _, i, j in sorted(cands):
        if i in used_o or j in used_n:
            continue
        pairs.append((i, j))
        used_o.add(i)
        used_n.add(j)
    # 3) Оставшиеся с одинаковым номером — изменённые.
    for i, u in enumerate(old):
        j = new_by_key.get(u.key)
        if i in used_o or j is None or j in used_n:
            continue
        pairs.append((i, j))
        used_o.add(i)
        used_n.add(j)
    return pairs, used_o, used_n


def _align_paragraphs(old, new):
    pairs = []
    sm = difflib.SequenceMatcher(None, [u.norm for u in old], [u.norm for u in new], autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal" or tag == "replace":
            for k in range(min(i2 - i1, j2 - j1)):
                pairs.append((i1 + k, j1 + k))
    used_o = {i for i, _ in pairs}
    used_n = {j for _, j in pairs}
    return pairs, used_o, used_n


def compare(old_text: str, new_text: str, context: int = 8) -> dict:
    """Сравнивает две редакции. Возвращает summary/added/removed/changed/renumbered/disclaimer."""
    old, old_num = split_units(old_text)
    new, new_num = split_units(new_text)
    mode = "points"
    sim = _Sim()
    if not (old_num and new_num):
        old, new = split_paragraphs(old_text), split_paragraphs(new_text)
        mode = "paragraphs"
        pairs, used_o, used_n = _align_paragraphs(old, new)
    else:
        pairs, used_o, used_n = _align_numbered(old, new, sim)
        # Освобождаем память: нужны только сопоставители для выбранных пар.
        keep = {(id(old[i]), id(new[j])) for i, j in pairs}
        sim.matchers = {k: v for k, v in sim.matchers.items() if k in keep}
    added, removed, changed, renumbered = [], [], [], []
    unchanged = 0
    for i, j in sorted(pairs, key=lambda p: (p[1], p[0])):
        a, b = old[i], new[j]
        same_text = a.norm == b.norm
        if mode == "points" and a.key != b.key and (same_text or sim.ratio(a, b) >= RENUMBER_THRESHOLD):
            entry = {"old_unit": a.key, "new_unit": b.key, "similarity": 1.0 if same_text else sim.ratio(a, b)}
            if not same_text:
                entry["diff"] = word_diff(a, b, context, sim.matcher(a, b))
            renumbered.append(entry)
            continue
        if same_text:
            unchanged += 1
            continue
        unit = a.key if a.key == b.key else f"{a.key} → {b.key}"
        changed.append({"unit": unit, "before": a.text, "after": b.text, "similarity": sim.ratio(a, b),
                        "diff": word_diff(a, b, context, sim.matcher(a, b))})
    for i, u in enumerate(old):
        if i not in used_o:
            removed.append({"unit": u.key, "text": u.text})
    for j, u in enumerate(new):
        if j not in used_n:
            added.append({"unit": u.key, "text": u.text})
    return {
        "mode": mode,
        "summary": {"added": len(added), "removed": len(removed), "changed": len(changed),
                    "unchanged": unchanged, "renumbered": len(renumbered)},
        "added": added,
        "removed": removed,
        "changed": changed,
        "renumbered": renumbered,
        "disclaimer": DISCLAIMER,
    }


def _md_escape(s):
    return s.replace("*", "\\*").replace("~", "\\~")


def _md_diff(diff):
    parts = []
    for d in diff:
        if d["op"] == "equal":
            parts.append(_md_escape(d["after"]))
        elif d["op"] == "delete":
            parts.append(f"~~**{_md_escape(d['before'])}**~~")
        elif d["op"] == "insert":
            parts.append(f"**{_md_escape(d['after'])}**")
        else:
            parts.append(f"~~**{_md_escape(d['before'])}**~~ **{_md_escape(d['after'])}**")
    joined = WS_RE.sub(" ", " ".join(p for p in parts if p)).strip()
    return re.sub(r" +([.,;:!?)»])", r"\1", joined)


def _quote(s):
    return "\n".join("> " + line if line else ">" for line in s.split("\n"))


def to_markdown(result: dict) -> str:
    s = result["summary"]
    out = ["# Сравнение редакций", "",
           f"Итого: добавлено — {s['added']}, исключено — {s['removed']}, изменено — {s['changed']}, "
           f"перенумеровано — {s['renumbered']}, без изменений — {s['unchanged']}.", ""]
    if result["mode"] == "paragraphs":
        out += ["Нумерация пунктов не найдена — сравнение выполнено по абзацам.", ""]
    out += ["## Добавлено", ""]
    if not result["added"]:
        out += ["Нет.", ""]
    for u in result["added"]:
        out += [f"### {u['unit']}", "", _quote(u["text"]), ""]
    out += ["## Исключено", ""]
    if not result["removed"]:
        out += ["Нет.", ""]
    for u in result["removed"]:
        out += [f"### {u['unit']}", "", _quote(u["text"]), ""]
    out += ["## Изменено", ""]
    if not result["changed"]:
        out += ["Нет.", ""]
    for u in result["changed"]:
        out += [f"### {u['unit']} (сходство {u['similarity']:.2f})", "", "Было:", "", _quote(u["before"]), "",
                "Стало:", "", _quote(u["after"]), "", "Правка: " + _md_diff(u["diff"]), ""]
    out += ["## Перенумеровано", ""]
    if not result["renumbered"]:
        out += ["Нет.", ""]
    for u in result["renumbered"]:
        line = f"- {u['old_unit']} → {u['new_unit']} (сходство {u['similarity']:.2f})"
        if u.get("diff"):
            line += "; правка: " + _md_diff(u["diff"])
        out.append(line)
    out += ["", f"> {result['disclaimer']}"]
    return "\n".join(out) + "\n"


# --- ввод-вывод ---

def decode_bytes(data: bytes, name: str) -> str:
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
        raise InputError(f"{name}: текст пустой — нечего сравнивать")
    if len(text) > MAX_CHARS:
        raise InputError(f"{name}: текст слишком большой: {len(text)} символов (допустимо не более {MAX_CHARS})")
    return text


def read_input(path: str, stdin_cache: dict) -> str:
    if path == "-":
        if "data" not in stdin_cache:
            stdin_cache["data"] = sys.stdin.buffer.read()
        return decode_bytes(stdin_cache["data"], "stdin")
    p = Path(path)
    if not p.is_file():
        raise InputError(f"файл не найден: {path}")
    try:
        data = p.read_bytes()
    except OSError as exc:
        raise InputError(f"не удалось прочитать файл {path}: {exc}") from None
    return decode_bytes(data, path)


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


def _non_negative(value):
    try:
        n = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("нужно целое число") from None
    if n < 0 or n > 1000:
        raise argparse.ArgumentTypeError("допустимо от 0 до 1000")
    return n


def main(argv=None) -> int:
    parser = _Parser(description="Сравнение двух редакций правового акта по пунктам.")
    parser.add_argument("old", help="файл старой редакции (или «-» — stdin)")
    parser.add_argument("new", help="файл новой редакции (или «-» — stdin)")
    parser.add_argument("--format", choices=["json", "md"], default="json", help="формат вывода")
    parser.add_argument("--context", type=_non_negative, default=8,
                        help="сколько слов неизменённого текста показывать вокруг правок (по умолчанию 8)")
    parser.add_argument("--debug", action="store_true", help="выводить трассировку при ошибке")
    args = parser.parse_args(argv)
    try:
        if args.old == "-" and args.new == "-":
            raise InputError("из stdin можно прочитать только одну редакцию")
        cache = {}
        result = compare(read_input(args.old, cache), read_input(args.new, cache), args.context)
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
