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


def build_docx(text: str) -> bytes:
    if len(text or "") > MAX_TEXT_CHARS:
        raise ValueError(f"Текст документа длиннее {MAX_TEXT_CHARS} символов")
    blocks = paragraphs(text)
    if not blocks:
        raise ValueError("Текст документа пустой")
    body = "".join(_paragraph(b) for b in blocks)
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
