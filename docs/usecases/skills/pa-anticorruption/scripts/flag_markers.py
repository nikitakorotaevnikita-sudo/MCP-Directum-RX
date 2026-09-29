"""Словесные маркеры, которые МОГУТ указывать на коррупциогенные факторы
(Методика, утв. постановлением Правительства РФ от 26.02.2010 № 96).

Это вспомогательный эвристический поиск формулировок для проверки экспертом, а не вывод.

Запуск:  python flag_markers.py ФАЙЛ|- [--format json|md] [--debug]
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
CONTEXT_CHARS = 80
DISCLAIMER = ("Маркеры — повод проверить формулировку, а не вывод о коррупциогенности. "
              "Вывод делает эксперт по Методике № 96.")

# Промежуток из 0–4 слов для маркеров вида «устанавливается … органом».
GAP4 = r"\W+(?:\w+\W+){0,4}?"
GAP6 = r"\W+(?:\w+\W+){0,6}?"

# Код фактора → (название по Методике, [(маркер для показа, шаблон)]).
FACTORS = {
    "3а": ("широта дискреционных полномочий — отсутствие или неопределённость сроков, условий или оснований "
           "принятия решения", [
        ("по усмотрению", r"по\s+(?:своему\s+|их\s+|его\s+)?усмотрению"),
        ("вправе", r"вправе"),
        ("может принять решение", r"мо(?:жет|гут)\s+принять\s+решени\w*"),
        ("в исключительных случаях", r"в\s+исключительных\s+случаях"),
        ("в случае необходимости", r"в\s+случае\s+необходимости"),
        ("при необходимости", r"при\s+необходимости"),
        ("по решению", r"по\s+решению"),
        ("в течение разумного срока", r"в\s+течение\s+разумного\s+срока"),
    ]),
    "3б": ("определение компетенции по формуле «вправе» — диспозитивное установление возможности совершения "
           "органами действий в отношении граждан и организаций", [
        ("вправе", r"вправе"),
        ("может", r"мо(?:жет|гут)"),
        ("имеет право", r"(?:имеет|имеют)\s+право"),
        ("допускается", r"допуска(?:ется|ются)"),
    ]),
    "3в": ("выборочное изменение объёма прав — возможность необоснованного установления исключений из общего "
           "порядка по усмотрению органов", [
        ("в отдельных случаях", r"в\s+отдельных\s+случаях"),
        ("за исключением случаев", r"за\s+исключением\s+случа\w*"),
        ("как правило", r"как\s+правило"),
        ("по согласованию с", r"по\s+согласованию\s+с(?:о)?"),
    ]),
    "3г": ("чрезмерная свобода подзаконного нормотворчества — наличие бланкетных и отсылочных норм", [
        ("устанавливается … органом", r"устанавлива(?:ется|ются)" + GAP4 + r"орган\w*"),
        ("определяется … органом", r"определя(?:ется|ются)" + GAP4 + r"орган\w*"),
        ("утверждается … органом", r"утвержда(?:ется|ются)" + GAP4 + r"орган\w*"),
        ("в порядке, установленном", r"в\s+порядке,?\s+установленн\w*"),
        ("в порядке, определяемом", r"в\s+порядке,?\s+определяем\w*"),
        ("отсылочная норма", r"отсылочн\w*"),
    ]),
    "3ж": ("отсутствие или неполнота административных процедур — отсутствие порядка совершения органами "
           "определённых действий", [
        ("в установленном порядке", r"в\s+установленном\s+порядке"),
        ("в порядке, установленном законодательством", r"в\s+порядке,?\s+установленн\w*\s+законодательств\w*"),
        ("в соответствии с законодательством", r"в\s+соответствии\s+с(?:о)?\s+(?:действующим\s+)?законодательств\w*"),
    ]),
    "3з": ("отказ от конкурсных (аукционных) процедур — закрепление административного порядка предоставления "
           "права (блага)", [
        ("без проведения конкурса", r"без\s+проведения\s+(?:конкурс|аукцион)\w*"),
        ("без проведения торгов", r"без\s+проведения\s+торг\w*"),
        ("единственного поставщика", r"единственн\w+\s+поставщик\w*"),
        ("по решению … без", r"по\s+решению" + GAP6 + r"без"),
    ]),
    "4а": ("наличие завышенных требований к лицу, предъявляемых для реализации принадлежащего ему права", [
        ("иные документы", r"ин(?:ые|ых|ыми|ым)\s+документ\w*"),
        ("другие документы", r"друг(?:ие|их|ими|им)\s+документ\w*"),
        ("и другие", r"и\s+други[ехм]\w*"),
        ("а также иные", r"а\s+также\s+ин(?:ые|ых|ыми|ым|ой|ое|ая)"),
        ("по требованию", r"по\s+требованию"),
        ("дополнительные документы", r"дополнительн\w+\s+документ\w*"),
    ]),
    "4в": ("юридико-лингвистическая неопределённость — употребление неустоявшихся, двусмысленных терминов и "
           "категорий оценочного характера", [
        ("и т.д.", r"и\s+т\.\s*д\.?"),
        ("и т.п.", r"и\s+т\.\s*п\.?"),
        ("и другие", r"и\s+други[ехм]\w*"),
        ("иные", r"ин(?:ые|ых|ыми|ым|ой|ое|ая|ого|ому|ую)"),
        ("соответствующий", r"соответствующ\w*"),
        ("надлежащий", r"надлежащ\w*"),
        ("своевременно", r"своевременн\w*"),
        ("в разумный срок", r"в\s+разумн\w+\s+срок\w*"),
        ("по возможности", r"по\s+возможности"),
        ("как правило", r"как\s+правило"),
    ]),
}
FACTOR_ORDER = list(FACTORS)
COMPILED = {
    code: [(label, re.compile(r"(?<!\w)" + p + r"(?!\w)", re.I)) for label, p in markers]
    for code, (_, markers) in FACTORS.items()
}

_TRANSLATE = {ord(c): " " for c in "       \t\f\v"}
_TRANSLATE.update({ord(c): "-" for c in "‐‑‒–—―−"})
_TRANSLATE.update({ord(c): '"' for c in "“”„‟"})
WS_RE = re.compile(r"\s+")


class InputError(Exception):
    """Ошибка входных данных (код выхода 2)."""


def _line_starts(text):
    starts = [0]
    idx = text.find("\n")
    while idx != -1:
        starts.append(idx + 1)
        idx = text.find("\n", idx + 1)
    return starts


def _context(text, start, end):
    a = max(0, start - CONTEXT_CHARS)
    b = min(len(text), end + CONTEXT_CHARS)
    s = WS_RE.sub(" ", text[a:b]).strip()
    return ("…" if a > 0 else "") + s + ("…" if b < len(text) else "")


def flag(text: str) -> dict:
    """Ищет словесные маркеры коррупциогенных факторов. Возвращает factors/total_matches/disclaimer."""
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n").translate(_TRANSLATE)
    starts = _line_starts(text)
    factors = []
    total = 0
    for code in FACTOR_ORDER:
        matches = []
        for label, rx in COMPILED[code]:
            for m in rx.finditer(text):
                matches.append((m.start(), -(m.end() - m.start()), m.end(), label))
        matches.sort()
        kept = []
        max_end = -1
        seen = set()
        for start, _, end, label in matches:
            if (label, start) in seen:
                continue
            # Совпадение целиком внутри уже учтённого (более длинного) маркера этого же фактора не дублируем.
            if end <= max_end:
                continue
            seen.add((label, start))
            max_end = max(max_end, end)
            kept.append({"marker": label, "line": bisect_right(starts, start), "found": text[start:end],
                         "context": _context(text, start, end)})
        if kept:
            factors.append({"code": code, "factor": FACTORS[code][0], "markers_found": kept, "count": len(kept)})
            total += len(kept)
    return {"factors": factors, "total_matches": total, "disclaimer": DISCLAIMER}


def to_markdown(result: dict) -> str:
    out = ["# Маркеры коррупциогенных факторов", "",
           f"Всего совпадений: **{result['total_matches']}**, факторов с маркерами: **{len(result['factors'])}**", ""]
    if not result["factors"]:
        out.append("Словесные маркеры не найдены. Это не означает отсутствия коррупциогенных факторов.")
        out.append("")
    for f in result["factors"]:
        out += [f"## Фактор {f['code']} — {f['factor']}", "", f"Совпадений: {f['count']}", "",
                "| Строка | Маркер | Контекст |", "|---|---|---|"]
        for m in f["markers_found"]:
            ctx = m["context"].replace("|", "\\|")
            out.append(f"| {m['line']} | {m['marker']} | {ctx} |")
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
    parser = _Parser(description="Поиск словесных маркеров коррупциогенных факторов (Методика № 96).")
    parser.add_argument("input", help="путь к текстовому файлу или «-» для чтения из stdin")
    parser.add_argument("--format", choices=["json", "md"], default="json", help="формат вывода")
    parser.add_argument("--debug", action="store_true", help="выводить трассировку при ошибке")
    args = parser.parse_args(argv)
    try:
        result = flag(read_input(args.input))
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
