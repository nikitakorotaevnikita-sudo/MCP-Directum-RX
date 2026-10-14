from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from src.mcp_server.app import build_server
from src.models.schemas import DocumentText
from src.services.content_search import ContentSearchService, stems
from src.services.directum_client import DirectumError
from tests.unit.mcp_fakes import FakeProvider, call_tool, error_text, payload

TZ = timezone(timedelta(hours=4))
NOW = datetime(2026, 10, 14, 12, 0, tzinfo=TZ)
QUERY = ("Обращается к вам Романова Елена Владимировна. Пожалуйста проработайте вопрос с ГИБДД о контроле над "
         "использованием не заводских глушителей на автомобилях и мотоциклах. Очень высокий уровень шума ночью.")
TEXTS = {
    1: "Жалоба. Прошу принять меры: незаводские глушители на мотоциклах и автомобилях создают сильный шум ночью, "
       "ГИБДД не реагирует. Романова Е. В.",
    2: "Приказ о проведении инвентаризации основных средств в учреждении.",
    3: "Информация о графике вывоза мусора в микрорайоне.",
    4: "",
}


class Client:
    def __init__(self):
        self.calls = []

    def query(self, entity_set, **kw):
        self.calls.append((entity_set, kw))
        if entity_set == "IQASearchAreaDocuments":
            return [{"Id": 4}, {"Id": 5}]
        if "contains(" in kw["filter_"]:
            return [{"Id": 3, "Name": "Вывоз мусора"}]
        if kw.get("orderby") == "Modified desc":
            return [{"Id": 4, "Name": "Скан"}, {"Id": 2, "Name": "Приказ"}]
        return [{"Id": 2, "Name": "Приказ"}, {"Id": 1, "Name": "Обращение"}]

    def build_document_card_url(self, doc_id):
        return f"https://rx/doc/{doc_id}"


class Texts:
    def get_text(self, doc_id, max_chars=20000):
        if doc_id == 99:
            raise DirectumError("нет", 404)
        return DocumentText(document_id=doc_id, text=TEXTS.get(doc_id, ""))


class QA:
    def __init__(self, score=0.8):
        self.score = score
        self.asked = []

    def areas(self):
        return [SimpleNamespace(id=1), SimpleNamespace(id=4)]

    def ask(self, question, area_ids=None, wait_seconds=30):
        self.asked.append(area_ids)
        return SimpleNamespace(score=self.score, sources=[SimpleNamespace(name="RAG документ", url="http://rx/Sungero?type=x&id=99")])


def svc(client=None, qa=None):
    return ContentSearchService(client or Client(), Texts(), qa, tz=TZ, now=lambda: NOW)


def test_finds_document_by_content_and_ranks():
    res = svc().search(QUERY, scan_limit=10)
    assert [i["id"] for i in res["items"]] == [1]
    top = res["items"][0]
    assert top["confidence"] in ("высокая", "средняя") and "глушит" in top["matched_terms"]
    assert "глушители" in top["snippet"] and top["url"] == "https://rx/doc/1"
    assert res["scanned"] == 4 and res["unreadable"] == 1 and res["message"] == ""
    assert res["period"] == {"from": "2026-04-17", "to": "2026-10-14"}


def test_keywords_and_surnames():
    res = svc().search(QUERY)
    assert res["keywords"][0] == "ГИБДД" and "глушит" in res["keywords"] and "мотоци" in res["keywords"]
    assert not any(k.startswith(("елена", "владим", "романо")) for k in res["keywords"])
    assert res["surnames"] == ["Романова"]


def test_candidate_queries():
    client = Client()
    svc(client).search(QUERY, entity_set="IMemos", date_from=date(2026, 1, 1), date_to=date(2026, 3, 31), scan_limit=20)
    sets = [es for es, _ in client.calls]
    assert sets == ["IMemos", "IMemos", "IMemos"]
    attr = client.calls[0][1]["filter_"]
    assert "contains(Name,'ГИБДД')" in attr and "contains(Subject,'Романо')" in attr
    assert "Created ge 2026-01-01T00:00:00+04:00" in attr and "HasVersions eq true" in attr
    assert client.calls[2][1]["orderby"] == "Modified desc"


def test_rag_sources_join_candidates():
    qa = QA()
    res = svc(qa=qa).search(QUERY)
    assert qa.asked == [[4]]  # только готовые области типа «Документы»
    assert res["scanned"] == 5  # 99 из RAG + остальные
    qa0 = QA(score=0)
    assert svc(qa=qa0).search(QUERY)["scanned"] == 4  # нулевая оценка RAG — источники не берём


def test_rag_failure_is_note():
    class Broken(QA):
        def ask(self, *a, **k):
            raise RuntimeError("RAG service request failed")

    res = svc(qa=Broken()).search(QUERY)
    assert any("RAG" in n for n in res["notes"]) and res["items"][0]["id"] == 1


def test_not_found_message():
    texts = dict(TEXTS)
    TEXTS[1] = "Совсем другой текст про бюджет."
    try:
        res = svc().search(QUERY)
    finally:
        TEXTS.update(texts)
    assert res["items"] == [] and "не найдено среди" in res["message"] and "перебором" in res["message"]


def test_short_query_rejected():
    with pytest.raises(ValueError):
        svc().search("ГИБДД")


def test_stems():
    assert stems("Пожалуйста, глушители ГИБДД ночью") == ["глушит", "гибдд", "ночью"]


def test_tool():
    seen = {}

    def search(text, **kw):
        seen.update(kw, text=text)
        return {"items": []}

    server = build_server(FakeProvider(SimpleNamespace(content_search=SimpleNamespace(search=search))))
    payload(call_tool(server, "find_documents_by_content", {"text": QUERY, "kind": "memo", "date_from": "2026-01-01", "limit": 3}))
    assert seen["entity_set"] == "IMemos" and seen["kind_label"] == "служебная записка" and seen["date_from"] == date(2026, 1, 1)
    assert seen["limit"] == 3 and seen["scan_limit"] == 40

    def short(text, **kw):
        raise ValueError("Слишком короткий образец")

    server = build_server(FakeProvider(SimpleNamespace(content_search=SimpleNamespace(search=short))))
    assert "Слишком короткий" in error_text(call_tool(server, "find_documents_by_content", {"text": "ГИБДД"}))
