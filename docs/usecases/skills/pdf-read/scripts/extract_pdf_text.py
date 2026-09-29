"""Извлечение текста из PDF с текстовым слоем (без OCR).

Читает PDF, извлекает текст по страницам, определяет страницы без текстового слоя (сканы) и повреждённый слой
(шрифты без таблицы Unicode — «кракозябры»), убирает переносы слов на границе строк и лишние пробелы.

Запуск:
  python extract_pdf_text.py ФАЙЛ.pdf|- [--pages 1-3,5] [--format text|json|md] [--max-chars 200000]
                             [--password ПАРОЛЬ] [--layout] [--raw] [--no-page-headers] [--out ФАЙЛ] [--debug]
Коды выхода: 0 — успех (в том числе если текстового слоя нет — это видно в результате), 2 — ошибка входных данных
(не PDF, повреждён, неверный пароль, неверные страницы), 1 — непредвиденная ошибка или не установлен pypdf.
"""

import argparse
import io
import json
import logging
import os
import re
import sys
import tempfile
import traceback
import unicodedata
from datetime import datetime
from pathlib import Path

MAX_BYTES = 100 * 1024 * 1024
MAX_PAGES = 5000
DEFAULT_MAX_CHARS = 200_000
MIN_PAGE_CHARS = 20
GARBAGE_SHARE = 0.3
INSTALL_HINT = "не установлен pypdf: выполните «pip install -r requirements.txt» (нужен pypdf ≥ 4.0)"

HYPHEN_BREAK = re.compile(r"(\w)[-­]\n[ \t]*(?=[a-zа-яё])")
SPACES = re.compile(r"[ \t  -​  　]+")
MANY_BLANK = re.compile(r"\n{3,}")
PAGES_RE = re.compile(r"^\s*(\d+)\s*(?:-\s*(\d*)\s*)?$")


class InputError(Exception):
    """Ошибка входных данных (код выхода 2)."""


def _load_pypdf():
    try:
        import pypdf  # noqa: PLC0415
    except ImportError:
        return None
    # pypdf пишет предупреждения о мелких дефектах файла в лог — не засоряем stderr.
    logging.getLogger("pypdf").setLevel(logging.ERROR)
    return pypdf


def parse_pages(spec, total: int) -> list:
    """«1-3,5,8-» → номера страниц с 1. Пусто — все страницы."""
    if spec is None or not str(spec).strip():
        return list(range(1, total + 1))
    pages = []
    for part in str(spec).split(","):
        m = PAGES_RE.match(part)
        if not m:
            raise InputError(f"--pages: не понимаю «{part.strip()}»; пример: 1-3,5,8-")
        start = int(m.group(1))
        end = start if m.group(2) is None else (int(m.group(2)) if m.group(2) else total)
        if start < 1 or end < start:
            raise InputError(f"--pages: неверный диапазон «{part.strip()}»")
        if start > total:
            raise InputError(f"--pages: страницы {start} нет — в документе {total} стр.")
        pages.extend(range(start, min(end, total) + 1))
    seen = set()
    return [p for p in pages if not (p in seen or seen.add(p))]


def clean_text(text: str) -> str:
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    text = unicodedata.normalize("NFC", text)
    text = HYPHEN_BREAK.sub(r"\1", text)
    text = text.replace("­", "")
    lines = [SPACES.sub(" ", line).strip() for line in text.split("\n")]
    return MANY_BLANK.sub("\n\n", "\n".join(lines)).strip()


def garbage_share(text: str) -> float:
    """Доля символов, которые говорят о повреждённом текстовом слое (замены, частная область, управляющие)."""
    chars = [c for c in text if not c.isspace()]
    if not chars:
        return 0.0
    bad = 0
    for c in chars:
        cat = unicodedata.category(c)
        if c == "�" or cat in ("Co", "Cn", "Cc", "Cs"):
            bad += 1
    return bad / len(chars)


def _meta_date(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, str) and value.startswith("D:") and len(value) >= 10:
        return f"{value[2:6]}-{value[6:8]}-{value[8:10]}"
    return None


def _metadata(reader) -> dict:
    try:
        meta = reader.metadata or {}
    except Exception:  # noqa: BLE001 — битые метаданные не повод не читать текст
        return {}
    out = {}
    for key, name in (("/Title", "title"), ("/Author", "author"), ("/Subject", "subject"),
                      ("/Creator", "creator"), ("/Producer", "producer")):
        try:
            value = meta.get(key)
        except Exception:  # noqa: BLE001
            value = None
        if value is not None and str(value).strip():
            out[name] = str(value).strip()
    for key, name in (("/CreationDate", "created"), ("/ModDate", "modified")):
        try:
            value = _meta_date(meta.get(key))
        except Exception:  # noqa: BLE001
            value = None
        if value:
            out[name] = value
    return out


def open_pdf(data: bytes, password=None):
    pypdf = _load_pypdf()
    if pypdf is None:
        raise RuntimeError(INSTALL_HINT)
    if not data:
        raise InputError("файл пустой")
    if len(data) > MAX_BYTES:
        raise InputError(f"файл слишком большой: {len(data)} байт (допустимо не более {MAX_BYTES})")
    if b"%PDF-" not in data[:1024]:
        raise InputError("это не PDF (нет заголовка %PDF-); для DOCX, TXT и других форматов навык не подходит")
    try:
        reader = pypdf.PdfReader(io.BytesIO(data), strict=False)
    except Exception as exc:  # noqa: BLE001
        raise InputError(f"PDF повреждён и не читается: {exc}") from None
    encrypted = bool(reader.is_encrypted)
    if encrypted:
        try:
            ok = reader.decrypt(password or "")
        except Exception as exc:  # noqa: BLE001
            raise InputError(f"не удалось расшифровать PDF: {exc}") from None
        if not ok:
            raise InputError("PDF защищён паролем — укажите --password" if not password else "неверный пароль PDF")
    try:
        total = len(reader.pages)
    except Exception as exc:  # noqa: BLE001
        raise InputError(f"PDF повреждён: не читается список страниц ({exc})") from None
    if total == 0:
        raise InputError("в PDF нет страниц")
    if total > MAX_PAGES:
        raise InputError(f"слишком много страниц: {total} (допустимо не более {MAX_PAGES}); укажите --pages")
    return reader, encrypted, total


def extract(data: bytes, pages=None, max_chars: int = DEFAULT_MAX_CHARS, password=None, layout: bool = False,
            raw: bool = False, file_name=None) -> dict:
    reader, encrypted, total = open_pdf(data, password)
    numbers = parse_pages(pages, total)
    result_pages = []
    warnings = []
    used = 0
    truncated = False
    for number in numbers:
        if used >= max_chars:
            truncated = True
            break
        item = {"page": number, "chars": 0, "text": "", "has_text": False}
        try:
            page = reader.pages[number - 1]
            text = page.extract_text(extraction_mode="layout") if layout else page.extract_text()
        except Exception as exc:  # noqa: BLE001 — одна битая страница не должна ронять весь документ
            item["error"] = f"не удалось извлечь текст: {exc}"
            warnings.append(f"Страница {number}: {item['error']}.")
            result_pages.append(item)
            continue
        text = (text or "") if raw else clean_text(text or "")
        visible = len(re.sub(r"\s", "", text))
        item["has_text"] = visible >= MIN_PAGE_CHARS
        share = garbage_share(text)
        if visible and share >= GARBAGE_SHARE:
            item["garbled"] = True
        if used + len(text) > max_chars:
            text = text[: max_chars - used]
            truncated = True
        item["text"] = text
        item["chars"] = len(text)
        used += len(text)
        result_pages.append(item)
    without_text = [p["page"] for p in result_pages if not p["has_text"] and "error" not in p]
    garbled = [p["page"] for p in result_pages if p.get("garbled")]
    extracted = [p["page"] for p in result_pages]
    has_text_layer = any(p["has_text"] for p in result_pages)
    if not has_text_layer:
        warnings.append("Текстового слоя нет (скан или изображение) — нужно распознавание (OCR); этот навык его не "
                        "делает.")
    elif without_text:
        warnings.append(f"Без текстового слоя страниц: {len(without_text)} — возможно, сканы вставлены как "
                        "изображения; их текст не извлечён.")
    if garbled:
        warnings.append(f"Текст похож на повреждённый (шрифты без таблицы Unicode) на страницах: "
                        f"{', '.join(map(str, garbled))} — не опирайтесь на него без проверки.")
    if truncated:
        warnings.append(f"Текст обрезан по лимиту {max_chars} символов — извлечены страницы "
                        f"{extracted[0]}–{extracted[-1]}; остальное — через --pages или --max-chars.")
    return {
        "file": file_name, "pages_total": total, "pages_requested": len(numbers), "pages_extracted": extracted,
        "encrypted": encrypted, "metadata": _metadata(reader), "has_text_layer": has_text_layer,
        "pages_without_text": without_text, "pages_garbled": garbled, "total_chars": used, "truncated": truncated,
        "warnings": warnings, "pages": result_pages,
    }


def to_text(result: dict, headers: bool = True) -> str:
    parts = []
    for p in result["pages"]:
        body = p["text"] if p["text"] else ("[нет текстового слоя]" if "error" not in p else f"[{p['error']}]")
        parts.append(f"=== Страница {p['page']} ===\n{body}" if headers else body)
    out = "\n\n".join(parts)
    if result["warnings"]:
        out += "\n\n" + "\n".join(f"[!] {w}" for w in result["warnings"])
    return out.strip() + "\n"


def to_markdown(result: dict) -> str:
    meta = result["metadata"]
    title = meta.get("title") or result.get("file") or "PDF"
    out = [f"# {title}", "",
           f"Страниц в документе: {result['pages_total']}, извлечено: {len(result['pages_extracted'])}, символов: "
           f"{result['total_chars']}. Текстовый слой: {'есть' if result['has_text_layer'] else 'нет'}."]
    if meta.get("author"):
        out.append(f"Автор: {meta['author']}.")
    out.append("")
    for w in result["warnings"]:
        out += [f"> {w}", ""]
    for p in result["pages"]:
        out += [f"## Страница {p['page']}", ""]
        out.append(p["text"] if p["text"] else ("_нет текстового слоя_" if "error" not in p else f"_{p['error']}_"))
        out.append("")
    return "\n".join(out).rstrip() + "\n"


# --- ввод-вывод ---

def read_input(path: str) -> bytes:
    if path == "-":
        return sys.stdin.buffer.read(MAX_BYTES + 1)
    p = Path(path)
    if not p.is_file():
        raise InputError(f"файл не найден: {path}")
    if p.stat().st_size > MAX_BYTES:
        raise InputError(f"файл слишком большой: {p.stat().st_size} байт (допустимо не более {MAX_BYTES})")
    try:
        return p.read_bytes()
    except OSError as exc:
        raise InputError(f"не удалось прочитать файл {path}: {exc}") from None


def write_atomic(path: str, content: str) -> str:
    target = Path(path).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=".tmp-", suffix=target.suffix)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(content.encode("utf-8"))
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return str(target)


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
    parser = _Parser(description="Извлечение текста из PDF с текстовым слоем.")
    parser.add_argument("input", help="путь к PDF или «-» для чтения из stdin")
    parser.add_argument("--pages", help="страницы: 1-3,5,8- (по умолчанию все)")
    parser.add_argument("--format", choices=["text", "json", "md"], default="text", help="формат вывода")
    parser.add_argument("--max-chars", type=int, default=DEFAULT_MAX_CHARS, help="лимит символов (1000–5000000)")
    parser.add_argument("--password", help="пароль, если PDF зашифрован")
    parser.add_argument("--layout", action="store_true", help="сохранять расположение текста (таблицы, колонки)")
    parser.add_argument("--raw", action="store_true", help="не чистить текст (переносы, пробелы)")
    parser.add_argument("--no-page-headers", action="store_true", help="без строк «=== Страница N ===» (text)")
    parser.add_argument("--out", help="записать результат в файл (UTF-8) вместо stdout")
    parser.add_argument("--debug", action="store_true", help="выводить трассировку при ошибке")
    args = parser.parse_args(argv)
    try:
        if not 1000 <= args.max_chars <= 5_000_000:
            raise InputError("--max-chars: допустимо от 1000 до 5000000")
        name = None if args.input == "-" else Path(args.input).name
        result = extract(read_input(args.input), args.pages, args.max_chars, args.password, args.layout, args.raw,
                         name)
        if args.format == "json":
            out = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
        elif args.format == "md":
            out = to_markdown(result)
        else:
            out = to_text(result, headers=not args.no_page_headers)
        if args.out:
            # В stdout — только путь к файлу, поэтому предупреждения дублируем в stderr.
            _write(sys.stdout, write_atomic(args.out, out) + "\n")
            for w in result["warnings"]:
                _write(sys.stderr, f"Предупреждение: {w}\n")
        else:
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
