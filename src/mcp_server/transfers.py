"""Передача файлов частями: выгрузка тела документа (download_chunk) и загрузка файла (upload_chunk).

Нужна клиентам с лимитом на размер одного вызова или ответа (песочницы агентов: ~5 МБ на строку). Части лежат в
памяти процесса: после перезапуска теряются, при нескольких экземплярах mcpOGV клиент должен попадать в один.
Каждая передача принадлежит учётке, которая её начала (отпечаток кредов): чужой id выглядит как «не найдена».
"""

import base64
import binascii
import hashlib
import math
import re
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

# Часть выгрузки — кратна 3 байтам: base64 каждой части без «=» в середине, склейка строк даёт base64 всего файла.
CHUNK_BYTES = 768 * 1024  # = 1 МБ base64
TTL_SECONDS = 1800
MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_ACTIVE_PER_OWNER = 10
MAX_TOTAL_BYTES = 256 * 1024 * 1024

_BASE64_CHARS = re.compile(r"^[A-Za-z0-9+/=]*$")


class TransferError(Exception):
    """Понятная агенту ошибка передачи частями (по-русски)."""


@dataclass
class _Transfer:
    owner: str
    kind: str  # download | upload
    touched: float
    data: bytes = b""
    chunks: dict[int, str] = field(default_factory=dict)
    committed: bool = False

    @property
    def size(self) -> int:
        return len(self.data) + sum(len(c) for c in self.chunks.values())


class TransferStore:
    def __init__(
        self,
        chunk_bytes: int = CHUNK_BYTES,
        ttl: int = TTL_SECONDS,
        max_file_bytes: int = MAX_FILE_BYTES,
        max_active_per_owner: int = MAX_ACTIVE_PER_OWNER,
        max_total_bytes: int = MAX_TOTAL_BYTES,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.chunk_bytes = chunk_bytes
        self.ttl = ttl
        self.max_file_bytes = max_file_bytes
        self.max_active_per_owner = max_active_per_owner
        self.max_total_bytes = max_total_bytes
        self._clock = clock
        self._items: dict[str, _Transfer] = {}
        self._lock = threading.Lock()

    # --- выгрузка ---

    def put_download(self, owner: str, data: bytes, meta: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self._admit(owner, len(data))
            transfer_id = "dl_" + secrets.token_urlsafe(12)
            self._items[transfer_id] = _Transfer(owner, "download", self._clock(), data=data)
        return {**meta, "transfer": "chunked", "download_id": transfer_id, "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(), "total_chunks": max(1, math.ceil(len(data) / self.chunk_bytes)),
                "chunk_base64_chars": self.chunk_bytes * 4 // 3}

    def download_chunk(self, owner: str, download_id: str, index: int) -> dict[str, Any]:
        with self._lock:
            transfer = self._get(owner, download_id, "download")
            total = max(1, math.ceil(len(transfer.data) / self.chunk_bytes))
            if not 0 <= index < total:
                raise TransferError(f"Номер части вне диапазона: допустимо 0–{total - 1}.")
            piece = transfer.data[index * self.chunk_bytes:(index + 1) * self.chunk_bytes]
        return {"download_id": download_id, "index": index, "total_chunks": total, "last": index == total - 1,
                "chunk_base64": base64.b64encode(piece).decode("ascii")}

    # --- загрузка ---

    def begin_upload(self, owner: str) -> dict[str, Any]:
        with self._lock:
            self._admit(owner, 0)
            transfer_id = "up_" + secrets.token_urlsafe(12)
            self._items[transfer_id] = _Transfer(owner, "upload", self._clock())
        return {"upload_id": transfer_id, "chunk_base64_chars": self.chunk_bytes * 4 // 3,
                "max_bytes": self.max_file_bytes}

    def upload_chunk(self, owner: str, upload_id: str, index: int, chunk: str) -> dict[str, Any]:
        text = re.sub(r"\s+", "", chunk or "")
        if index == 0 and text.startswith("data:") and "," in text:
            text = text.split(",", 1)[1]  # data:application/pdf;base64,…
        if not text or not _BASE64_CHARS.match(text):
            raise TransferError(f"Часть {index}: ожидается непустой кусок строки base64.")
        if index < 0:
            raise TransferError("Номер части — с 0.")
        with self._lock:
            transfer = self._get(owner, upload_id, "upload")
            if transfer.committed:
                raise TransferError("Загрузка уже завершена (commit_upload). Для нового файла — begin_upload.")
            previous = len(transfer.chunks.get(index, ""))
            if transfer.size - previous + len(text) > 4 * math.ceil(self.max_file_bytes / 3):
                raise TransferError(f"Файл больше {self.max_file_bytes // (1024 * 1024)} МБ.")
            self._ensure_memory(len(text) - previous)
            transfer.chunks[index] = text
            return {"upload_id": upload_id, "index": index, "received_chunks": len(transfer.chunks),
                    "received_base64_chars": transfer.size}

    def commit_upload(self, owner: str, upload_id: str, total_chunks: int) -> dict[str, Any]:
        with self._lock:
            transfer = self._get(owner, upload_id, "upload")
            if transfer.committed:
                return self._upload_summary(upload_id, transfer.data)
            missing = [str(i) for i in range(total_chunks) if i not in transfer.chunks]
            extra = sorted(i for i in transfer.chunks if i >= total_chunks)
            if total_chunks < 1 or missing or extra:
                details = f"нет частей: {', '.join(missing[:20])}" if missing else f"лишние части: {extra[:20]}"
                raise TransferError(f"Загрузка не собрана — {details}. Дошлите upload_chunk и повторите commit_upload.")
            text = "".join(transfer.chunks[i] for i in range(total_chunks))
            try:
                data = base64.b64decode(text, validate=True)
            except (ValueError, binascii.Error):
                raise TransferError("Склеенные части — не base64: проверьте порядок и полноту частей.") from None
            if not data:
                raise TransferError("Пустой файл.")
            transfer.data, transfer.chunks, transfer.committed = data, {}, True
            return self._upload_summary(upload_id, data)

    def take_upload(self, owner: str, upload_id: str) -> bytes:
        """Содержимое завершённой загрузки (остаётся в хранилище до abort или истечения срока)."""
        with self._lock:
            transfer = self._get(owner, upload_id, "upload")
            if not transfer.committed:
                raise TransferError("Загрузка не завершена: сначала commit_upload.")
            return transfer.data

    def abort(self, owner: str, transfer_id: str) -> bool:
        with self._lock:
            transfer = self._items.get(transfer_id)
            if transfer is None or transfer.owner != owner:
                return False
            del self._items[transfer_id]
            return True

    # --- внутреннее (под self._lock) ---

    @staticmethod
    def _upload_summary(upload_id: str, data: bytes) -> dict[str, Any]:
        return {"upload_id": upload_id, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}

    def _get(self, owner: str, transfer_id: str, kind: str) -> _Transfer:
        self._purge()
        transfer = self._items.get(transfer_id)
        if transfer is None or transfer.owner != owner or transfer.kind != kind:
            what = "Выгрузка" if kind == "download" else "Загрузка"
            raise TransferError(f"{what} {transfer_id} не найдена: истёк срок ({self.ttl // 60} мин без обращений), "
                                "отменена или сервер перезапущен. Начните заново.")
        transfer.touched = self._clock()
        return transfer

    def _admit(self, owner: str, size: int) -> None:
        self._purge()
        if sum(1 for t in self._items.values() if t.owner == owner) >= self.max_active_per_owner:
            raise TransferError(f"Не больше {self.max_active_per_owner} незавершённых передач. Освободите ненужные "
                                "через abort_transfer.")
        self._ensure_memory(size)

    def _ensure_memory(self, extra: int) -> None:
        if sum(t.size for t in self._items.values()) + extra > self.max_total_bytes:
            raise TransferError("Сервер занят другими передачами файлов: не хватает памяти. Повторите позже.")

    def _purge(self) -> None:
        deadline = self._clock() - self.ttl
        for transfer_id in [k for k, t in self._items.items() if t.touched < deadline]:
            del self._items[transfer_id]
