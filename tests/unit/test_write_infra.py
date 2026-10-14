import io
import json
import zipfile
from xml.etree import ElementTree as ET

import pytest

from src.mcp_server.write_guard import ConfirmTokens, WriteAudit
from src.services.docx_builder import MAX_TEXT_CHARS, build_docx, paragraphs

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def doc_paragraphs(data: bytes) -> list[str]:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        assert set(z.namelist()) == {"[Content_Types].xml", "_rels/.rels", "word/document.xml"}
        root = ET.fromstring(z.read("word/document.xml"))
    out = []
    for p in root.iter(W + "p"):
        parts = []
        for node in p.iter():
            if node.tag == W + "t":
                parts.append(node.text or "")
            elif node.tag == W + "br":
                parts.append("\n")
        out.append("".join(parts))
    return out


def test_docx_paragraphs_and_escaping():
    data = build_docx("Уважаемый коллега!\n\nПервая строка\nвторая <b>&\x01\n\n\n  \n\nПодпись")
    assert doc_paragraphs(data) == ["Уважаемый коллега!", "Первая строка\nвторая <b>&", "Подпись"]
    assert build_docx("текст") == build_docx("текст")  # воспроизводимо


def test_docx_errors():
    with pytest.raises(ValueError):
        build_docx(" \n\n ")
    with pytest.raises(ValueError):
        build_docx("а" * (MAX_TEXT_CHARS + 1))
    assert paragraphs("a\r\n\r\nb") == ["a", "b"]


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def test_tokens_roundtrip_and_binding():
    clock = Clock()
    tokens = ConfirmTokens(secret=b"s" * 32, ttl=600, clock=clock)
    plan = {"subject": "Письмо", "to": {"id": 1}}
    token = tokens.issue("create_outgoing_letter", plan, "fp1")
    assert tokens.check(token, "create_outgoing_letter", dict(plan), "fp1") is None
    assert "параметры изменились" in tokens.check(token, "create_outgoing_letter", {**plan, "subject": "Другое"}, "fp1")
    assert "параметры изменились" in tokens.check(token, "create_task", plan, "fp1")
    assert "параметры изменились" in tokens.check(token, "create_outgoing_letter", plan, "fp2")
    assert "повреждён" in tokens.check("мусор", "create_outgoing_letter", plan, "fp1")
    assert "повреждён" in tokens.check("", "create_outgoing_letter", plan, "fp1")
    clock.now += 601
    assert "истёк" in tokens.check(token, "create_outgoing_letter", plan, "fp1")
    # Другой процесс (другой секрет) — токен не подходит.
    assert ConfirmTokens(clock=clock).check(token, "create_outgoing_letter", plan, "fp1") is not None


def test_tokens_forged_expiry_rejected():
    clock = Clock()
    tokens = ConfirmTokens(secret=b"k" * 32, clock=clock)
    token = tokens.issue("t", {"a": 1}, "fp")
    expires, mac = token.split(".")
    assert tokens.check(f"{int(expires) + 10000}.{mac}", "t", {"a": 1}, "fp") is not None


def test_audit_writes_jsonl(tmp_path):
    path = tmp_path / "sub" / "writes.log"
    audit = WriteAudit(str(path))
    audit.record("create_outgoing_letter", "u1", "Иванов", {"document_id": 5, "url": "x", "warnings": ["w"]})
    audit.record("create_task", None, None, {"task_id": 7, "started": True})
    lines = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()]
    assert lines[0]["created"] == {"document_id": 5} and lines[0]["warnings"] == 1 and lines[0]["on_behalf_of"] == "Иванов"
    assert lines[1]["created"] == {"task_id": 7, "started": True}
    WriteAudit(None).record("x", None, None, {})  # без файла — только лог
