from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

from src.mcp_server.app import build_server
from src.models.schemas import DocumentText, EmployeeSummary
from src.services.citizen_answers import CitizenAnswersService
from src.services.directum_client import DirectumError
from tests.unit.mcp_fakes import FakeProvider, call_tool, error_text, payload

TZ = timezone(timedelta(hours=4))
NOW = datetime(2026, 10, 14, 12, 0, tzinfo=TZ)

ROWS = [
    {"Id": 16661, "Name": "Ответ", "Subject": "Ответ на обращение", "RegistrationNumber": "05-ОГ",
     "RegistrationDate": "2026-09-24T00:00:00+04:00", "Result": "ActionsTaken",
     "PreparedBy": {"Id": 63, "Name": "Концева Н. И."}, "OurSignatory": {"Name": "Иванов И. И."},
     "Requests": [
         {"Result": "ActionsTaken", "Question": {"Name": "Ремонт дорог", "FullCode": "0003.0009.0096.0684"},
          "Request": {"Id": 14, "RegistrationNumber": "14-ОГ", "RegistrationDate": "2026-09-01T00:00:00+04:00",
                      "Deadline": "2026-09-20T00:00:00+04:00", "IsRepeated": False}},
         {"Result": "Explained", "Question": {"Name": "Парковки", "FullCode": "0003.0009.0096.0690"},
          "Request": {"Id": 14, "RegistrationNumber": "14-ОГ", "RegistrationDate": "2026-09-01T00:00:00+04:00",
                      "Deadline": "2026-09-20T00:00:00+04:00", "IsRepeated": False}},
     ], "Request": None},
    {"Id": 15715, "Name": "Ответ", "RegistrationNumber": "311-ИСХ", "RegistrationDate": "2026-06-18T00:00:00+04:00",
     "Result": None, "PreparedBy": None, "OurSignatory": None, "Requests": [],
     "Request": {"Id": 15713, "RegistrationNumber": "301-ОГ", "RegistrationDate": "2026-06-01T00:00:00+04:00",
                 "Deadline": "2026-07-01T00:00:00+04:00", "IsRepeated": True,
                 "Questions": [{"ReviewResult": "Supported", "Question": {"Name": "Освещение", "FullCode": "0005.0005"}}]}},
]


class Client:
    def __init__(self):
        self.calls = []

    def query(self, entity_set, **kw):
        self.calls.append(kw)
        return ROWS[: kw["top"]]

    def count(self, entity_set, filter_=None):
        return 7

    def build_document_card_url(self, doc_id):
        return f"https://rx/doc/{doc_id}"


class Texts:
    def get_text(self, document_id, max_chars=6000):
        if document_id == 15715:
            raise DirectumError("Документ недоступен", 404)
        return DocumentText(document_id=document_id, text="Уважаемая…" * 3, truncated=False, message="")


def svc(client=None):
    return CitizenAnswersService(client or Client(), Texts(), tz=TZ, now=lambda: NOW)


def test_filter_defaults_and_paging():
    client = Client()
    res = svc(client).list_answers(limit=2, skip=0)
    kw = client.calls[0]
    assert kw["filter_"] == ("RegistrationState eq 'Registered' and DocumentKind/Name eq 'Ответное письмо' and "
                             "RegistrationDate ge 2026-10-01T00:00:00+04:00 and RegistrationDate lt 2026-10-15T00:00:00+04:00")
    assert kw["orderby"] == "RegistrationDate desc,Id desc" and kw["top"] == 2 and kw["skip"] == 0
    assert res["total"] == 7 and res["returned"] == 2 and res["next_skip"] == 2 and res["truncated"]


def test_filters_by_author_and_code():
    client = Client()
    svc(client).list_answers(date_from=date(2026, 1, 1), date_to=date(2026, 6, 30), prepared_by_id=63,
                             classifier_code="0003.0009", limit=50, skip=4)
    kw = client.calls[0]
    assert "PreparedBy/Id eq 63" in kw["filter_"] and "startswith(r/Question/FullCode,'0003.0009')" in kw["filter_"]
    assert "Request/Questions/any(q: startswith(q/Question/FullCode,'0003.0009'))" in kw["filter_"]
    assert "RegistrationDate ge 2026-01-01" in kw["filter_"] and kw["top"] == 20 and kw["skip"] == 4


def test_answer_shape_requests_and_lateness():
    res = svc().list_answers(limit=2)
    first, second = res["answers"]
    assert first["registration_number"] == "05-ОГ" and first["prepared_by"] == "Концева Н. И." and first["result_label"] == "Меры приняты"
    assert len(first["requests"]) == 1  # две строки одного обращения сведены
    req = first["requests"][0]
    assert req["days_late"] == 4 and [q["review_result_label"] for q in req["questions"]] == ["Меры приняты", "Разъяснено"]
    assert first["text"].startswith("Уважаемая") and first["url"] == "https://rx/doc/16661"
    # Запасной путь: одиночное «Обращение» с вопросами самого обращения.
    assert second["requests"][0]["registration_number"] == "301-ОГ" and second["requests"][0]["is_repeated"] is True
    assert second["requests"][0]["questions"][0]["review_result_label"] == "Поддержано"
    assert second["text"] == "" and "Текст не получен" in second["text_message"]


def test_without_text():
    res = svc().list_answers(limit=1, include_text=False)
    assert "text" not in res["answers"][0]


def test_tool():
    seen = {}

    def list_answers(**kw):
        seen.update(kw)
        return {"answers": [], "total": 0}

    services = SimpleNamespace(
        citizen_answers=SimpleNamespace(list_answers=list_answers),
        action_items=SimpleNamespace(search_employee=lambda q: [EmployeeSummary(id=63, name="Концева")], get_employee=lambda i: None),
    )
    server = build_server(FakeProvider(services))
    payload(call_tool(server, "list_citizen_request_answers", {"date_from": "2026-09-01", "prepared_by": "Концева",
                                                               "classifier_code": "0003", "limit": 3, "skip": 6}))
    assert seen == {"date_from": date(2026, 9, 1), "date_to": None, "prepared_by_id": 63, "classifier_code": "0003",
                    "include_text": True, "max_chars": 6000, "limit": 3, "skip": 6}
    assert "Код классификатора" in error_text(call_tool(server, "list_citizen_request_answers", {"classifier_code": "дороги"}))
