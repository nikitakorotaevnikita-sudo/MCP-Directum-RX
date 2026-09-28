from datetime import datetime, timedelta, timezone

import pytest

from src.services.citizen_requests import FULL_EXPAND, MINIMAL_EXPAND, CitizenRequestService
from src.services.directum_client import DirectumError

STAND_TZ = timezone(timedelta(hours=4))
NOW = datetime(2026, 9, 26, 10, 0, tzinfo=STAND_TZ)


def request_row(**overrides):
    row = {
        "Id": 900,
        "Name": "Обращение Степановой В.Г.",
        "RegistrationNumber": "13-ОГ",
        "RegistrationDate": "2026-09-24T00:00:00+04:00",
        "RequestDate": "2026-09-23T00:00:00+04:00",
        "Deadline": "2026-10-24T00:00:00+04:00",
        "InitialDeadline": "2026-10-24T00:00:00+04:00",
        "ResponseDate": None,
        "ExecutionState": "OnExecution",
        "IsRepeated": False,
        "RequestType": "Complaint",
        "ReceiptForm": "Electronic",
        "QuestionsNames": "Ремонт дорог",
        "FullName": "Степанова Валентина Григорьевна",
        "PostalAddress": "секретный адрес",
        "Email": "secret@mail.ru",
        "Phones": "+7 900 000-00-00",
        "PIN": "1234",
        "Assignee": {"Name": "Концева Надежда Ивановна"},
        "TransferredTo": None,
        "AnswerLetter": None,
        "ProlongationDeadline": [],
        "Questions": [{"ReviewResult": "Explained", "Question": {"Name": "Ремонт дорог", "FullCode": "0003.0008.0086.0567"}}],
    }
    row.update(overrides)
    return row


class FakeClient:
    def __init__(self, rows_for=None, fail_expands=()):
        self.rows_for = rows_for or (lambda filter_: [request_row()])
        self.fail_expands = set(fail_expands)
        self.calls = []

    def query(self, entity_set, **kwargs):
        self.calls.append((entity_set, kwargs))
        if kwargs.get("expand") in self.fail_expands:
            raise DirectumError("Could not find a property", 400)
        return self.rows_for(kwargs["filter_"])

    def build_document_card_url(self, document_id):
        return f"https://rx.example/doc/{document_id}"


def service(client):
    return CitizenRequestService(client, tz=STAND_TZ, now=lambda: NOW)


def test_criterion_required():
    with pytest.raises(ValueError):
        service(FakeClient()).find()


def test_lookup_by_id_uses_requests_set_and_full_expand():
    client = FakeClient()

    service(client).find(request_id=900)

    entity_set, kwargs = client.calls[0]
    assert entity_set == "IRequests"
    assert kwargs["filter_"] == "Id eq 900"
    assert kwargs["expand"] == FULL_EXPAND
    assert "PIN" not in kwargs["select"] and "Email" not in kwargs["select"] and "Phones" not in kwargs["select"]


def test_registration_number_exact_then_contains():
    def rows_for(filter_):
        return [] if filter_.startswith("RegistrationNumber eq") else [request_row()]

    client = FakeClient(rows_for=rows_for)

    items = service(client).find(registration_number="13-ОГ")

    assert [call[1]["filter_"] for call in client.calls] == [
        "RegistrationNumber eq '13-ОГ'",
        "contains(RegistrationNumber,'13-ОГ')",
    ]
    assert len(items) == 1


def test_applicant_search_by_full_name():
    client = FakeClient()

    service(client).find(applicant="Степанова")

    assert client.calls[0][1]["filter_"] == "contains(FullName,'Степанова')"


def test_falls_back_to_minimal_expand_then_none():
    client = FakeClient(fail_expands={FULL_EXPAND, MINIMAL_EXPAND})

    items = service(client).find(request_id=900)

    assert [call[1]["expand"] for call in client.calls] == [FULL_EXPAND, MINIMAL_EXPAND, None]
    assert items[0].id == 900


def test_in_work_status_with_days_left_and_no_personal_contacts():
    item = service(FakeClient()).find(request_id=900)[0]

    assert item.answered is False
    assert item.overdue is False
    assert item.days_left == 28
    assert item.status_text == "На рассмотрении (на исполнении), до срока 24.10.2026 осталось 28 дн."
    assert item.applicant == "Степанова Валентина Григорьевна"
    assert item.assignee == "Концева Надежда Ивановна"
    assert item.questions == [
        {"question": "Ремонт дорог", "code": "0003.0008.0086.0567", "review_result": "Explained", "review_result_label": "Разъяснено"}
    ]
    assert item.url == "https://rx.example/doc/900"
    dumped = item.model_dump_json()
    for secret in ("секретный адрес", "secret@mail.ru", "+7 900", "1234"):
        assert secret not in dumped


def test_overdue_status():
    client = FakeClient(rows_for=lambda f: [request_row(Deadline="2026-09-20T00:00:00+04:00", InitialDeadline="2026-09-20T00:00:00+04:00")])

    item = service(client).find(request_id=900)[0]

    assert item.overdue is True
    assert item.days_left == -6
    assert item.status_text == "На рассмотрении (на исполнении), срок 20.09.2026 просрочен на 6 дн."


def test_prolonged_deadline():
    client = FakeClient(
        rows_for=lambda f: [
            request_row(
                Deadline="2026-11-23T00:00:00+04:00",
                ProlongationDeadline=[{"Number": 1, "NewDeadline": "2026-11-23T00:00:00+04:00", "ReasonChangeDeadline": "Запрос документов"}],
            )
        ]
    )

    item = service(client).find(request_id=900)[0]

    assert item.prolonged is True
    assert item.prolongations == [{"number": 1, "new_deadline": "2026-11-23", "reason": "Запрос документов"}]
    assert item.status_text == "На рассмотрении (на исполнении), до срока 23.11.2026 осталось 58 дн. Срок продлён."


def test_answered_status_with_answer_letter():
    client = FakeClient(
        rows_for=lambda f: [
            request_row(
                ExecutionState="Executed",
                ResponseDate="2026-09-25T00:00:00+04:00",
                AnswerLetter={"Id": 16660, "Name": "Ответное письмо №04-ОГ", "RegistrationNumber": "04-ОГ", "RegistrationDate": "2026-09-24T00:00:00+04:00"},
            )
        ]
    )

    item = service(client).find(request_id=900)[0]

    assert item.answered is True
    assert item.status_text == "Рассмотрено (исполнено), ответ от 25.09.2026."
    assert item.answer_letter == {
        "id": 16660,
        "name": "Ответное письмо №04-ОГ",
        "registration_number": "04-ОГ",
        "registration_date": "2026-09-24",
        "url": "https://rx.example/doc/16660",
    }
    assert item.days_left is None


def test_transferred_status():
    client = FakeClient(rows_for=lambda f: [request_row(TransferredTo={"Name": "Минстрой РТ"}, ExecutionState="WithoutExecut")])

    item = service(client).find(request_id=900)[0]

    assert item.status_text == "Перенаправлено: Минстрой РТ (не требует исполнения)."


def test_aborted_status_and_unknown_state_passthrough():
    aborted = service(FakeClient(rows_for=lambda f: [request_row(ExecutionState="Aborted")])).find(request_id=900)[0]
    odd = service(FakeClient(rows_for=lambda f: [request_row(ExecutionState="Strange", Deadline=None)])).find(request_id=900)[0]

    assert aborted.status_text == "Рассмотрение прекращено."
    assert odd.status_text == "На рассмотрении (Strange), срок не указан."


def test_limit_passed_as_top():
    client = FakeClient()

    service(client).find(applicant="Иванов", limit=3)

    assert client.calls[0][1]["top"] == 3
