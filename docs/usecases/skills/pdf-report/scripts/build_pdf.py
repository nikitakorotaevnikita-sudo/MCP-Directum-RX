#!/usr/bin/env python3
"""Сборка PDF-отчёта из JSON-спецификации.

Запуск:  python build_pdf.py SPEC.json OUTPUT.pdf   (SPEC = "-" — читать из stdin)
Импорт:  from build_pdf import build; build(spec_dict, "out.pdf")

Коды выхода: 0 — успех (в stdout путь к файлу), 2 — ошибка спецификации, 1 — непредвиденная ошибка.
"""
from __future__ import annotations

import json
import math
import os
import re
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from xml.sax.saxutils import escape

# Лимиты защищают от случайно огромного ввода.
MAX_SPEC_BYTES = 30 * 1024 * 1024
MAX_BLOCKS = 500
MAX_TABLE_ROWS = 5000  # PDF на тысячи страниц бесполезен; большие выгрузки — в Excel
MAX_TOTAL_CELLS = 60000
MAX_COLUMNS = 30
MAX_LIST_ITEMS = 2000
MAX_KV_PAIRS = 500
MAX_CELL_CHARS = 20000
MAX_TEXT_CHARS = 100000
MAX_TITLE_CHARS = 500
MAX_URL_CHARS = 2000

EMPTY_TABLE_TEXT = "Нет данных"
FONT_REGULAR = "ReportSans"
FONT_BOLD = "ReportSans-Bold"

SKILL_DIR = Path(__file__).resolve().parent.parent
SYSTEM_FONTS = [
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ("/usr/share/fonts/dejavu/DejaVuSans.ttf", "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf"),
    ("/usr/share/fonts/TTF/DejaVuSans.ttf", "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf"),
    ("/usr/share/fonts/dejavu-sans-fonts/DejaVuSans.ttf", "/usr/share/fonts/dejavu-sans-fonts/DejaVuSans-Bold.ttf"),
    ("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
     "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"),
    ("C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/arialbd.ttf"),
    ("/Library/Fonts/Arial.ttf", "/Library/Fonts/Arial Bold.ttf"),
]

BLOCK_FIELDS = {
    "heading": ({"type", "text"}, {"level"}),
    "paragraph": ({"type", "text"}, set()),
    "bullets": ({"type", "items"}, {"title"}),
    "key_value": ({"type", "pairs"}, {"title"}),
    "table": ({"type", "columns", "rows"}, {"caption", "totals", "font_size"}),
    "note": ({"type", "text"}, {"title"}),
    "warning": ({"type", "text"}, {"title"}),
    "page_break": ({"type"}, set()),
}
TOP_REQUIRED = {"title", "blocks"}
TOP_OPTIONAL = {"subtitle", "author", "date", "orientation"}

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
DATETIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2}(\.\d{1,6})?)?(Z|[+-]\d{2}:?\d{2})?$")
MD_LINK_RE = re.compile(r"\[([^\[\]\n]+)\]\(([a-zA-Z][a-zA-Z0-9+.\-]*:(?:[^\s()]|\([^\s()]*\))*)\)")
BOLD_RE = re.compile(r"\*\*(.+?)\*\*", re.S)


class SpecError(Exception):
    """Ошибка во входной спецификации (код выхода 2)."""

    def __init__(self, errors: list[str] | str):
        self.errors = [errors] if isinstance(errors, str) else list(errors)
        super().__init__("\n".join(self.errors))


def values_word(count: int) -> str:
    """4 значения, 5 значений, 1 значение."""
    if count % 10 == 1 and count % 100 != 11:
        return f"{count} значение"
    if 2 <= count % 10 <= 4 and not 12 <= count % 100 <= 14:
        return f"{count} значения"
    return f"{count} значений"


# ---------- значения ----------

def format_date_text(value: str) -> str | None:
    """ISO-дата/время → ДД.ММ.ГГГГ[ ЧЧ:ММ]. Время берётся как есть, без пересчёта пояса; полночь — только дата."""
    text = value.strip()
    try:
        if DATE_RE.match(text):
            return date.fromisoformat(text).strftime("%d.%m.%Y")
        if DATETIME_RE.match(text):
            moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
            if (moment.hour, moment.minute, moment.second) == (0, 0, 0):
                return moment.strftime("%d.%m.%Y")
            return moment.strftime("%d.%m.%Y %H:%M")
    except ValueError:
        return None
    return None


def format_number(value: int | float) -> str:
    if isinstance(value, int):
        return f"{value:,}".replace(",", "\u00a0")
    text = f"{value:,.2f}".rstrip("0").rstrip(".")
    return text.replace(",", "\u00a0").replace(".", ",")


def check_url(url: Any, path: str, errors: list[str]) -> str | None:
    if not isinstance(url, str) or not url.strip():
        errors.append(f"{path}: ссылка должна быть непустой строкой")
        return None
    url = url.strip()
    if len(url) > MAX_URL_CHARS:
        errors.append(f"{path}: ссылка длиннее {MAX_URL_CHARS} символов")
        return None
    if any(ch.isspace() or ord(ch) < 32 for ch in url):
        errors.append(f"{path}: ссылка содержит пробелы или управляющие символы")
        return None
    parsed = urlparse(url)
    if parsed.scheme.lower() not in ("http", "https") or not parsed.netloc:
        errors.append(f"{path}: допустимы только ссылки http:// или https://, получено «{url[:80]}»")
        return None
    return url


def inline_markup(text: str, path: str, errors: list[str]) -> str:
    """Простой текст → разметка reportlab: экранирование, **жирный**, [текст](url), переводы строк."""
    parts: list[str] = []
    pos = 0
    for match in MD_LINK_RE.finditer(text):
        parts.append(_bold(escape(text[pos:match.start()])))
        url = check_url(match.group(2), path, errors)
        label = _bold(escape(match.group(1)))
        if url:
            parts.append(_anchor(url, label))
        else:
            parts.append(label)
        pos = match.end()
    parts.append(_bold(escape(text[pos:])))
    return "".join(parts).replace("\r\n", "\n").replace("\r", "\n").replace("\n", "<br/>")


def _bold(escaped: str) -> str:
    return BOLD_RE.sub(r"<b>\1</b>", escaped)


def _anchor(url: str, label: str) -> str:
    return f'<a href="{escape(url, {chr(34): "&quot;"})}" color="#1a56b0"><u>{label}</u></a>'


def value_markup(value: Any, path: str, errors: list[str], max_chars: int = MAX_CELL_CHARS) -> str:
    """Значение ячейки/поля → разметка. None → пусто."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "Да" if value else "Нет"
    if isinstance(value, (int, float)):
        if isinstance(value, float) and not math.isfinite(value):
            errors.append(f"{path}: недопустимое число {value}")
            return ""
        return format_number(value)
    if isinstance(value, str):
        if len(value) > max_chars:
            errors.append(f"{path}: текст длиннее {max_chars} символов ({len(value)})")
            return ""
        formatted = format_date_text(value)
        if formatted is not None:
            return formatted
        return inline_markup(value, path, errors)
    if isinstance(value, dict):
        extra = set(value) - {"text", "url"}
        if extra:
            errors.append(f"{path}: неизвестные поля ссылки {sorted(extra)}; допустимы text и url")
            return ""
        if "url" not in value:
            errors.append(f"{path}: у объекта-ссылки нет поля url")
            return ""
        url = check_url(value["url"], f"{path}.url", errors)
        label_raw = value.get("text")
        if label_raw is None or label_raw == "":
            label_raw = value["url"] if isinstance(value["url"], str) else ""
        if not isinstance(label_raw, (str, int, float)) or isinstance(label_raw, bool):
            errors.append(f"{path}.text: ожидалась строка")
            return ""
        label = escape(str(label_raw))
        return _anchor(url, label) if url else label
    errors.append(f"{path}: неподдерживаемый тип значения {type(value).__name__}; "
                  "допустимы строка, число, true/false, null или {\"text\", \"url\"}")
    return ""


# ---------- валидация ----------

def _check_fields(obj: dict, required: set, optional: set, path: str, errors: list[str]) -> bool:
    ok = True
    for name in sorted(required - set(obj)):
        errors.append(f"{path}: отсутствует обязательное поле «{name}»")
        ok = False
    unknown = set(obj) - required - optional
    for name in sorted(unknown):
        errors.append(f"{path}: неизвестное поле «{name}»; допустимые: {', '.join(sorted(required | optional))}")
    return ok


def _text(value: Any, path: str, errors: list[str], max_chars: int, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        errors.append(f"{path}: ожидалась строка, получено {type(value).__name__}")
        return ""
    if not allow_empty and not value.strip():
        errors.append(f"{path}: пустая строка")
    if len(value) > max_chars:
        errors.append(f"{path}: текст длиннее {max_chars} символов ({len(value)})")
        return ""
    return value


def validate(spec: Any) -> dict:
    """Проверяет спецификацию и возвращает нормализованную модель. Бросает SpecError со списком ошибок."""
    errors: list[str] = []
    if not isinstance(spec, dict):
        raise SpecError("спецификация: ожидался JSON-объект {\"title\": ..., \"blocks\": [...]}")
    _check_fields(spec, TOP_REQUIRED, TOP_OPTIONAL, "спецификация", errors)
    model: dict[str, Any] = {"blocks": []}
    model["title"] = _text(spec.get("title", ""), "title", errors, MAX_TITLE_CHARS) if "title" in spec else ""
    for name in ("subtitle", "author"):
        if spec.get(name) is not None:
            model[name] = _text(spec[name], name, errors, MAX_TITLE_CHARS, allow_empty=True)
    if spec.get("date") is not None:
        raw = _text(spec["date"], "date", errors, MAX_TITLE_CHARS, allow_empty=True)
        model["date"] = format_date_text(raw) or raw
    orientation = spec.get("orientation", "portrait")
    if orientation not in ("portrait", "landscape"):
        errors.append(f"orientation: ожидалось \"portrait\" или \"landscape\", получено {orientation!r}")
    model["orientation"] = orientation

    blocks = spec.get("blocks")
    if "blocks" in spec:
        if not isinstance(blocks, list):
            errors.append("blocks: ожидался список блоков")
            blocks = []
        elif len(blocks) > MAX_BLOCKS:
            errors.append(f"blocks: слишком много блоков ({len(blocks)}), максимум {MAX_BLOCKS}")
            blocks = []
    else:
        blocks = []
    total_cells = [0]
    for index, block in enumerate(blocks):
        normalized = _validate_block(block, f"blocks[{index}]", errors, total_cells)
        if normalized:
            model["blocks"].append(normalized)
    if total_cells[0] > MAX_TOTAL_CELLS:
        errors.append(f"blocks: всего ячеек в таблицах {total_cells[0]}, максимум {MAX_TOTAL_CELLS}; "
                      "сократите выборку или выгрузите полный список в Excel")
    if errors:
        raise SpecError(errors)
    return model


def _validate_block(block: Any, path: str, errors: list[str], total_cells: list[int]) -> dict | None:
    if not isinstance(block, dict):
        errors.append(f"{path}: ожидался объект блока с полем type")
        return None
    kind = block.get("type")
    if kind not in BLOCK_FIELDS:
        errors.append(f"{path}.type: неизвестный тип блока {kind!r}; допустимые: {', '.join(BLOCK_FIELDS)}")
        return None
    required, optional = BLOCK_FIELDS[kind]
    if not _check_fields(block, required, optional, path, errors):
        return None
    out: dict[str, Any] = {"type": kind}
    if block.get("title") is not None:
        out["title"] = inline_markup(_text(block["title"], f"{path}.title", errors, MAX_TITLE_CHARS), path, errors)

    if kind == "heading":
        level = block.get("level", 1)
        if level not in (1, 2, 3) or isinstance(level, bool):
            errors.append(f"{path}.level: ожидалось 1, 2 или 3, получено {level!r}")
        out["level"] = level
        out["text"] = inline_markup(_text(block["text"], f"{path}.text", errors, MAX_TITLE_CHARS), path, errors)
    elif kind in ("paragraph", "note", "warning"):
        out["text"] = inline_markup(_text(block["text"], f"{path}.text", errors, MAX_TEXT_CHARS), f"{path}.text", errors)
    elif kind == "bullets":
        items = block["items"]
        if not isinstance(items, list):
            errors.append(f"{path}.items: ожидался список")
            return None
        if len(items) > MAX_LIST_ITEMS:
            errors.append(f"{path}.items: слишком много пунктов ({len(items)}), максимум {MAX_LIST_ITEMS}")
            return None
        out["items"] = [value_markup(item, f"{path}.items[{i}]", errors) for i, item in enumerate(items)]
    elif kind == "key_value":
        out["pairs"] = _validate_pairs(block["pairs"], f"{path}.pairs", errors)
    elif kind == "table":
        _validate_table(block, path, errors, out, total_cells)
    return out


def _validate_pairs(pairs: Any, path: str, errors: list[str]) -> list[tuple[str, str]]:
    if isinstance(pairs, dict):
        pairs = [[key, value] for key, value in pairs.items()]
    if not isinstance(pairs, list):
        errors.append(f"{path}: ожидался список пар [подпись, значение] или объект {{подпись: значение}}")
        return []
    if len(pairs) > MAX_KV_PAIRS:
        errors.append(f"{path}: слишком много пар ({len(pairs)}), максимум {MAX_KV_PAIRS}")
        return []
    result = []
    for i, pair in enumerate(pairs):
        item_path = f"{path}[{i}]"
        if isinstance(pair, dict):
            extra = set(pair) - {"label", "value"}
            if "label" not in pair or extra:
                errors.append(f"{item_path}: ожидался объект {{\"label\", \"value\"}} или пара [подпись, значение]")
                continue
            label, value = pair["label"], pair.get("value")
        elif isinstance(pair, list) and len(pair) == 2:
            label, value = pair
        else:
            got = len(pair) if isinstance(pair, list) else type(pair).__name__
            errors.append(f"{item_path}: ожидалась пара [подпись, значение], получено {got}")
            continue
        label_text = inline_markup(_text(label, f"{item_path}.label", errors, MAX_TITLE_CHARS), item_path, errors)
        result.append((label_text, value_markup(value, f"{item_path}.value", errors)))
    return result


def _validate_table(block: dict, path: str, errors: list[str], out: dict, total_cells: list[int]) -> None:
    columns = block["columns"]
    if not isinstance(columns, list) or not columns:
        errors.append(f"{path}.columns: ожидался непустой список колонок")
        return
    if len(columns) > MAX_COLUMNS:
        errors.append(f"{path}.columns: слишком много колонок ({len(columns)}), максимум {MAX_COLUMNS}")
        return
    cols = []
    for i, col in enumerate(columns):
        col_path = f"{path}.columns[{i}]"
        if isinstance(col, str):
            col = {"title": col}
        if not isinstance(col, dict):
            errors.append(f"{col_path}: ожидался объект {{\"title\", \"width\"?, \"align\"?}} или строка")
            continue
        _check_fields(col, {"title"}, {"width", "align"}, col_path, errors)
        title = inline_markup(_text(col.get("title", ""), f"{col_path}.title", errors, MAX_TITLE_CHARS, allow_empty=True),
                              col_path, errors)
        width = col.get("width", 1)
        if isinstance(width, bool) or not isinstance(width, (int, float)) or not (0 < width <= 1000):
            errors.append(f"{col_path}.width: ожидалось положительное число (относительный вес) до 1000, получено {width!r}")
            width = 1
        align = col.get("align", "left")
        if align not in ("left", "center", "right"):
            errors.append(f"{col_path}.align: ожидалось \"left\", \"center\" или \"right\", получено {align!r}")
            align = "left"
        cols.append({"title": title, "width": float(width), "align": align})
    out["columns"] = cols
    ncols = len(columns)

    rows = block["rows"]
    if not isinstance(rows, list):
        errors.append(f"{path}.rows: ожидался список строк (каждая строка — список значений)")
        return
    if len(rows) > MAX_TABLE_ROWS:
        errors.append(f"{path}.rows: слишком много строк ({len(rows)}), максимум {MAX_TABLE_ROWS}; "
                      "сократите выборку или выгрузите полный список в Excel")
        return
    total_cells[0] += len(rows) * ncols
    if total_cells[0] > MAX_TOTAL_CELLS:
        return
    out["rows"] = [_validate_row(row, f"{path}.rows[{r}]", ncols, errors) for r, row in enumerate(rows)]
    if block.get("totals") is not None:
        out["totals"] = _validate_row(block["totals"], f"{path}.totals", ncols, errors)
    if block.get("caption") is not None:
        out["caption"] = inline_markup(_text(block["caption"], f"{path}.caption", errors, MAX_TITLE_CHARS), path, errors)
    font_size = block.get("font_size")
    if font_size is not None and (isinstance(font_size, bool) or not isinstance(font_size, (int, float))
                                  or not 5 <= font_size <= 14):
        errors.append(f"{path}.font_size: ожидалось число от 5 до 14, получено {font_size!r}")
        font_size = None
    out["font_size"] = font_size


def _validate_row(row: Any, path: str, ncols: int, errors: list[str]) -> list[str]:
    if not isinstance(row, list):
        errors.append(f"{path}: ожидался список из {values_word(ncols)}, получено {type(row).__name__}")
        return [""] * ncols
    if len(row) != ncols:
        errors.append(f"{path}: ожидалось {values_word(ncols)}, получено {len(row)}")
        return [""] * ncols
    return [value_markup(value, f"{path}[{c}]", errors) for c, value in enumerate(row)]


# ---------- шрифты ----------

def find_fonts() -> tuple[str, str]:
    """Порядок поиска: переменные PDF_FONT_REGULAR/PDF_FONT_BOLD, папка fonts/ навыка, системные пути."""
    env_regular = os.environ.get("PDF_FONT_REGULAR")
    env_bold = os.environ.get("PDF_FONT_BOLD")
    if env_regular:
        if not Path(env_regular).is_file():
            raise RuntimeError(f"PDF_FONT_REGULAR указывает на несуществующий файл: {env_regular}")
        bold = env_bold if env_bold and Path(env_bold).is_file() else env_regular
        return env_regular, bold
    candidates = [(str(SKILL_DIR / "fonts" / "DejaVuSans.ttf"), str(SKILL_DIR / "fonts" / "DejaVuSans-Bold.ttf"))]
    candidates += SYSTEM_FONTS
    for regular, bold in candidates:
        if Path(regular).is_file():
            return regular, bold if Path(bold).is_file() else regular
    raise RuntimeError(
        "Не найден TTF-шрифт с кириллицей. Положите DejaVuSans.ttf и DejaVuSans-Bold.ttf в папку fonts/ навыка "
        "или задайте переменные окружения PDF_FONT_REGULAR и PDF_FONT_BOLD."
    )


_fonts_registered = False


def register_fonts() -> None:
    global _fonts_registered
    if _fonts_registered:
        return
    from reportlab.lib.fonts import addMapping
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    regular, bold = find_fonts()
    pdfmetrics.registerFont(TTFont(FONT_REGULAR, regular))
    pdfmetrics.registerFont(TTFont(FONT_BOLD, bold))
    addMapping(FONT_REGULAR, 0, 0, FONT_REGULAR)
    addMapping(FONT_REGULAR, 1, 0, FONT_BOLD)
    addMapping(FONT_REGULAR, 0, 1, FONT_REGULAR)
    addMapping(FONT_REGULAR, 1, 1, FONT_BOLD)
    _fonts_registered = True


# ---------- вёрстка ----------

def _styles():
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
    from reportlab.lib.styles import ParagraphStyle

    base = ParagraphStyle("base", fontName=FONT_REGULAR, fontSize=10, leading=13.5, splitLongWords=1,
                          textColor=colors.HexColor("#1f2328"))
    return {
        "base": base,
        "title": ParagraphStyle("title", parent=base, fontName=FONT_BOLD, fontSize=18, leading=23, spaceAfter=4),
        "subtitle": ParagraphStyle("subtitle", parent=base, fontSize=12, leading=16, spaceAfter=2,
                                   textColor=colors.HexColor("#444c56")),
        "meta": ParagraphStyle("meta", parent=base, fontSize=9, leading=12, textColor=colors.HexColor("#57606a"),
                               spaceAfter=10),
        "h1": ParagraphStyle("h1", parent=base, fontName=FONT_BOLD, fontSize=15, leading=19, spaceBefore=10,
                             spaceAfter=6, keepWithNext=1),
        "h2": ParagraphStyle("h2", parent=base, fontName=FONT_BOLD, fontSize=13, leading=17, spaceBefore=8,
                             spaceAfter=4, keepWithNext=1),
        "h3": ParagraphStyle("h3", parent=base, fontName=FONT_BOLD, fontSize=11, leading=14, spaceBefore=6,
                             spaceAfter=3, keepWithNext=1),
        "para": ParagraphStyle("para", parent=base, spaceAfter=6),
        "bullet": ParagraphStyle("bullet", parent=base, leftIndent=14, bulletIndent=3, spaceAfter=2),
        "caption": ParagraphStyle("caption", parent=base, fontName=FONT_BOLD, fontSize=10, spaceBefore=4,
                                  spaceAfter=4, keepWithNext=1),
        "align": {"left": TA_LEFT, "center": TA_CENTER, "right": TA_RIGHT},
    }


def _cell_style(styles, size: float, align: str, bold: bool = False, header: bool = False):
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle

    key = ("cell", size, align, bold, header)
    if key not in styles:
        styles[key] = ParagraphStyle(
            f"cell-{size}-{align}-{bold}-{header}", parent=styles["base"], fontSize=size, leading=size * 1.25,
            alignment=styles["align"][align], fontName=FONT_BOLD if bold else FONT_REGULAR,
            textColor=colors.white if header else colors.HexColor("#1f2328"),
        )
    return styles[key]


def _table_flowables(block: dict, styles: dict, avail_width: float) -> list:
    from reportlab.lib import colors
    from reportlab.platypus import Paragraph, Table, TableStyle

    cols = block["columns"]
    ncols = len(cols)
    size = block.get("font_size") or (9 if ncols <= 6 else 8 if ncols <= 10 else 7)
    weight_sum = sum(col["width"] for col in cols)
    widths = [avail_width * col["width"] / weight_sum for col in cols]

    header = [Paragraph(col["title"], _cell_style(styles, size, col["align"], bold=True, header=True)) for col in cols]
    data = [header]
    rows = block.get("rows", [])
    commands = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2f4a6d")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#b8c2cc")),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
    ]
    if rows:
        for row in rows:
            data.append([Paragraph(text, _cell_style(styles, size, cols[c]["align"])) for c, text in enumerate(row)])
        # Одна команда на всю таблицу: тысячи BACKGROUND-команд замедляют разбиение по страницам.
        commands.append(("ROWBACKGROUNDS", (0, 1), (-1, len(rows)), [colors.white, colors.HexColor("#f1f4f8")]))
    else:
        empty = Paragraph(EMPTY_TABLE_TEXT, _cell_style(styles, size, "center"))
        data.append([empty] + [""] * (ncols - 1))
        commands.append(("SPAN", (0, 1), (-1, 1)))
    if block.get("totals"):
        index = len(data)
        data.append([Paragraph(text, _cell_style(styles, size, cols[c]["align"], bold=True))
                     for c, text in enumerate(block["totals"])])
        commands += [("BACKGROUND", (0, index), (-1, index), colors.HexColor("#dde5ee")),
                     ("LINEABOVE", (0, index), (-1, index), 1, colors.HexColor("#2f4a6d"))]

    table = Table(data, colWidths=widths, repeatRows=1, splitInRow=1, hAlign="LEFT", longTableOptimize=1)
    table.setStyle(TableStyle(commands))
    out = []
    if block.get("caption"):
        out.append(Paragraph(block["caption"], styles["caption"]))
    out.append(table)
    return out


def _callout(block: dict, styles: dict, avail_width: float):
    from reportlab.lib import colors
    from reportlab.platypus import Paragraph, Table, TableStyle

    warning = block["type"] == "warning"
    title = block.get("title") or ("Внимание" if warning else "Примечание")
    accent, fill = ("#c0392b", "#fdecea") if warning else ("#2f6fb0", "#eaf2fb")
    cell = [Paragraph(f"<b>{title}</b>", styles["para"]), Paragraph(block["text"], styles["para"])]
    table = Table([[cell]], colWidths=[avail_width], splitInRow=1, hAlign="LEFT")
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(fill)),
        ("LINEBEFORE", (0, 0), (0, -1), 3, colors.HexColor(accent)),
        ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))
    return table


def _key_value(block: dict, styles: dict, avail_width: float):
    from reportlab.lib import colors
    from reportlab.platypus import Paragraph, Table, TableStyle

    label_style = _cell_style(styles, 9.5, "left", bold=True)
    value_style = _cell_style(styles, 9.5, "left")
    data = [[Paragraph(label, label_style), Paragraph(value, value_style)] for label, value in block["pairs"]]
    if not data:
        data = [[Paragraph(EMPTY_TABLE_TEXT, value_style), ""]]
    table = Table(data, colWidths=[avail_width * 0.35, avail_width * 0.65], splitInRow=1, hAlign="LEFT")
    table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, -1), 0.3, colors.HexColor("#d0d7de")),
        ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    return table


def _story(model: dict, styles: dict, avail_width: float) -> list:
    from reportlab.platypus import PageBreak, Paragraph, Spacer

    story = [Paragraph(inline_markup(model["title"], "title", []), styles["title"])]
    if model.get("subtitle"):
        story.append(Paragraph(inline_markup(model["subtitle"], "subtitle", []), styles["subtitle"]))
    meta = [part for part in (model.get("author"), model.get("date")) if part]
    story.append(Paragraph(escape(" · ".join(meta)), styles["meta"]) if meta else Spacer(1, 8))

    for block in model["blocks"]:
        kind = block["type"]
        if block.get("title") and kind in ("bullets", "key_value"):
            story.append(Paragraph(block["title"], styles["caption"]))
        if kind == "heading":
            story.append(Paragraph(block["text"], styles[f"h{block['level']}"]))
        elif kind == "paragraph":
            story.append(Paragraph(block["text"], styles["para"]))
        elif kind == "bullets":
            items = block["items"] or [EMPTY_TABLE_TEXT]
            story += [Paragraph(item, styles["bullet"], bulletText="•") for item in items]
            story.append(Spacer(1, 4))
        elif kind == "key_value":
            story += [_key_value(block, styles, avail_width), Spacer(1, 6)]
        elif kind == "table":
            story += _table_flowables(block, styles, avail_width) + [Spacer(1, 8)]
        elif kind in ("note", "warning"):
            story += [_callout(block, styles, avail_width), Spacer(1, 6)]
        elif kind == "page_break":
            story.append(PageBreak())
    return story


def _numbered_canvas_class(generated: str):
    from reportlab.lib import colors
    from reportlab.pdfgen import canvas

    class NumberedCanvas(canvas.Canvas):
        """Откладывает отрисовку страниц, чтобы в колонтитуле знать общее число страниц."""

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._saved_pages: list[dict] = []

        def showPage(self):
            self._saved_pages.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            total = len(self._saved_pages)
            for state in self._saved_pages:
                self.__dict__.update(state)
                self._draw_footer(total)
                super().showPage()
            super().save()

        def _draw_footer(self, total: int):
            width, _ = self._pagesize
            self.saveState()
            self.setFont(FONT_REGULAR, 8)
            self.setFillColor(colors.HexColor("#57606a"))
            self.drawString(15 * 2.835, 10 * 2.835, f"Сформировано: {generated}")
            self.drawRightString(width - 15 * 2.835, 10 * 2.835, f"стр. {self._pageNumber} из {total}")
            self.restoreState()

    return NumberedCanvas


def _render(model: dict, target: str) -> None:
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate

    register_fonts()
    pagesize = landscape(A4) if model["orientation"] == "landscape" else A4
    doc = SimpleDocTemplate(
        target, pagesize=pagesize, leftMargin=15 * mm, rightMargin=15 * mm, topMargin=15 * mm, bottomMargin=18 * mm,
        title=model["title"], author=model.get("author") or "", subject=model.get("subtitle") or "",
        creator="pdf-report skill", lang="ru-RU",
    )
    styles = _styles()
    story = _story(model, styles, doc.width)
    generated = datetime.now().strftime("%d.%m.%Y %H:%M")
    doc.build(story, canvasmaker=_numbered_canvas_class(generated))


# ---------- запись ----------

def _atomic_target(output_path: str | os.PathLike, suffix: str) -> Path:
    path = Path(output_path)
    if path.suffix.lower() != suffix:
        raise SpecError(f"OUTPUT_PATH: ожидалось расширение {suffix}, получено «{path.name}»")
    if path.exists() and path.is_dir():
        raise SpecError(f"OUTPUT_PATH: «{path}» — это папка, нужен путь к файлу")
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def build(spec: dict, output_path: str | os.PathLike) -> Path:
    """Проверяет спецификацию и атомарно записывает PDF. Возвращает абсолютный путь."""
    model = validate(spec)
    path = _atomic_target(output_path, ".pdf")
    fd, tmp = tempfile.mkstemp(prefix=f".{path.stem}.", suffix=".tmp", dir=str(path.parent))
    os.close(fd)
    try:
        _render(model, tmp)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return path.resolve()


def load_spec(source: str) -> Any:
    try:
        if source == "-":
            raw = sys.stdin.buffer.read(MAX_SPEC_BYTES + 1)
        else:
            spec_path = Path(source)
            if not spec_path.is_file():
                raise SpecError(f"SPEC: файл не найден: {source}")
            if spec_path.stat().st_size > MAX_SPEC_BYTES:
                raise SpecError(f"SPEC: файл больше {MAX_SPEC_BYTES // (1024 * 1024)} МБ")
            raw = spec_path.read_bytes()
    except OSError as exc:
        raise SpecError(f"SPEC: не удалось прочитать: {exc}") from exc
    if len(raw) > MAX_SPEC_BYTES:
        raise SpecError(f"SPEC: больше {MAX_SPEC_BYTES // (1024 * 1024)} МБ")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise SpecError(f"SPEC: файл не в кодировке UTF-8 (байт {exc.start})") from exc
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise SpecError(f"SPEC: некорректный JSON: {exc.msg}, строка {exc.lineno}, столбец {exc.colno}") from exc


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2:
        print("Использование: python build_pdf.py SPEC.json|- OUTPUT.pdf", file=sys.stderr)
        return 2
    try:
        result = build(load_spec(args[0]), args[1])
    except SpecError as exc:
        print("Ошибка спецификации:", file=sys.stderr)
        for line in exc.errors[:50]:
            print(f"- {line}", file=sys.stderr)
        if len(exc.errors) > 50:
            print(f"- … и ещё {len(exc.errors) - 50} ошибок", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001
        print(f"Непредвиденная ошибка: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
