import base64
import hashlib

import pytest

from src.mcp_server.transfers import CHUNK_BYTES, TransferError, TransferStore


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def test_download_chunks_concatenate_into_full_base64():
    data = bytes(range(256)) * 10  # 2560 байт
    store = TransferStore(chunk_bytes=999)
    info = store.put_download("fp", data, {"document_id": 1})
    assert info["total_chunks"] == 3 and info["bytes"] == 2560
    assert info["sha256"] == hashlib.sha256(data).hexdigest()
    parts = [store.download_chunk("fp", info["download_id"], i) for i in range(3)]
    assert [p["last"] for p in parts] == [False, False, True]
    assert "".join(p["chunk_base64"] for p in parts) == b64(data)
    assert base64.b64decode(parts[1]["chunk_base64"]) == data[999:1998]  # каждая часть — самостоятельный base64


def test_default_chunk_is_one_megabyte_of_base64():
    assert CHUNK_BYTES % 3 == 0 and CHUNK_BYTES * 4 // 3 == 1024 * 1024


def test_download_is_bound_to_owner_and_index_checked():
    store = TransferStore(chunk_bytes=3)
    info = store.put_download("fp", b"abcdef", {})
    with pytest.raises(TransferError, match="не найдена"):
        store.download_chunk("other", info["download_id"], 0)
    with pytest.raises(TransferError, match="0–1"):
        store.download_chunk("fp", info["download_id"], 2)


def test_upload_commit_and_take():
    data = b"%PDF-1.4 " + bytes(range(200))
    text = b64(data)
    store = TransferStore()
    upload_id = store.begin_upload("fp")["upload_id"]
    for index, start in enumerate(range(0, len(text), 50)):  # режем строку base64 где угодно
        store.upload_chunk("fp", upload_id, index, text[start:start + 50])
    total = (len(text) + 49) // 50
    result = store.commit_upload("fp", upload_id, total)
    assert result["bytes"] == len(data) and result["sha256"] == hashlib.sha256(data).hexdigest()
    assert store.take_upload("fp", upload_id) == data
    with pytest.raises(TransferError, match="уже завершена"):
        store.upload_chunk("fp", upload_id, 0, "AAAA")


def test_upload_chunk_resend_replaces_and_missing_chunks_reported():
    store = TransferStore()
    upload_id = store.begin_upload("fp")["upload_id"]
    store.upload_chunk("fp", upload_id, 0, "zzzz")
    store.upload_chunk("fp", upload_id, 0, b64(b"abc"))
    store.upload_chunk("fp", upload_id, 2, b64(b"ghi"))
    with pytest.raises(TransferError, match="нет частей: 1"):
        store.commit_upload("fp", upload_id, 3)
    store.upload_chunk("fp", upload_id, 1, b64(b"def"))
    assert store.commit_upload("fp", upload_id, 3)["bytes"] == 9
    assert store.take_upload("fp", upload_id) == b"abcdefghi"


def test_upload_rejects_garbage_and_uncommitted_take():
    store = TransferStore()
    upload_id = store.begin_upload("fp")["upload_id"]
    with pytest.raises(TransferError, match="base64"):
        store.upload_chunk("fp", upload_id, 0, "не base64!")
    store.upload_chunk("fp", upload_id, 0, "QUJD")
    with pytest.raises(TransferError, match="commit_upload"):
        store.take_upload("fp", upload_id)
    with pytest.raises(TransferError, match="не найдена"):
        store.take_upload("other", upload_id)


def test_upload_size_limit():
    store = TransferStore(max_file_bytes=6)
    upload_id = store.begin_upload("fp")["upload_id"]
    store.upload_chunk("fp", upload_id, 0, b64(b"abcdef"))
    with pytest.raises(TransferError, match="больше"):
        store.upload_chunk("fp", upload_id, 1, b64(b"ghi"))


def test_ttl_expires_inactive_transfers():
    clock = Clock()
    store = TransferStore(ttl=60, clock=clock)
    info = store.put_download("fp", b"abc", {})
    clock.now += 30
    store.download_chunk("fp", info["download_id"], 0)  # обращение продлевает срок
    clock.now += 50
    store.download_chunk("fp", info["download_id"], 0)
    clock.now += 61
    with pytest.raises(TransferError, match="не найдена"):
        store.download_chunk("fp", info["download_id"], 0)


def test_limits_on_active_transfers_and_memory():
    store = TransferStore(max_active_per_owner=2, max_total_bytes=10)
    store.begin_upload("fp")
    store.put_download("fp", b"12345", {})
    with pytest.raises(TransferError, match="abort_transfer"):
        store.begin_upload("fp")
    store.begin_upload("other")
    with pytest.raises(TransferError, match="памят"):
        store.put_download("other", b"123456", {})


def test_abort_frees_transfer():
    store = TransferStore(max_active_per_owner=1)
    info = store.put_download("fp", b"abc", {})
    assert store.abort("other", info["download_id"]) is False
    assert store.abort("fp", info["download_id"]) is True
    store.begin_upload("fp")  # место освободилось
