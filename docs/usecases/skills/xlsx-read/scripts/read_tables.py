#!/usr/bin/env python3
"""Чтение и анализ таблиц Excel (.xlsx, .xlsm) и CSV — одной или нескольких.

Команды:
  inspect  — обзор файлов: листы, где таблица, колонки и их типы, строки «Итого», разделы, примеры строк;
  read     — строки таблицы (одной или склеенных нескольких);
  profile  — профиль колонок: заполненность, уникальные, частые значения, мин/макс/сумма, «не те» значения,
             сверка с итоговой строкой файла;
  query    — отбор (--where), выбор колонок, свод (--group-by + --agg), сортировка;
  combine  — склейка одинаковых таблиц из нескольких файлов/листов с колонками «Файл», «Лист»;
  diff     — сверка двух таблиц по ключу: добавленные, удалённые, изменённые строки;
  join     — подтянуть колонки второй таблицы к первой по ключу.

Таблица на листе находится автоматически: пропускаются заголовок отчёта и пустые строки над шапкой, шапка в две строки
склеивается («Сумма / План»), объединённые ячейки заполняются, строка нумерации колонок (1, 2, 3…) пропускается,
строки «Итого/Всего» и подписи под таблицей не попадают в данные, строки-разделы («Отдел кадров») превращаются в
колонку «Раздел». Числа и даты, записанные текстом («1 234,50», «01.11.2026»), распознаются.

Источник: путь к файлу или «путь::Лист» (имя листа или его номер с 1). Коды выхода: 0 — успех, 2 — плохой вход
(нет файла, не тот формат, нет листа или колонки, ошибка в условии), 1 — непредвиденная ошибка или нет openpyxl.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import math
import re
import statistics
import sys
from collections import Counter
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

EXIT_ERROR = 1
EXIT_INPUT = 2
HEADER_SCAN_ROWS = 30
TYPE_SHARE = 0.9
DEFAULT_LIMIT = 100
DIFF_LIMIT = 200
MAX_ROWS_DEFAULT = 100_000
SAMPLE_ROWS = 5
TOP_VALUES = 5
SPACE_CHARS = "\u00a0\u202f\u2009"
TOTAL_RE = re.compile(r"^\s*(итого|итог|всего|total)\b", re.I)
NUMBER_RE = re.compile(r"^([+\-\u2212]?)(\d{1,3}(?:[ \u00a0\u202f\u2009]\d{3})+|\d+)(?:[.,](\d+))?$")
SUFFIX_RE = re.compile(r"\s*(%|₽|руб\.?|р\.)$", re.I)
DATE_RU_RE = re.compile(r"^(\d{1,2})\.(\d{1,2})\.(\d{4}|\d{2})(?:[ T]+(\d{1,2}):(\d{2})(?::(\d{2}))?)?$")
DATE_ISO_RE = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})(?:[ T](\d{2}):(\d{2})(?::(\d{2})(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?$"
)
LETTERS_RE = re.compile(r"^[A-Z]{1,3}$")
RANGE_RE = re.compile(r"^([A-Z]{1,3})(\d+):([A-Z]{1,3})(\d+)$")
WHERE_EMPTY_RE = re.compile(r"^(.+?)\s+(is\s+not\s+empty|is\s+empty|не\s+пусто|пусто)$", re.I)
WHERE_IN_RE = re.compile(r"^(.+?)\s+(not\s+in|in)\s+(.+)$", re.I)
WHERE_OP_RE = re.compile(r"^(.+?)\s*(>=|<=|!=|!~|=|>|<|~)\s*(.*)$")
AGG_FUNCS = ("count", "sum", "avg", "min", "max", "median", "nunique")
TYPE_LABELS = {"integer": "целое", "number": "число", "date": "дата", "datetime": "дата и время", "bool": "да/нет",
               "text": "текст", "empty": "пусто"}


class InputError(Exception):
    """Плохой вход: сообщение для пользователя, код выхода 2."""


# --- значения ---------------------------------------------------------------


def is_empty(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def clean_text(value: str) -> str:
    for ch in SPACE_CHARS:
        value = value.replace(ch, " ")
    return value.strip()


def norm_name(value: str) -> str:
    return re.sub(r"\s+", " ", clean_text(str(value))).casefold().replace("ё", "е")


def tidy_number(value: float) -> int | float:
    if isinstance(value, int):
        return value
    value = round(value, 10)
    if value.is_integer() and abs(value) < 1e15:
        return int(value)
    return value


def parse_number(text: str) -> tuple[int | float, bool] | None:
    """Число из строки «1 234,50», «-12.5», «12,5%», «300 ₽». Коды с ведущим нулём («007») и длинные
    цифровые идентификаторы (ИНН, СНИЛС, телефоны без разделителей) числом не считаются."""
    s = clean_text(text)
    if not s:
        return None
    percent = s.endswith("%")
    s = SUFFIX_RE.sub("", s)
    match = NUMBER_RE.match(s)
    if not match:
        return None
    sign, whole, frac = match.groups()
    digits = re.sub(r"\D", "", whole)
    if len(digits) > 1 and digits.startswith("0") and whole == digits:
        return None
    if frac is None and whole == digits and len(digits) >= 9:
        return None
    negative = sign in ("-", "\u2212")
    value = float(f"{digits}.{frac or '0'}") * (-1 if negative else 1)
    if percent:
        value /= 100
    return tidy_number(value), percent


def parse_date(text: str) -> tuple[datetime, bool] | None:
    s = clean_text(text)
    match = DATE_RU_RE.match(s)
    try:
        if match:
            d, m, y, hh, mm, ss = match.groups()
            year = int(y) + (2000 if len(y) == 2 else 0)
            moment = datetime(year, int(m), int(d), int(hh or 0), int(mm or 0), int(ss or 0))
            return moment, hh is not None
        match = DATE_ISO_RE.match(s)
        if match:
            y, m, d, hh, mm, ss = match.groups()
            moment = datetime(int(y), int(m), int(d), int(hh or 0), int(mm or 0), int(ss or 0))
            return moment, hh is not None and (int(hh), int(mm), int(ss or 0)) != (0, 0, 0)
    except ValueError:
        return None
    return None


def normalize_cell(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str):
        value = clean_text(value.replace("\r\n", "\n"))
        return value or None
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return tidy_number(value)
    if isinstance(value, time):
        return value.strftime("%H:%M" if not value.second else "%H:%M:%S")
    if isinstance(value, timedelta):
        hours = value.total_seconds() / 3600
        return tidy_number(hours)
    return value


def kind_of(value: Any) -> str:
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, datetime):
        return "datetime" if value.time() != time(0, 0) else "date"
    if isinstance(value, date):
        return "date"
    if isinstance(value, str):
        if parse_number(value) is not None:
            return "number"
        parsed = parse_date(value)
        if parsed:
            return "datetime" if parsed[1] else "date"
    return "text"


def classify(values: list[Any]) -> str:
    filled = [v for v in values if v is not None]
    if not filled:
        return "empty"
    kinds = Counter(kind_of(v) for v in filled)
    n = len(filled)

    def mostly(count: int) -> bool:
        # Большинство значений этого вида, а «посторонних» («н/д», «—») — не больше двух или 10 %.
        return count / n >= TYPE_SHARE or (count >= 2 and count / n > 0.5 and n - count <= max(2, n * 0.1))

    if mostly(kinds["number"]):
        numbers = [to_number(v) for v in filled]
        integral = all(isinstance(x, int) for x in numbers if x is not None)
        return "integer" if integral else "number"
    if mostly(kinds["date"] + kinds["datetime"]):
        return "datetime" if kinds["datetime"] else "date"
    if mostly(kinds["bool"]):
        return "bool"
    return "text"


def to_number(value: Any) -> int | float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return tidy_number(value)
    if isinstance(value, str):
        parsed = parse_number(value)
        return parsed[0] if parsed else None
    return None


def to_date_text(value: Any, with_time: bool) -> str | None:
    if isinstance(value, datetime):
        moment = value
    elif isinstance(value, date):
        moment = datetime(value.year, value.month, value.day)
    elif isinstance(value, str):
        parsed = parse_date(value)
        if not parsed:
            return None
        moment = parsed[0]
    else:
        return None
    return moment.strftime("%Y-%m-%dT%H:%M:%S") if with_time else moment.strftime("%Y-%m-%d")


def as_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return "да" if value else "нет"
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d" if value.time() == time(0, 0) else "%Y-%m-%dT%H:%M:%S")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (int, float)):
        return str(tidy_number(value))
    return str(value)


def convert(value: Any, col_type: str) -> tuple[Any, bool]:
    """Значение в тип колонки; второй элемент — False, если значение типу не соответствует (оставлено как есть)."""
    if value is None:
        return None, True
    if col_type in ("integer", "number"):
        number = to_number(value)
        return (number, True) if number is not None else (as_text(value), False)
    if col_type in ("date", "datetime"):
        text = to_date_text(value, col_type == "datetime")
        return (text, True) if text is not None else (as_text(value), False)
    if col_type == "bool":
        return (value, True) if isinstance(value, bool) else (as_text(value), False)
    return as_text(value), True


def column_letter(index: int) -> str:
    """1 → A, 27 → AA."""
    letters = ""
    while index:
        index, rem = divmod(index - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


def letter_index(letters: str) -> int:
    index = 0
    for ch in letters:
        index = index * 26 + (ord(ch) - 64)
    return index


# --- чтение файлов ----------------------------------------------------------


class SheetGrid:
    """Прямоугольник значений листа с координатами и служебными сведениями."""

    def __init__(self, source: str, sheet: str, state: str = "visible"):
        self.source = source
        self.sheet = sheet
        self.state = state
        self.grid: list[list[Any]] = []
        self.first_row = 1
        self.first_col = 1
        self.merged: list[tuple[int, int, int, int]] = []  # абсолютные min_row, min_col, max_row, max_col
        self.hidden_rows: set[int] = set()
        self.hidden_cols: list[str] = []
        self.formulas_without_values = 0
        self.truncated = False
        self.used_range = ""

    @property
    def empty(self) -> bool:
        return not any(not is_empty(v) for row in self.grid for v in row)


def openpyxl_module():
    try:
        import openpyxl  # noqa: PLC0415
    except ImportError as exc:
        raise RuntimeError("не установлен openpyxl: pip install -r requirements.txt") from exc
    return openpyxl


def check_file(path: Path) -> str:
    if not path.is_file():
        raise InputError(f"Файл не найден: {path}")
    suffix = path.suffix.lower()
    if suffix in (".csv", ".tsv", ".txt"):
        return "csv"
    head = path.read_bytes()[:8]
    if suffix in (".xls", ".xlsb", ".ods") or head.startswith(b"\xd0\xcf\x11\xe0"):
        if head.startswith(b"\xd0\xcf\x11\xe0"):
            raise InputError(f"{path.name}: старый формат .xls или файл защищён паролем — откройте в Excel и "
                             "сохраните как .xlsx без пароля.")
        raise InputError(f"{path.name}: формат {suffix} не поддерживается — сохраните как .xlsx или .csv.")
    if not head.startswith(b"PK"):
        raise InputError(f"{path.name}: не похоже на файл Excel .xlsx (и не CSV).")
    return "xlsx"


def read_csv_grid(path: Path) -> SheetGrid:
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "cp1251"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise InputError(f"{path.name}: не удалось определить кодировку (ожидались UTF-8 или Windows-1251).")
    sample = text[:20000]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=";,\t|")
        delimiter = dialect.delimiter
    except csv.Error:
        delimiter = ";" if sample.count(";") >= sample.count(",") else ","
    grid = SheetGrid(path.name, path.stem)
    rows = list(csv.reader(io.StringIO(text), delimiter=delimiter))
    width = max((len(r) for r in rows), default=0)
    grid.grid = [[normalize_cell(v) for v in r] + [None] * (width - len(r)) for r in rows]
    grid.used_range = f"A1:{column_letter(max(width, 1))}{max(len(rows), 1)}"
    return grid


def workbook_grids(path: Path, max_rows: int, cell_range: str | None, with_formulas: bool) -> list[SheetGrid]:
    openpyxl = openpyxl_module()
    try:
        wb = openpyxl.load_workbook(path, data_only=True)
    except Exception as exc:  # noqa: BLE001 — повреждённый файл, не тот zip и т. п.
        raise InputError(f"{path.name}: файл не открывается как .xlsx ({type(exc).__name__}: {exc}).") from None
    formula_wb = None
    if with_formulas:
        try:
            formula_wb = openpyxl.load_workbook(path, data_only=False)
        except Exception:  # noqa: BLE001
            formula_wb = None
    grids = []
    for ws in wb.worksheets:
        grids.append(sheet_grid(path.name, ws, formula_wb[ws.title] if formula_wb else None, max_rows, cell_range))
    return grids


def _cells(ws) -> dict[tuple[int, int], Any]:
    """Только реально заполненные ячейки — лист с форматированием «до миллионной строки» не перебираем целиком."""
    store = getattr(ws, "_cells", None)
    if isinstance(store, dict):
        return {key: cell.value for key, cell in store.items() if cell.value is not None}
    return {(c.row, c.column): c.value for row in ws.iter_rows() for c in row if c.value is not None}


def sheet_grid(source: str, ws, formula_ws, max_rows: int, cell_range: str | None) -> SheetGrid:
    grid = SheetGrid(source, ws.title, getattr(ws, "sheet_state", "visible"))
    cells = {key: normalize_cell(value) for key, value in _cells(ws).items()}
    cells = {key: value for key, value in cells.items() if value is not None}
    if formula_ws is not None:
        for (r, c), value in _cells(formula_ws).items():
            if isinstance(value, str) and value.startswith("=") and (r, c) not in cells:
                grid.formulas_without_values += 1
    grid.merged = [(m.min_row, m.min_col, m.max_row, m.max_col) for m in ws.merged_cells.ranges]
    grid.hidden_rows = {r for r, dim in ws.row_dimensions.items() if dim.hidden}
    hidden_cols = []
    for dim in ws.column_dimensions.values():
        if dim.hidden and dim.min:
            hidden_cols.extend(column_letter(i) for i in range(dim.min, (dim.max or dim.min) + 1))
    grid.hidden_cols = sorted(set(hidden_cols), key=letter_index)
    if not cells:
        return grid
    rows = [r for r, _ in cells]
    cols = [c for _, c in cells]
    r0, r1, c0, c1 = min(rows), max(rows), min(cols), max(cols)
    for mr0, mc0, mr1, mc1 in grid.merged:
        if (mr0, mc0) in cells:
            r1, c1 = max(r1, mr1), max(c1, mc1)
    grid.used_range = f"{column_letter(c0)}{r0}:{column_letter(c1)}{r1}"
    if cell_range:
        match = RANGE_RE.match(cell_range.upper().replace("$", ""))
        if not match:
            raise InputError(f"--range: ожидается диапазон вида A5:H200, получено «{cell_range}».")
        ca, ra, cb, rb = match.groups()
        r0, r1 = max(r0, int(ra)), min(r1, int(rb))
        c0, c1 = max(c0, letter_index(ca)), min(c1, letter_index(cb))
        if r0 > r1 or c0 > c1:
            return grid
    if r1 - r0 + 1 > max_rows:
        r1 = r0 + max_rows - 1
        grid.truncated = True
    grid.first_row, grid.first_col = r0, c0
    width = c1 - c0 + 1
    grid.grid = [[cells.get((r, c)) for c in range(c0, c1 + 1)] for r in range(r0, r1 + 1)]
    # Объединённые ячейки: значение левой верхней — во все ячейки диапазона.
    for mr0, mc0, mr1, mc1 in grid.merged:
        top = cells.get((mr0, mc0))
        if top is None:
            continue
        for r in range(max(mr0, r0), min(mr1, r1) + 1):
            for c in range(max(mc0, c0), min(mc1, c1) + 1):
                grid.grid[r - r0][c - c0] = top
    del width
    return grid


# --- таблица ----------------------------------------------------------------


class Table:
    def __init__(self, source: str, sheet: str):
        self.source = source
        self.sheet = sheet
        self.columns: list[str] = []
        self.letters: list[str | None] = []
        self.types: list[str] = []
        self.nonconforming: list[list[Any]] = []
        self.rows: list[list[Any]] = []
        self.row_numbers: list[int | None] = []
        self.header_rows: list[int] = []
        self.totals: list[dict[str, Any]] = []
        self.footer_notes: list[str] = []
        self.sections: list[str] = []
        self.skipped_empty = 0
        self.numbering_row: int | None = None
        self.hidden_data_rows = 0
        self.data_range = ""
        self.warnings: list[str] = []

    @property
    def label(self) -> str:
        return f"{self.source} :: {self.sheet}"

    def records(self) -> list[dict[str, Any]]:
        return [dict(zip(self.columns, row)) for row in self.rows]

    def index_of(self, name: str) -> int:
        target = norm_name(name)
        for i, col in enumerate(self.columns):
            if col == name:
                return i
        for i, col in enumerate(self.columns):
            if norm_name(col) == target:
                return i
        if LETTERS_RE.match(name.strip().upper()) and self.letters and any(self.letters):
            for i, letter in enumerate(self.letters):
                if letter == name.strip().upper():
                    return i
        candidates = [col for col in self.columns if target and target in norm_name(col)]
        if len(candidates) == 1:
            return self.columns.index(candidates[0])
        if candidates:
            raise InputError(f"Колонка «{name}» неоднозначна в {self.label}: подходят "
                             + "; ".join(f"«{c}»" for c in candidates) + ". Укажите точнее.")
        known = "; ".join(self.columns)
        raise InputError(f"Колонка «{name}» не найдена в {self.label}. Есть: {known}")


def header_like(row: list[Any]) -> bool:
    cells = [v for v in row if not is_empty(v)]
    if len(cells) < 2:
        return False
    texts = [v for v in cells if isinstance(v, str) and kind_of(v) == "text"]
    if len(texts) < 0.7 * len(cells):
        return False
    return len({norm_name(t) for t in texts}) >= max(2, 0.6 * len(texts))


def detect_header(grid: list[list[Any]]) -> int | None:
    widths = [sum(1 for v in row if not is_empty(v)) for row in grid]
    max_width = max(widths[:200], default=0)
    for i in range(min(HEADER_SCAN_ROWS, len(grid))):
        if widths[i] < max(2, 0.5 * max_width):
            continue
        if not header_like(grid[i]):
            continue
        if any(widths[j] for j in range(i + 1, len(grid))):
            return i
    return None


def is_numbering(row: list[Any]) -> bool:
    values = [v for v in row if not is_empty(v)]
    if len(values) < 2:
        return False
    numbers = [to_number(v) for v in values]
    if any(not isinstance(n, int) for n in numbers):
        return False
    return numbers == list(range(numbers[0], numbers[0] + len(numbers))) and numbers[0] in (0, 1)


def build_table(sg: SheetGrid, header_row: int | None = None, no_header: bool = False,
                keep_totals: bool = False, skip_hidden: bool = False) -> Table:
    table = Table(sg.source, sg.sheet)
    grid = sg.grid
    if not grid or sg.empty:
        table.warnings.append("Лист пустой.")
        return table
    r0 = sg.first_row
    if no_header:
        h = None
    elif header_row is not None:
        h = header_row - r0
        if not 0 <= h < len(grid):
            raise InputError(f"--header-row {header_row}: на листе «{sg.sheet}» данные в строках "
                             f"{r0}–{r0 + len(grid) - 1}.")
    else:
        h = detect_header(grid)
        if h is None:
            table.warnings.append("Строка заголовков не найдена — колонки названы буквами (A, B, …); укажите "
                                  "--header-row, если шапка есть.")
    header_idx = [] if h is None else [h]
    if h is not None and h + 1 < len(grid) and header_row is None:
        merged_here = any(mr0 <= h + r0 <= mr1 and (mc1 > mc0 or mr1 > h + r0)
                          for mr0, mc0, mr1, mc1 in sg.merged if mr0 >= r0)
        if merged_here and header_like(grid[h + 1]):
            header_idx.append(h + 1)
            table.warnings.append(f"Шапка в две строки ({h + r0}–{h + r0 + 1}) склеена: «Верх / Низ».")
    start = (header_idx[-1] + 1) if header_idx else 0
    if start < len(grid) and is_numbering(grid[start]) and header_idx:
        table.numbering_row = start + r0
        start += 1
    # Колонки — от первой до последней непустой в шапке и данных.
    area = [grid[i] for i in header_idx] + grid[start:]
    width = len(grid[0])
    used = [c for c in range(width) if any(not is_empty(row[c]) for row in area)]
    if not used:
        table.warnings.append("Под шапкой нет данных.")
        return table
    c_from, c_to = used[0], used[-1]
    span = range(c_from, c_to + 1)
    names: list[str] = []
    for c in span:
        parts = []
        for i in header_idx:
            v = grid[i][c]
            if not is_empty(v):
                text = re.sub(r"\s+", " ", as_text(v) or "").strip()
                if text and (not parts or norm_name(parts[-1]) != norm_name(text)):
                    parts.append(text)
        names.append(" / ".join(parts) if parts else f"Колонка {column_letter(sg.first_col + c)}")
    seen: Counter = Counter()
    for i, name in enumerate(names):
        seen[norm_name(name)] += 1
        if seen[norm_name(name)] > 1:
            names[i] = f"{name}_{seen[norm_name(name)]}"
    table.columns = names
    table.letters = [column_letter(sg.first_col + c) for c in span]
    table.header_rows = [i + r0 for i in header_idx]

    # Разметка строк: данные, итоги, одиночные (раздел или подпись).
    marked = []
    for i in range(start, len(grid)):
        row = [grid[i][c] for c in span]
        filled = [v for v in row if not is_empty(v)]
        rownum = i + r0
        if not filled:
            table.skipped_empty += 1
            continue
        first_texts = [v for v in filled[:3] if isinstance(v, str)]
        if any(TOTAL_RE.match(t) for t in first_texts):
            marked.append(("total", rownum, row))
        elif len(span) >= 3 and isinstance(filled[0], str) and len({norm_name(as_text(v)) for v in filled}) == 1 \
                and (len(filled) == 1 or len(filled) == len(span)):
            marked.append(("single", rownum, row))
        else:
            marked.append(("data", rownum, row))
    data_positions = [k for k, item in enumerate(marked) if item[0] == "data"]
    last_data = data_positions[-1] if data_positions else -1
    singles = [k for k, item in enumerate(marked) if item[0] == "single" and k < last_data]
    # Раздел — одиночная строка, за которой сразу идут данные; разделов меньше, чем строк данных.
    use_sections = bool(singles) and len(singles) <= max(1, len(data_positions) // 2) and all(
        k + 1 < len(marked) and marked[k + 1][0] == "data" for k in singles)
    section = None
    rows, rownums = [], []
    for k, (kind, rownum, row) in enumerate(marked):
        if kind == "single" and k > last_data:
            table.footer_notes.append(as_text(next(v for v in row if not is_empty(v))))
            continue
        if kind == "single" and use_sections:
            section = as_text(next(v for v in row if not is_empty(v)))
            table.sections.append(section)
            continue
        if kind == "total":
            label = as_text(next(v for v in row if not is_empty(v)))
            table.totals.append({"row": rownum, "label": label, "section": section, "values": row})
            if not keep_totals:
                continue
        if skip_hidden and rownum in sg.hidden_rows:
            table.hidden_data_rows += 1
            continue
        if rownum in sg.hidden_rows:
            table.hidden_data_rows += 1
        rows.append(([section] if use_sections else []) + row)
        rownums.append(rownum)
    if use_sections:
        name = "Раздел" if "Раздел" not in table.columns else "Раздел (группа строк)"
        table.columns.insert(0, name)
        table.letters.insert(0, None)
    # Типы колонок и приведение значений.
    table.types, table.nonconforming = [], []
    for c in range(len(table.columns)):
        col_type = classify([row[c] for row in rows])
        bad = []
        for row in rows:
            row[c], ok = convert(row[c], col_type)
            if not ok:
                bad.append(row[c])
        table.types.append(col_type)
        table.nonconforming.append(bad)
    # Итоговые строки — в типах колонок (для сверки сумм).
    offset = 1 if use_sections else 0
    for total in table.totals:
        values = {}
        for c, value in enumerate(total.pop("values")):
            col = c + offset
            converted, _ = convert(normalize_cell(value), table.types[col])
            if converted is not None:
                values[table.columns[col]] = converted
        total["values"] = values
    table.rows, table.row_numbers = rows, rownums
    if rownums:
        table.data_range = f"{table.letters[offset]}{rownums[0]}:{table.letters[-1]}{rownums[-1]}"
    # Предупреждения.
    if table.numbering_row:
        table.warnings.append(f"Строка {table.numbering_row} с нумерацией колонок (1, 2, 3…) пропущена.")
    if table.totals and not keep_totals:
        labels = ", ".join(f"«{t['label']}» (стр. {t['row']})" for t in table.totals[:5])
        table.warnings.append(f"Итоговые строки не включены в данные: {labels}"
                              + (" …" if len(table.totals) > 5 else "") + ".")
    if table.sections:
        table.warnings.append(f"Строки-разделы ({len(table.sections)}) вынесены в колонку «{table.columns[0]}».")
    if table.footer_notes:
        table.warnings.append(f"Подписи под таблицей не включены в данные: {len(table.footer_notes)}.")
    if table.hidden_data_rows:
        table.warnings.append(f"Скрытых строк в данных: {table.hidden_data_rows}"
                              + (" — исключены (--skip-hidden)." if skip_hidden else " — включены в данные."))
    hidden_in_table = [letter for letter in sg.hidden_cols if letter in table.letters]
    if hidden_in_table:
        table.warnings.append(f"Скрытые колонки в таблице: {', '.join(hidden_in_table)}.")
    for name, col_type, bad in zip(table.columns, table.types, table.nonconforming):
        if bad and col_type != "text":
            examples = ", ".join(f"«{b}»" for b in list(dict.fromkeys(bad))[:3])
            table.warnings.append(f"«{name}» ({TYPE_LABELS[col_type]}): значений другого вида — {len(bad)} "
                                  f"({examples}); в расчётах не участвуют.")
    if sg.truncated:
        table.warnings.append("Лист обрезан по --max-rows: прочитаны не все строки.")
    return table


# --- выбор листов -----------------------------------------------------------


def split_spec(spec: str) -> tuple[Path, str | None]:
    if "::" in spec:
        path, sheet = spec.rsplit("::", 1)
        return Path(path), sheet.strip() or None
    return Path(spec), None


def pick_sheet(grids: list[SheetGrid], wanted: str, source: str) -> SheetGrid:
    names = [g.sheet for g in grids]
    if wanted.isdigit() and 1 <= int(wanted) <= len(grids):
        return grids[int(wanted) - 1]
    for g in grids:
        if g.sheet == wanted:
            return g
    for g in grids:
        if norm_name(g.sheet) == norm_name(wanted):
            return g
    raise InputError(f"{source}: нет листа «{wanted}». Листы: {', '.join(names)}")


class Loader:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.notes: list[str] = []

    def grids(self, path: Path, with_formulas: bool = False) -> list[SheetGrid]:
        kind = check_file(path)
        if kind == "csv":
            return [read_csv_grid(path)]
        return workbook_grids(path, self.args.max_rows, getattr(self.args, "range", None), with_formulas)

    def table(self, sg: SheetGrid) -> Table:
        a = self.args
        return build_table(sg, header_row=getattr(a, "header_row", None), no_header=getattr(a, "no_header", False),
                           keep_totals=getattr(a, "keep_totals", False), skip_hidden=getattr(a, "skip_hidden", False))

    def tables(self, specs: list[str], all_sheets: bool | None = None) -> list[Table]:
        all_sheets = getattr(self.args, "all_sheets", False) if all_sheets is None else all_sheets
        out = []
        for spec in specs:
            path, wanted = split_spec(spec)
            wanted = wanted or getattr(self.args, "sheet", None)
            grids = self.grids(path)
            if wanted:
                out.append(self.table(pick_sheet(grids, wanted, path.name)))
                continue
            candidates = [g for g in grids if g.state == "visible" and not g.empty]
            if not candidates:
                raise InputError(f"{path.name}: нет видимых листов с данными.")
            if all_sheets:
                out.extend(self.table(g) for g in candidates)
                continue
            out.append(self.table(candidates[0]))
            if len(candidates) > 1:
                others = ", ".join(f"«{g.sheet}»" for g in candidates[1:])
                self.notes.append(f"{path.name}: взят лист «{candidates[0].sheet}», есть ещё {others} — укажите "
                                  f"«{path.name}::Лист» или --all-sheets.")
        return out


# --- склейка ----------------------------------------------------------------


def stack(tables: list[Table], always_source: bool = False) -> Table:
    if len(tables) == 1 and not always_source:
        return tables[0]
    merged = Table(", ".join(dict.fromkeys(t.source for t in tables)), ", ".join(dict.fromkeys(t.sheet for t in tables)))
    columns: list[str] = []
    by_norm: dict[str, int] = {}
    for t in tables:
        for col in t.columns:
            key = norm_name(col)
            if key not in by_norm:
                by_norm[key] = len(columns)
                columns.append(col)
    merged.columns = ["Файл", "Лист"] + columns
    merged.letters = [None] * len(merged.columns)
    for t in tables:
        positions = [by_norm[norm_name(col)] for col in t.columns]
        for row, rownum in zip(t.rows, t.row_numbers):
            out = [None] * len(columns)
            for pos, value in zip(positions, row):
                out[pos] = value
            merged.rows.append([t.source, t.sheet] + out)
            merged.row_numbers.append(rownum)
        merged.totals.extend({**total, "source": t.label} for total in t.totals)
        merged.warnings.extend(f"{t.label}: {w}" for w in t.warnings)
    merged.types, merged.nonconforming = [], []
    for c in range(len(merged.columns)):
        col_type = classify([row[c] for row in merged.rows])
        bad = []
        for row in merged.rows:
            row[c], ok = convert(row[c], col_type)
            if not ok:
                bad.append(row[c])
        merged.types.append(col_type)
        merged.nonconforming.append(bad)
    for t in tables:
        missing = [col for col in columns if norm_name(col) not in {norm_name(x) for x in t.columns}]
        if missing:
            merged.warnings.append(f"{t.label}: нет колонок {', '.join(f'«{m}»' for m in missing)} — там пусто.")
    return merged


# --- отбор и свод -----------------------------------------------------------


def parse_condition(expr: str, table: Table):
    expr = expr.strip()
    match = WHERE_EMPTY_RE.match(expr)
    if match:
        idx = table.index_of(match.group(1).strip())
        want_empty = match.group(2).lower().replace(" ", "") in ("isempty", "пусто")
        return lambda row: (row[idx] is None) == want_empty
    match = WHERE_IN_RE.match(expr)
    if match:
        idx = table.index_of(match.group(1).strip())
        options = [o.strip() for o in match.group(3).split("|")]
        negate = match.group(2).lower().startswith("not")
        targets = {comparable(o, table.types[idx]) for o in options}
        return lambda row: (comparable(row[idx], table.types[idx]) in targets) != negate
    match = WHERE_OP_RE.match(expr)
    if not match:
        raise InputError(f"Условие «{expr}» не разобрано. Примеры: «Сумма > 1000», «Статус = Просрочено», "
                         "«Тема ~ дорог», «Статус in Новый|В работе», «Срок is empty».")
    name, op, raw = match.group(1).strip(), match.group(2), match.group(3).strip()
    idx = table.index_of(name)
    col_type = table.types[idx]
    if op in ("~", "!~"):
        needle = norm_name(raw)
        return lambda row: (needle in norm_name(as_text(row[idx]) or "")) != (op == "!~")
    target = comparable(raw, col_type)
    if col_type in ("integer", "number") and not isinstance(target, (int, float)):
        raise InputError(f"«{name}» — числовая колонка, а «{raw}» не число.")
    if col_type in ("date", "datetime") and target is None:
        raise InputError(f"«{name}» — колонка дат, а «{raw}» не дата (ДД.ММ.ГГГГ или ГГГГ-ММ-ДД).")
    prefix = col_type == "datetime" and isinstance(target, str) and len(target) == 10

    def check(row):
        value = comparable(row[idx], col_type)
        if value is None:
            return op == "!="
        if type(value) is not type(target) and not (isinstance(value, (int, float)) and isinstance(target, (int, float))):
            return op == "!="
        if prefix and op in ("=", "!="):
            value = value[:10]
        try:
            return {"=": value == target, "!=": value != target, ">": value > target, ">=": value >= target,
                    "<": value < target, "<=": value <= target}[op]
        except TypeError:
            return False

    return check


def comparable(value: Any, col_type: str) -> Any:
    if value is None:
        return None
    if col_type in ("integer", "number"):
        number = to_number(value)
        return number if number is not None else norm_name(as_text(value))
    if col_type in ("date", "datetime"):
        if isinstance(value, str) and len(value) >= 10 and value[4] == "-":
            return value
        text = to_date_text(value, col_type == "datetime")
        return text if text else None
    if col_type == "bool":
        if isinstance(value, bool):
            return value
        return norm_name(str(value)) in ("да", "true", "1", "yes", "истина")
    return norm_name(as_text(value) or "")


def parse_agg(spec: str, table: Table) -> tuple[str, int | None, str]:
    func, _, column = spec.partition(":")
    func = func.strip().lower()
    aliases = {"mean": "avg", "среднее": "avg", "сумма": "sum", "количество": "count", "уникальных": "nunique"}
    func = aliases.get(func, func)
    if func not in AGG_FUNCS:
        raise InputError(f"--agg «{spec}»: функции {', '.join(AGG_FUNCS)}; формат «sum:Сумма» или «count».")
    if not column.strip():
        if func != "count":
            raise InputError(f"--agg «{spec}»: укажите колонку, например «{func}:Сумма».")
        return func, None, "Количество"
    idx = table.index_of(column.strip())
    title = {"count": "Заполнено", "sum": "Сумма", "avg": "Среднее", "min": "Минимум", "max": "Максимум",
             "median": "Медиана", "nunique": "Уникальных"}[func]
    return func, idx, f"{title}: {table.columns[idx]}"


def aggregate(func: str, values: list[Any]) -> Any:
    filled = [v for v in values if v is not None]
    if func == "count":
        return len(filled)
    if func == "nunique":
        return len({json.dumps(v, ensure_ascii=False, default=str) for v in filled})
    if func in ("min", "max"):
        comparable_values = [v for v in filled if isinstance(v, (int, float)) and not isinstance(v, bool)] \
            or [v for v in filled if isinstance(v, str)]
        if not comparable_values:
            return None
        return min(comparable_values) if func == "min" else max(comparable_values)
    numbers = [v for v in filled if isinstance(v, (int, float)) and not isinstance(v, bool)]
    if not numbers:
        return None
    if func == "sum":
        return tidy_number(math.fsum(numbers))
    if func == "avg":
        return tidy_number(math.fsum(numbers) / len(numbers))
    return tidy_number(statistics.median(numbers))


def run_query(table: Table, args: argparse.Namespace) -> dict[str, Any]:
    conditions = [parse_condition(expr, table) for expr in args.where or []]
    rows = [row for row in table.rows if all(cond(row) for cond in conditions)]
    matched = len(rows)
    if args.group_by or args.agg:
        groups = [table.index_of(name.strip()) for g in (args.group_by or []) for name in g.split(",") if name.strip()]
        aggs = [parse_agg(spec, table) for a in (args.agg or ["count"]) for spec in a.split(",") if spec.strip()]
        buckets: dict[tuple, list[list[Any]]] = {}
        for row in rows:
            buckets.setdefault(tuple(row[i] for i in groups), []).append(row)
        if not groups:
            buckets = {(): rows}
        columns = [table.columns[i] for i in groups] + [title for _, _, title in aggs]
        out_rows = []
        for key, members in buckets.items():
            values = [len(members) if idx is None else aggregate(func, [m[idx] for m in members])
                      for func, idx, _ in aggs]
            out_rows.append(list(key) + values)
        types = [table.types[i] for i in groups] + [classify([r[len(groups) + k] for r in out_rows])
                                                    for k in range(len(aggs))]
        if args.share and aggs:
            first = len(groups)
            total = math.fsum(r[first] for r in out_rows if isinstance(r[first], (int, float)))
            columns.append(f"Доля, % ({columns[first]})")
            types.append("number")
            for r in out_rows:
                r.append(round(100 * r[first] / total, 1) if total and isinstance(r[first], (int, float)) else None)
        sort = args.sort or ([f"{columns[len(groups)]}:desc"] if groups else [])
        result_columns = columns
    else:
        result_columns = [table.columns[table.index_of(name.strip())] for s in (args.select or [])
                          for name in s.split(",") if name.strip()] or list(table.columns)
        picks = [table.columns.index(c) for c in result_columns]
        out_rows = [[row[i] for i in picks] for row in rows]
        types = [table.types[i] for i in picks]
        sort = args.sort or []
    view = Table(table.source, table.sheet)
    view.columns, view.types, view.rows = result_columns, types, out_rows
    for spec in reversed(sort):
        name, _, direction = spec.rpartition(":") if spec.lower().endswith((":desc", ":asc")) else (spec, "", "asc")
        idx = view.index_of(name)
        present = [r for r in view.rows if r[idx] is not None]
        missing = [r for r in view.rows if r[idx] is None]
        present.sort(key=lambda r: sort_key(r[idx]), reverse=direction.lower() == "desc")
        view.rows = present + missing
    return {"view": view, "matched": matched, "total": len(table.rows)}


def sort_key(value: Any):
    if isinstance(value, bool):
        return (0, int(value), "")
    if isinstance(value, (int, float)):
        return (0, value, "")
    return (1, 0, norm_name(str(value)))


# --- профиль ----------------------------------------------------------------


def profile_table(table: Table) -> dict[str, Any]:
    columns = []
    n = len(table.rows)
    for c, (name, col_type) in enumerate(zip(table.columns, table.types)):
        values = [row[c] for row in table.rows]
        filled = [v for v in values if v is not None]
        info: dict[str, Any] = {
            "name": name, "letter": table.letters[c] if c < len(table.letters) else None, "type": col_type,
            "type_label": TYPE_LABELS[col_type], "filled": len(filled), "empty": n - len(filled),
            "empty_pct": round(100 * (n - len(filled)) / n, 1) if n else 0.0,
            "unique": len({json.dumps(v, ensure_ascii=False, default=str) for v in filled}),
        }
        bad = table.nonconforming[c] if c < len(table.nonconforming) else []
        if bad and col_type != "text":
            info["nonconforming"] = len(bad)
            info["nonconforming_examples"] = list(dict.fromkeys(map(str, bad)))[:5]
        numbers = [v for v in filled if isinstance(v, (int, float)) and not isinstance(v, bool)]
        if col_type in ("integer", "number") and numbers:
            info.update(min=min(numbers), max=max(numbers), sum=tidy_number(math.fsum(numbers)),
                        mean=tidy_number(round(math.fsum(numbers) / len(numbers), 6)),
                        median=tidy_number(statistics.median(numbers)), negative=sum(1 for x in numbers if x < 0),
                        zero=sum(1 for x in numbers if x == 0))
        elif col_type in ("date", "datetime"):
            dates = [v for v in filled if isinstance(v, str) and len(v) >= 10 and v[4] == "-"]
            if dates:
                info.update(min=min(dates), max=max(dates))
        if col_type in ("text", "bool") or info["unique"] <= max(20, n // 2):
            counts = Counter(as_text(v) for v in filled)
            info["top"] = [{"value": v, "count": k} for v, k in counts.most_common(TOP_VALUES)]
        if col_type == "text" and filled:
            info["max_length"] = max(len(str(v)) for v in filled)
        columns.append(info)
    keys = Counter(json.dumps(row, ensure_ascii=False, default=str) for row in table.rows)
    result = {
        "source": table.source, "sheet": table.sheet, "rows": n,
        "duplicate_rows": sum(k - 1 for k in keys.values() if k > 1), "columns": columns,
        "totals_check": totals_check(table), "warnings": table.warnings,
    }
    return result


def totals_check(table: Table) -> list[dict[str, Any]]:
    """Сверка суммы колонок с последней строкой «Итого/Всего» файла (общий итог)."""
    if not table.totals:
        return []
    grand = table.totals[-1]
    checks = []
    for c, (name, col_type) in enumerate(zip(table.columns, table.types)):
        if col_type not in ("integer", "number") or name not in grand["values"]:
            continue
        in_file = grand["values"][name]
        if not isinstance(in_file, (int, float)):
            continue
        computed = tidy_number(math.fsum(row[c] for row in table.rows if isinstance(row[c], (int, float))
                                         and not isinstance(row[c], bool)))
        checks.append({"column": name, "computed": computed, "in_file": in_file, "row": grand["row"],
                       "match": math.isclose(computed, in_file, rel_tol=1e-9, abs_tol=0.005)})
    return checks


# --- сверка и объединение ---------------------------------------------------


def key_value(value: Any, ignore_case: bool) -> Any:
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return tidy_number(value)
    text = re.sub(r"\s+", " ", str(value)).strip()
    number = parse_number(text)
    if number is not None:
        return number[0]
    return text.casefold() if ignore_case else text


def same(a: Any, b: Any, ignore_case: bool) -> bool:
    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool):
        return math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-9)
    return key_value(a, ignore_case) == key_value(b, ignore_case)


def index_by_key(table: Table, keys: list[int], ignore_case: bool) -> tuple[dict[tuple, list[Any]], list[str]]:
    index: dict[tuple, list[Any]] = {}
    duplicates = []
    for row in table.rows:
        key = tuple(key_value(row[i], ignore_case) for i in keys)
        if all(k is None for k in key):
            continue
        if key in index:
            duplicates.append(" | ".join(map(str, key)))
            continue
        index[key] = row
    return index, list(dict.fromkeys(duplicates))


def run_diff(a: Table, b: Table, args: argparse.Namespace) -> dict[str, Any]:
    key_names = [k.strip() for spec in args.key for k in spec.split(",") if k.strip()]
    keys_a = [a.index_of(k) for k in key_names]
    keys_b = [b.index_of(k) for k in key_names]
    ia, dup_a = index_by_key(a, keys_a, args.ignore_case)
    ib, dup_b = index_by_key(b, keys_b, args.ignore_case)
    key_set_a = {norm_name(a.columns[i]) for i in keys_a}
    common = [col for col in a.columns if norm_name(col) in {norm_name(x) for x in b.columns}
              and norm_name(col) not in key_set_a]
    if args.columns:
        wanted = [c.strip() for spec in args.columns for c in spec.split(",") if c.strip()]
        common = [a.columns[a.index_of(c)] for c in wanted]
    pairs = [(a.columns.index(col), b.index_of(col), col) for col in common]
    key_titles = [a.columns[i] for i in keys_a]
    added = [dict(zip(b.columns, ib[k])) for k in ib if k not in ia]
    removed = [dict(zip(a.columns, ia[k])) for k in ia if k not in ib]
    changed, unchanged = [], 0
    for k, row_a in ia.items():
        row_b = ib.get(k)
        if row_b is None:
            continue
        changes = [{"column": col, "a": row_a[ca], "b": row_b[cb]} for ca, cb, col in pairs
                   if not same(row_a[ca], row_b[cb], args.ignore_case)]
        if changes:
            changed.append({"key": dict(zip(key_titles, (row_a[i] for i in keys_a))), "changes": changes})
        else:
            unchanged += 1
    limit = args.limit or DIFF_LIMIT
    warnings = []
    if dup_a:
        warnings.append(f"Повторяющиеся ключи в первой таблице ({len(dup_a)}), взята первая строка: "
                        + ", ".join(dup_a[:5]))
    if dup_b:
        warnings.append(f"Повторяющиеся ключи во второй таблице ({len(dup_b)}), взята первая строка: "
                        + ", ".join(dup_b[:5]))
    for name, items in (("added", added), ("removed", removed), ("changed", changed)):
        if len(items) > limit:
            warnings.append(f"Список {name} показан не целиком: {limit} из {len(items)} (--limit).")
    only_a = [c for c in a.columns if norm_name(c) not in {norm_name(x) for x in b.columns}]
    only_b = [c for c in b.columns if norm_name(c) not in {norm_name(x) for x in a.columns}]
    return {
        "a": a.label, "b": b.label, "key": key_titles, "rows_a": len(a.rows), "rows_b": len(b.rows),
        "added_count": len(added), "removed_count": len(removed), "changed_count": len(changed),
        "unchanged_count": unchanged, "compared_columns": common, "columns_only_a": only_a, "columns_only_b": only_b,
        "added": added[:limit], "removed": removed[:limit], "changed": changed[:limit],
        "warnings": a.warnings + b.warnings + warnings,
    }


def run_join(a: Table, b: Table, args: argparse.Namespace) -> tuple[Table, dict[str, Any]]:
    left_names = [k.strip() for spec in args.key for k in spec.split(",") if k.strip()]
    right_names = [k.strip() for spec in (args.right_key or args.key) for k in spec.split(",") if k.strip()]
    if len(left_names) != len(right_names):
        raise InputError("--key и --right-key: одинаковое число колонок.")
    keys_a = [a.index_of(k) for k in left_names]
    keys_b = [b.index_of(k) for k in right_names]
    ib, dup_b = index_by_key(b, keys_b, args.ignore_case)
    take = [i for i in range(len(b.columns)) if i not in keys_b]
    if args.columns:
        take = [b.index_of(c.strip()) for spec in args.columns for c in spec.split(",") if c.strip()]
    existing = {norm_name(c) for c in a.columns}
    titles = []
    for i in take:
        title = b.columns[i]
        if norm_name(title) in existing:
            title = f"{title} (2)"
        titles.append(title)
    view = Table(a.source, a.sheet)
    view.columns = a.columns + titles
    view.types = a.types + [b.types[i] for i in take]
    matched = 0
    for row in a.rows:
        key = tuple(key_value(row[i], args.ignore_case) for i in keys_a)
        other = ib.get(key)
        if other is None and args.how == "inner":
            continue
        matched += other is not None
        view.rows.append(row + ([other[i] for i in take] if other is not None else [None] * len(take)))
    stats = {"rows_a": len(a.rows), "rows_b": len(b.rows), "matched": matched,
             "unmatched_a": len(a.rows) - matched, "how": args.how,
             "warnings": a.warnings + b.warnings + ([f"Повторяющиеся ключи во второй таблице ({len(dup_b)}), "
                                                     f"взята первая строка: " + ", ".join(dup_b[:5])] if dup_b else [])}
    return view, stats


# --- вывод ------------------------------------------------------------------


def md_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "да" if value else "нет"
    if isinstance(value, (int, float)):
        if isinstance(value, int):
            text = f"{value:,}".replace(",", " ")
        else:
            text = f"{value:,.2f}".replace(",", " ").replace(".", ",") if abs(value) >= 1 else \
                f"{value:.4g}".replace(".", ",")
        return text
    if isinstance(value, str) and DATE_ISO_RE.match(value):
        d = value[8:10] + "." + value[5:7] + "." + value[0:4]
        return d + (" " + value[11:16] if len(value) > 10 and value[11:16] != "00:00" else "")
    text = str(value).replace("|", "\\|").replace("\n", " ")
    return text if len(text) <= 120 else text[:119] + "…"


def md_table(columns: list[str], rows: list[list[Any]]) -> str:
    if not columns:
        return "_нет колонок_"
    head = "| " + " | ".join(c.replace("|", "\\|") for c in columns) + " |"
    sep = "|" + "|".join("---" for _ in columns) + "|"
    body = ["| " + " | ".join(md_value(v) for v in row) + " |" for row in rows]
    return "\n".join([head, sep, *body]) if body else head + "\n" + sep + "\n_строк нет_"


def xlsx_spec(view: Table, title: str) -> dict[str, Any]:
    mapping = {"integer": "integer", "number": "number", "date": "date", "datetime": "datetime", "bool": "bool"}
    keys = [f"c{i + 1}" for i in range(len(view.columns))]
    sheet_name = re.sub(r"[\\/*?:\[\]]", " ", title)[:31] or "Данные"
    return {
        "properties": {"title": title},
        "sheets": [{
            "name": sheet_name, "title": title,
            "columns": [{"title": col, "key": key, "type": mapping.get(t, "text")}
                        for col, key, t in zip(view.columns, keys, view.types)],
            "rows": [dict(zip(keys, row)) for row in view.rows],
        }],
    }


def emit(text: str, out: str | None) -> None:
    if out:
        Path(out).write_text(text, encoding="utf-8-sig" if out.lower().endswith(".csv") else "utf-8")
        print(out)
    else:
        sys.stdout.write(text if text.endswith("\n") else text + "\n")


def dump_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2, default=str)


def rows_output(view: Table, args: argparse.Namespace, meta: dict[str, Any], title: str) -> str:
    limit = args.limit if args.limit is not None else (None if args.out else DEFAULT_LIMIT)
    offset = getattr(args, "offset", 0) or 0
    total = len(view.rows)
    shown = view.rows[offset: offset + limit if limit is not None else None]
    truncated = offset + len(shown) < total
    fmt = args.format
    if fmt == "csv":
        buf = io.StringIO()
        writer = csv.writer(buf, delimiter=";", lineterminator="\n")
        writer.writerow(view.columns)
        for row in shown:
            writer.writerow(["" if v is None else (str(v).replace(".", ",") if isinstance(v, float) else v)
                             for v in row])
        return buf.getvalue()
    if fmt == "xlsx-spec":
        part = Table(view.source, view.sheet)
        part.columns, part.types, part.rows = view.columns, view.types, shown
        return dump_json(xlsx_spec(part, title))
    if fmt == "md":
        lines = [f"**{title}**", ""]
        for key, value in meta.items():
            if key != "warnings" and value not in (None, "", []):
                lines.append(f"- {key}: {value if not isinstance(value, list) else ', '.join(map(str, value))}")
        lines += ["", md_table(view.columns, shown)]
        if truncated:
            lines.append(f"\n_Показано {len(shown)} из {total} строк (--limit/--offset)._")
        for w in meta.get("warnings") or []:
            lines.append(f"\n> {w}")
        return "\n".join(lines)
    data = {**meta, "columns": [{"name": c, "type": t} for c, t in zip(view.columns, view.types)],
            "rows_total": total, "offset": offset, "returned": len(shown), "truncated": truncated,
            "rows": [dict(zip(view.columns, row)) for row in shown]}
    data["warnings"] = meta.get("warnings", [])
    return dump_json(data)


# --- команды ----------------------------------------------------------------


def cmd_inspect(args: argparse.Namespace, loader: Loader) -> str:
    files = []
    for spec in args.sources:
        path, wanted = split_spec(spec)
        wanted = wanted or args.sheet
        grids = loader.grids(path, with_formulas=check_file(path) == "xlsx")
        if wanted:
            grids = [pick_sheet(grids, wanted, path.name)]
        sheets = []
        for sg in grids:
            info: dict[str, Any] = {"sheet": sg.sheet, "state": sg.state, "used_range": sg.used_range or None,
                                    "empty": sg.empty}
            if not sg.empty:
                t = loader.table(sg)
                info["table"] = {
                    "header_rows": t.header_rows, "data_range": t.data_range, "rows": len(t.rows),
                    "columns": [{"name": n, "letter": l, "type": ty, "type_label": TYPE_LABELS[ty],
                                 "filled": sum(1 for r in t.rows if r[i] is not None)}
                                for i, (n, l, ty) in enumerate(zip(t.columns, t.letters, t.types))],
                    "totals_rows": [{"row": x["row"], "label": x["label"], "section": x["section"]} for x in t.totals],
                    "sections": t.sections[:20], "footer_notes": t.footer_notes[:10],
                    "sample": [dict(zip(t.columns, r)) for r in t.rows[:args.sample]],
                }
                info["merged_cells"] = len(sg.merged)
                info["hidden_rows"] = len(sg.hidden_rows)
                info["hidden_columns"] = sg.hidden_cols
                info["formulas_without_values"] = sg.formulas_without_values
                warnings = list(t.warnings)
                if sg.formulas_without_values:
                    warnings.append(f"{sg.formulas_without_values} формул без сохранённых значений (файл сохранён не "
                                    "в Excel?) — такие ячейки читаются пустыми; пересохраните файл в Excel/LibreOffice.")
                info["warnings"] = warnings
            sheets.append(info)
        files.append({"file": path.name, "sheets": sheets})
    if args.format == "md":
        out = []
        for f in files:
            out.append(f"## {f['file']}")
            for s in f["sheets"]:
                state = "" if s["state"] == "visible" else f" (лист {'скрыт' if s['state'] == 'hidden' else 'очень скрыт'})"
                if s["empty"]:
                    out.append(f"\n### {s['sheet']}{state} — пустой")
                    continue
                t = s["table"]
                header = ", ".join(map(str, t["header_rows"])) or "нет"
                out.append(f"\n### {s['sheet']}{state}\n\nЗанято: {s['used_range']}; шапка — строка {header}; "
                           f"данные {t['data_range'] or '—'}, строк: {t['rows']}.")
                out.append("\n" + md_table(["Колонка", "Буква", "Тип", "Заполнено"],
                                           [[c["name"], c["letter"] or "", c["type_label"], c["filled"]]
                                            for c in t["columns"]]))
                if t["sample"]:
                    cols = list(t["sample"][0].keys())
                    out.append("\nПервые строки:\n\n" + md_table(cols, [[r[c] for c in cols] for r in t["sample"]]))
                for w in s.get("warnings") or []:
                    out.append(f"\n> {w}")
        for note in loader.notes:
            out.append(f"\n> {note}")
        return "\n".join(out)
    return dump_json({"files": files, "notes": loader.notes})


def cmd_read(args: argparse.Namespace, loader: Loader) -> str:
    tables = loader.tables(args.sources)
    view = stack(tables)
    if args.select:
        names = [view.columns[view.index_of(n.strip())] for s in args.select for n in s.split(",") if n.strip()]
        picks = [view.columns.index(n) for n in names]
        part = Table(view.source, view.sheet)
        part.columns, part.types = names, [view.types[i] for i in picks]
        part.rows = [[row[i] for i in picks] for row in view.rows]
        part.warnings = view.warnings
        view = part
    meta = {"sources": [t.label for t in tables], "warnings": view.warnings + loader.notes}
    return rows_output(view, args, meta, " + ".join(t.label for t in tables))


def cmd_profile(args: argparse.Namespace, loader: Loader) -> str:
    tables = loader.tables(args.sources)
    if args.stack and len(tables) > 1:
        tables = [stack(tables)]
    profiles = [profile_table(t) for t in tables]
    if args.format == "md":
        out = []
        for p in profiles:
            out.append(f"## {p['source']} :: {p['sheet']}\n\nСтрок: {p['rows']}; полных дублей строк: "
                       f"{p['duplicate_rows']}.")
            rows = []
            for c in p["columns"]:
                stats = ""
                if c["type"] in ("integer", "number") and "sum" in c:
                    stats = (f"мин {md_value(c['min'])}, макс {md_value(c['max'])}, сумма {md_value(c['sum'])}, "
                             f"среднее {md_value(c['mean'])}")
                elif c["type"] in ("date", "datetime") and "min" in c:
                    stats = f"{md_value(c['min'])} — {md_value(c['max'])}"
                elif c.get("top"):
                    stats = "; ".join(f"{md_value(x['value'])} ({x['count']})" for x in c["top"][:3])
                bad = f"{c['nonconforming']} ({', '.join(c['nonconforming_examples'][:2])})" if c.get("nonconforming") else ""
                rows.append([c["name"], c["type_label"], c["filled"], f"{c['empty_pct']}%", c["unique"], stats, bad])
            out.append("\n" + md_table(["Колонка", "Тип", "Заполнено", "Пусто", "Уникальных", "Значения", "Не того вида"],
                                       rows))
            if p["totals_check"]:
                out.append("\nСверка с итоговой строкой файла:\n\n" + md_table(
                    ["Колонка", "Сумма строк", "В файле", "Совпадает"],
                    [[c["column"], c["computed"], c["in_file"], "да" if c["match"] else "НЕТ"] for c in p["totals_check"]]))
            for w in p["warnings"]:
                out.append(f"\n> {w}")
        for note in loader.notes:
            out.append(f"\n> {note}")
        return "\n".join(out)
    return dump_json({"tables": profiles, "notes": loader.notes})


def cmd_query(args: argparse.Namespace, loader: Loader) -> str:
    tables = loader.tables(args.sources)
    table = stack(tables)
    result = run_query(table, args)
    view = result["view"]
    meta = {"sources": [t.label for t in tables], "where": args.where or [], "group_by": args.group_by or [],
            "agg": args.agg or [], "rows_in_table": result["total"], "rows_matched": result["matched"],
            "warnings": table.warnings + loader.notes}
    title = "Свод" if (args.group_by or args.agg) else "Отбор"
    return rows_output(view, args, meta, f"{title}: " + " + ".join(t.label for t in tables))


def cmd_combine(args: argparse.Namespace, loader: Loader) -> str:
    tables = loader.tables(args.sources)
    view = stack(tables, always_source=True)
    meta = {"sources": [f"{t.label} — {len(t.rows)} строк" for t in tables], "warnings": view.warnings + loader.notes}
    return rows_output(view, args, meta, "Склейка: " + " + ".join(t.label for t in tables))


def cmd_diff(args: argparse.Namespace, loader: Loader) -> str:
    tables = loader.tables(args.sources, all_sheets=False)
    if len(tables) != 2:
        raise InputError("diff сравнивает ровно две таблицы: «файл1 файл2» или «файл::Лист1 файл::Лист2».")
    result = run_diff(tables[0], tables[1], args)
    result["warnings"] += loader.notes
    if args.format != "md":
        return dump_json(result)
    out = [f"**Сверка по ключу {', '.join(result['key'])}:** {result['a']} → {result['b']}", "",
           f"- строк: {result['rows_a']} → {result['rows_b']}",
           f"- добавлено: {result['added_count']}, удалено: {result['removed_count']}, "
           f"изменено: {result['changed_count']}, без изменений: {result['unchanged_count']}"]
    if result["columns_only_a"] or result["columns_only_b"]:
        out.append(f"- колонки только в первой: {', '.join(result['columns_only_a']) or '—'}; только во второй: "
                   f"{', '.join(result['columns_only_b']) or '—'}")
    if result["changed"]:
        rows = [[" | ".join(md_value(v) for v in item["key"].values()), ch["column"], ch["a"], ch["b"]]
                for item in result["changed"] for ch in item["changes"]]
        out += ["", "Изменения:", "", md_table(["Ключ", "Колонка", "Было", "Стало"], rows)]
    for name, title in (("added", "Добавлены"), ("removed", "Удалены")):
        if result[name]:
            cols = list(result[name][0].keys())
            out += ["", f"{title}:", "", md_table(cols, [[r[c] for c in cols] for r in result[name]])]
    for w in result["warnings"]:
        out.append(f"\n> {w}")
    return "\n".join(out)


def cmd_join(args: argparse.Namespace, loader: Loader) -> str:
    tables = loader.tables(args.sources, all_sheets=False)
    if len(tables) != 2:
        raise InputError("join соединяет ровно две таблицы: основную и ту, из которой берутся колонки.")
    view, stats = run_join(tables[0], tables[1], args)
    stats["warnings"] += loader.notes
    meta = {"sources": [t.label for t in tables], **stats}
    return rows_output(view, args, meta, f"Объединение: {tables[0].label} ← {tables[1].label}")


# --- CLI --------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Чтение и анализ таблиц Excel/CSV")
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p, formats=("json", "md", "csv", "xlsx-spec"), default="json", rows=True):
        p.add_argument("sources", nargs="+", help="файл .xlsx/.xlsm/.csv или «файл::Лист» (имя или номер с 1)")
        p.add_argument("--sheet", help="лист для всех файлов (имя или номер с 1)")
        p.add_argument("--all-sheets", action="store_true", help="взять все видимые листы с данными")
        p.add_argument("--header-row", type=int, help="номер строки шапки на листе (по умолчанию — поиск)")
        p.add_argument("--no-header", action="store_true", help="шапки нет — колонки называются буквами")
        p.add_argument("--range", help="ограничить лист диапазоном, например A5:H200")
        p.add_argument("--keep-totals", action="store_true", help="оставить строки «Итого/Всего» в данных")
        p.add_argument("--skip-hidden", action="store_true", help="исключить скрытые строки")
        p.add_argument("--max-rows", type=int, default=MAX_ROWS_DEFAULT, help="предел строк на лист")
        p.add_argument("--format", choices=formats, default=default)
        p.add_argument("--out", help="записать результат в файл (в stdout — путь)")
        if rows:
            p.add_argument("--limit", type=int, help=f"сколько строк вывести (по умолчанию {DEFAULT_LIMIT}; "
                                                      "с --out — все)")
            p.add_argument("--offset", type=int, default=0, help="пропустить строк")

    p = sub.add_parser("inspect", help="обзор файлов и листов")
    common(p, formats=("json", "md"), default="md", rows=False)
    p.add_argument("--sample", type=int, default=SAMPLE_ROWS, help="сколько строк-примеров на лист")

    p = sub.add_parser("read", help="строки таблицы")
    common(p)
    p.add_argument("--select", action="append", help="колонки через запятую")

    p = sub.add_parser("profile", help="профиль колонок")
    common(p, formats=("json", "md"), default="md", rows=False)
    p.add_argument("--stack", action="store_true", help="профиль склеенной таблицы, а не каждой отдельно")

    p = sub.add_parser("query", help="отбор и свод")
    common(p)
    p.add_argument("--where", action="append", help="условие; несколько --where — все сразу (И)")
    p.add_argument("--select", action="append", help="колонки через запятую")
    p.add_argument("--group-by", action="append", help="колонки группировки через запятую")
    p.add_argument("--agg", action="append", help="count, count:Кол, sum:Кол, avg:, min:, max:, median:, nunique:")
    p.add_argument("--share", action="store_true", help="добавить долю (%) первого показателя")
    p.add_argument("--sort", action="append", help="колонка[:desc]")

    p = sub.add_parser("combine", help="склейка таблиц")
    common(p)

    p = sub.add_parser("diff", help="сверка двух таблиц по ключу")
    common(p, formats=("json", "md"), default="md")
    p.add_argument("--key", action="append", required=True, help="ключевые колонки через запятую")
    p.add_argument("--columns", action="append", help="сравнивать только эти колонки")
    p.add_argument("--ignore-case", action="store_true", help="не различать регистр в тексте")

    p = sub.add_parser("join", help="подтянуть колонки второй таблицы")
    common(p)
    p.add_argument("--key", action="append", required=True, help="ключ в первой таблице")
    p.add_argument("--right-key", action="append", help="ключ во второй таблице, если называется иначе")
    p.add_argument("--columns", action="append", help="какие колонки второй таблицы взять")
    p.add_argument("--how", choices=("left", "inner"), default="left")
    p.add_argument("--ignore-case", action="store_true")
    return parser


COMMANDS = {"inspect": cmd_inspect, "read": cmd_read, "profile": cmd_profile, "query": cmd_query,
            "combine": cmd_combine, "diff": cmd_diff, "join": cmd_join}


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    try:
        text = COMMANDS[args.command](args, Loader(args))
    except InputError as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return EXIT_INPUT
    except RuntimeError as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return EXIT_ERROR
    emit(text, args.out)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001
        print(f"Непредвиденная ошибка: {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(EXIT_ERROR)
