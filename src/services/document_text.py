import base64
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
# Файл в base64 уходит в контекст модели: по умолчанию 1 МБ, максимум 5 МБ.
DEFAULT_BODY_BASE64_BYTES = 1024 * 1024
MAX_BODY_BASE64_BYTES = 5 * 1024 * 1024
# Вид сущности → (набор для проверки, набор для ссылки на карточку).
LINK_KINDS = {
    "document": ("IOfficialDocuments", None),
    "task": ("ITasks", "ISimpleTasks"),
    "action_item": ("IActionItemExecutionTasks", "IActionItemExecutionTasks"),
    "assignment": ("IAssignments", "IAssignments"),
    "meeting": ("IMeetings", "IMeetings"),
}
LINK_LABELS = {"document": "Документ", "task": "Задача", "action_item": "Поручение", "assignment": "Задание",
               "meeting": "Совещание"}


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


def _sniff(data: bytes) -> str | None:
    if data.startswith(b"%PDF"):
        return "pdf"
    if data.startswith(b"PK\x03\x04"):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                if "word/document.xml" in archive.namelist():
                    return "docx"
        except zipfile.BadZipFile:
            return None
    return None


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
        storage_failed = False
        data = None
        if extension in SUPPORTED_EXTENSIONS:
            try:
                data = self._body(document_id, version_id, "Body")
            except DirectumError as exc:
                if exc.status_code == 413:
                    raise
                # RX не смог отдать файл из хранилища (на стенде бывают версии с потерянным телом).
                storage_failed = True
        if extension not in SUPPORTED_EXTENSIONS or storage_failed:
            data = self._public_body(document_id, version_id)
            if data is None:
                result.extension = extension or None
                result.message = (
                    "Файл версии не удалось получить из хранилища RX, PDF-представления нет."
                    if storage_failed
                    else f"Формат «{extension or 'без расширения'}» не поддерживается, PDF-представления нет."
                )
                return result
            notes.append(
                "Исходный файл недоступен в хранилище RX, текст взят из PDF-представления."
                if storage_failed
                else f"Исходный формат «{extension}» не поддерживается, текст взят из PDF-представления."
            )
            extension = "pdf"
        if data is None:
            result.extension = extension
            result.message = "Файл версии пуст."
            return result
        # Расширение в RX бывает неверным (docx с пометкой pdf): формат определяем по содержимому.
        extension = _sniff(data) or extension
        result.extension = extension
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

    def get_body(self, document_id: int, max_bytes: int = DEFAULT_BODY_BASE64_BYTES) -> dict[str, Any]:
        """Файл последней версии целиком (base64) — чтобы передать его дальше, например в create_document_body_base64."""
        result, data = self.read_body(document_id)
        if data is None:
            return result
        if len(data) > max_bytes:
            raise DirectumError(
                f"Файл {len(data) // 1024} КБ больше лимита {max_bytes // 1024} КБ. Увеличьте max_bytes (до "
                f"{MAX_BODY_BASE64_BYTES // 1024} КБ) или прочитайте текст через get_document_text.", 413)
        return {**result, "bytes": len(data), "content_base64": base64.b64encode(data).decode("ascii")}

    def read_body(self, document_id: int) -> tuple[dict[str, Any], bytes | None]:
        """Сведения о последней версии и её файл (до 20 МБ). Нет файла — (сведения с message, None)."""
        rows = self.client.query(
            "IOfficialDocuments", filter_=f"Id eq {int(document_id)}", select="Id,Name", expand=VERSIONS_EXPAND, top=1
        )
        if not rows:
            raise DirectumError(f"Документ {document_id} не найден или у вас нет к нему доступа.", 404)
        versions = rows[0].get("Versions") or []
        result: dict[str, Any] = {"document_id": int(document_id), "name": rows[0].get("Name") or "",
                                  "url": self._url(document_id)}
        if not versions:
            return {**result, "message": "У документа нет файла (версий)."}, None
        latest = max(versions, key=lambda item: item.get("Number") or 0)
        extension = ((latest.get("AssociatedApplication") or {}).get("Extension") or "").lower().lstrip(".")
        try:
            data = self._body(document_id, int(latest["Id"]), "Body")
        except DirectumError as exc:
            if exc.status_code == 413:
                raise DirectumError(f"Файл больше {MAX_BODY_BYTES // (1024 * 1024)} МБ — не передаётся. "
                                    "Текст — get_document_text.", 413) from None
            raise
        if data is None:
            return {**result, "version": latest.get("Number"), "message": "Файл версии не удалось получить из хранилища RX."}, None
        return {**result, "version": latest.get("Number"), "extension": _sniff(data) or extension or None}, data

    def entity_link(self, kind: str, entity_id: int) -> dict[str, Any]:
        """Ссылка на карточку в веб-клиенте RX: документ, задача, задание или совещание (с проверкой, что есть)."""
        if kind not in LINK_KINDS:
            raise ValueError(f"Unknown entity kind: {kind}")
        entity_set, card_set = LINK_KINDS[kind]
        name_field = "Name" if kind in ("document", "meeting") else "Subject"
        rows = self.client.query(entity_set, filter_=f"Id eq {int(entity_id)}", select=f"Id,{name_field}", top=1)
        if not rows:
            raise DirectumError(f"{LINK_LABELS[kind]} с id {entity_id} не найден(о) или нет доступа.", 404)
        url = (self._url(entity_id) if kind == "document"
               else self.client.build_client_card_url(f"{card_set}({int(entity_id)})"))
        return {"kind": kind, "id": int(entity_id), "title": rows[0].get(name_field) or "", "url": url}

    def template_text(self, query: str | None = None, max_chars: int = DEFAULT_MAX_CHARS) -> dict[str, Any]:
        """Шаблоны документов: без запроса или при нескольких совпадениях — список; при одном — текст шаблона."""
        text = (query or "").strip()
        if text.isdigit():
            filter_ = f"Id eq {int(text)}"
        elif text:
            filter_ = f"Status eq 'Active' and contains(Name,'{text.replace(chr(39), chr(39) * 2)}')"
        else:
            filter_ = "Status eq 'Active'"
        rows = self.client.query(
            "IDocumentTemplates", filter_=filter_, select="Id,Name",
            expand="DocumentKinds($expand=DocumentKind($select=Name))," + VERSIONS_EXPAND, orderby="Name asc", top=101)
        exact = [r for r in rows if text and (r.get("Name") or "").casefold() == text.casefold()]
        if exact:
            rows = exact[:1]
        if len(rows) != 1:
            return {"query": text or None, "templates": [{"id": int(r["Id"]), "name": r.get("Name")} for r in rows[:100]],
                    "truncated": len(rows) > 100,
                    "message": "Шаблон не найден." if not rows else "Выберите шаблон и запросите его по id."}
        row = rows[0]
        result: dict[str, Any] = {
            "id": int(row["Id"]), "name": row.get("Name"),
            "document_kinds": [k for k in ((d.get("DocumentKind") or {}).get("Name") for d in row.get("DocumentKinds") or []) if k],
        }
        versions = row.get("Versions") or []
        if not versions:
            return {**result, "message": "У шаблона нет файла."}
        latest = max(versions, key=lambda item: item.get("Number") or 0)
        data = self.client.get_binary(f"IDocumentTemplates({int(row['Id'])})/Versions({int(latest['Id'])})/Body/$value", MAX_BODY_BYTES)
        if not data:
            return {**result, "message": "Файл шаблона не удалось получить."}
        extension = _sniff(data) or ((latest.get("AssociatedApplication") or {}).get("Extension") or "").lower()
        try:
            body = _extract(data, extension)
        except _Unreadable:
            return {**result, "message": "Файл шаблона повреждён или в неожиданном формате."}
        markdown = "\n\n".join(line.strip() for line in body.split("\n") if line.strip())
        return {**result, "extension": extension, "chars_total": len(markdown), "truncated": len(markdown) > max_chars,
                "body_md": markdown[:max_chars],
                "message": "Поля шаблона (в угловых скобках, например <Почтовый адрес организации>) RX заполняет из "
                           "карточки, если создать документ с template (create_document)."}

    def _url(self, document_id: int) -> str | None:
        return self.client.build_document_card_url(document_id) if hasattr(self.client, "build_document_card_url") else None

    def _public_body(self, document_id: int, version_id: int) -> bytes | None:
        try:
            return self._body(document_id, version_id, "PublicBody")
        except DirectumError as exc:
            if exc.status_code == 413:
                raise
            return None

    def _body(self, document_id: int, version_id: int, prop: str) -> bytes | None:
        # Тело отдаётся потоком по …/$value (без $value стенд отвечает 404).
        return self.client.get_binary(
            f"IOfficialDocuments({int(document_id)})/Versions({version_id})/{prop}/$value", MAX_BODY_BYTES
        )
