from types import SimpleNamespace

from src.mcp_server.app import build_server
from src.mcp_server.write_guard import WriteAudit
from src.models.schemas import EmployeeSummary
from src.services.document_create import CreateError
from tests.unit.mcp_fakes import FakeProvider, call_tool, error_text, list_tools, payload

WRITE_TOOLS = {"create_outgoing_letter", "create_legal_act", "create_action_item", "send_document_for_review",
               "send_document_for_approval", "create_task", "request_action_item_report"}


class FakeCreate:
    def __init__(self):
        self.created = []

    def plan_outgoing_letter(self, subject, correspondent, prepared_by_id, **kw):
        if subject == "плохо":
            raise CreateError("Корреспондент не найден.")
        return {"operation": "outgoing_letter", "subject": subject, "correspondent": correspondent,
                "prepared_by": {"id": prepared_by_id, "name": "Босов"}, "signatory": kw.get("signatory_id")}

    def create_outgoing_letter(self, plan, body_text):
        self.created.append((plan, body_text))
        return {"document_id": 16800, "url": "https://rx/doc/16800", "warnings": []}

    def plan_action_item(self, document_id, assignee_id, assigned_by_id, deadline, text, **kw):
        return {"operation": "action_item", "document": document_id, "assignee": assignee_id,
                "assigned_by": {"id": assigned_by_id, "name": "Концева"}, "deadline": deadline.isoformat(), "text": text}

    def plan_report_request(self, action_item_id, requested_by_id, deadline=None, text=None, assignee_id=None):
        return {"operation": "report_request", "action_item": {"id": action_item_id}, "author": {"id": requested_by_id, "name": "Концева"},
                "assignee": assignee_id, "deadline": deadline.isoformat() if deadline else None, "text": text}

    def create_report_request(self, plan):
        self.created.append(plan)
        return {"task_id": 9100, "started": True, "warnings": []}

    def create_action_item(self, plan):
        self.created.append(plan)
        return {"task_id": 9001, "started": True, "warnings": []}


def make(tmp_path=None, service_account=False, write=True):
    create = FakeCreate()
    people = {"Босов": 53, "Концева": 63, "Иванов": 77}
    services = SimpleNamespace(
        document_create=create,
        service_account=service_account,
        credentials_fingerprint="fp",
        usage_id="u1",
        action_items=SimpleNamespace(
            search_employee=lambda q: [EmployeeSummary(id=people[q], name=q)] if q in people else [],
            get_employee=lambda i: EmployeeSummary(id=i, name="X")),
        current_user=SimpleNamespace(get_current_user=lambda: SimpleNamespace(id=1)),
    )
    audit = WriteAudit(str(tmp_path / "w.log")) if (write and tmp_path) else (WriteAudit(None) if write else None)
    return build_server(FakeProvider(services), write_audit=audit), create


def test_write_tools_only_when_enabled():
    server, _ = make(write=False)
    assert not WRITE_TOOLS & {t.name for t in list_tools(server)}
    server, _ = make()
    tools = {t.name: t for t in list_tools(server)}
    assert WRITE_TOOLS <= set(tools)
    assert tools["create_outgoing_letter"].annotations.read_only_hint is False


def test_preview_then_confirm(tmp_path):
    server, create = make(tmp_path)
    args = {"subject": "О совещании", "correspondent": "Аппарат", "on_behalf_of": "Босов", "body_text": "Текст"}
    preview = payload(call_tool(server, "create_outgoing_letter", args))
    assert preview["mode"] == "preview" and preview["confirm_token"] and "Ничего не создано" in preview["next_step"]
    assert preview["will_create"]["prepared_by"] == {"id": 53, "name": "Босов"} and create.created == []
    done = payload(call_tool(server, "create_outgoing_letter", {**args, "confirm_token": preview["confirm_token"]}))
    assert done["mode"] == "created" and done["document_id"] == 16800
    assert create.created[0][1] == "Текст"
    assert '"document_id": 16800' in (tmp_path / "w.log").read_text(encoding="utf-8")


def test_changed_params_invalidate_token():
    server, create = make()
    args = {"subject": "О совещании", "correspondent": "Аппарат", "on_behalf_of": "Босов"}
    token = payload(call_tool(server, "create_outgoing_letter", args))["confirm_token"]
    err = error_text(call_tool(server, "create_outgoing_letter", {**args, "subject": "Другое", "confirm_token": token}))
    assert "параметры изменились" in err and create.created == []
    err = error_text(call_tool(server, "create_outgoing_letter", {**args, "confirm_token": "1.abc"}))
    assert "параметры изменились" in err


def test_on_behalf_rules():
    server, _ = make(service_account=True)
    args = {"subject": "О", "correspondent": "Аппарат", "on_behalf_of": "me"}
    assert "служебной учётной записью" in error_text(call_tool(server, "create_outgoing_letter", args))
    preview = payload(call_tool(server, "create_outgoing_letter", {**args, "on_behalf_of": "Босов"}))
    assert "служебной учёткой" in preview["service_account_note"]
    assert "Укажите от чьего имени" in error_text(call_tool(server, "create_outgoing_letter", {**args, "on_behalf_of": " "}))
    assert "не найден" in error_text(call_tool(server, "create_outgoing_letter", {**args, "on_behalf_of": "Нектоев"}))


def test_create_error_is_readable():
    server, _ = make()
    err = error_text(call_tool(server, "create_outgoing_letter", {"subject": "плохо", "correspondent": "x", "on_behalf_of": "Босов"}))
    assert "Корреспондент не найден." in err and "Внутренняя ошибка" not in err


def test_action_item_tool():
    server, create = make()
    args = {"document_id": 500, "assignee": "Иванов", "deadline": "2026-10-30", "text": "Подготовить", "on_behalf_of": "Концева"}
    preview = payload(call_tool(server, "create_action_item", args))
    assert preview["will_create"]["assignee"] == 77 and preview["will_create"]["assigned_by"]["id"] == 63
    done = payload(call_tool(server, "create_action_item", {**args, "confirm_token": preview["confirm_token"]}))
    assert done["task_id"] == 9001 and done["started"] is True
    assert "Укажите срок" in error_text(call_tool(server, "create_action_item", {**args, "deadline": " "}))


def test_request_report_tool():
    server, create = make()
    args = {"action_item_id": 80872, "on_behalf_of": "Концева", "deadline": "2026-10-20", "assignee": "Иванов"}
    preview = payload(call_tool(server, "request_action_item_report", args))
    assert preview["will_create"] == {"operation": "report_request", "action_item": {"id": 80872},
                                      "author": {"id": 63, "name": "Концева"}, "assignee": 77, "deadline": "2026-10-20", "text": None}
    done = payload(call_tool(server, "request_action_item_report", {**args, "confirm_token": preview["confirm_token"]}))
    assert done["task_id"] == 9100 and create.created[-1]["operation"] == "report_request"
