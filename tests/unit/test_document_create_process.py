"""Документ, созданный в рамках задачи процесса: «Подготовил» = автор задачи, вложение, права, без дублей."""

import pytest

from src.services.directum_client import DirectumError
from src.services.document_create import (
    NOTE_TEMPLATE,
    SIMPLE_PROCESS_DOCUMENTS_GROUP,
    CreateError,
    DocumentCreateService,
)
from tests.unit.test_document_create import EMPLOYEES, NOW, TZ, FakeClient, paths

KIM = {"Id": 396, "Name": "Ким Диана Альбертовна"}
KIM_CARD = {"Id": 396, "Name": "Ким Диана Альбертовна",
            "Department": {"Id": 49, "Name": "Секретариат", "BusinessUnit": {"Id": 52, "Name": "Администрация"}}}
PROCESS = "#Sungero.IntegrationService.Models.Generated.DocflowApproval.ISimpleProcessTaskDto"
CUSTOM = "#Sungero.IntegrationService.Models.Generated.Docflow.ICustomTaskDto"
MCP_NOTE = NOTE_TEMPLATE.split(":")[0]

TASKS = {
    86309: {"@odata.type": PROCESS, "Id": 86309, "Subject": "Создать документ от лица пользователя", "Status": "InProcess",
            "Author": KIM, "AttachmentDetails": []},
    86310: {"@odata.type": PROCESS, "Id": 86310, "Subject": "Завершённая", "Status": "Completed",
            "Author": KIM, "AttachmentDetails": []},
    86311: {"@odata.type": PROCESS, "Id": 86311, "Subject": "Без автора", "Status": "InProcess",
            "Author": None, "AttachmentDetails": []},
    86312: {"@odata.type": PROCESS, "Id": 86312, "Subject": "Уже с документом", "Status": "InProcess", "Author": KIM,
            "AttachmentDetails": [{"AttachmentId": 16900, "GroupId": SIMPLE_PROCESS_DOCUMENTS_GROUP}]},
    86400: {"@odata.type": CUSTOM, "Id": 86400, "Subject": "Неизвестный тип", "Status": "InProcess", "Author": KIM,
            "AttachmentDetails": []},
    86401: {"@odata.type": CUSTOM, "Id": 86401, "Subject": "Тип с вложениями", "Status": "InProcess", "Author": KIM,
            "AttachmentDetails": [{"AttachmentId": 1, "GroupId": "g-known"}, {"AttachmentId": 2, "GroupId": "g-known"}]},
    86316: {"@odata.type": "#Sungero.IntegrationService.Models.Generated.Workflow.ISimpleTaskDto", "Id": 86316,
            "Subject": "Простая задача", "Status": "InProcess", "Author": {"Id": 53, "Name": "Босов А. С."},
            "AttachmentDetails": [{"AttachmentId": 16877, "GroupId": None, "AttachmentTypeGuid": "4d9e2016"}]},
    86402: {"@odata.type": PROCESS, "Id": 86402, "Subject": "Свой документ", "Status": "InProcess",
            "Author": {"Id": 53, "Name": "Босов А. С."}, "AttachmentDetails": []},
}

EXISTING = {16900: {"Id": 16900, "Name": "Простой документ «Тема»", "Subject": " тема ",
                    "Note": f"{MCP_NOTE}: Ким Диана Альбертовна, 14.10.2026."}}


class ProcessClient(FakeClient):
    def __init__(self, existing=None, **kw):
        super().__init__(**kw)
        self.existing = EXISTING if existing is None else existing

    def get_one(self, path):
        if path.startswith("ITasks("):
            task_id = int(path.split("(")[1].split(")")[0])
            if task_id not in TASKS:
                raise DirectumError("Запись не существует", status_code=404)
            return TASKS[task_id]
        if path.startswith("IEmployees(396)"):
            return KIM_CARD
        return super().get_one(path)

    def query(self, entity_set, **kw):
        if entity_set == "IOfficialDocuments" and "Subject" in (kw.get("select") or ""):
            f = kw.get("filter_", "")
            return [doc for doc_id, doc in self.existing.items() if f"Id eq {doc_id}" in f]
        return super().query(entity_set, **kw)


def svc(client=None, guid=None, types=None):
    return DocumentCreateService(client or ProcessClient(), tz=TZ, grant_rights_guid=guid, now=lambda: NOW,
                                 process_task_types=types)


def grants(client):
    return [p for path, p in client.posts if path == "Docflow/GrantAccessRightsToDocument"]


# --- «Подготовил» = автор задачи ---

def test_preparer_is_task_author_when_not_given():
    plan = svc().plan_outgoing_letter("Тема", "Аппарат", None, process_task_id=86309)
    assert plan["prepared_by"] == {"id": 396, "name": "Ким Диана Альбертовна"}
    assert plan["department"] == {"id": 49, "name": "Секретариат"}
    assert plan["process_task"] == {
        "id": 86309, "subject": "Создать документ от лица пользователя", "author": {"id": 396, "name": "Ким Диана Альбертовна"},
        "entity_set": "ISimpleProcessTasks", "group_id": SIMPLE_PROCESS_DOCUMENTS_GROUP,
    }


def test_preparer_matching_author_is_ok():
    assert svc().plan_outgoing_letter("О", "Аппарат", 396, process_task_id=86309)["prepared_by"]["id"] == 396


def test_preparer_conflicting_with_author_is_error():
    with pytest.raises(CreateError, match="от имени её автора \\(Ким Диана Альбертовна\\)"):
        svc().plan_legal_act("project", "О", 53, False, process_task_id=86309)


def test_task_without_author_is_error():
    with pytest.raises(CreateError, match="нет автора"):
        svc().plan_outgoing_letter("Тема", "Аппарат", None, process_task_id=86311)


def test_no_preparer_and_no_task_is_error():
    with pytest.raises(CreateError, match="Укажите, от чьего имени"):
        svc().plan_outgoing_letter("Тема", "Аппарат", None)


def test_plan_without_task_has_no_process_task():
    assert svc().plan_outgoing_letter("Тема", "Аппарат", 53)["process_task"] is None


# --- какая задача подходит ---

def test_unknown_task_is_readable_error():
    with pytest.raises(CreateError, match="Задача с id 1 не найдена"):
        svc().plan_outgoing_letter("Тема", "Аппарат", None, process_task_id=1)


def test_completed_task_is_error():
    with pytest.raises(CreateError, match="Задача 86310 не в работе \\(Completed\\)"):
        svc().plan_outgoing_letter("Тема", "Аппарат", None, process_task_id=86310)


def test_task_type_outside_allowlist_is_error():
    with pytest.raises(CreateError, match="ICustomTasks"):
        svc().plan_outgoing_letter("Тема", "Аппарат", None, process_task_id=86400)


def test_group_from_existing_attachments_when_type_allowed():
    plan = svc(types={"ISimpleProcessTasks", "ICustomTasks"}).plan_legal_act("project", "О", None, False, process_task_id=86401)
    assert plan["process_task"]["entity_set"] == "ICustomTasks" and plan["process_task"]["group_id"] == "g-known"


# --- создание: вложение и права ---

def test_created_document_attached_to_task_and_rights_granted_to_both():
    client = ProcessClient()
    s = svc(client, guid="g-change")
    result = s.create_outgoing_letter(s.plan_outgoing_letter("Тема", "Аппарат", None, process_task_id=86309), None)
    attach = dict(client.posts)["ISimpleProcessTasks(86309)/AttachmentDetails"]
    assert attach == {"GroupId": SIMPLE_PROCESS_DOCUMENTS_GROUP, "AttachmentId": 16800, "AttachmentTypeGuid": "d1d2a452-type"}
    assert [g["recipientId"] for g in grants(client)] == [396]
    assert grants(client)[0] == {"documentId": 16800, "recipientId": 396, "accessRightsTypeGuid": "g-change"}
    assert result["process_task"] == {"id": 86309, "attached": True} and result["warnings"] == []


def test_without_task_rights_go_to_preparer_only():
    client = ProcessClient()
    s = svc(client, guid="g")
    s.create_outgoing_letter(s.plan_outgoing_letter("Тема", "Аппарат", 53), None)
    assert [g["recipientId"] for g in grants(client)] == [53]


def test_no_guid_under_service_account_warns_attachment_gives_no_access():
    client = ProcessClient()
    s = svc(client)
    result = s.create_outgoing_letter(s.plan_outgoing_letter("Тема", "Аппарат", None, process_task_id=86309), None)
    assert grants(client) == [] and result["process_task"]["attached"] is True
    assert len(result["warnings"]) == 1
    warning = result["warnings"][0]
    assert "MCP_WRITE_GRANT_RIGHTS_GUID" in warning and "вложение в задачу доступа не даёт" in warning


def test_personal_mode_without_guid_has_no_rights_warning():
    client = ProcessClient()
    s = svc(client)
    s.service_account = False  # под личной учёткой пользователь — автор документа, права у него есть
    result = s.create_outgoing_letter(s.plan_outgoing_letter("Тема", "Аппарат", None, process_task_id=86309), None)
    assert grants(client) == [] and result["warnings"] == []


def test_attach_failure_is_warning_and_rights_still_granted():
    err = DirectumError("POST failed with 400: Задача завершена", status_code=400)
    client = ProcessClient(fail={"ISimpleProcessTasks(86309)/AttachmentDetails": err})
    s = svc(client, guid="g")
    result = s.create_outgoing_letter(s.plan_outgoing_letter("Тема", "Аппарат", None, process_task_id=86309), None)
    assert result["document_id"] == 16800 and result["process_task"] == {"id": 86309, "attached": False}
    assert any("не приложен к задаче 86309" in w for w in result["warnings"])
    assert [g["recipientId"] for g in grants(client)] == [396]


def test_unknown_group_is_warning():
    client = ProcessClient()
    s = svc(client, guid="g", types={"ICustomTasks"})
    result = s.create_legal_act(s.plan_legal_act("act", "О", None, True, process_task_id=86400), None)
    assert not [p for p in paths(client) if p.endswith("/AttachmentDetails")]
    assert result["process_task"] == {"id": 86400, "attached": False}
    assert any("группу вложений" in w for w in result["warnings"])


def test_task_with_ungrouped_attachments_gets_document_without_group():
    client = ProcessClient()
    s = svc(client, types={"ISimpleTasks"})
    plan = s.plan_outgoing_letter("Тема", "Аппарат", None, process_task_id=86316)
    assert plan["process_task"]["entity_set"] == "ISimpleTasks" and plan["process_task"]["group_id"] == ""
    result = s.create_outgoing_letter(plan, None)
    attach = dict(client.posts)["ISimpleTasks(86316)/AttachmentDetails"]
    assert attach == {"GroupId": None, "AttachmentId": 16800, "AttachmentTypeGuid": "d1d2a452-type"}
    assert result["process_task"] == {"id": 86316, "attached": True}


def test_grant_failure_names_person():
    err = DirectumError("POST failed with 400: Нет прав", status_code=400)
    client = ProcessClient(fail={"Docflow/GrantAccessRightsToDocument": err})
    s = svc(client, guid="g")
    result = s.create_outgoing_letter(s.plan_outgoing_letter("Тема", "Аппарат", None, process_task_id=86309), None)
    assert any("Ким Диана Альбертовна" in w and "не выданы" in w for w in result["warnings"])


# --- без дублей при повторе ---

def test_repeat_returns_existing_document_without_creating():
    client = ProcessClient()
    s = svc(client, guid="g")
    result = s.create_outgoing_letter(s.plan_outgoing_letter("Тема", "Аппарат", None, process_task_id=86312), None)
    assert result["document_id"] == 16900 and result["already_existed"] is True
    assert "уже создан" in result["warnings"][0]
    assert "IOutgoingLetters" not in paths(client) and grants(client) == []


def test_other_subject_creates_new_document():
    client = ProcessClient()
    s = svc(client)
    result = s.create_outgoing_letter(s.plan_outgoing_letter("Другая тема", "Аппарат", None, process_task_id=86312), None)
    assert result["document_id"] == 16800 and "already_existed" not in result


def test_document_without_mcp_note_is_not_a_duplicate():
    client = ProcessClient(existing={16900: {**EXISTING[16900], "Note": "Создан вручную"}})
    s = svc(client)
    result = s.create_outgoing_letter(s.plan_outgoing_letter("Тема", "Аппарат", None, process_task_id=86312), None)
    assert result["document_id"] == 16800


def test_kim_card_is_not_leaking_into_shared_fixture():
    assert 396 not in EMPLOYEES
