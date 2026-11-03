import base64
from types import SimpleNamespace

from src.mcp_server.app import build_server
from src.mcp_server.write_guard import WriteAudit
from src.models.schemas import EmployeeSummary
from tests.unit.mcp_fakes import FakeProvider, call_tool, error_text, list_tools, payload

PDF = b"%PDF-1.4 " + bytes(range(256)) * 8  # 2057 байт


class FakeText:
    def __init__(self, data=PDF):
        self.data = data

    def read_body(self, document_id):
        return {"document_id": document_id, "name": "Письмо", "url": "u", "version": 2, "extension": "pdf"}, self.data

    def versions(self, document_id):
        return {"document_id": document_id, "total": 2, "next_version_number": 3, "versions_locked": False,
                "last_version_signed": False, "versions": [{"number": 2}, {"number": 1}]}


class FakeCreate:
    def __init__(self):
        self.created = []

    def plan_body(self, document_id, content_base64=None, content=None, extension=None, **kw):
        data = content if content is not None else base64.b64decode(content_base64)
        return {"operation": "body", "document": {"id": document_id}, "source": "file", "extension": extension,
                "bytes": len(data)}

    def create_body(self, plan, content_base64=None, content=None, **kw):
        self.created.append(content)
        return {"document_id": plan["document"]["id"], "version_id": 5, "warnings": []}

    def _person(self, person_id, receives_work=True):
        return {"id": person_id, "name": "Б"}


def make(data=PDF, write=True, fingerprint="fp"):
    create = FakeCreate()
    services = SimpleNamespace(
        document_text=FakeText(data), document_create=create, service_account=False,
        credentials_fingerprint=fingerprint, usage_id="u1",
        action_items=SimpleNamespace(search_employee=lambda q: [EmployeeSummary(id=53, name=q)],
                                     get_employee=lambda i: EmployeeSummary(id=i, name="X")),
        current_user=SimpleNamespace(get_current_user=lambda: SimpleNamespace(id=1)),
    )
    server = build_server(FakeProvider(services), write_audit=WriteAudit(None) if write else None)
    return server, create, services


def test_tools_registered_by_mode():
    names = {t.name for t in list_tools(make()[0])}
    assert {"download_chunk", "abort_transfer", "begin_upload", "upload_chunk", "commit_upload"} <= names
    read_only = {t.name for t in list_tools(make(write=False)[0])}
    assert {"download_chunk", "abort_transfer"} <= read_only
    assert not {"begin_upload", "upload_chunk", "commit_upload"} & read_only


def test_small_file_returned_inline():
    server, _, _ = make()
    result = payload(call_tool(server, "get_doc_body_base64", {"document_id": 16800}))
    assert base64.b64decode(result["content_base64"]) == PDF and "download_id" not in result


def test_large_file_switches_to_chunked_download():
    big = b"%PDF" + b"x" * (1024 * 1024 + 10)
    server, _, _ = make(big)
    result = payload(call_tool(server, "get_doc_body_base64", {"document_id": 16800}))
    assert "content_base64" not in result
    assert result["transfer"] == "chunked" and result["total_chunks"] == 2 and result["bytes"] == len(big)
    assert result["name"] == "Письмо" and "download_chunk" in result["next_step"]
    parts = [payload(call_tool(server, "download_chunk", {"download_id": result["download_id"], "index": i}))
             for i in range(2)]
    assert base64.b64decode("".join(p["chunk_base64"] for p in parts)) == big
    assert payload(call_tool(server, "abort_transfer", {"transfer_id": result["download_id"]}))["aborted"] is True
    assert "не найдена" in error_text(call_tool(server, "download_chunk", {"download_id": result["download_id"], "index": 0}))


def test_upload_then_create_body_with_confirm():
    server, create, _ = make()
    text = base64.b64encode(PDF).decode("ascii")
    upload_id = payload(call_tool(server, "begin_upload", {}))["upload_id"]
    payload(call_tool(server, "upload_chunk", {"upload_id": upload_id, "index": 0, "chunk_base64": text[:1000]}))
    payload(call_tool(server, "upload_chunk", {"upload_id": upload_id, "index": 1, "chunk_base64": text[1000:]}))
    committed = payload(call_tool(server, "commit_upload", {"upload_id": upload_id, "total_chunks": 2}))
    assert committed["bytes"] == len(PDF)
    args = {"document_id": 16800, "upload_id": upload_id, "extension": "pdf", "on_behalf_of": "Босов"}
    preview = payload(call_tool(server, "create_document_body_base64", args))
    assert preview["mode"] == "preview" and preview["will_create"]["bytes"] == len(PDF) and not create.created
    done = payload(call_tool(server, "create_document_body_base64", {**args, "confirm_token": preview["confirm_token"]}))
    assert done["mode"] == "created" and create.created == [PDF]
    # после создания загрузка освобождена
    assert "не найдена" in error_text(call_tool(server, "create_document_body_base64", args))


def test_create_body_needs_exactly_one_source():
    server, _, _ = make()
    base = {"document_id": 16800, "extension": "pdf", "on_behalf_of": "Босов"}
    assert "content_base64 или upload_id" in error_text(call_tool(server, "create_document_body_base64", base))
    both = {**base, "content_base64": "JVBERg==", "upload_id": "up_x"}
    assert "content_base64 или upload_id" in error_text(call_tool(server, "create_document_body_base64", both))
    assert payload(call_tool(server, "create_document_body_base64", {**base, "content_base64": "JVBERg=="}))["mode"] == "preview"


def test_upload_not_committed_is_rejected():
    server, _, _ = make()
    upload_id = payload(call_tool(server, "begin_upload", {}))["upload_id"]
    payload(call_tool(server, "upload_chunk", {"upload_id": upload_id, "index": 0, "chunk_base64": "JVBERg=="}))
    args = {"document_id": 16800, "upload_id": upload_id, "extension": "pdf", "on_behalf_of": "Босов"}
    assert "commit_upload" in error_text(call_tool(server, "create_document_body_base64", args))


def test_list_document_versions_is_read_only_and_available_without_write():
    server, _, _ = make(write=False)
    tool = {t.name: t for t in list_tools(server)}["list_document_versions"]
    assert tool.annotations.read_only_hint is True
    result = payload(call_tool(server, "list_document_versions", {"document_id": 16800}))
    assert result["next_version_number"] == 3 and [v["number"] for v in result["versions"]] == [2, 1]
