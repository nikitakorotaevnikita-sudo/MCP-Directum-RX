import base64
import io
import zipfile

import pytest
from pypdf import PdfWriter

from src.services.directum_client import DirectumError
from src.services.document_text import MAX_BODY_BYTES, DocumentTextService


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def make_docx(*paragraphs: str) -> bytes:
    body = "".join(f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>" for text in paragraphs)
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body}</w:body></w:document>"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", xml)
    return buffer.getvalue()


def make_text_pdf(text: str) -> bytes:
    """Минимальный PDF с одной строкой текста (Helvetica, латиница)."""
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(b"%d 0 obj\n" % number + body + b"\nendobj\n")
    xref = out.tell()
    out.write(b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1))
    for offset in offsets:
        out.write(b"%010d 00000 n \n" % offset)
    out.write(b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref))
    return out.getvalue()


def make_blank_pdf() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


class FakeClient:
    def __init__(self, versions=None, bodies=None, found=True, public_error=False):
        self.versions = versions if versions is not None else []
        self.bodies = bodies or {}
        self.found = found
        self.public_error = public_error
        self.queries = []
        self.paths = []

    def query(self, entity_set, **kwargs):
        self.queries.append((entity_set, kwargs))
        if not self.found:
            return []
        return [{"Id": 42, "Name": "Письмо о ремонте", "Versions": self.versions}]

    def get_binary(self, entity_path, max_bytes):
        self.paths.append(entity_path)
        self.max_bytes = max_bytes
        if entity_path.endswith("PublicBody/$value") and self.public_error:
            return None
        body = self.bodies.get(entity_path)
        if isinstance(body, DirectumError):
            raise body
        return body

    def build_document_card_url(self, document_id):
        return f"https://rx.example/doc/{document_id}"


def version(version_id, number, extension):
    return {"Id": version_id, "Number": number, "AssociatedApplication": {"Extension": extension}}


def body_path(version_id, prop="Body"):
    return f"IOfficialDocuments(42)/Versions({version_id})/{prop}/$value"


def test_reads_latest_docx_version():
    client = FakeClient(
        versions=[version(1, 1, "docx"), version(3, 3, "docx"), version(2, 2, "docx")],
        bodies={body_path(3): make_docx("Прошу отремонтировать дорогу.", "Срок — 30 дней.")},
    )

    result = DocumentTextService(client).get_text(42)

    entity_set, kwargs = client.queries[0]
    assert entity_set == "IOfficialDocuments"
    assert kwargs["filter_"] == "Id eq 42"
    assert "Versions(" in kwargs["expand"] and "AssociatedApplication" in kwargs["expand"]
    assert client.paths == [body_path(3)]
    assert result.text == "Прошу отремонтировать дорогу.\nСрок — 30 дней."
    assert result.version == 3
    assert result.extension == "docx"
    assert result.name == "Письмо о ремонте"
    assert result.url == "https://rx.example/doc/42"
    assert result.truncated is False
    assert result.chars_total == len(result.text)


def test_reads_pdf_text():
    client = FakeClient(versions=[version(5, 1, "PDF")], bodies={body_path(5): make_text_pdf("Road repair request")})

    result = DocumentTextService(client).get_text(42)

    assert "Road repair request" in result.text
    assert result.extension == "pdf"


def test_scan_pdf_reports_no_text_layer():
    client = FakeClient(versions=[version(5, 1, "pdf")], bodies={body_path(5): make_blank_pdf()})

    result = DocumentTextService(client).get_text(42)

    assert result.text == ""
    assert "скан" in result.message


@pytest.mark.parametrize("encoding", ["utf-8", "cp1251"])
def test_reads_plain_text_in_utf8_or_cp1251(encoding):
    client = FakeClient(versions=[version(5, 1, "txt")], bodies={body_path(5): "Служебная записка".encode(encoding)})

    assert DocumentTextService(client).get_text(42).text == "Служебная записка"


def test_unsupported_format_falls_back_to_public_body_pdf():
    client = FakeClient(
        versions=[version(5, 1, "odt")],
        bodies={body_path(5, "PublicBody"): make_text_pdf("Public version")},
    )

    result = DocumentTextService(client).get_text(42)

    assert client.paths == [body_path(5, "PublicBody")]
    assert "Public version" in result.text
    assert result.extension == "pdf"
    assert "PDF-представлени" in result.message


def test_unsupported_format_without_public_body():
    client = FakeClient(versions=[version(5, 1, "odt")], public_error=True)

    result = DocumentTextService(client).get_text(42)

    assert result.text == ""
    assert "не поддерживается" in result.message


def test_truncates_to_max_chars():
    client = FakeClient(versions=[version(5, 1, "txt")], bodies={body_path(5): ("а" * 3000).encode()})

    result = DocumentTextService(client).get_text(42, max_chars=1000)

    assert len(result.text) == 1000
    assert result.chars_total == 3000
    assert result.truncated is True


def test_document_without_versions():
    result = DocumentTextService(FakeClient(versions=[])).get_text(42)

    assert result.text == ""
    assert "нет файла" in result.message


def test_document_not_found_raises():
    with pytest.raises(DirectumError, match="не найден"):
        DocumentTextService(FakeClient(found=False)).get_text(42)


def test_empty_body_reported():
    client = FakeClient(versions=[version(5, 1, "docx")], bodies={body_path(5): None})

    result = DocumentTextService(client).get_text(42)

    assert result.text == ""
    assert "пуст" in result.message


def test_body_read_with_size_limit_and_limit_error_propagates():
    too_big = DirectumError("Файл слишком большой для чтения в чате (больше 20 МБ).", 413)
    client = FakeClient(versions=[version(5, 1, "txt")], bodies={body_path(5): too_big})

    with pytest.raises(DirectumError, match="слишком большой"):
        DocumentTextService(client).get_text(42)
    assert client.max_bytes == MAX_BODY_BYTES


def test_broken_docx_reported():
    client = FakeClient(versions=[version(5, 1, "docx")], bodies={body_path(5): b"not a zip"})

    result = DocumentTextService(client).get_text(42)

    assert result.text == ""
    assert "Не удалось прочитать" in result.message


def test_storage_failure_falls_back_to_public_body():
    client = FakeClient(
        versions=[version(5, 1, "docx")],
        bodies={
            body_path(5): DirectumError("Directum OData request failed with status 500", 500),
            body_path(5, "PublicBody"): make_text_pdf("Public copy"),
        },
    )

    result = DocumentTextService(client).get_text(42)

    assert "Public copy" in result.text
    assert "PDF-представлени" in result.message


def test_storage_failure_without_public_body_is_explained_not_raised():
    client = FakeClient(
        versions=[version(5, 1, "pdf")],
        bodies={body_path(5): DirectumError("Directum OData request failed with status 500", 500)},
        public_error=True,
    )

    result = DocumentTextService(client).get_text(42)

    assert result.text == ""
    assert "хранилищ" in result.message


def test_docx_labelled_as_pdf_is_read_by_content():
    client = FakeClient(versions=[version(5, 1, "pdf")], bodies={body_path(5): make_docx("На самом деле это docx.")})

    result = DocumentTextService(client).get_text(42)

    assert result.text == "На самом деле это docx."
    assert result.extension == "docx"


def test_pdf_labelled_as_txt_is_read_by_content():
    client = FakeClient(versions=[version(5, 1, "txt")], bodies={body_path(5): make_text_pdf("Real pdf inside")})

    result = DocumentTextService(client).get_text(42)

    assert "Real pdf inside" in result.text
    assert result.extension == "pdf"


W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def make_docx_xml(body: str) -> bytes:
    xml = f'<?xml version="1.0" encoding="UTF-8"?><w:document {W}><w:body>{body}</w:body></w:document>'
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", xml)
    return buffer.getvalue()


def para(text, style=None, bold=False, italic=False, numbered=False):
    ppr = ""
    if style or numbered:
        ppr = "<w:pPr>" + (f'<w:pStyle w:val="{style}"/>' if style else "") + (
            '<w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr>' if numbered else "") + "</w:pPr>"
    rpr = ("<w:rPr>" + ("<w:b/>" if bold else "") + ("<w:i/>" if italic else "") + "</w:rPr>") if (bold or italic) else ""
    return f"<w:p>{ppr}<w:r>{rpr}<w:t>{text}</w:t></w:r></w:p>"


def table(*rows):
    cells = "".join("<w:tr>" + "".join(f"<w:tc>{para(c)}</w:tc>" for c in row) + "</w:tr>" for row in rows)
    return f"<w:tbl>{cells}</w:tbl>"


MARKDOWN_DOCX = make_docx_xml(
    para("Приказ", style="Heading1")
    + para("О ремонте", style="Heading2")
    + '<w:p><w:r><w:t xml:space="preserve">Срок — </w:t></w:r><w:r><w:rPr><w:b/></w:rPr><w:t>30 дней</w:t></w:r></w:p>'
    + para("курсив", italic=True)
    + para("Пункт списка", numbered=True)
    + para("Маркер", style="ListBullet")
    + '<w:p><w:r><w:rPr><w:b w:val="0"/></w:rPr><w:t>не жирный</w:t></w:r></w:p>'
    + table(["Исполнитель", "Срок"], ["Отдел ЖКХ", "01.12.2026"])
)


def test_markdown_format_keeps_headings_emphasis_lists_and_tables():
    client = FakeClient(versions=[version(3, 3, "docx")], bodies={body_path(3): MARKDOWN_DOCX})

    result = DocumentTextService(client).get_text(42, body_format="markdown")

    assert result.format == "markdown"
    assert result.text.split("\n") == [
        "# Приказ",
        "## О ремонте",
        "Срок — **30 дней**",
        "*курсив*",
        "- Пункт списка",
        "- Маркер",
        "не жирный",
        "",
        "| Исполнитель | Срок |",
        "| --- | --- |",
        "| Отдел ЖКХ | 01.12.2026 |",
    ]


def test_text_format_is_default_and_unchanged():
    client = FakeClient(versions=[version(3, 3, "docx")], bodies={body_path(3): MARKDOWN_DOCX})

    result = DocumentTextService(client).get_text(42)

    assert result.format == "text"
    assert "**" not in result.text and "# Приказ" not in result.text
    assert "Приказ" in result.text and "Отдел ЖКХ" in result.text


def test_markdown_for_pdf_returns_plain_text_with_note():
    client = FakeClient(versions=[version(3, 3, "pdf")], bodies={body_path(3): make_text_pdf("Hello PDF")})

    result = DocumentTextService(client).get_text(42, body_format="markdown")

    assert "Hello PDF" in result.text
    assert result.format == "text"
    assert "PDF" in result.message and "разметк" in result.message


def test_markdown_resolves_localized_heading_style_ids_via_styles_xml():
    styles = (
        f'<?xml version="1.0" encoding="UTF-8"?><w:styles {W}>'
        '<w:style w:type="paragraph" w:styleId="1"><w:name w:val="heading 1"/></w:style>'
        '<w:style w:type="paragraph" w:styleId="a3"><w:name w:val="List Bullet"/></w:style>'
        "</w:styles>"
    )
    xml = f'<?xml version="1.0" encoding="UTF-8"?><w:document {W}><w:body>{para("Раздел", style="1")}{para("Пункт", style="a3")}</w:body></w:document>'
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", xml)
        archive.writestr("word/styles.xml", styles)
    client = FakeClient(versions=[version(3, 3, "docx")], bodies={body_path(3): buffer.getvalue()})

    result = DocumentTextService(client).get_text(42, body_format="markdown")

    assert result.text == "# Раздел\n- Пункт"


def test_markdown_turns_typed_bullets_into_list_items():
    client = FakeClient(versions=[version(3, 3, "docx")],
                        bodies={body_path(3): make_docx_xml(para("• первый") + para("· второй") + para("–третий"))})

    result = DocumentTextService(client).get_text(42, body_format="markdown")

    assert result.text == "- первый\n- второй\n–третий"
