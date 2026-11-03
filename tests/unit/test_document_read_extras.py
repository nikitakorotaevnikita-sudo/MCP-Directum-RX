import base64

import pytest

from src.services.directum_client import DirectumError
from src.services.docx_builder import build_docx
from src.services.document_text import DocumentTextService

PDF = b"%PDF-1.4 hello"
TEMPLATE_DOCX = build_docx("Уважаемый [Адресат]!\n\nНаправляем информацию.")


class Client:
    def __init__(self, body=PDF, versions=True, templates=None):
        self.body = body
        self.versions = versions
        self.templates = templates if templates is not None else [
            {"Id": 347, "Name": "Шаблон исходящего письма", "DocumentKinds": [{"DocumentKind": {"Name": "Исходящее письмо"}}],
             "Versions": [{"Id": 1, "Number": 1, "AssociatedApplication": {"Extension": "docx"}},
                          {"Id": 5, "Number": 5, "AssociatedApplication": {"Extension": "docx"}}]},
            {"Id": 365, "Name": "Шаблон сопроводительного письма", "DocumentKinds": [], "Versions": []},
        ]
        self.binary_paths = []

    def query(self, entity_set, **kw):
        f = kw["filter_"]
        if entity_set == "IOfficialDocuments":
            if "Id eq 404" in f:
                return []
            versions = [{"Id": 9, "Number": 2, "AssociatedApplication": {"Extension": "doc"}}] if self.versions else []
            return [{"Id": 16800, "Name": "Письмо", "Versions": versions}]
        if entity_set == "IDocumentTemplates":
            if "Id eq" in f:
                return [t for t in self.templates if f"Id eq {t['Id']}" == f]
            if "contains" in f:
                word = f.split("contains(Name,'")[1].split("')")[0].lower()
                return [t for t in self.templates if word in t["Name"].lower()]
            return self.templates
        if entity_set in ("ITasks", "IActionItemExecutionTasks", "IAssignments", "IMeetings"):
            return [] if "Id eq 404" in f else [{"Id": 1, "Subject": "Тема", "Name": "Совещание"}]
        raise AssertionError(entity_set)

    def get_binary(self, path, max_bytes):
        self.binary_paths.append(path)
        if path.startswith("IDocumentTemplates"):
            return TEMPLATE_DOCX
        if self.body is None:
            return None
        if len(self.body) > max_bytes:
            raise DirectumError("too large", 413)
        return self.body

    def build_document_card_url(self, doc_id):
        return f"https://rx/doc/{doc_id}"

    def build_client_card_url(self, path):
        return f"https://rx/card/{path}"


def test_get_body_base64_sniffs_extension():
    result = DocumentTextService(Client()).get_body(16800)
    assert result["extension"] == "pdf" and result["bytes"] == len(PDF) and result["version"] == 2
    assert base64.b64decode(result["content_base64"]) == PDF


def test_get_body_limits_and_missing():
    with pytest.raises(DirectumError, match="больше лимита"):
        DocumentTextService(Client(body=b"x" * 3000)).get_body(16800, max_bytes=2000)
    assert "нет файла" in DocumentTextService(Client(versions=False)).get_body(16800)["message"]
    assert "не удалось" in DocumentTextService(Client(body=None)).get_body(16800)["message"]
    with pytest.raises(DirectumError) as exc:
        DocumentTextService(Client()).get_body(404)
    assert exc.value.status_code == 404


def test_entity_links():
    s = DocumentTextService(Client())
    assert s.entity_link("document", 16800) == {"kind": "document", "id": 16800, "title": "Письмо", "url": "https://rx/doc/16800"}
    assert s.entity_link("action_item", 955)["url"] == "https://rx/card/IActionItemExecutionTasks(955)"
    assert s.entity_link("task", 5)["url"] == "https://rx/card/ISimpleTasks(5)"
    assert s.entity_link("meeting", 7)["title"] == "Совещание"
    with pytest.raises(DirectumError, match="не найден"):
        s.entity_link("assignment", 404)
    with pytest.raises(ValueError):
        s.entity_link("user", 1)


def test_template_list_and_text():
    s = DocumentTextService(Client())
    listing = s.template_text()
    assert [t["id"] for t in listing["templates"]] == [347, 365] and "Выберите" in listing["message"]
    assert [t["id"] for t in s.template_text("письма")["templates"]] == [347, 365]
    one = s.template_text("исходящего")
    assert one["id"] == 347 and one["document_kinds"] == ["Исходящее письмо"]
    assert one["body_md"] == "Уважаемый [Адресат]!\n\nНаправляем информацию." and one["extension"] == "docx"
    assert s.template_text("Шаблон исходящего письма")["id"] == 347  # точное совпадение
    assert s.template_text("347")["id"] == 347
    assert "нет файла" in s.template_text("365")["message"]
    assert s.template_text("нет такого")["message"] == "Шаблон не найден."
    client = Client()
    DocumentTextService(client).template_text("347")
    assert client.binary_paths == ["IDocumentTemplates(347)/Versions(5)/Body/$value"]  # последняя версия


class VersionsClient:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def query(self, entity_set, **kw):
        self.calls.append((entity_set, kw))
        return self.rows

    def build_client_card_url(self, path):
        return f"https://rx/{path}"


def test_versions_lists_newest_first_with_flags():
    row = {"Id": 16800, "Name": "Письмо", "VersionsLocked": False, "LastVersionApproved": True,
           "LastVersionSignatureType": 5,
           "Versions": [
               {"Id": 1, "Number": 1, "Note": "Первоначальная версия", "Created": "2026-10-14T12:16:32+04:00",
                "IsHidden": None, "Author": {"Id": 12, "Name": "Administrator"},
                "AssociatedApplication": {"Extension": "docx"}},
               {"Id": 3, "Number": 3, "Note": None, "Created": "2026-10-15T10:00:00+04:00", "IsHidden": True,
                "Author": None, "AssociatedApplication": {"Extension": "PDF"}},
               {"Id": 2, "Number": 2, "Note": "Версия 2", "Created": "2026-10-14T14:57:39+04:00",
                "Author": {"Id": 12, "Name": "Administrator"}, "AssociatedApplication": None},
           ]}
    client = VersionsClient([row])
    result = DocumentTextService(client).versions(16800)
    entity_set, kw = client.calls[0]
    assert entity_set == "IElectronicDocuments" and kw["filter_"] == "Id eq 16800"
    assert "VersionsLocked" in kw["select"] and "Note" in kw["expand"] and "Author" in kw["expand"]
    assert result["document_id"] == 16800 and result["name"] == "Письмо"
    assert result["versions_locked"] is False and result["last_version_signed"] is True
    assert result["total"] == 3 and result["next_version_number"] == 4
    assert [v["number"] for v in result["versions"]] == [3, 2, 1]
    assert result["versions"][0] == {"number": 3, "extension": "pdf", "note": None, "author": None,
                                     "created": "2026-10-15T10:00:00+04:00", "hidden": True}
    assert result["versions"][2]["author"] == "Administrator" and result["versions"][1]["extension"] is None


def test_versions_of_missing_document_and_document_without_files():
    with pytest.raises(DirectumError, match="не найден"):
        DocumentTextService(VersionsClient([])).versions(404)
    empty = DocumentTextService(VersionsClient([{"Id": 5, "Name": "Пусто", "Versions": []}])).versions(5)
    assert empty["total"] == 0 and empty["versions"] == [] and empty["next_version_number"] == 1
    assert empty["versions_locked"] is False and empty["last_version_signed"] is False
