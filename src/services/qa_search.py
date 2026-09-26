import time
from collections.abc import Callable
from typing import Any

from src.models.schemas import QAAnswer, QASearchArea, QASource
from src.services.directum_client import sanitize_error_detail, unwrap_method_result

POLL_INTERVAL_SECONDS = 2
MAX_FRAGMENTS = 2
FRAGMENT_CHARS = 500
MAX_ERROR_CHARS = 300
# Статусы задачи RAG-сервиса (Sungero.RagExtensions) — сравниваем без регистра и пробелов.
COMPLETED_STATUSES = {"completed", "done", "success", "finished"}
ERROR_STATUSES = {"error", "failed", "failure", "cancelled", "canceled"}


def _safe(text: Any) -> str:
    return sanitize_error_detail(str(text or ""))[:MAX_ERROR_CHARS]


class QASearchService:
    """Вопросно-ответный поиск RX (QASearchCore): задача в RAG-сервисе и опрос её результата. Права учитывает RX."""

    def __init__(
        self,
        client: Any,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.client = client
        self._sleep = sleep
        self._clock = clock

    def areas(self) -> list[QASearchArea]:
        rows = self.client.call_function("QASearchCore/GetReadySearchAreas") or []
        return [QASearchArea(id=int(row["Id"]), name=row.get("Name") or "") for row in rows]

    def ask(self, question: str, area_ids: list[int] | None = None, wait_seconds: float = 30) -> QAAnswer:
        ids = list(area_ids or [area.id for area in self.areas()])
        if not ids:
            return QAAnswer(
                status="unavailable",
                message="Поиск по документам (QASearch) на стенде не настроен: нет готовых областей поиска.",
            )
        started = unwrap_method_result(
            self.client.post("QASearchCore/CreateSearchTask", {"question": question, "searchAreaIds": ids})
        ) or {}
        task = started.get("Task") or {}
        if not task.get("Id"):
            reason = _safe(started.get("ErrorMessage")) or "сервис поиска не ответил"
            return QAAnswer(status="error", message=f"Не удалось запустить поиск: {reason}")
        return self._wait(str(task["Id"]), wait_seconds)

    def result(self, task_id: str) -> QAAnswer:
        return self._wait(task_id, 0)

    def _wait(self, task_id: str, wait_seconds: float) -> QAAnswer:
        deadline = self._clock() + wait_seconds
        while True:
            info = unwrap_method_result(self.client.post("QASearchCore/GetSearchTaskInfo", {"taskId": task_id})) or {}
            answer = self._interpret(task_id, info)
            if answer.status != "in_progress" or self._clock() >= deadline:
                return answer
            self._sleep(POLL_INTERVAL_SECONDS)

    def _interpret(self, task_id: str, info: dict[str, Any]) -> QAAnswer:
        status = str(info.get("Status") or "").replace(" ", "").lower()
        result = info.get("Result")
        if info.get("ErrorMessage") or status in ERROR_STATUSES:
            reason = _safe(info.get("ErrorMessage") or (result or {}).get("ErrorMessage")) or "без описания"
            return QAAnswer(status="error", task_id=task_id, message=f"Поиск завершился ошибкой: {reason}")
        if result or status in COMPLETED_STATUSES:
            result = result or {}
            return QAAnswer(
                status="completed",
                task_id=task_id,
                answer=result.get("Answer") or "",
                score=result.get("Score"),
                search_area=info.get("SearchAreaName") or None,
                sources=[self._source(entity) for entity in result.get("Entities") or []],
                message="" if result.get("Answer") else "Ответ не найден в доступных документах.",
            )
        return QAAnswer(
            status="in_progress",
            task_id=task_id,
            message="Поиск ещё идёт: вызови get_ask_documents_result с этим task_id через несколько секунд.",
        )

    def _source(self, entity: dict[str, Any]) -> QASource:
        link = entity.get("EntityLink")
        entity_id = str(entity.get("EntityId") or "")
        if not (isinstance(link, str) and link.startswith("http")):
            link = (
                self.client.build_document_card_url(int(entity_id))
                if entity_id.isdigit() and hasattr(self.client, "build_document_card_url")
                else None
            )
        chunks = sorted(entity.get("Chunks") or [], key=lambda chunk: chunk.get("Score") or 0, reverse=True)
        return QASource(
            name=entity.get("EntityName") or "",
            url=link,
            extension=entity.get("EntityExtension") or None,
            fragments=[(chunk.get("Text") or "")[:FRAGMENT_CHARS] for chunk in chunks[:MAX_FRAGMENTS]],
        )
