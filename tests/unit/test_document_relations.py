from datetime import timedelta, timezone
from types import SimpleNamespace

import pytest

from src.mcp_server.app import build_server
from src.services import document_relations
from src.services.directum_client import DirectumError
from src.services.document_relations import DocumentRelationsService, RelationsUnavailable
from tests.unit.mcp_fakes import FakeProvider, call_tool, error_text, payload

TZ = timezone(timedelta(hours=4))
TYPES = [
    {"Name": "Simple relation", "Description": "Связанный", "SourceTitle": "Прочие", "TargetTitle": "Прочие"},
    {"Name": "Addendum", "Description": "Приложение", "SourceTitle": "Документ", "TargetTitle": "Приложения"},
    {"Name": "ConclusionsAndReports", "Description": "Заключения и отчеты", "SourceTitle": "Заключения и отчеты",
     "TargetTitle": "Заключения и отчеты"},
    {"Name": "LegalAct", "Description": "Проект НПА", "SourceTitle": "Нормативный правовой акт", "TargetTitle": "Проект НПА"},
]


def rel(doc, related, name, group, direction="From", **extra):
    return {"DocumentId": doc, "RelationName": name, "GroupTitle": group, "Direction": direction, "RelatedId": related,
            "RelatedName": f"Документ {related}", "RelatedKind": "Простой документ", "RelatedTypeGuid": "g",
            "RegistrationNumber": None, "RegistrationDate": None, "Created": "2026-10-14T14:20:06+04:00",
            "HasVersions": True, **extra}


GRAPH = {
    16789: [rel(16789, 16805, "Simple relation", "Прочие"), rel(16789, 16809, "ConclusionsAndReports", "Заключения и отчеты"),
            rel(16789, 16806, "ConclusionsAndReports", "Заключения и отчеты"),
            rel(16789, 16808, "LegalAct", "Нормативный правовой акт", "To", RegistrationNumber="001/26")],
    16808: [rel(16808, 16789, "LegalAct", "Проект НПА"), rel(16808, 16816, "Cancel", "Отменен на основании")],
    16809: [rel(16809, 16789, "ConclusionsAndReports", "Заключения и отчеты", "To")],
    16805: [rel(16805, 16789, "Simple relation", "Прочие", "To")],
    16806: [rel(16806, 16789, "ConclusionsAndReports", "Заключения и отчеты", "To")],
}


class Client:
    base_url = "https://rx"

    def __init__(self, missing=(), unavailable=False):
        self.calls = []
        self.missing = set(missing)
        self.unavailable = unavailable

    def call_function(self, path, params=None):
        self.calls.append((path, dict(params or {})))
        if self.unavailable:
            raise DirectumError("Directum OData request failed with status 404", 404)
        method = path.split("/")[1]
        if method == "GetRelationTypes":
            return TYPES
        names = [n for n in params["relationNames"].split(",") if n]
        if method == "GetDocumentRelations":
            ids = [params["documentId"]]
        else:
            ids = [int(i) for i in params["documentIds"].split(",")]
        for doc_id in ids:
            if doc_id in self.missing:
                raise DirectumError(f"Directum OData request failed with status 400: Не найден документ с ИД = {doc_id}", 400)
        rows = [r for doc_id in ids for r in GRAPH.get(doc_id, [])
                if (not names or r["RelationName"] in names)
                and params["direction"] in ("Both", r["Direction"])]
        return rows or None  # 204 у стенда — None у клиента

    def build_document_card_url(self, doc_id):
        return f"https://rx/doc/{doc_id}"


@pytest.fixture(autouse=True)
def clear_cache():
    document_relations._types_cache.clear()


def svc(client=None):
    return DocumentRelationsService(client or Client(), tz=TZ)


def test_groups_like_panel():
    res = svc().get(16789)
    assert res["total"] == 4
    groups = {(g["group"], g["direction"]): [i["id"] for i in g["items"]] for g in res["groups"]}
    assert groups == {("Прочие", "From"): [16805], ("Заключения и отчеты", "From"): [16809, 16806],
                      ("Нормативный правовой акт", "To"): [16808]}
    item = res["groups"][0]["items"][0]
    assert item["url"] == "https://rx/doc/16805" and item["created"] == "2026-10-14" and item["has_text"] is True


def test_relation_by_group_title_and_direction():
    client = Client()
    res = svc(client).get(16789, relation="заключения и отчеты", direction="from")
    assert res["relation_filter"] == ["ConclusionsAndReports"] and res["total"] == 2
    call = client.calls[-1]
    assert call == ("DocumentProcessing/GetDocumentRelations",
                    {"documentId": 16789, "relationNames": "ConclusionsAndReports", "direction": "From"})
    assert svc(client).get(16789, relation="Проект НПА, Прочие")["relation_filter"] == ["LegalAct", "Simple relation"]


def test_unknown_relation_and_direction():
    with pytest.raises(ValueError, match="Допустимые: Simple relation"):
        svc().get(16789, relation="Родство")
    with pytest.raises(ValueError, match="direction"):
        svc().get(16789, direction="sideways")


def test_no_relations_message():
    res = svc().get(16817)
    assert res["total"] == 0 and res["groups"] == [] and res["message"] == "Связей нет."


def test_missing_document_is_404():
    with pytest.raises(DirectumError) as exc:
        svc(Client(missing={5})).get(5)
    assert exc.value.status_code == 404 and "не найден" in exc.value.safe_message


def test_second_level_skips_seen_and_missing():
    client = Client(missing={16806})
    res = svc(client).get(16789, depth=2)
    level = res["second_level"]
    assert level["documents"] == [{"document_id": 16808, "groups": [
        {"group": "Отменен на основании", "relation": "Cancel", "direction": "From",
         "items": [svc().item(GRAPH[16808][1])]}]}]
    batches = [p["documentIds"] for m, p in client.calls if m.endswith("GetDocumentsRelations")]
    assert batches == ["16805,16809,16806,16808", "16805,16809,16808"]  # ненайденный 16806 выкинут, пакет повторён


def test_types_cached():
    client = Client()
    service = svc(client)
    assert [t["name"] for t in service.relation_types()][:2] == ["Simple relation", "Addendum"]
    service.relation_types()
    assert sum(1 for m, _ in client.calls if m.endswith("GetRelationTypes")) == 1


def test_unavailable_on_old_stand():
    with pytest.raises(RelationsUnavailable):
        svc(Client(unavailable=True)).get_rows(1)


def test_tools():
    seen = {}

    def get(document_id, **kw):
        seen.update(kw, document_id=document_id)
        return {"total": 0}

    relations = SimpleNamespace(get=get, relation_types=lambda: [{"name": "Addendum"}])
    server = build_server(FakeProvider(SimpleNamespace(document_relations=relations)))
    payload(call_tool(server, "get_document_relations", {"document_id": 16789, "relation": "Приложения", "depth": 2}))
    assert seen == {"document_id": 16789, "relation": "Приложения", "direction": "both", "depth": 2}
    assert payload(call_tool(server, "list_relation_types", {}))["items"] == [{"name": "Addendum"}]

    def bad(document_id, **kw):
        raise ValueError("Неизвестный тип связи «Родство»")

    server = build_server(FakeProvider(SimpleNamespace(document_relations=SimpleNamespace(get=bad))))
    assert "Родство" in error_text(call_tool(server, "get_document_relations", {"document_id": 1, "relation": "Родство"}))
