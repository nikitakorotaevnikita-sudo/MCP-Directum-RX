#!/usr/bin/env python3
"""Сборка Excel-файла (.xlsx) из JSON-спецификации.

Запуск:  python build_xlsx.py SPEC.json OUTPUT.xlsx   (SPEC = "-" — читать из stdin)
Импорт:  from build_xlsx import build; build(spec_dict, "out.xlsx")

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

# Лимиты защищают от случайно огромного ввода.
MAX_SPEC_BYTES = 50 * 1024 * 1024
MAX_SHEETS = 50
MAX_COLUMNS = 100
MAX_ROWS_PER_SHEET = 50000
MAX_TOTAL_CELLS = 1_000_000
MAX_HIGHLIGHTS = 20
MAX_CELL_CHARS = 32000  # предел Excel — 32767 символов в ячейке
MAX_TITLE_CHARS = 500
MAX_URL_CHARS = 2000  # у Excel предел длины гиперссылки 2079
MIN_WIDTH, MAX_WIDTH = 10, 60

EMPTY_TABLE_TEXT = "Нет данных"
COLUMN_TYPES = ("text", "number", "integer", "percent", "date", "datetime", "url", "bool")
NUMERIC_TYPES = ("number", "integer", "percent")
DEFAULT_FORMATS = {
    "number": "#,##0.00",
    "integer": "#,##0",
    "percent": "0.0%",
    "date": "DD.MM.YYYY",
    "datetime": "DD.MM.YYYY HH:MM",
}
OPS = {">": ">", "<": "<", ">=": ">=", "<=": "<=", "==": "=", "!=": "<>"}
NAMED_COLORS = {
    "red": "F8CBAD", "orange": "FCE4D6", "yellow": "FFF2CC", "green": "E2EFDA",
    "blue": "DDEBF7", "gray": "EDEDED", "grey": "EDEDED",
}
TOTAL_FUNCS = {"sum": "SUM", "count": "COUNTA", "avg": "AVERAGE"}

SHEET_FIELDS = ({"name", "columns", "rows"}, {"title", "totals", "highlight"})
COLUMN_FIELDS = ({"title"}, {"key", "type", "width", "format"})
FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r", "\n")
SHEET_BAD_CHARS = re.compile(r"[\[\]:*?/\\]")
ILLEGAL_XML_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
DATETIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2}(\.\d{1,6})?)?(Z|[+-]\d{2}:?\d{2})?$")
RU_DATE_RE = re.compile(r"^(\d{2})\.(\d{2})\.(\d{4})(?: (\d{1,2}):(\d{2}))?$")
NUMBER_RE = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$")
MD_LINK_RE = re.compile(r"\[([^\[\]\n]+)\]\(([a-zA-Z][a-zA-Z0-9+.\-]*:(?:[^\s()]|\([^\s()]*\))*)\)")
COLOR_RE = re.compile(r"^#?[0-9A-Fa-f]{6}$")


class SpecError(Exception):
    """Ошибка во входной спецификации (код выхода 2)."""

    def __init__(self, errors: list[str] | str):
        self.errors = [errors] if isinstance(errors, str) else list(errors)
        super().__init__("\n".join(self.errors))


class Link:
    __slots__ = ("text", "url")

    def __init__(self, text: str, url: str):
        self.text, self.url = text, url


def values_word(count: int) -> str:
    """4 значения, 5 значений, 1 значение."""
    if count % 10 == 1 and count % 100 != 11:
        return f"{count} значение"
    if 2 <= count % 10 <= 4 and not 12 <= count % 100 <= 14:
        return f"{count} значения"
    return f"{count} значений"


# ---------- разбор значений ----------

def parse_moment(value: Any) -> datetime | None:
    """Строка/дата → datetime без пояса (время как в исходной строке, без пересчёта)."""
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    if not isinstance(value, str):
        return None
    text = value.strip()
    try:
        if DATE_RE.match(text):
            day = date.fromisoformat(text)
            return datetime(day.year, day.month, day.day)
        if DATETIME_RE.match(text):
            return datetime.fromisoformat(text.replace("Z", "+00:00")).replace(tzinfo=None)
        match = RU_DATE_RE.match(text)
        if match:
            d, m, y, hh, mm = match.groups()
            return datetime(int(y), int(m), int(d), int(hh or 0), int(mm or 0))
    except ValueError:
        return None
    return None


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


def _link_object(value: dict, path: str, errors: list[str]) -> Link | None:
    extra = set(value) - {"text", "url"}
    if extra:
        errors.append(f"{path}: неизвестные поля ссылки {sorted(extra)}; допустимы text и url")
        return None
    if "url" not in value:
        errors.append(f"{path}: у объекта-ссылки нет поля url")
        return None
    url = check_url(value["url"], f"{path}.url", errors)
    label = value.get("text")
    if label is None or label == "":
        label = value["url"]
    if isinstance(label, bool) or not isinstance(label, (str, int, float)):
        errors.append(f"{path}.text: ожидалась строка")
        return None
    return Link(_clean_text(str(label)), url) if url else None


def _clean_text(text: str) -> str:
    return ILLEGAL_XML_CHARS.sub("", text)


def _number(value: Any) -> float | int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value if not isinstance(value, float) or math.isfinite(value) else None
    if isinstance(value, str):
        text = value.strip().replace(" ", "").replace(" ", "").replace(",", ".")
        if NUMBER_RE.match(text):
            number = float(text)
            if not math.isfinite(number):
                return None
            return int(number) if re.fullmatch(r"[+-]?\d+", text) else number
    return None


def convert(value: Any, ctype: str, path: str, errors: list[str]) -> Any:
    """Значение из спецификации → значение ячейки для типа колонки. None → пустая ячейка."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, dict):
        link = _link_object(value, path, errors)
        return link
    if isinstance(value, (list, tuple)):
        errors.append(f"{path}: значение ячейки не может быть списком")
        return None

    if ctype == "text":
        if isinstance(value, bool):
            return "Да" if value else "Нет"
        if isinstance(value, (int, float)):
            if isinstance(value, float) and not math.isfinite(value):
                errors.append(f"{path}: недопустимое число {value}")
                return None
            return str(value)
        if isinstance(value, (date, datetime)):
            moment = parse_moment(value)
            return _format_moment(moment, isinstance(value, datetime))
        if not isinstance(value, str):
            errors.append(f"{path}: неподдерживаемый тип {type(value).__name__}")
            return None
        if len(value) > MAX_CELL_CHARS:
            errors.append(f"{path}: текст длиннее {MAX_CELL_CHARS} символов ({len(value)}) — предел ячейки Excel")
            return None
        stripped = value.strip()
        if DATE_RE.match(stripped) or DATETIME_RE.match(stripped):
            moment = parse_moment(stripped)
            if moment is not None:
                return _format_moment(moment, bool(DATETIME_RE.match(stripped)))
        match = MD_LINK_RE.search(value)
        if match:
            url = check_url(match.group(2), path, errors)
            if url is None:
                return None
            # В ячейке Excel одна гиперссылка — берём первую, остальные оставляем подписями.
            text = MD_LINK_RE.sub(lambda m: m.group(1), value)
            return Link(_clean_text(text), url)
        return _clean_text(value)

    if ctype in NUMERIC_TYPES:
        if ctype == "percent" and isinstance(value, str) and value.strip().endswith("%"):
            number = _number(value.strip()[:-1])
            if number is not None:
                return number / 100
        number = _number(value)
        if number is None:
            errors.append(f"{path}: ожидалось число для колонки типа {ctype}, получено {value!r}")
            return None
        if ctype == "integer":
            if isinstance(number, float) and not number.is_integer():
                errors.append(f"{path}: ожидалось целое число, получено {value!r}")
                return None
            return int(number)
        return number

    if ctype in ("date", "datetime"):
        moment = parse_moment(value)
        if moment is None:
            errors.append(f"{path}: ожидалась дата (ГГГГ-ММ-ДД, ISO-дата-время или ДД.ММ.ГГГГ), получено {value!r}")
            return None
        return moment.date() if ctype == "date" else moment

    if ctype == "url":
        if not isinstance(value, str):
            errors.append(f"{path}: ожидалась ссылка http(s) или объект {{\"text\", \"url\"}}, получено {value!r}")
            return None
        match = MD_LINK_RE.fullmatch(value.strip())
        if match:
            url = check_url(match.group(2), path, errors)
            return Link(_clean_text(match.group(1)), url) if url else None
        url = check_url(value, path, errors)
        return Link(url, url) if url else None

    if ctype == "bool":
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in ("true", "да", "1", "yes"):
            return True
        if text in ("false", "нет", "0", "no"):
            return False
        errors.append(f"{path}: ожидалось true/false (или «да»/«нет»), получено {value!r}")
        return None
    return None


def _format_moment(moment: datetime | None, with_time: bool) -> str:
    if moment is None:
        return ""
    if with_time and (moment.hour, moment.minute) != (0, 0):
        return moment.strftime("%d.%m.%Y %H:%M")
    return moment.strftime("%d.%m.%Y")


# ---------- валидация ----------

def _check_fields(obj: dict, required: set, optional: set, path: str, errors: list[str]) -> bool:
    ok = True
    for name in sorted(required - set(obj)):
        errors.append(f"{path}: отсутствует обязательное поле «{name}»")
        ok = False
    for name in sorted(set(obj) - required - optional):
        errors.append(f"{path}: неизвестное поле «{name}»; допустимые: {', '.join(sorted(required | optional))}")
    return ok


def _text(value: Any, path: str, errors: list[str], max_chars: int = MAX_TITLE_CHARS) -> str:
    if not isinstance(value, str):
        errors.append(f"{path}: ожидалась строка, получено {type(value).__name__}")
        return ""
    if len(value) > max_chars:
        errors.append(f"{path}: текст длиннее {max_chars} символов")
        return ""
    return _clean_text(value)


def sanitize_sheet_name(raw: str, index: int, used: set[str]) -> str:
    """Excel: ≤31 символа, без []:*?/\\, не начинается/не заканчивается апострофом, уникально без учёта регистра."""
    name = re.sub(r"\s+", " ", SHEET_BAD_CHARS.sub(" ", _clean_text(raw))).strip().strip("'").strip()
    if not name:
        name = f"Лист{index + 1}"
    if name.lower() == "history":
        name = f"{name}_"
    name = name[:31].rstrip("'")
    candidate, counter = name, 2
    while candidate.lower() in used:
        suffix = f" ({counter})"
        candidate = name[: 31 - len(suffix)].rstrip("'") + suffix
        counter += 1
    used.add(candidate.lower())
    return candidate


def _resolve_column(ref: Any, columns: list[dict], path: str, errors: list[str]) -> int | None:
    if isinstance(ref, int) and not isinstance(ref, bool):
        if 0 <= ref < len(columns):
            return ref
        errors.append(f"{path}: индекс колонки {ref} вне диапазона 0..{len(columns) - 1}")
        return None
    if isinstance(ref, str):
        for i, col in enumerate(columns):
            if col["key"] == ref:
                return i
        for i, col in enumerate(columns):
            if col["title"] == ref:
                return i
    names = ", ".join(repr(c["key"] or c["title"]) for c in columns)
    errors.append(f"{path}: колонка {ref!r} не найдена; укажите key, заголовок или индекс (с 0). Колонки: {names}")
    return None


def validate(spec: Any) -> dict:
    errors: list[str] = []
    if not isinstance(spec, dict):
        raise SpecError("спецификация: ожидался JSON-объект {\"sheets\": [...]}")
    _check_fields(spec, {"sheets"}, {"properties"}, "спецификация", errors)
    model: dict[str, Any] = {"sheets": [], "properties": {}}
    props = spec.get("properties")
    if props is not None:
        if not isinstance(props, dict):
            errors.append("properties: ожидался объект {\"title\", \"author\"}")
        else:
            _check_fields(props, set(), {"title", "author", "subject"}, "properties", errors)
            for name in ("title", "author", "subject"):
                if props.get(name) is not None:
                    model["properties"][name] = _text(props[name], f"properties.{name}", errors)
    sheets = spec.get("sheets")
    if "sheets" in spec:
        if not isinstance(sheets, list) or not sheets:
            errors.append("sheets: ожидался непустой список листов")
            sheets = []
        elif len(sheets) > MAX_SHEETS:
            errors.append(f"sheets: слишком много листов ({len(sheets)}), максимум {MAX_SHEETS}")
            sheets = []
    else:
        sheets = []
    used: set[str] = set()
    total = [0]
    for i, sheet in enumerate(sheets):
        normalized = _validate_sheet(sheet, i, f"sheets[{i}]", errors, used, total)
        if normalized:
            model["sheets"].append(normalized)
    if errors:
        raise SpecError(errors)
    return model


def _validate_sheet(sheet: Any, index: int, path: str, errors: list[str], used: set[str], total: list[int]):
    if not isinstance(sheet, dict):
        errors.append(f"{path}: ожидался объект листа {{\"name\", \"columns\", \"rows\"}}")
        return None
    if not _check_fields(sheet, *SHEET_FIELDS, path, errors):
        return None
    out: dict[str, Any] = {}
    raw_name = sheet["name"]
    if not isinstance(raw_name, str):
        errors.append(f"{path}.name: ожидалась строка")
        raw_name = ""
    out["name"] = sanitize_sheet_name(raw_name, index, used)
    if sheet.get("title") is not None:
        out["title"] = _text(sheet["title"], f"{path}.title", errors)

    columns = sheet["columns"]
    if not isinstance(columns, list) or not columns:
        errors.append(f"{path}.columns: ожидался непустой список колонок")
        return None
    if len(columns) > MAX_COLUMNS:
        errors.append(f"{path}.columns: слишком много колонок ({len(columns)}), максимум {MAX_COLUMNS}")
        return None
    cols = []
    keys: set[str] = set()
    for i, col in enumerate(columns):
        col_path = f"{path}.columns[{i}]"
        if isinstance(col, str):
            col = {"title": col}
        if not isinstance(col, dict):
            errors.append(f"{col_path}: ожидался объект {{\"title\", \"type\"?, \"key\"?}}")
            cols.append({"title": "", "key": None, "type": "text", "width": None, "format": None})
            continue
        _check_fields(col, *COLUMN_FIELDS, col_path, errors)
        ctype = col.get("type", "text")
        if ctype not in COLUMN_TYPES:
            errors.append(f"{col_path}.type: неизвестный тип {ctype!r}; допустимые: {', '.join(COLUMN_TYPES)}")
            ctype = "text"
        key = col.get("key")
        if key is not None:
            if not isinstance(key, str) or not key:
                errors.append(f"{col_path}.key: ожидалась непустая строка")
                key = None
            elif key in keys:
                errors.append(f"{col_path}.key: ключ {key!r} повторяется")
            else:
                keys.add(key)
        width = col.get("width")
        if width is not None and (isinstance(width, bool) or not isinstance(width, (int, float)) or not 1 <= width <= 255):
            errors.append(f"{col_path}.width: ожидалось число от 1 до 255 (ширина в символах), получено {width!r}")
            width = None
        fmt = col.get("format")
        if fmt is not None and (not isinstance(fmt, str) or not fmt or len(fmt) > 100):
            errors.append(f"{col_path}.format: ожидалась строка числового формата Excel, например \"0.00\"")
            fmt = None
        cols.append({"title": _text(col.get("title", ""), f"{col_path}.title", errors), "key": key,
                     "type": ctype, "width": width, "format": fmt or DEFAULT_FORMATS.get(ctype)})
    out["columns"] = cols

    rows = sheet["rows"]
    if not isinstance(rows, list):
        errors.append(f"{path}.rows: ожидался список строк (списки значений или объекты по key)")
        return None
    if len(rows) > MAX_ROWS_PER_SHEET:
        errors.append(f"{path}.rows: слишком много строк ({len(rows)}), максимум {MAX_ROWS_PER_SHEET} на лист; "
                      "разбейте данные на несколько листов или файлов")
        return None
    total[0] += len(rows) * len(cols)
    if total[0] > MAX_TOTAL_CELLS:
        errors.append(f"{path}.rows: всего ячеек в файле больше {MAX_TOTAL_CELLS}; уменьшите объём выгрузки")
        return None
    out["rows"] = []
    ncols = len(cols)
    for r, row in enumerate(rows):
        row_path = f"{path}.rows[{r}]"
        if isinstance(row, dict):
            missing_keys = [i for i, c in enumerate(cols) if not c["key"]]
            if missing_keys:
                errors.append(f"{row_path}: строка-объект, но у колонок {missing_keys} нет key")
                break
            values = [row.get(c["key"]) for c in cols]
        elif isinstance(row, list):
            if len(row) != ncols:
                errors.append(f"{row_path}: ожидалось {values_word(ncols)}, получено {len(row)}")
                continue
            values = row
        else:
            errors.append(f"{row_path}: ожидался список значений или объект, получено {type(row).__name__}")
            continue
        out["rows"].append([convert(v, cols[c]["type"], f"{row_path}[{c}]", errors) for c, v in enumerate(values)])
        if len(errors) > 200:
            break

    if sheet.get("totals") is not None:
        out["totals"] = _validate_totals(sheet["totals"], cols, f"{path}.totals", errors)
    out["highlight"] = _validate_highlight(sheet.get("highlight"), cols, f"{path}.highlight", errors)
    return out


def _validate_totals(totals: Any, cols: list[dict], path: str, errors: list[str]) -> dict | None:
    if not isinstance(totals, dict):
        errors.append(f"{path}: ожидался объект {{\"label\", \"columns\", \"func\"}}")
        return None
    _check_fields(totals, {"columns"}, {"label", "func"}, path, errors)
    func = totals.get("func", "sum")
    if func not in TOTAL_FUNCS:
        errors.append(f"{path}.func: ожидалось \"sum\", \"count\" или \"avg\", получено {func!r}")
        return None
    refs = totals.get("columns")
    if not isinstance(refs, list) or not refs:
        errors.append(f"{path}.columns: ожидался непустой список колонок (key, заголовок или индекс)")
        return None
    indexes = []
    for i, ref in enumerate(refs):
        index = _resolve_column(ref, cols, f"{path}.columns[{i}]", errors)
        if index is None:
            continue
        if func in ("sum", "avg") and cols[index]["type"] not in NUMERIC_TYPES:
            errors.append(f"{path}.columns[{i}]: {func} применим только к числовым колонкам "
                          f"(number/integer/percent), у колонки тип {cols[index]['type']}")
            continue
        indexes.append(index)
    label = totals.get("label", "Итого")
    label = _text(label, f"{path}.label", errors) if label is not None else ""
    label_col = next((i for i in range(len(cols)) if i not in indexes), None)
    if label and label_col is None:
        errors.append(f"{path}.label: все колонки заняты итогами — подпись некуда поставить; уберите label")
    return {"func": func, "columns": sorted(set(indexes)), "label": label, "label_col": label_col}


def _validate_highlight(rules: Any, cols: list[dict], path: str, errors: list[str]) -> list[dict]:
    if rules is None:
        return []
    if not isinstance(rules, list):
        errors.append(f"{path}: ожидался список правил")
        return []
    if len(rules) > MAX_HIGHLIGHTS:
        errors.append(f"{path}: слишком много правил ({len(rules)}), максимум {MAX_HIGHLIGHTS}")
        return []
    result = []
    for i, rule in enumerate(rules):
        rule_path = f"{path}[{i}]"
        if not isinstance(rule, dict):
            errors.append(f"{rule_path}: ожидался объект {{\"column\", \"op\", \"value\", \"color\"}}")
            continue
        if not _check_fields(rule, {"column", "op", "value"}, {"color", "row"}, rule_path, errors):
            continue
        index = _resolve_column(rule["column"], cols, f"{rule_path}.column", errors)
        op = rule["op"]
        if op not in OPS:
            errors.append(f"{rule_path}.op: ожидалось одно из {', '.join(OPS)}, получено {op!r}")
            continue
        color = rule.get("color", "yellow")
        if isinstance(color, str) and color.lower() in NAMED_COLORS:
            color = NAMED_COLORS[color.lower()]
        elif isinstance(color, str) and COLOR_RE.match(color):
            color = color.lstrip("#").upper()
        else:
            errors.append(f"{rule_path}.color: ожидался цвет #RRGGBB или одно из {', '.join(NAMED_COLORS)}")
            continue
        whole_row = rule.get("row", False)
        if not isinstance(whole_row, bool):
            errors.append(f"{rule_path}.row: ожидалось true или false")
            continue
        if index is None:
            continue
        literal = _rule_literal(rule["value"], cols[index]["type"], op, f"{rule_path}.value", errors)
        if literal is None:
            continue
        result.append({"col": index, "op": OPS[op], "literal": literal[0], "numeric": literal[1],
                       "color": color, "row": whole_row})
    return result


def _rule_literal(value: Any, ctype: str, op: str, path: str, errors: list[str]) -> tuple[str, bool] | None:
    """Значение правила → литерал формулы Excel и признак «сравнение чисел»."""
    if ctype in NUMERIC_TYPES:
        if ctype == "percent" and isinstance(value, str) and value.strip().endswith("%"):
            number = _number(value.strip()[:-1])
            number = number / 100 if number is not None else None
        else:
            number = _number(value)
        if number is None:
            errors.append(f"{path}: для колонки типа {ctype} нужно число, получено {value!r}")
            return None
        return repr(float(number)) if isinstance(number, float) else str(number), True
    if ctype in ("date", "datetime"):
        moment = parse_moment(value)
        if moment is None:
            errors.append(f"{path}: для колонки-даты нужна дата ГГГГ-ММ-ДД, получено {value!r}")
            return None
        literal = f"DATE({moment.year},{moment.month},{moment.day})"
        if ctype == "datetime" and (moment.hour or moment.minute):
            literal += f"+TIME({moment.hour},{moment.minute},0)"
        return literal, True
    if ctype == "bool":
        if not isinstance(value, bool):
            errors.append(f"{path}: для колонки типа bool нужно true или false")
            return None
        return ("TRUE" if value else "FALSE"), False
    if op not in ("==", "!="):
        errors.append(f"{path}: для текстовой колонки допустимы только операции == и !=")
        return None
    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
        errors.append(f"{path}: ожидалась строка")
        return None
    text = _clean_text(str(value))[:255].replace('"', '""')
    return f'"{text}"', False


# ---------- запись ----------

def _put_text(cell, text: str) -> None:
    """Текст всегда пишем как строку: защита от выполнения формул (=, +, -, @)."""
    cell.value = text
    cell.data_type = "s"
    if text.startswith(FORMULA_PREFIXES):
        cell.quotePrefix = True


def _display_len(value: Any, ctype: str) -> int:
    if value is None:
        return 0
    if isinstance(value, Link):
        value = value.text
    if ctype == "date":
        return 10
    if ctype == "datetime":
        return 16
    if isinstance(value, bool):
        return 6
    if isinstance(value, float):
        return len(f"{value:,.2f}")
    if isinstance(value, int):
        return len(f"{value:,}")
    text = str(value)
    return max((len(line) for line in text.splitlines()), default=0)


def _write_sheet(ws, sheet: dict) -> None:
    from openpyxl.formatting.rule import FormulaRule
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    cols = sheet["columns"]
    ncols = len(cols)
    last_letter = get_column_letter(ncols)
    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill("solid", fgColor="2F4A6D")
    header_align = Alignment(wrap_text=True, vertical="center", horizontal="center")
    thin = Side(style="thin", color="B8C2CC")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    wrap_top = Alignment(wrap_text=True, vertical="top")
    top = Alignment(vertical="top")
    link_font = Font(color="0563C1", underline="single")
    bold = Font(bold=True)

    row_index = 1
    if sheet.get("title"):
        cell = ws.cell(row=1, column=1)
        _put_text(cell, sheet["title"])
        cell.font = Font(bold=True, size=14)
        cell.alignment = Alignment(vertical="center")
        if ncols > 1:
            ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=ncols)
        ws.row_dimensions[1].height = 24
        row_index = 2
    header_row = row_index
    for c, col in enumerate(cols, start=1):
        cell = ws.cell(row=header_row, column=c)
        _put_text(cell, col["title"])
        cell.font, cell.fill, cell.alignment, cell.border = header_font, header_fill, header_align, border

    widths = [max(len(w) for w in (col["title"] or " ").split()) + 2 for col in cols]
    first = header_row + 1
    rows = sheet["rows"]
    for r, values in enumerate(rows, start=first):
        for c, value in enumerate(values, start=1):
            col = cols[c - 1]
            cell = ws.cell(row=r, column=c)
            cell.border = border
            if value is None:
                continue
            if isinstance(value, Link):
                _put_text(cell, value.text)
                cell.hyperlink = value.url
                cell.font = link_font
                cell.alignment = wrap_top
            elif isinstance(value, str):
                _put_text(cell, value)
                cell.alignment = wrap_top
            else:
                cell.value = value
                cell.alignment = top
                if col["format"] and not isinstance(value, bool):
                    cell.number_format = col["format"]
            widths[c - 1] = max(widths[c - 1], _display_len(value, col["type"]) + 2)
    last = first + len(rows) - 1
    if not rows:
        cell = ws.cell(row=first, column=1)
        _put_text(cell, EMPTY_TABLE_TEXT)
        cell.font = Font(italic=True, color="808080")
        cell.alignment = Alignment(horizontal="center")
        if ncols > 1:
            ws.merge_cells(start_row=first, start_column=1, end_row=first, end_column=ncols)
        last = first

    totals = sheet.get("totals")
    if totals:
        total_row = last + 1
        fill = PatternFill("solid", fgColor="DDE5EE")
        for c in range(1, ncols + 1):
            cell = ws.cell(row=total_row, column=c)
            cell.fill, cell.font, cell.border = fill, bold, border
        if totals["label"] and totals["label_col"] is not None:
            _put_text(ws.cell(row=total_row, column=totals["label_col"] + 1), totals["label"])
        func = TOTAL_FUNCS[totals["func"]]
        for index in totals["columns"]:
            letter = get_column_letter(index + 1)
            cell = ws.cell(row=total_row, column=index + 1)
            if rows:
                formula = f"{func}({letter}{first}:{letter}{last})"
                cell.value = f'=IFERROR({formula},"")' if func == "AVERAGE" else f"={formula}"
            elif func != "AVERAGE":
                cell.value = 0
            fmt = cols[index]["format"] if func != "COUNTA" else "#,##0"
            if fmt:
                cell.number_format = fmt

    for c, col in enumerate(cols, start=1):
        width = col["width"] if col["width"] else min(MAX_WIDTH, max(MIN_WIDTH, widths[c - 1]))
        ws.column_dimensions[get_column_letter(c)].width = width

    ws.freeze_panes = ws.cell(row=header_row + 1, column=1)
    ws.auto_filter.ref = f"A{header_row}:{last_letter}{last}"

    if rows:
        for rule in sheet["highlight"]:
            letter = get_column_letter(rule["col"] + 1)
            ref = f"${letter}{first}"
            condition = f"{ref}{rule['op']}{rule['literal']}"
            if rule["numeric"]:
                condition = f"AND(ISNUMBER({ref}),{condition})"
            else:
                condition = f'AND({ref}<>"",{condition})'
            target = f"A{first}:{last_letter}{last}" if rule["row"] else f"{letter}{first}:{letter}{last}"
            fill = PatternFill("solid", start_color=rule["color"], end_color=rule["color"])
            ws.conditional_formatting.add(target, FormulaRule(formula=[condition], fill=fill))


def _render(model: dict, target: str) -> None:
    from openpyxl import Workbook

    wb = Workbook()
    wb.remove(wb.active)
    for sheet in model["sheets"]:
        _write_sheet(wb.create_sheet(title=sheet["name"]), sheet)
    props = model["properties"]
    wb.properties.title = props.get("title")
    wb.properties.creator = props.get("author") or "excel-report skill"
    wb.properties.subject = props.get("subject")
    wb.save(target)


def build(spec: dict, output_path: str | os.PathLike) -> Path:
    """Проверяет спецификацию и атомарно записывает .xlsx. Возвращает абсолютный путь."""
    model = validate(spec)
    path = Path(output_path)
    if path.suffix.lower() != ".xlsx":
        raise SpecError(f"OUTPUT_PATH: ожидалось расширение .xlsx, получено «{path.name}»")
    if path.is_dir():
        raise SpecError(f"OUTPUT_PATH: «{path}» — это папка, нужен путь к файлу")
    path.parent.mkdir(parents=True, exist_ok=True)
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
        print("Использование: python build_xlsx.py SPEC.json|- OUTPUT.xlsx", file=sys.stderr)
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
