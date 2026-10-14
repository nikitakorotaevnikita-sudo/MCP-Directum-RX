from datetime import date, datetime, timedelta, timezone

import pytest

from src.services.directum_client import DirectumError
from src.services.document_create import CreateError, DocumentCreateService, body_fingerprint

TZ = timezone(timedelta(hours=4))
NOW = datetime(2026, 10, 14, 12, 0, tzinfo=TZ)

EMPLOYEES = {
    53: {"Id": 53, "Name": "Босов А. С.", "Department": {"Id": 49, "Name": "Секретариат", "BusinessUnit": {"Id": 52, "Name": "Администрация"}}},
    63: {"Id": 63, "Name": "Концева Н. И.", "Department": {"Id": 49, "Name": "Секретариат", "BusinessUnit": {"Id": 52, "Name": "Администрация"}}},
    66: {"Id": 66, "Name": "Орлова Е. В.", "Department": None},
    70: {"Id": 70, "Name": "Александров Александр", "Status": "Closed",
         "Department": {"Id": 49, "Name": "Секретариат", "BusinessUnit": {"Id": 52, "Name": "Администрация"}}},
}


ACTION_ITEMS = {
    80872: {"Id": 80872, "Subject": ">> Поручение: Подготовить доклад", "Status": "InProcess",
            "Assignee": {"Id": 53, "Name": "Босов А. С."}, "AssignedBy": {"Id": 63, "Name": "Концева Н. И."},
            "Supervisor": {"Id": 66, "Name": "Орлова Е. В."}, "Author": {"Id": 63, "Name": "Концева Н. И."}},
    80873: {"Id": 80873, "Subject": "Поручение: Составное", "Status": "InProcess", "Assignee": None,
            "AssignedBy": {"Id": 63, "Name": "Концева Н. И."}, "Supervisor": None, "Author": None},
    955: {"Id": 955, "Subject": ">> Поручение: В работу", "Status": "InProcess",
          "Assignee": {"Id": 63, "Name": "Концева Н. И."}, "AssignedBy": {"Id": 70, "Name": "Александров Александр"},
          "Supervisor": {"Id": 70, "Name": "Александров Александр"}, "Author": {"Id": 70, "Name": "Александров Александр"}},
    956: {"Id": 956, "Subject": "Поручение: у закрытого", "Status": "InProcess",
          "Assignee": {"Id": 70, "Name": "Александров Александр"}, "AssignedBy": {"Id": 63, "Name": "Концева Н. И."},
          "Supervisor": None, "Author": None},
    80874: {"Id": 80874, "Subject": "Поручение: Готово", "Status": "Completed", "Assignee": {"Id": 53, "Name": "Б"},
            "AssignedBy": {"Id": 63, "Name": "К"}},
}


class FakeClient:
    def __init__(self, counterparties=None, fail=None):
        self.counterparties = counterparties if counterparties is not None else [{"Id": 1464, "Name": "Аппарат Правительства"}]
        self.fail = fail or {}
        self.posts, self.patches = [], []

    def get_one(self, path):
        if path.startswith("IActionItemExecutionTasks("):
            task_id = int(path.split("(")[1].split(")")[0])
            if task_id not in ACTION_ITEMS:
                raise DirectumError("Запись не существует", status_code=400)
            return ACTION_ITEMS[task_id]
        emp_id = int(path.split("(")[1].split(")")[0])
        if emp_id not in EMPLOYEES:
            raise DirectumError("Запись не существует", status_code=400)
        return EMPLOYEES[emp_id]

    def query(self, entity_set, **kw):
        f = kw.get("filter_", "")
        if entity_set == "ICounterparties":
            if f.startswith("Id eq"):
                return [c for c in self.counterparties if f"Id eq {c['Id']}" == f]
            return self.counterparties
        if entity_set == "IDocumentKinds":
            name = f.split("Name eq '")[1].rstrip("'")
            return [{"Id": {"Исходящее письмо": 34, "Проект правового акта": 114, "Правовой акт": 115}[name], "Name": name}]
        if entity_set == "IOfficialDocuments":
            return [{"Id": 500, "Name": "Вх. письмо"}] if "500" in f else []
        if entity_set == "IAssociatedApplications":
            return [{"Id": 2}]
        raise AssertionError(entity_set)

    def post(self, path, payload):
        self.posts.append((path, payload))
        if path in self.fail:
            raise self.fail[path]
        if path == "IStatusReportRequestTasks":
            return {"Id": 9100}
        if path in ("IOutgoingLetters", "ILegalActsLegalActs"):
            return {"Id": 16800, "Name": "Исх. письмо"}
        if path.endswith("/Versions"):
            return {"Id": 77}
        if path in ("RecordManagement/CreateActionItemExecution", "RecordManagement/CreateDocumentReviewTask",
                    "Docflow/CreateApprovalTask", "Docflow/CreateSimpleTask"):
            return {"value": 9001}
        return {}

    def patch(self, path, payload):
        self.patches.append((path, payload))
        if "patch" in self.fail:
            raise self.fail["patch"]

    def build_client_card_url(self, path):
        return "https://rx/card/" + path

    def build_document_card_url(self, doc_id):
        return f"https://rx/doc/{doc_id}"


def svc(client=None, guid=None):
    return DocumentCreateService(client or FakeClient(), tz=TZ, grant_rights_guid=guid, now=lambda: NOW)


def paths(client):
    return [p for p, _ in client.posts]


def test_plan_outgoing_letter():
    plan = svc().plan_outgoing_letter("  О  совещании ", "Аппарат", 53, signatory_id=63, in_response_to_id=500,
                                      body_text="Текст", note="Срочно")
    assert plan["subject"] == "О совещании" and plan["document_kind"] == {"id": 34, "name": "Исходящее письмо"}
    assert plan["correspondent"] == {"id": 1464, "name": "Аппарат Правительства"}
    assert plan["department"] == {"id": 49, "name": "Секретариат"} and plan["business_unit"]["id"] == 52
    assert plan["signatory"] == {"id": 63, "name": "Концева Н. И."} and plan["in_response_to"]["id"] == 500
    assert plan["note"] == "Срочно\nПодготовлено ИИ-ассистентом (mcpOGV) по запросу: Босов А. С., 14.10.2026."
    assert plan["body"] == body_fingerprint("Текст") and plan["body"]["chars"] == 5


def test_create_outgoing_letter_full_flow():
    client = FakeClient()
    s = svc(client, guid="g-1")
    plan = s.plan_outgoing_letter("О совещании", "1464", 53, body_text="Абзац 1\n\nАбзац 2")
    result = s.create_outgoing_letter(plan, "Абзац 1\n\nАбзац 2")
    assert paths(client) == ["IOutgoingLetters", "IOutgoingLetters(16800)/Versions", "Docflow/GrantAccessRightsToDocument"]
    payload = client.posts[0][1]
    assert payload["PreparedBy"] == {"Id": 53} and payload["Department"] == {"Id": 49} and payload["BusinessUnit"] == {"Id": 52}
    assert payload["Correspondent"] == {"Id": 1464} and "OurSignatory" not in payload and "InResponseTo" not in payload
    assert client.posts[1][1] == {"AssociatedApplication": {"Id": 2}}
    assert client.patches[0][0] == "IOutgoingLetters(16800)/Versions(77)/Body" and client.patches[0][1]["Value"]
    assert client.posts[2][1] == {"documentId": 16800, "recipientId": 53, "accessRightsTypeGuid": "g-1"}
    assert result == {"document_id": 16800, "name": "Исх. письмо", "url": "https://rx/doc/16800", "version_id": 77, "warnings": []}


def test_letter_without_body_and_grant_guid_warns():
    client = FakeClient()
    s = svc(client)
    result = s.create_outgoing_letter(s.plan_outgoing_letter("Тема", "Аппарат", 53), None)
    assert paths(client) == ["IOutgoingLetters"] and "version_id" not in result
    assert "MCP_WRITE_GRANT_RIGHTS_GUID" in result["warnings"][0]


def test_letter_body_upload_failure_is_warning():
    client = FakeClient(fail={"patch": DirectumError("PATCH failed with 500: x", status_code=500)})
    s = svc(client, guid="g")
    result = s.create_outgoing_letter(s.plan_outgoing_letter("Тема", "Аппарат", 53, body_text="т"), "т")
    assert result["document_id"] == 16800 and "текст не загружен" in result["warnings"][0]


def test_letter_save_error_shows_rx_reason():
    err = DirectumError('POST IOutgoingLetters failed with 400. Payload had 3 fields: [...]. Response detail: '
                        'Не заполнено обязательное свойство "Подготовил".', status_code=400)
    s = svc(FakeClient(fail={"IOutgoingLetters": err}))
    with pytest.raises(DirectumError) as exc:
        s.create_outgoing_letter(s.plan_outgoing_letter("Тема", "Аппарат", 53), None)
    assert exc.value.safe_message.startswith('RX не сохранил документ: Не заполнено обязательное свойство "Подготовил".')


def test_counterparty_resolution():
    many = [{"Id": 1, "Name": "Минздрав"}, {"Id": 2, "Name": "Минздрав края"}]
    assert svc(FakeClient(many)).counterparty("минздрав") == {"id": 1, "name": "Минздрав"}
    with pytest.raises(CreateError, match="несколько корреспондентов"):
        svc(FakeClient([{"Id": 2, "Name": "Минздрав края"}, {"Id": 3, "Name": "Минздрав РФ"}])).counterparty("Минздрав")
    with pytest.raises(CreateError, match="не найден"):
        svc(FakeClient([])).counterparty("Нет такого")
    with pytest.raises(CreateError, match="не найден"):
        svc().counterparty("999")
    with pytest.raises(CreateError, match="Укажите корреспондента"):
        svc().counterparty(" ")


def test_plan_validation():
    with pytest.raises(CreateError, match="подразделение"):
        svc().plan_outgoing_letter("Тема", "Аппарат", 66)
    with pytest.raises(CreateError, match="не найден"):
        svc().plan_outgoing_letter("Тема", "Аппарат", 999)
    with pytest.raises(CreateError, match="Не заполнено"):
        svc().plan_outgoing_letter("  ", "Аппарат", 53)
    with pytest.raises(CreateError, match="не длиннее"):
        svc().plan_outgoing_letter("т" * 300, "Аппарат", 53)
    with pytest.raises(CreateError, match="не найден"):
        svc().plan_outgoing_letter("Тема", "Аппарат", 53, in_response_to_id=1)


def test_legal_act():
    client = FakeClient()
    s = svc(client, guid="g")
    plan = s.plan_legal_act("project", "О порядке", 53, True, signatory_id=63)
    assert plan["document_kind"]["id"] == 114 and plan["is_normative"] is True
    s.create_legal_act(plan)
    assert client.posts[0][0] == "ILegalActsLegalActs"
    assert client.posts[0][1]["IsNormative"] is True and client.posts[0][1]["OurSignatory"] == {"Id": 63}
    assert svc().plan_legal_act("act", "О", 53, False)["document_kind"]["id"] == 115
    with pytest.raises(CreateError):
        svc().plan_legal_act("law", "О", 53, False)


def test_action_item_flow():
    client = FakeClient()
    s = svc(client)
    plan = s.plan_action_item(500, 53, 63, date(2026, 10, 30), "Подготовить ответ",
                              under_control=True, supervisor_id=63)
    result = s.create_action_item(plan)
    path, payload = client.posts[0]
    assert path == "RecordManagement/CreateActionItemExecution"
    assert payload == {"documentId": 500, "assigneeId": 53, "isUnderControl": True, "supervisorId": 63, "coassigneeId": None,
                       "deadline": "2026-10-30T00:00:00+04:00", "activeText": "Подготовить ответ"}
    assert client.patches == [("IActionItemExecutionTasks(9001)", {"AssignedBy": {"Id": 63}})]
    assert client.posts[1] == ("Docflow/StartTask", {"taskId": 9001})
    assert result == {"task_id": 9001, "url": "https://rx/card/IActionItemExecutionTasks(9001)", "started": True, "warnings": []}


def test_action_item_not_started_when_patch_fails():
    client = FakeClient(fail={"patch": DirectumError("PATCH failed with 400: нельзя", status_code=400)})
    s = svc(client)
    result = s.create_action_item(s.plan_action_item(500, 53, 63, date(2026, 10, 30), "Текст"))
    assert result["started"] is False and "НЕ запущена" in result["warnings"][0]
    assert "Docflow/StartTask" not in paths(client)


def test_action_item_start_failure():
    client = FakeClient(fail={"Docflow/StartTask": DirectumError("StartTask failed", status_code=500)})
    s = svc(client)
    result = s.create_action_item(s.plan_action_item(500, 53, 63, date(2026, 10, 30), "Текст"))
    assert result["started"] is False and "не запущена" in result["warnings"][0]


def test_action_item_validation():
    with pytest.raises(CreateError, match="уже прошёл"):
        svc().plan_action_item(500, 53, 63, date(2026, 10, 1), "т")
    with pytest.raises(CreateError, match="Контролёр"):
        svc().plan_action_item(500, 53, 63, date(2026, 10, 30), "т", supervisor_id=63)
    with pytest.raises(CreateError, match="совпадает"):
        svc().plan_action_item(500, 53, 63, date(2026, 10, 30), "т", co_assignee_id=53)
    with pytest.raises(CreateError, match="текст поручения"):
        svc().plan_action_item(500, 53, 63, date(2026, 10, 30), " ")


def test_review_approval_task():
    client = FakeClient()
    s = svc(client)
    s.create_review(s.plan_review(500, 53, 63, "Проект резолюции: Иванову"))
    assert client.posts[0] == ("RecordManagement/CreateDocumentReviewTask",
                               {"documentId": 500, "addresseeId": 53, "activeText": "Проект резолюции: Иванову"})
    assert client.patches[0] == ("IDocumentReviewTasks(9001)", {"Author": {"Id": 63}})
    client = FakeClient()
    s = svc(client)
    s.create_approval(s.plan_approval(500, 63, 53, [53, 53, 63], None))
    assert client.posts[0][1] == {"documentId": 500, "text": None, "signatoryId": 53, "addApproverIds": [53, 63]}
    assert client.patches[0][0] == "IApprovalTasks(9001)"
    with pytest.raises(CreateError):
        svc().plan_approval(500, 63, None, list(range(11)))
    client = FakeClient()
    s = svc(client)
    s.create_task(s.plan_task("Сверка", [53, 63], 63, date(2026, 10, 20), "Текст", [500], "high"))
    assert client.posts[0][1] == {"assignmentType": "Assignment", "subject": "Сверка", "deadline": "2026-10-20T00:00:00+04:00",
                                  "importance": "High", "text": "Текст", "performerIds": [53, 63], "observerIds": [],
                                  "documentIds": [500]}
    assert client.patches[0][0] == "ISimpleTasks(9001)"
    for bad in (dict(performer_ids=[]), dict(importance="x"), dict(deadline=date(2026, 1, 1))):
        args = dict(subject="С", performer_ids=[53], author_id=63, deadline=None)
        args.update(bad)
        with pytest.raises(CreateError):
            svc().plan_task(**args)


def test_report_request_defaults_and_flow():
    client = FakeClient()
    s = svc(client)
    # 14.10.2026 — среда; +2 рабочих дня = пятница 16.10.
    plan = s.plan_report_request(80872, 66)
    assert plan["assignee"] == {"id": 53, "name": "Босов А. С."} and plan["author"]["id"] == 66
    assert plan["deadline"] == "2026-10-16" and plan["deadline_is_default"] is True
    assert plan["action_item"]["subject"] == "Подготовить доклад"
    assert plan["text"] == "Прошу представить отчёт о ходе исполнения поручения."
    result = s.create_report_request(plan)
    assert client.posts[0] == ("IStatusReportRequestTasks", {
        "Subject": "Запрос отчета: Подготовить доклад", "ParentTask": {"Id": 80872}, "Assignee": {"Id": 53},
        "MaxDeadline": "2026-10-16T00:00:00+04:00"})
    assert client.posts[1] == ("IStatusReportRequestTasks(9100)/Texts", {"Body": plan["text"]})
    assert client.patches == [("IStatusReportRequestTasks(9100)", {"Author": {"Id": 66}})]
    assert client.posts[2] == ("Docflow/StartTask", {"taskId": 9100})
    assert result["started"] is True and result["warnings"] == []


def test_report_request_friday_deadline_skips_weekend():
    s = DocumentCreateService(FakeClient(), tz=TZ, now=lambda: datetime(2026, 10, 16, 12, 0, tzinfo=TZ))
    assert s.plan_report_request(80872, 63)["deadline"] == "2026-10-20"


def test_report_request_rules():
    with pytest.raises(CreateError, match="выдал поручение"):
        svc().plan_report_request(80872, 53)  # исполнитель сам у себя — нельзя
    with pytest.raises(CreateError, match="укажите"):
        svc().plan_report_request(80873, 63)
    assert svc().plan_report_request(80873, 63, assignee_id=53)["assignee"]["id"] == 53
    with pytest.raises(CreateError, match="в работе"):
        svc().plan_report_request(80874, 63)
    with pytest.raises(CreateError, match="не найдено"):
        svc().plan_report_request(1, 63)
    with pytest.raises(CreateError, match="уже прошёл"):
        svc().plan_report_request(80872, 63, deadline=date(2026, 10, 1))
    plan = svc().plan_report_request(80872, 63, deadline=date(2026, 10, 20), text="  Доложить   о сроках ")
    assert plan["text"] == "Доложить о сроках" and plan["deadline_is_default"] is False


def test_report_request_text_failure_is_warning():
    client = FakeClient(fail={"IStatusReportRequestTasks(9100)/Texts": DirectumError("x failed with 400: нельзя", status_code=400)})
    s = svc(client)
    result = s.create_report_request(s.plan_report_request(80872, 63))
    assert result["started"] is True and "Текст запроса не добавлен" in result["warnings"][0]


def test_closed_employee_cannot_receive_work():
    with pytest.raises(CreateError, match="закрыт в RX"):
        svc().plan_action_item(500, 70, 63, date(2026, 10, 30), "Текст")
    with pytest.raises(CreateError, match="закрыт в RX"):
        svc().plan_task("Тема", [53, 70], 63, None)
    with pytest.raises(CreateError, match="«Подготовил»"):
        svc().plan_outgoing_letter("Тема", "Аппарат", 70)
    with pytest.raises(CreateError, match="закрыт в RX"):
        svc().plan_outgoing_letter("Тема", "Аппарат", 53, signatory_id=70)


def test_closed_employee_allowed_as_author_with_note():
    plan = svc().plan_action_item(500, 53, 70, date(2026, 10, 30), "Текст")
    assert plan["assigned_by"]["closed"] is True and "закрыт в RX" in plan["assigned_by"]["note"]
    assert svc().plan_review(500, 53, 70)["author"]["closed"] is True


def test_report_request_from_closed_issuer_case_955():
    # Поручение 955: выдал и контролирует закрытый сотрудник, исполнитель — действующий.
    plan = svc().plan_report_request(955, 70)
    assert plan["author"]["id"] == 70 and plan["author"]["closed"] is True
    assert plan["assignee"] == {"id": 63, "name": "Концева Н. И."}
    with pytest.raises(CreateError, match="закрыт в RX"):
        svc().plan_report_request(956, 63)  # исполнитель по умолчанию закрыт
    assert svc().plan_report_request(956, 63, assignee_id=53)["assignee"]["id"] == 53


def test_save_errors_are_actionable():
    err404 = DirectumError("POST X failed with 404. Payload had 3 fields. Response detail: ", status_code=404)
    s = svc(FakeClient(fail={"ILegalActsLegalActs": err404}))
    with pytest.raises(DirectumError, match="не принимает создание"):
        s.create_legal_act(s.plan_legal_act("project", "О", 53, False))
    category = DirectumError('failed with 400. Response detail: "Не заполнено обязательное свойство \\"Категория\\"."', status_code=400)
    s = svc(FakeClient(fail={"ILegalActsLegalActs": category}))
    with pytest.raises(DirectumError, match="не опубликовано в сервисе интеграции"):
        s.create_legal_act(s.plan_legal_act("project", "О", 53, False))
    other = DirectumError('failed with 400. Response detail: Не заполнено обязательное свойство "Адресат".', status_code=400)
    s = svc(FakeClient(fail={"IOutgoingLetters": other}))
    with pytest.raises(DirectumError, match="«?Адресат.*Повторять с теми же данными бессмысленно"):
        s.create_outgoing_letter(s.plan_outgoing_letter("Тема", "Аппарат", 53), None)
