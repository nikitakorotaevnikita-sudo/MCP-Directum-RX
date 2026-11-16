#!/usr/bin/env python3
"""Презентация PowerPoint (.pptx) из JSON-спецификации: титульный слайд + ровно 4 слайда содержания.

Вызов:  python build_pptx.py SPEC.json OUTPUT.pptx   (SPEC = "-" — спецификация из stdin)
Коды выхода: 0 — успех (в stdout абсолютный путь), 2 — ошибка спецификации (в stderr — что исправить),
1 — непредвиденная ошибка.
Из Python: build(spec_dict, "файл.pptx") -> Path; при ошибке спецификации — SpecError.

Стиль: 16:9 (13.333 x 7.5 дюйма), Calibri, заголовки тёмно-синие #1F497D, акцент #4F81BD, текст #262626.
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from datetime import date
from pathlib import Path
from typing import Any

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Inches, Pt

CONTENT_SLIDES = 4
MAX_BULLETS = 5
MAX_SUB_BULLETS = 3
MAX_TITLE_CHARS = 120
MAX_SLIDE_TITLE_CHARS = 90
MAX_BULLET_CHARS = 220
MAX_TABLE_COLUMNS = 6
MAX_TABLE_ROWS = 8
MAX_CELL_CHARS = 120
MAX_NOTES_CHARS = 3000
PENDING_LABEL = "данные уточняются"

FONT = "Calibri"
NAVY = RGBColor(0x1F, 0x49, 0x7D)
BLUE = RGBColor(0x4F, 0x81, 0xBD)
DARK = RGBColor(0x26, 0x26, 0x26)
GRAY = RGBColor(0x7F, 0x7F, 0x7F)
LIGHT = RGBColor(0xF2, 0xF2, 0xF2)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
ORANGE = RGBColor(0xC0, 0x50, 0x4D)

SLIDE_W, SLIDE_H = Inches(13.333), Inches(7.5)
MARGIN = Inches(0.6)
ISO_DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})(?:[T ][0-9:.+\-Z]*)?$")
TOP_FIELDS = {"title", "subtitle", "author", "date", "slides"}
SLIDE_FIELDS = {"title", "bullets", "table", "metric", "pending", "notes"}


class SpecError(Exception):
    """Ошибка спецификации; str() — список «поле: что не так»."""

    def __init__(self, errors: list[str] | str):
        self.errors = [errors] if isinstance(errors, str) else list(errors)
        super().__init__("\n".join(f"- {e}" for e in self.errors))


# --- проверка спецификации -------------------------------------------------------------------------------------


def _ru_date(value: str) -> str:
    match = ISO_DATE.match(value.strip())
    if not match:
        return value
    year, month, day = (int(x) for x in match.groups())
    try:
        date(year, month, day)
    except ValueError:
        return value
    return f"{day:02d}.{month:02d}.{year:04d}"


def _text(value: Any, path: str, errors: list[str], limit: int, required: bool = True) -> str | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        if required:
            errors.append(f"{path}: обязательное поле, нужна непустая строка")
        return None
    if not isinstance(value, str):
        errors.append(f"{path}: ожидалась строка, получено {type(value).__name__}")
        return None
    text = " ".join(value.split())
    if len(text) > limit:
        errors.append(f"{path}: не больше {limit} символов, сейчас {len(text)} — сократи формулировку")
        return None
    return text


def _url(value: Any, path: str, errors: list[str]) -> str | None:
    if not isinstance(value, str) or not re.match(r"^https?://\S+$", value.strip()):
        errors.append(f"{path}: допустимы только ссылки http:// или https://, получено «{value}»")
        return None
    return value.strip()


def _unknown(obj: dict, allowed: set, path: str, errors: list[str]) -> None:
    for key in obj:
        if key not in allowed:
            errors.append(f"{path}.{key}: неизвестное поле (допустимы: {', '.join(sorted(allowed))})")


def _bullets(raw: Any, path: str, errors: list[str]) -> list[dict]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        errors.append(f"{path}: ожидался список пунктов")
        return []
    items, top, subs = [], 0, 0
    for i, item in enumerate(raw):
        ipath = f"{path}[{i}]"
        if isinstance(item, str):
            item = {"text": item}
        if not isinstance(item, dict):
            errors.append(f"{ipath}: ожидалась строка или объект {{\"text\", \"level\", \"url\"}}")
            continue
        _unknown(item, {"text", "level", "url"}, ipath, errors)
        text = _text(item.get("text"), f"{ipath}.text", errors, MAX_BULLET_CHARS)
        level = item.get("level", 0)
        if level not in (0, 1):
            errors.append(f"{ipath}.level: допустимо 0 (пункт) или 1 (подпункт), получено {level!r}")
            level = 0
        url = _url(item["url"], f"{ipath}.url", errors) if item.get("url") is not None else None
        if level == 0:
            top, subs = top + 1, 0
        else:
            subs += 1
            if top == 0:
                errors.append(f"{ipath}.level: подпункт не может идти первым — сначала пункт уровня 0")
            elif subs > MAX_SUB_BULLETS:
                errors.append(f"{ipath}: не больше {MAX_SUB_BULLETS} подпунктов у одного пункта")
        if text is not None:
            items.append({"text": text, "level": level, "url": url})
    if top > MAX_BULLETS:
        errors.append(f"{path}: не больше {MAX_BULLETS} пунктов на слайде, сейчас {top} — раздели мысль или сократи")
    return items


def _cell(value: Any, path: str, errors: list[str]) -> dict:
    if value is None:
        return {"text": ""}
    if isinstance(value, bool):
        return {"text": "да" if value else "нет"}
    if isinstance(value, (int, float)):
        return {"text": f"{value:,}".replace(",", " ") if isinstance(value, int) else f"{value:g}".replace(".", ",")}
    if isinstance(value, dict):
        _unknown(value, {"text", "url"}, path, errors)
        text = _text(value.get("text"), f"{path}.text", errors, MAX_CELL_CHARS) or ""
        url = _url(value.get("url"), f"{path}.url", errors) if value.get("url") is not None else None
        return {"text": text, "url": url}
    if isinstance(value, str):
        text = " ".join(value.split())
        if len(text) > MAX_CELL_CHARS:
            errors.append(f"{path}: не больше {MAX_CELL_CHARS} символов в ячейке, сейчас {len(text)}")
        return {"text": _ru_date(text[:MAX_CELL_CHARS])}
    errors.append(f"{path}: недопустимое значение ячейки ({type(value).__name__})")
    return {"text": ""}


def _table(raw: Any, path: str, errors: list[str]) -> dict | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        errors.append(f"{path}: ожидался объект {{\"columns\": [...], \"rows\": [[...]]}}")
        return None
    _unknown(raw, {"columns", "rows"}, path, errors)
    columns = raw.get("columns")
    if not isinstance(columns, list) or not columns:
        errors.append(f"{path}.columns: нужен непустой список заголовков колонок")
        return None
    if len(columns) > MAX_TABLE_COLUMNS:
        errors.append(f"{path}.columns: не больше {MAX_TABLE_COLUMNS} колонок, сейчас {len(columns)}")
    heads = [_text(c, f"{path}.columns[{i}]", errors, MAX_CELL_CHARS) or "" for i, c in enumerate(columns)]
    rows_raw = raw.get("rows")
    if not isinstance(rows_raw, list) or not rows_raw:
        errors.append(f"{path}.rows: нужен непустой список строк")
        return None
    if len(rows_raw) > MAX_TABLE_ROWS:
        errors.append(f"{path}.rows: не больше {MAX_TABLE_ROWS} строк на слайде, сейчас {len(rows_raw)} — "
                      "оставь главное, полный список — в excel-report")
    rows = []
    for r, row in enumerate(rows_raw):
        rpath = f"{path}.rows[{r}]"
        if not isinstance(row, list):
            errors.append(f"{rpath}: ожидался список значений")
            continue
        if len(row) != len(columns):
            errors.append(f"{rpath}: ожидалось {len(columns)} значений, получено {len(row)}")
            continue
        rows.append([_cell(v, f"{rpath}[{c}]", errors) for c, v in enumerate(row)])
    return {"columns": heads, "rows": rows}


def _metric(raw: Any, path: str, errors: list[str]) -> dict | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        errors.append(f"{path}: ожидался объект {{\"value\", \"label\"}}")
        return None
    _unknown(raw, {"value", "label"}, path, errors)
    value = raw.get("value")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        value = str(value)
    value = _text(value, f"{path}.value", errors, 12)
    label = _text(raw.get("label"), f"{path}.label", errors, 60)
    return {"value": value, "label": label} if value and label else None


def validate(spec: Any) -> dict:
    if not isinstance(spec, dict):
        raise SpecError("спецификация: ожидался JSON-объект {\"title\": ..., \"slides\": [...]}")
    errors: list[str] = []
    _unknown(spec, TOP_FIELDS, "спецификация", errors)
    model = {
        "title": _text(spec.get("title"), "title", errors, MAX_TITLE_CHARS),
        "subtitle": _text(spec.get("subtitle"), "subtitle", errors, MAX_TITLE_CHARS, required=False),
        "author": _text(spec.get("author"), "author", errors, MAX_TITLE_CHARS, required=False),
        "date": None,
        "slides": [],
    }
    if spec.get("date") is not None:
        raw = _text(spec.get("date"), "date", errors, 40)
        model["date"] = _ru_date(raw) if raw else None
    slides = spec.get("slides")
    if not isinstance(slides, list):
        errors.append("slides: нужен список из ровно 4 слайдов (титульный строится из title/subtitle/author/date)")
        slides = []
    elif len(slides) != CONTENT_SLIDES:
        errors.append(f"slides: нужно ровно 4 слайда после титульного (всего 5), сейчас {len(slides)}")
    for i, slide in enumerate(slides):
        path = f"slides[{i}]"
        if not isinstance(slide, dict):
            errors.append(f"{path}: ожидался объект слайда")
            continue
        _unknown(slide, SLIDE_FIELDS, path, errors)
        item = {
            "title": _text(slide.get("title"), f"{path}.title", errors, MAX_SLIDE_TITLE_CHARS),
            "bullets": _bullets(slide.get("bullets"), f"{path}.bullets", errors),
            "table": _table(slide.get("table"), f"{path}.table", errors),
            "metric": _metric(slide.get("metric"), f"{path}.metric", errors),
            "pending": bool(slide.get("pending", False)),
            "notes": _text(slide.get("notes"), f"{path}.notes", errors, MAX_NOTES_CHARS, required=False),
        }
        if not item["bullets"] and not item["table"] and not item["metric"]:
            errors.append(f"{path}: пустой слайд — нужны bullets, table или metric")
        if item["table"] and len([b for b in item["bullets"] if b["level"] == 0]) > 3:
            errors.append(f"{path}: с таблицей — не больше 3 пунктов над ней")
        model["slides"].append(item)
    if errors:
        raise SpecError(errors)
    return model


# --- вёрстка -----------------------------------------------------------------------------------------------------


def _style_run(run, size: int, color: RGBColor, bold: bool = False) -> None:
    run.font.name = FONT
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.color.rgb = color


def _textbox(slide, left, top, width, height, text: str, size: int, color: RGBColor, bold: bool = False,
             align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP):
    box = slide.shapes.add_textbox(left, top, width, height)
    frame = box.text_frame
    frame.word_wrap = True
    frame.vertical_anchor = anchor
    paragraph = frame.paragraphs[0]
    paragraph.alignment = align
    _style_run(paragraph.add_run(), size, color, bold)
    paragraph.runs[0].text = text
    return box


def _bar(slide, left, top, width, height, color: RGBColor):
    shape = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, left, top, width, height)
    shape.fill.solid()
    shape.fill.fore_color.rgb = color
    shape.line.fill.background()
    shape.shadow.inherit = False
    return shape


def _footer(slide, number: int, total: int, title: str) -> None:
    _textbox(slide, MARGIN, SLIDE_H - Inches(0.5), Inches(9), Inches(0.35), title, 10, GRAY)
    _textbox(slide, SLIDE_W - MARGIN - Inches(1.5), SLIDE_H - Inches(0.5), Inches(1.5), Inches(0.35),
             f"{number} / {total}", 10, GRAY, align=PP_ALIGN.RIGHT)


def _title_slide(prs, model: dict) -> None:
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _bar(slide, 0, 0, Inches(0.35), SLIDE_H, NAVY)
    _textbox(slide, Inches(1.1), Inches(2.0), Inches(11.4), Inches(1.6), model["title"], 40, NAVY, bold=True,
             anchor=MSO_ANCHOR.BOTTOM)
    _bar(slide, Inches(1.1), Inches(3.75), Inches(2.5), Inches(0.08), BLUE)
    if model["subtitle"]:
        _textbox(slide, Inches(1.1), Inches(4.0), Inches(11.4), Inches(0.9), model["subtitle"], 22, BLUE)
    meta = " · ".join(x for x in (model["author"], model["date"]) if x)
    if meta:
        _textbox(slide, Inches(1.1), Inches(5.3), Inches(11.4), Inches(0.6), meta, 16, DARK)


def _bullets_box(slide, left, top, width, height, bullets: list[dict]) -> None:
    box = slide.shapes.add_textbox(left, top, width, height)
    frame = box.text_frame
    frame.word_wrap = True
    long_text = sum(len(b["text"]) for b in bullets) > 600 or len(bullets) > 8
    for i, item in enumerate(bullets):
        paragraph = frame.paragraphs[0] if i == 0 else frame.add_paragraph()
        paragraph.level = item["level"]
        top_level = item["level"] == 0
        paragraph.space_before = Pt(10 if top_level and i else 4)
        marker = paragraph.add_run()
        _style_run(marker, 18 if top_level else 16, BLUE, bold=True)
        marker.text = "■  " if top_level else "–  "
        run = paragraph.add_run()
        size = (16 if long_text else 18) if top_level else (14 if long_text else 16)
        _style_run(run, size, DARK, bold=top_level)
        run.text = item["text"]
        if item["url"]:
            run.hyperlink.address = item["url"]
            run.font.color.rgb = BLUE
            run.font.underline = True


def _bullets_height(bullets: list[dict], width) -> int:
    """Высота блока пунктов по объёму текста: ~0.42 дюйма на строку плюс отступы между пунктами."""
    chars_per_line = max(20, int(width / Inches(1) * 9))
    lines = sum(max(1, -(-(len(b["text"]) + 3) // chars_per_line)) for b in bullets)
    gaps = sum(1 for b in bullets if b["level"] == 0)
    return Inches(0.42) * lines + Pt(10) * gaps + Inches(0.15)


def _table_shape(slide, left, top, width, height, table: dict) -> None:
    rows, cols = len(table["rows"]) + 1, len(table["columns"])
    shape = slide.shapes.add_table(rows, cols, left, top, width, height)
    grid = shape.table
    weights = [max(6, len(table["columns"][c]), *(len(row[c]["text"]) for row in table["rows"])) for c in range(cols)]
    for c, weight in enumerate(weights):
        grid.columns[c].width = int(width * weight / sum(weights))
    for c, head in enumerate(table["columns"]):
        cell = grid.cell(0, c)
        cell.fill.solid()
        cell.fill.fore_color.rgb = NAVY
        cell.text_frame.paragraphs[0].text = ""
        _style_run(cell.text_frame.paragraphs[0].add_run(), 14, WHITE, bold=True)
        cell.text_frame.paragraphs[0].runs[0].text = head
    for r, row in enumerate(table["rows"], start=1):
        for c, value in enumerate(row):
            cell = grid.cell(r, c)
            cell.fill.solid()
            cell.fill.fore_color.rgb = LIGHT if r % 2 else WHITE
            paragraph = cell.text_frame.paragraphs[0]
            paragraph.text = ""
            run = paragraph.add_run()
            _style_run(run, 13, DARK)
            run.text = value["text"]
            if value.get("url"):
                run.hyperlink.address = value["url"]
                run.font.color.rgb = BLUE


def _metric_box(slide, left, top, width, metric: dict) -> None:
    card = _bar(slide, left, top, width, Inches(2.4), LIGHT)
    card.shadow.inherit = False
    _bar(slide, left, top, Inches(0.08), Inches(2.4), BLUE)
    _textbox(slide, left + Inches(0.3), top + Inches(0.25), width - Inches(0.45), Inches(1.2), metric["value"], 54,
             NAVY, bold=True)
    _textbox(slide, left + Inches(0.3), top + Inches(1.45), width - Inches(0.45), Inches(0.85), metric["label"], 16,
             DARK)


def _content_slide(prs, item: dict, number: int, total: int, deck_title: str) -> None:
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _textbox(slide, MARGIN, Inches(0.35), Inches(10.4), Inches(0.9), item["title"], 30, NAVY, bold=True,
             anchor=MSO_ANCHOR.BOTTOM)
    _bar(slide, MARGIN, Inches(1.3), Inches(2.2), Inches(0.06), BLUE)
    if item["pending"]:
        _textbox(slide, SLIDE_W - MARGIN - Inches(2.6), Inches(0.5), Inches(2.6), Inches(0.4), PENDING_LABEL, 12,
                 ORANGE, bold=True, align=PP_ALIGN.RIGHT)
    content_left, content_top = MARGIN + Inches(0.1), Inches(1.6)
    content_w = SLIDE_W - 2 * MARGIN - Inches(0.1)
    bottom = SLIDE_H - Inches(0.7)
    if item["metric"]:
        metric_w = Inches(3.4)
        _metric_box(slide, SLIDE_W - MARGIN - metric_w, content_top, metric_w, item["metric"])
        content_w -= metric_w + Inches(0.4)
    top = content_top
    if item["bullets"]:
        height = min(_bullets_height(item["bullets"], content_w), bottom - top) if item["table"] else bottom - top
        _bullets_box(slide, content_left, top, content_w, height, item["bullets"])
        top += height + Inches(0.1)
    if item["table"]:
        rows = len(item["table"]["rows"]) + 1
        height = min(bottom - top, Inches(0.45) * rows)
        _table_shape(slide, content_left, top, content_w, height, item["table"])
    if item["notes"]:
        slide.notes_slide.notes_text_frame.text = item["notes"]
    _footer(slide, number, total, deck_title)


def _render(model: dict, target: str) -> None:
    prs = Presentation()
    prs.slide_width, prs.slide_height = SLIDE_W, SLIDE_H
    prs.core_properties.title = model["title"]
    if model["author"]:
        prs.core_properties.author = model["author"]
    _title_slide(prs, model)
    total = CONTENT_SLIDES + 1
    for i, item in enumerate(model["slides"], start=2):
        _content_slide(prs, item, i, total, model["title"])
    prs.save(target)


def build(spec: dict, output_path: str | os.PathLike) -> Path:
    path = Path(output_path)
    if path.suffix.lower() != ".pptx":
        raise SpecError(f"OUTPUT_PATH: ожидалось расширение .pptx, получено «{path.name}»")
    if path.is_dir():
        raise SpecError(f"OUTPUT_PATH: «{path}» — это папка, нужен путь к файлу")
    model = validate(spec)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", suffix=".pptx", dir=str(path.parent))
    os.close(fd)
    try:
        _render(model, tmp)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise
    return path.resolve()


def load_spec(source: str) -> Any:
    try:
        if source == "-":
            text = sys.stdin.buffer.read().decode("utf-8-sig")
        else:
            if not os.path.isfile(source):
                raise SpecError(f"SPEC: файл не найден: {source}")
            text = Path(source).read_text(encoding="utf-8-sig")
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise SpecError(f"SPEC: некорректный JSON — {exc.msg} (строка {exc.lineno}, столбец {exc.colno})") from None


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2:
        print("Использование: python build_pptx.py SPEC.json|- OUTPUT.pptx", file=sys.stderr)
        return 2
    try:
        path = build(load_spec(args[0]), args[1])
    except SpecError as exc:
        print("Ошибка спецификации:\n" + str(exc), file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 — сообщить пользователю, не падать трассировкой
        print(f"Непредвиденная ошибка: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(path)
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    sys.exit(main())
