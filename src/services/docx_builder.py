"""Минимальный .docx из текста (стандартная библиотека): абзацы, Times New Roman 14, поля по ГОСТ Р 7.0.97."""

import io
import re
import zipfile
from xml.sax.saxutils import escape

MAX_TEXT_CHARS = 200_000
# Управляющие символы недопустимы в XML 1.0 — убираем, чтобы Word открыл файл.
_INVALID_XML = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]")
_FIXED_DATE = (2026, 1, 1, 0, 0, 0)

CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml" '
    'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
    "</Types>"
)
RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
    'Target="word/document.xml"/></Relationships>'
)
RUN_PROPS = '<w:rPr><w:rFonts w:ascii="Times New Roman" w:hAnsi="Times New Roman" w:cs="Times New Roman"/><w:sz w:val="28"/></w:rPr>'
SECTION = (
    '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
    '<w:pgMar w:top="1134" w:right="567" w:bottom="1134" w:left="1701" w:header="709" w:footer="709" w:gutter="0"/></w:sectPr>'
)


def paragraphs(text: str) -> list[str]:
    """Абзацы: пустая строка — граница абзаца, перевод строки внутри абзаца сохраняется."""
    text = _INVALID_XML.sub("", (text or "").replace("\r\n", "\n").replace("\r", "\n"))
    return [block.strip("\n") for block in re.split(r"\n\s*\n", text) if block.strip()]


def _paragraph(block: str) -> str:
    lines = block.split("\n")
    runs = '<w:r>{props}<w:br/></w:r>'.format(props=RUN_PROPS).join(
        f'<w:r>{RUN_PROPS}<w:t xml:space="preserve">{escape(line)}</w:t></w:r>' for line in lines
    )
    return f'<w:p><w:pPr><w:spacing w:after="120"/><w:ind w:firstLine="709"/><w:jc w:val="both"/></w:pPr>{runs}</w:p>'


_BOLD = re.compile(r"\*\*(.+?)\*\*")
_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_BULLET = re.compile(r"^\s*[-*•]\s+(.*)$")
_NUMBERED = re.compile(r"^\s*(\d+[.)])\s+(.*)$")


def _runs(text: str, bold: bool = False, size: int = 28) -> str:
    """Текст с **жирными** фрагментами → набор w:r."""
    out = []
    pos = 0
    pieces = []
    for m in _BOLD.finditer(text):
        pieces.append((text[pos:m.start()], bold))
        pieces.append((m.group(1), True))
        pos = m.end()
    pieces.append((text[pos:], bold))
    for piece, is_bold in pieces:
        if not piece:
            continue
        props = RUN_PROPS.replace('<w:sz w:val="28"/>', f'<w:sz w:val="{size}"/>' + ("<w:b/>" if is_bold else ""))
        out.append(f'<w:r>{props}<w:t xml:space="preserve">{escape(piece)}</w:t></w:r>')
    return "".join(out)


def _markdown_paragraphs(text: str) -> list[str]:
    """Простой markdown: # заголовки, - / * / • списки, 1. нумерация, **жирный**. Каждая строка — абзац."""
    text = _INVALID_XML.sub("", (text or "").replace("\r\n", "\n").replace("\r", "\n"))
    out = []
    for line in text.split("\n"):
        if not line.strip():
            continue
        if m := _HEADING.match(line.strip()):
            level = len(m.group(1))
            size = 32 if level == 1 else 28
            align = "center" if level == 1 else "left"
            out.append(f'<w:p><w:pPr><w:spacing w:before="120" w:after="120"/><w:jc w:val="{align}"/></w:pPr>'
                       f'{_runs(m.group(2), bold=True, size=size)}</w:p>')
        elif m := _BULLET.match(line):
            out.append(f'<w:p><w:pPr><w:spacing w:after="60"/><w:ind w:left="709" w:hanging="283"/></w:pPr>'
                       f'{_runs("• " + m.group(1))}</w:p>')
        elif m := _NUMBERED.match(line):
            out.append(f'<w:p><w:pPr><w:spacing w:after="60"/><w:ind w:left="709" w:hanging="425"/></w:pPr>'
                       f'{_runs(m.group(1) + " " + m.group(2))}</w:p>')
        else:
            out.append(f'<w:p><w:pPr><w:spacing w:after="120"/><w:ind w:firstLine="709"/><w:jc w:val="both"/></w:pPr>'
                       f'{_runs(line.strip())}</w:p>')
    return out


def build_docx(text: str, markdown: bool = False) -> bytes:
    """markdown=False — абзацы через пустую строку; True — простой markdown (заголовки, списки, жирный)."""
    if len(text or "") > MAX_TEXT_CHARS:
        raise ValueError(f"Текст документа длиннее {MAX_TEXT_CHARS} символов")
    parts = _markdown_paragraphs(text) if markdown else [_paragraph(b) for b in paragraphs(text)]
    if not parts:
        raise ValueError("Текст документа пустой")
    body = "".join(parts)
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body}{SECTION}</w:body></w:document>"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in (("[Content_Types].xml", CONTENT_TYPES), ("_rels/.rels", RELS), ("word/document.xml", document)):
            info = zipfile.ZipInfo(name, date_time=_FIXED_DATE)
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data.encode("utf-8"))
    return buffer.getvalue()
