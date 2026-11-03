"""Документ, созданный в рамках задачи процесса: вложение в задачу и права сотрудникам."""

import pytest

from src.services.directum_client import DirectumError
from src.services.document_create import SIMPLE_PROCESS_DOCUMENTS_GROUP, CreateError
from tests.unit.test_document_create import FakeClient, paths, svc

KIM = {"Id": 396, "Name": "Ким Диана Альбертовна"}

TASKS = {
    86309: {"@odata.type": "#Sungero.IntegrationService.Models.Generated.DocflowApproval.ISimpleProcessTaskDto",
            "Id": 86309, "Subject": "Создать документ от лица пользователя", "Status": "InProcess",
            "Author": KIM, "AttachmentDetails": []},
    86400: {"@odata.type": "#Sungero.IntegrationService.Models.Generated.Docflow.ICustomTaskDto",
            "Id": 86400, "Subject": "Неизвестный тип", "Status": "InProcess", "Author": KIM, "AttachmentDetails": []},
    86401: {"@odata.type": "#Sungero.IntegrationService.Models.Generated.Docflow.ICustomTaskDto",
            "Id": 86401, "Subject": "Тип с вложениями", "Status": "InProcess", "Author": KIM,
            "AttachmentDetails": [{"AttachmentId": 1, "GroupId": "g-known"}, {"AttachmentId": 2, "GroupId": "g-known"}]},
    86402: {"@odata.type": "#Sungero.IntegrationService.Models.Generated.DocflowApproval.ISimpleProcessTaskDto",
            "Id": 86402, "Subject": "Свой документ", "Status": "InProcess",
            "Author": {"Id": 53, "Name": "Босов А. С."}, "AttachmentDetails": []},
}


class ProcessClient(FakeClient):
    def get_one(self, path):
        if path.startswith("ITasks("):
            task_id = int(path.split("(")[1].split(")")[0])
            if task_id not in TASKS:
                raise DirectumError("Запись не существует", status_code=404)
            return TASKS[task_id]
        return super().get_one(path)


def grants(client):
    return [p for path, p in client.posts if path == "Docflow/GrantAccessRightsToDocument"]


def test_plan_resolves_process_task():
    plan = svc(ProcessClient()).plan_outgoing_letter("Тема", "Аппарат", 53, process_task_id=86309)
    assert plan["process_task"] == {
        "id": 86309, "subject": "Создать документ от лица пользователя", "author": {"id": 396, "name": "Ким Диана Альбертовна"},
        "entity_set": "ISimpleProcessTasks", "group_id": SIMPLE_PROCESS_DOCUMENTS_GROUP,
    }


def test_plan_without_task_has_no_process_task():
    assert svc(ProcessClient()).plan_outgoing_letter("Тема", "Аппарат", 53)["process_task"] is None


def test_unknown_task_is_readable_error():
    with pytest.raises(CreateError, match="Задача с id 1 не найдена"):
        svc(ProcessClient()).plan_outgoing_letter("Тема", "Аппарат", 53, process_task_id=1)


def test_group_from_existing_attachments_when_type_unknown():
    plan = svc(ProcessClient()).plan_legal_act("project", "О", 53, False, process_task_id=86401)
    assert plan["process_task"]["entity_set"] == "ICustomTasks" and plan["process_task"]["group_id"] == "g-known"


def test_created_document_attached_to_task_and_rights_granted_to_both():
    client = ProcessClient()
    s = svc(client, guid="g-change")
    result = s.create_outgoing_letter(s.plan_outgoing_letter("Тема", "Аппарат", 53, process_task_id=86309), None)
    assert "ISimpleProcessTasks(86309)/AttachmentDetails" in paths(client)
    attach = dict(client.posts)["ISimpleProcessTasks(86309)/AttachmentDetails"]
    assert attach == {"GroupId": SIMPLE_PROCESS_DOCUMENTS_GROUP, "AttachmentId": 16800, "AttachmentTypeGuid": "d1d2a452-type"}
    assert [g["recipientId"] for g in grants(client)] == [53, 396]
    assert all(g == {"documentId": 16800, "recipientId": g["recipientId"], "accessRightsTypeGuid": "g-change"} for g in grants(client))
    assert result["process_task"] == {"id": 86309, "attached": True} and result["warnings"] == []


def test_same_person_gets_rights_once():
    client = ProcessClient()
    s = svc(client, guid="g")
    s.create_outgoing_letter(s.plan_outgoing_letter("Тема", "Аппарат", 53, process_task_id=86402), None)
    assert [g["recipientId"] for g in grants(client)] == [53]


def test_no_guid_but_attached_explains_rights_via_task():
    client = ProcessClient()
    s = svc(client)
    result = s.create_outgoing_letter(s.plan_outgoing_letter("Тема", "Аппарат", 53, process_task_id=86309), None)
    assert grants(client) == [] and result["process_task"]["attached"] is True
    assert len(result["warnings"]) == 1
    warning = result["warnings"][0]
    assert "MCP_WRITE_GRANT_RIGHTS_GUID" in warning and "приложен к задаче 86309" in warning


def test_attach_failure_is_warning_and_rights_still_granted():
    err = DirectumError("POST failed with 400: Задача завершена", status_code=400)
    client = ProcessClient(fail={"ISimpleProcessTasks(86309)/AttachmentDetails": err})
    s = svc(client, guid="g")
    result = s.create_outgoing_letter(s.plan_outgoing_letter("Тема", "Аппарат", 53, process_task_id=86309), None)
    assert result["document_id"] == 16800 and result["process_task"] == {"id": 86309, "attached": False}
    assert any("не приложен к задаче 86309" in w for w in result["warnings"])
    assert [g["recipientId"] for g in grants(client)] == [53, 396]


def test_unknown_group_is_warning():
    client = ProcessClient()
    s = svc(client, guid="g")
    result = s.create_legal_act(s.plan_legal_act("act", "О", 53, True, process_task_id=86400), None)
    assert not [p for p in paths(client) if p.endswith("/AttachmentDetails")]
    assert result["process_task"] == {"id": 86400, "attached": False}
    assert any("группу вложений" in w for w in result["warnings"])


def test_grant_failure_names_person():
    err = DirectumError("POST failed with 400: Нет прав", status_code=400)
    client = ProcessClient(fail={"Docflow/GrantAccessRightsToDocument": err})
    s = svc(client, guid="g")
    result = s.create_outgoing_letter(s.plan_outgoing_letter("Тема", "Аппарат", 53, process_task_id=86309), None)
    assert any("Ким Диана Альбертовна" in w and "не выданы" in w for w in result["warnings"])
