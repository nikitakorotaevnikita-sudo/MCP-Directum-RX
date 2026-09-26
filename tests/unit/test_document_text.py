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

    def get_one(self, entity_path):
        self.paths.append(entity_path)
        if entity_path.endswith("PublicBody") and self.public_error:
            raise DirectumError("no public body", 404)
        return self.bodies.get(entity_path, {"Value": None})

    def build_document_card_url(self, document_id):
        return f"https://rx.example/doc/{document_id}"


def version(version_id, number, extension):
    return {"Id": version_id, "Number": number, "AssociatedApplication": {"Extension": extension}}


def body_path(version_id, prop="Body"):
    return f"IOfficialDocuments(42)/Versions({version_id})/{prop}"


def test_reads_latest_docx_version():
    client = FakeClient(
        versions=[version(1, 1, "docx"), version(3, 3, "docx"), version(2, 2, "docx")],
        bodies={body_path(3): {"Value": b64(make_docx("Прошу отремонтировать дорогу.", "Срок — 30 дней."))}},
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
    client = FakeClient(versions=[version(5, 1, "PDF")], bodies={body_path(5): {"Value": b64(make_text_pdf("Road repair request"))}})

    result = DocumentTextService(client).get_text(42)

    assert "Road repair request" in result.text
    assert result.extension == "pdf"


def test_scan_pdf_reports_no_text_layer():
    client = FakeClient(versions=[version(5, 1, "pdf")], bodies={body_path(5): {"Value": b64(make_blank_pdf())}})

    result = DocumentTextService(client).get_text(42)

    assert result.text == ""
    assert "скан" in result.message


@pytest.mark.parametrize("encoding", ["utf-8", "cp1251"])
def test_reads_plain_text_in_utf8_or_cp1251(encoding):
    client = FakeClient(versions=[version(5, 1, "txt")], bodies={body_path(5): {"Value": b64("Служебная записка".encode(encoding))}})

    assert DocumentTextService(client).get_text(42).text == "Служебная записка"


def test_unsupported_format_falls_back_to_public_body_pdf():
    client = FakeClient(
        versions=[version(5, 1, "odt")],
        bodies={body_path(5, "PublicBody"): {"Value": b64(make_text_pdf("Public version"))}},
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
    client = FakeClient(versions=[version(5, 1, "txt")], bodies={body_path(5): {"Value": b64(("а" * 3000).encode())}})

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
    client = FakeClient(versions=[version(5, 1, "docx")], bodies={body_path(5): {"Value": None}})

    result = DocumentTextService(client).get_text(42)

    assert result.text == ""
    assert "пуст" in result.message


def test_too_large_body_rejected():
    huge = "A" * (MAX_BODY_BYTES // 3 * 4 + 8)
    client = FakeClient(versions=[version(5, 1, "txt")], bodies={body_path(5): {"Value": huge}})

    with pytest.raises(DirectumError, match="слишком большой"):
        DocumentTextService(client).get_text(42)


def test_broken_docx_reported():
    client = FakeClient(versions=[version(5, 1, "docx")], bodies={body_path(5): {"Value": b64(b"not a zip")}})

    result = DocumentTextService(client).get_text(42)

    assert result.text == ""
    assert "Не удалось прочитать" in result.message
