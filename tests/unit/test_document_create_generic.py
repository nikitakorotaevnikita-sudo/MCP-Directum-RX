import base64
from datetime import date, datetime, timedelta, timezone

import pytest

from src.services import document_create as dc
from src.services.directum_client import DirectumError
from src.services.document_create import CreateError, DocumentCreateService, decode_base64
from tests.unit.test_document_create import FakeClient as BaseClient

TZ = timezone(timedelta(hours=4))
NOW = datetime(2026, 10, 14, 12, 0, tzinfo=TZ)

METADATA = """
<EntityType Name="IElectronicDocumentDto"><Property Name="Id" Type="Edm.Int64"/><Property Name="Name" Type="Edm.String"/></EntityType>
<EntityType Name="IOfficialDocumentDto" BaseType="X.IElectronicDocumentDto"><Property Name="Subject" Type="Edm.String"/>
  <Property Name="PageCount" Type="Edm.Int32"/><Property Name="RegistrationNumber" Type="Edm.String"/>
  <NavigationProperty Name="DocumentKind" Type="X.IDocumentKindDto"/></EntityType>
<EntityType Name="IMemoDto" BaseType="X.IOfficialDocumentDto"><Property Name="IsManyAddressees" Type="Edm.Boolean"/></EntityType>
<EntityType Name="IOutgoingLetterDto" BaseType="X.IOfficialDocumentDto"><Property Name="TrackNumber" Type="Edm.String"/></EntityType>
<EntitySet Name="IMemos" EntityType="X.IMemoDto"/>
<EntitySet Name="IOutgoingLetters" EntityType="X.IOutgoingLetterDto"/>
<EntitySet Name="IOfficialDocuments" EntityType="X.IOfficialDocumentDto"/>
"""


class Client(BaseClient):
    base_url = "test-generic"

    def __init__(self, **kw):
        super().__init__(**kw)
        self.metadata_calls = 0

    def get_metadata_xml(self):
        self.metadata_calls += 1
        return METADATA

    def get_one(self, path):
        if path.startswith("IElectronicDocuments("):
            doc_id = int(path.split("(")[1].split(")")[0])
            if doc_id == 404:
                raise DirectumError("Запись не существует", status_code=400)
            return {"@odata.type": "#X.TestSolution.IOutgoingLetterDto", "Id": doc_id, "Name": "Исх. письмо"}
        return super().get_one(path)

    def query(self, entity_set, **kw):
        f = kw.get("filter_", "")
        if entity_set == "IDocumentKinds":
            name = f.split("Name eq '")[1].rstrip("'")
            if name == "Нет":
                return []
            return [{"Id": 2, "Name": name}]
        if entity_set == "IDocumentTemplates":
            return [{"Id": 347, "Name": "Шаблон исходящего письма"}] if "347" in f or "исходящего" in f else []
        if entity_set == "IAssociatedApplications":
            ext = f.split("'")[1]
            return [{"Id": {"docx": 2, "pdf": 3}[ext]}] if ext in ("docx", "pdf") else []
        return super().query(entity_set, **kw)

    def post(self, path, payload):
        if path in ("IMemos", "IOrders"):
            self.posts.append((path, payload))
            return {"Id": 17000, "Name": "Служебная записка"}
        if path == "Docflow/CreateVersionFromTemplate":
            self.posts.append((path, payload))
            return {}
        return super().post(path, payload)


@pytest.fixture(autouse=True)
def clear_cache():
    dc._METADATA_CACHE.clear()
    yield
    dc._METADATA_CACHE.clear()


def svc(client=None):
    return DocumentCreateService(client or Client(), tz=TZ, grant_rights_guid="g", now=lambda: NOW)


def test_plan_memo_with_addressee_and_extra_properties():
    plan = svc().plan_document("memo", "О совещании", 53, addressee_id=63, extra_properties={"PageCount": 2},
                               body_text="# Заголовок\n- пункт", body_markdown=True)
    assert plan["entity_set"] == "IMemos" and plan["document_kind"]["name"] == "Служебная записка"
    assert plan["addressee"] == {"id": 63, "name": "Концева Н. И."} and plan["extra_properties"] == {"PageCount": 2}
    assert plan["body_markdown"] is True and plan["template"] is None


def test_create_memo_payload_and_body():
    client = Client()
    s = svc(client)
    plan = s.plan_document("memo", "О совещании", 53, addressee_id=63, extra_properties={"PageCount": 2}, body_text="Текст")
    result = s.create_document(plan, "Текст")
    path, payload = client.posts[0]
    assert path == "IMemos" and payload["Addressee"] == {"Id": 63} and payload["PageCount"] == 2
    assert payload["PreparedBy"] == {"Id": 53} and "Correspondent" not in payload
    assert result["document_id"] == 17000 and result["version_id"] == 77


def test_create_from_template():
    client = Client()
    s = svc(client)
    plan = s.plan_document("outgoing_letter", "Ответ", 53, correspondent="Аппарат", template="347")
    assert plan["template"] == {"id": 347, "name": "Шаблон исходящего письма"}
    result = s.create_document(plan)
    assert ("Docflow/CreateVersionFromTemplate", {"documentId": 16800, "templateId": 347}) in client.posts
    assert result["from_template"] == "Шаблон исходящего письма"


def test_template_failure_is_warning():
    client = Client(fail={"Docflow/CreateVersionFromTemplate": DirectumError("x failed: нельзя", status_code=400)})
    client.post = BaseClient.post.__get__(client)  # без перехвата шаблона — пусть упадёт через fail
    s = svc(client)
    result = s.create_document(s.plan_document("outgoing_letter", "Ответ", 53, correspondent="Аппарат", template="347"))
    assert any("по шаблону" in w for w in result["warnings"])


def test_plan_document_validation():
    with pytest.raises(CreateError, match="Тип документа"):
        svc().plan_document("contract", "Т", 53)
    with pytest.raises(CreateError, match="укажите вид"):
        svc().plan_document("order", "Т", 53)
    assert svc().plan_document("order", "О премии", 53, document_kind="Приказ")["entity_set"] == "IOrders"
    with pytest.raises(CreateError, match="не подходят поля: correspondent"):
        svc().plan_document("memo", "Т", 53, correspondent="Аппарат")
    with pytest.raises(CreateError, match="ведущий документ"):
        svc().plan_document("addendum", "Т", 53, document_kind="Приложение")
    with pytest.raises(CreateError, match="корреспондент"):
        svc().plan_document("outgoing_letter", "Т", 53)
    with pytest.raises(CreateError, match="либо текст"):
        svc().plan_document("memo", "Т", 53, body_text="т", template="347")
    with pytest.raises(CreateError, match="не найден"):
        svc().plan_document("memo", "Т", 53, document_kind="Нет")


def test_extra_properties_rules():
    s = svc()
    with pytest.raises(CreateError, match="задавать нельзя"):
        s.plan_document("memo", "Т", 53, extra_properties={"RegistrationNumber": "1"})
    with pytest.raises(CreateError, match="нет простого свойства"):
        s.plan_document("memo", "Т", 53, extra_properties={"DocumentKind": "x"})
    with pytest.raises(CreateError, match="допустимы"):
        s.plan_document("memo", "Т", 53, extra_properties={"PageCount": [1]})
    assert s.plan_document("memo", "Т", 53, extra_properties={"IsManyAddressees": True})["extra_properties"] == {"IsManyAddressees": True}


def test_metadata_index_cached_and_type_mapping():
    client = Client()
    s = svc(client)
    assert s.document_entity_set(16800) == ("IOutgoingLetters", "Исх. письмо")
    s.document_entity_set(16800)
    assert client.metadata_calls == 1
    with pytest.raises(CreateError, match="не найден"):
        s.document_entity_set(404)


def test_body_from_text_and_file():
    client = Client()
    s = svc(client)
    plan = s.plan_body(16800, body_text="# Новый текст", markdown=True)
    assert plan["entity_set"] == "IOutgoingLetters" and plan["source"] == "markdown" and plan["extension"] == "docx"
    result = s.create_body(plan, body_text="# Новый текст")
    assert client.posts[-1] == ("IOutgoingLetters(16800)/Versions", {"AssociatedApplication": {"Id": 2}})
    assert result == {"document_id": 16800, "version_id": 77, "url": "https://rx/doc/16800", "warnings": []}
    pdf = base64.b64encode(b"%PDF-1.4 test").decode()
    plan = s.plan_body(16800, content_base64="data:application/pdf;base64," + pdf, extension=".PDF")
    assert plan["source"] == "file" and plan["extension"] == "pdf" and plan["bytes"] == 13
    s.create_body(plan, content_base64=pdf)
    assert client.posts[-1][1] == {"AssociatedApplication": {"Id": 3}}
    assert base64.b64decode(client.patches[-1][1]["Value"]) == b"%PDF-1.4 test"


def test_body_validation():
    s = svc()
    with pytest.raises(CreateError, match="Нет текста"):
        s.plan_body(16800, body_text="  ")
    with pytest.raises(CreateError, match="не похоже"):
        s.plan_body(16800, content_base64=base64.b64encode(b"hello").decode(), extension="pdf")
    with pytest.raises(CreateError, match="расширение"):
        s.plan_body(16800, content_base64=base64.b64encode(b"hello").decode(), extension="")
    with pytest.raises(CreateError, match="не base64"):
        s.plan_body(16800, content_base64="%%%", extension="txt")
    plan = s.plan_body(16800, content_base64=base64.b64encode(b"hello").decode(), extension="exe")
    with pytest.raises(CreateError, match="нет приложения"):
        s.create_body(plan, content_base64=base64.b64encode(b"hello").decode())
    with pytest.raises(CreateError, match="Пустое"):
        decode_base64("  ")


def test_action_items_batch():
    client = Client()
    s = svc(client)
    items = [{"assignee_id": 53, "deadline": date(2026, 10, 30), "text": "Подготовить доклад"},
             {"assignee_id": 66, "deadline": date(2026, 10, 25), "text": "Подготовить справку", "under_control": True,
              "supervisor_id": 63}]
    plan = s.plan_action_items(500, 63, items)
    assert plan["document"]["id"] == 500 and plan["assigned_by"]["id"] == 63 and len(plan["items"]) == 2
    assert plan["items"][1]["supervisor"] == {"id": 63, "name": "Концева Н. И."}
    result = s.create_action_items(plan)
    assert result["created"] == 2 and result["started"] == 2 and result["failed"] == 0 and result["task_ids"] == [9001, 9001]
    created = [p for p, _ in client.posts if p == "IActionItemExecutionTasks"]
    assert len(created) == 2


def test_action_items_batch_validation_and_partial_failure():
    s = svc()
    with pytest.raises(CreateError, match="Нет ни одного"):
        s.plan_action_items(500, 63, [])
    with pytest.raises(CreateError, match="Не больше 20"):
        s.plan_action_items(500, 63, [{"assignee_id": 53, "deadline": date(2026, 10, 30), "text": "т"}] * 21)
    with pytest.raises(CreateError, match="Поручение 2: .*закрыт"):
        s.plan_action_items(500, 63, [{"assignee_id": 53, "deadline": date(2026, 10, 30), "text": "т"},
                                      {"assignee_id": 70, "deadline": date(2026, 10, 30), "text": "т"}])

    class Flaky(Client):
        n = 0

        def post(self, path, payload):
            if path == "IActionItemExecutionTasks":
                Flaky.n += 1
                if Flaky.n == 2:
                    raise DirectumError("failed with 400. Response detail: Срок раньше даты", status_code=400)
            return super().post(path, payload)

    s = svc(Flaky())
    plan = s.plan_action_items(500, 63, [{"assignee_id": 53, "deadline": date(2026, 10, 30), "text": "а"},
                                         {"assignee_id": 66, "deadline": date(2026, 10, 30), "text": "б"}])
    result = s.create_action_items(plan)
    assert result["created"] == 1 and result["failed"] == 1
    assert result["items"][1]["error"] == "RX не создал поручение: Срок раньше даты."
    assert "Поручение 2 (Орлова Е. В.): не создано — RX не создал поручение: Срок раньше даты." in result["warnings"]
