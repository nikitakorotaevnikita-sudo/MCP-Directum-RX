import base64
import binascii
import io
import re
import zipfile
from typing import Any
from xml.etree import ElementTree

from src.models.schemas import DocumentText
from src.services.directum_client import DirectumError

MAX_BODY_BYTES = 20 * 1024 * 1024
DEFAULT_MAX_CHARS = 20000
TEXT_EXTENSIONS = {"txt", "csv", "md", "xml", "html", "htm", "json"}
SUPPORTED_EXTENSIONS = {"docx", "pdf"} | TEXT_EXTENSIONS
WORD_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
VERSIONS_EXPAND = "Versions($select=Id,Number;$expand=AssociatedApplication($select=Extension))"


class _Unreadable(Exception):
    """Файл есть, но прочитать его не удалось (битый архив, повреждённый PDF)."""


def _docx_text(data: bytes) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            root = ElementTree.fromstring(archive.read("word/document.xml"))
    except (zipfile.BadZipFile, KeyError, ElementTree.ParseError) as exc:
        raise _Unreadable from exc
    paragraphs = []
    for paragraph in root.iter(f"{WORD_NS}p"):
        parts = []
        for node in paragraph.iter():
            if node.tag == f"{WORD_NS}t" and node.text:
                parts.append(node.text)
            elif node.tag == f"{WORD_NS}tab":
                parts.append("\t")
            elif node.tag in (f"{WORD_NS}br", f"{WORD_NS}cr"):
                parts.append("\n")
        paragraphs.append("".join(parts))
    return "\n".join(paragraphs)


def _pdf_text(data: bytes) -> str:
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(io.BytesIO(data))
        return "\n\n".join((page.extract_text() or "") for page in reader.pages)
    except (PdfReadError, ValueError, KeyError) as exc:
        raise _Unreadable from exc


def _plain_text(data: bytes) -> str:
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1251", errors="replace")


def _extract(data: bytes, extension: str) -> str:
    if extension == "docx":
        text = _docx_text(data)
    elif extension == "pdf":
        text = _pdf_text(data)
    else:
        text = _plain_text(data)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


class DocumentTextService:
    """Текст последней версии документа для агента: docx, pdf, текстовые форматы, иначе — PDF-представление."""

    def __init__(self, client: Any):
        self.client = client

    def get_text(self, document_id: int, max_chars: int = DEFAULT_MAX_CHARS) -> DocumentText:
        rows = self.client.query(
            "IOfficialDocuments", filter_=f"Id eq {int(document_id)}", select="Id,Name", expand=VERSIONS_EXPAND, top=1
        )
        if not rows:
            raise DirectumError(f"Документ {document_id} не найден или у вас нет к нему доступа.", 404)
        document = rows[0]
        result = DocumentText(
            document_id=int(document_id),
            name=document.get("Name") or "",
            url=self.client.build_document_card_url(document_id) if hasattr(self.client, "build_document_card_url") else None,
        )
        versions = document.get("Versions") or []
        if not versions:
            result.message = "У документа нет файла (версий)."
            return result
        latest = max(versions, key=lambda item: item.get("Number") or 0)
        result.version = latest.get("Number")
        extension = ((latest.get("AssociatedApplication") or {}).get("Extension") or "").lower().lstrip(".")
        version_id = int(latest["Id"])

        notes = []
        if extension in SUPPORTED_EXTENSIONS:
            data = self._body(document_id, version_id, "Body")
        else:
            data = self._public_body(document_id, version_id)
            if data is None:
                result.extension = extension or None
                result.message = f"Формат «{extension or 'без расширения'}» не поддерживается, PDF-представления нет."
                return result
            notes.append(f"Исходный формат «{extension}» не поддерживается, текст взят из PDF-представления.")
            extension = "pdf"
        result.extension = extension
        if data is None:
            result.message = "Файл версии пуст."
            return result
        try:
            text = _extract(data, extension)
        except _Unreadable:
            result.message = "Не удалось прочитать файл: он повреждён или в неожиданном формате."
            return result
        if not text and extension == "pdf":
            notes.append("Похоже на скан без текстового слоя: текст извлечь нельзя.")
        result.chars_total = len(text)
        result.truncated = len(text) > max_chars
        result.text = text[:max_chars]
        result.message = " ".join(notes)
        return result

    def _public_body(self, document_id: int, version_id: int) -> bytes | None:
        try:
            return self._body(document_id, version_id, "PublicBody")
        except DirectumError as exc:
            if exc.status_code == 413:
                raise
            return None

    def _body(self, document_id: int, version_id: int, prop: str) -> bytes | None:
        payload = self.client.get_one(f"IOfficialDocuments({int(document_id)})/Versions({version_id})/{prop}")
        value = payload.get("Value") if isinstance(payload, dict) else None
        if not value:
            return None
        if len(value) * 3 // 4 > MAX_BODY_BYTES:
            raise DirectumError("Файл слишком большой для чтения в чате (больше 20 МБ).", 413)
        try:
            return base64.b64decode(value)
        except (binascii.Error, ValueError) as exc:
            raise DirectumError("Directum вернул тело версии в неожиданном формате.") from exc
