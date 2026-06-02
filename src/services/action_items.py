from typing import Any

from datetime import date, datetime, timedelta, timezone

from src.models.schemas import (
    ActionItemCreateRequest,
    ActionItemCreateResult,
    CounterpartySummary,
    DocumentSummary,
    DocumentsByCounterpartyResult,
    EmployeeSummary,
    TaskCreateRequest,
    TaskCreateResult,
)
from src.services.directum_client import DirectumClient, DirectumError


EMPLOYEE_QUERY_STRIP_CHARS = " \t\r\n.,;:!?\"'\u00ab\u00bb"

# \u041e\u0431\u0438\u0445\u043e\u0434\u043d\u044b\u0435 \u0441\u043e\u043a\u0440\u0430\u0449\u0435\u043d\u0438\u044f \u043e\u0440\u0433\u0430\u043d\u0438\u0437\u0430\u0446\u0438\u0439 \u2192 \u043f\u043e\u0434\u0441\u0442\u0440\u043e\u043a\u0430 \u0434\u043b\u044f contains(Name,...).
COUNTERPARTY_ABBREVIATIONS = {"\u043c\u0446": "\u041c\u0438\u043d\u0446\u0438\u0444\u0440"}

# \u041d\u0430\u0431\u043e\u0440\u044b \u043f\u0438\u0441\u0435\u043c \u043f\u043e \u043d\u0430\u043f\u0440\u0430\u0432\u043b\u0435\u043d\u0438\u044e.
LETTER_ENTITY_SETS = {
    "incoming": "IIncomingLetters",
    "outgoing": "IOutgoingLetters",
}

# \u0427\u0430\u0441\u043e\u0432\u043e\u0439 \u043f\u043e\u044f\u0441 \u0441\u0442\u0435\u043d\u0434\u0430 (\u0434\u0430\u0442\u044b \u0432 Directum \u0445\u0440\u0430\u043d\u044f\u0442\u0441\u044f \u043a\u0430\u043a \u043b\u043e\u043a\u0430\u043b\u044c\u043d\u0430\u044f \u043f\u043e\u043b\u043d\u043e\u0447\u044c +04:00).
# OData \u0442\u0440\u0435\u0431\u0443\u0435\u0442 timezone-offset \u0432 DateTimeOffset-\u043b\u0438\u0442\u0435\u0440\u0430\u043b\u0435 \u0444\u0438\u043b\u044c\u0442\u0440\u0430.
DIRECTUM_TZ = timezone(timedelta(hours=4))

# \u041d\u0430\u0431\u043e\u0440 \u043a\u043e\u043d\u0442\u0440\u0430\u0433\u0435\u043d\u0442\u043e\u0432-\u043e\u0440\u0433\u0430\u043d\u0438\u0437\u0430\u0446\u0438\u0439 (\u043f\u0440\u043e\u0432\u0435\u0440\u0435\u043d\u043e \u0432\u0436\u0438\u0432\u0443\u044e: ICounterparties \u043e\u0442\u0434\u0430\u0451\u0442 400,
# \u0440\u0430\u0431\u043e\u0447\u0438\u0439 \u043d\u0430\u0431\u043e\u0440 \u2014 ICompanies).
COUNTERPARTY_ENTITY_SET = "ICompanies"

# \u0422\u0438\u043f\u0438\u0437\u0438\u0440\u043e\u0432\u0430\u043d\u043d\u044b\u0435 \u043d\u0430\u0431\u043e\u0440\u044b \u0434\u043e\u043a\u0443\u043c\u0435\u043d\u0442\u043e\u0432 \u0438 \u043d\u0430\u0432\u0438\u0433\u0430\u0446\u0438\u044f \u043d\u0430 \u043a\u043e\u043d\u0442\u0440\u0430\u0433\u0435\u043d\u0442\u0430.
# \u041f\u0440\u043e\u0432\u0435\u0440\u0435\u043d\u043e \u0432\u0436\u0438\u0432\u0443\u044e \u043d\u0430 \u0441\u0442\u0435\u043d\u0434\u0435 ogvsale253: \u0444\u0438\u043b\u044c\u0442\u0440 Counterparty/Id eq X \u0440\u0430\u0431\u043e\u0442\u0430\u0435\u0442,
# IContractualDocuments \u2014 \u0431\u0430\u0437\u043e\u0432\u044b\u0439 \u0442\u0438\u043f (\u0432\u043a\u043b\u044e\u0447\u0430\u0435\u0442 IContracts/\u0434\u043e\u0433\u043e\u0432\u043e\u0440\u043d\u044b\u0435),
# \u043f\u043e\u044d\u0442\u043e\u043c\u0443 \u0434\u043e\u0441\u0442\u0430\u0442\u043e\u0447\u043d\u043e \u0435\u0433\u043e + \u0443\u0447\u0451\u0442\u043d\u044b\u0435 \u0434\u043e\u043a\u0443\u043c\u0435\u043d\u0442\u044b. \u0414\u0443\u0431\u043b\u0438\u043a\u0430\u0442\u044b \u043f\u043e Id \u043e\u0442\u0441\u0435\u043a\u0430\u044e\u0442\u0441\u044f.
COUNTERPARTY_DOCUMENT_SETS: tuple[tuple[str, str], ...] = (
    ("IContractualDocuments", "Counterparty"),
    ("IIncomingInvoices", "Counterparty"),
    ("IUniversalTransferDocuments", "Counterparty"),
    # Письма ссылаются на контрагента через навигацию Correspondent, а не
    # Counterparty (проверено вживую на ogvsale253: IIncomingLetters/IOutgoingLetters,
    # фильтр Correspondent/Id eq X работает).
    ("IIncomingLetters", "Correspondent"),
    ("IOutgoingLetters", "Correspondent"),
)


class ActionItemService:
    def __init__(self, client: DirectumClient):
        self.client = client

    def search_employee(self, query: str, top: int = 10) -> list[EmployeeSummary]:
        cleaned = self._clean_employee_query(query)
        if not cleaned:
            return []

        rows = self._query_employees(cleaned, top)
        if not rows:
            for token in self._fallback_name_tokens(cleaned):
                rows = self._query_employees(token, top)
                if rows:
                    break
        return [
            EmployeeSummary(
                id=int(row["Id"]),
                name=row["Name"],
                status=row.get("Status"),
            )
            for row in rows
        ]

    def _query_employees(self, query: str, top: int) -> list[dict[str, Any]]:
        escaped_query = query.replace("'", "''")
        return self.client.query(
            "IEmployees",
            filter_=f"contains(Name,'{escaped_query}') and Status eq 'Active'",
            select="Id,Name,Status",
            top=top,
        )

    def _clean_employee_query(self, query: str) -> str:
        return query.strip(EMPLOYEE_QUERY_STRIP_CHARS)

    def _fallback_name_tokens(self, query: str) -> list[str]:
        tokens = [token.strip(EMPLOYEE_QUERY_STRIP_CHARS) for token in query.split()]
        return [token for token in reversed(tokens) if len(token) > 1 and token != query]

    def _fallback_counterparty_tokens(self, query: str) -> list[str]:
        # Для контрагентов специфичность важнее позиции: длинные токены
        # («Минцифры») пробуем раньше коротких («РФ»), чтобы шум вроде «РФ»
        # не совпал с чужой организацией. При равной длине — порядок справа налево.
        tokens = [token.strip(EMPLOYEE_QUERY_STRIP_CHARS) for token in query.split()]
        # Раскрытые аббревиатуры («МЦ» → «Минцифр») пробуем первыми — они
        # специфичнее любого исходного токена.
        expansions: list[str] = []
        for token in tokens:
            expanded = COUNTERPARTY_ABBREVIATIONS.get(token.lower())
            if expanded and expanded not in expansions:
                expansions.append(expanded)
        candidates = [token for token in tokens if len(token) > 1 and token != query]
        indexed = list(enumerate(candidates))
        indexed.sort(key=lambda item: (-len(item[1]), -item[0]))
        ordered = [token for _, token in indexed]
        return expansions + [token for token in ordered if token not in expansions]

    def search_documents(self, query: str, top: int = 10) -> list[DocumentSummary]:
        cleaned = self._clean_employee_query(query)
        if not cleaned:
            return []

        rows = self._query_documents(cleaned, top)
        if not rows:
            for token in self._fallback_document_tokens(cleaned):
                rows = self._query_documents(token, top)
                if rows:
                    break
        return [
            DocumentSummary(
                id=int(row["Id"]),
                name=row.get("Name") or "",
                subject=row.get("Subject"),
                registration_number=row.get("RegistrationNumber"),
                registration_date=row.get("RegistrationDate"),
            )
            for row in rows
        ]

    def get_document(self, document_id: int) -> DocumentSummary | None:
        rows = self.client.query(
            "IOfficialDocuments",
            filter_=f"Id eq {int(document_id)}",
            select="Id,Name,Subject,RegistrationNumber,RegistrationDate",
            top=1,
        )
        if not rows:
            return None
        return self._document_summary(rows[0], "IOfficialDocuments")

    def _query_documents(self, query: str, top: int) -> list[dict[str, Any]]:
        escaped_query = query.replace("'", "''")
        return self.client.query(
            "IOfficialDocuments",
            filter_=(
                f"(contains(Name,'{escaped_query}') or contains(Subject,'{escaped_query}')) "
                "and RegistrationDate ne null"
            ),
            select="Id,Name,Subject,RegistrationNumber,RegistrationDate",
            orderby="RegistrationDate desc",
            top=top,
        )

    def search_counterparty(self, query: str, top: int = 10) -> list[CounterpartySummary]:
        cleaned = self._clean_employee_query(query)
        if not cleaned:
            return []

        rows = self._query_counterparties(cleaned, top)
        if not rows:
            for token in self._fallback_counterparty_tokens(cleaned):
                rows = self._query_counterparties(token, top)
                if rows:
                    break
        return [
            CounterpartySummary(
                id=int(row["Id"]),
                name=row.get("Name") or "",
                tin=row.get("TIN"),
            )
            for row in rows
        ]

    def _query_counterparties(self, query: str, top: int) -> list[dict[str, Any]]:
        escaped_query = query.replace("'", "''")
        return self.client.query(
            COUNTERPARTY_ENTITY_SET,
            filter_=f"contains(Name,'{escaped_query}')",
            select="Id,Name,TIN",
            top=top,
        )

    def search_documents_by_counterparty(
        self, query: str, top: int = 20
    ) -> DocumentsByCounterpartyResult:
        matches = self.search_counterparty(query, top=5)
        if not matches:
            return DocumentsByCounterpartyResult(
                counterparty=None,
                documents=[],
                message="Контрагент не найден.",
            )
        # Берём первое совпадение — права доступа Directum не дадут прочитать лишнее.
        counterparty = matches[0]
        documents = self._documents_for_counterparty(counterparty.id, top)
        return DocumentsByCounterpartyResult(counterparty=counterparty, documents=documents)

    def _documents_for_counterparty(self, counterparty_id: int, top: int) -> list[DocumentSummary]:
        collected: list[DocumentSummary] = []
        seen_ids: set[int] = set()
        for entity_set, nav in COUNTERPARTY_DOCUMENT_SETS:
            try:
                rows = self.client.query(
                    entity_set,
                    filter_=f"{nav}/Id eq {counterparty_id}",
                    select="Id,Name,Subject,RegistrationNumber,RegistrationDate",
                    orderby="RegistrationDate desc",
                    top=top,
                )
            except DirectumError:
                # Набор может не поддерживать эту навигацию в данной конфигурации — пропускаем.
                continue
            for row in rows:
                document_id = int(row["Id"])
                # IContractualDocuments — базовый тип, его подтипы могут вернуться
                # повторно из других наборов: отсекаем дубликаты по Id.
                if document_id in seen_ids:
                    continue
                seen_ids.add(document_id)
                collected.append(self._document_summary(row, entity_set))
        collected.sort(key=self._registration_sort_key, reverse=True)
        return collected[:top]

    def list_letters(
        self,
        direction: str,
        date_from: str | None = None,
        date_to: str | None = None,
        top: int = 50,
    ) -> list[DocumentSummary]:
        entity_set = LETTER_ENTITY_SETS.get((direction or "").strip().lower())
        if entity_set is None:
            raise DirectumError(
                f"Unknown letters direction '{direction}'; expected 'incoming' or 'outgoing'"
            )

        # Только официально зарегистрированные письма (черновики не показываем).
        filters = ["RegistrationState eq 'Registered'"]
        start = self._normalize_filter_datetime(date_from)
        end = self._normalize_filter_datetime(date_to, end_of_day=True)
        if start:
            filters.append(f"RegistrationDate ge {start}")
        if end:
            filters.append(f"RegistrationDate le {end}")

        rows = self.client.query(
            entity_set,
            filter_=" and ".join(filters),
            select="Id,Name,Subject,RegistrationNumber,RegistrationDate",
            orderby="RegistrationDate desc",
            top=top,
        )
        return [self._document_summary(row, entity_set) for row in rows]

    def _normalize_filter_datetime(self, value: str | None, end_of_day: bool = False) -> str | None:
        """ISO-дату/время → безопасный OData-литерал. Мусор отбрасываем (анти-инъекция)."""
        if not value or not value.strip():
            return None
        text = value.strip().replace("Z", "+00:00")
        had_time = "T" in text or " " in text
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            try:
                day = date.fromisoformat(text)
            except ValueError:
                return None
            parsed = datetime(day.year, day.month, day.day)
            had_time = False
        if not had_time and end_of_day:
            parsed = parsed.replace(hour=23, minute=59, second=59)
        # OData-фильтр требует timezone-offset; наивную дату трактуем как время стенда.
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=DIRECTUM_TZ)
        return parsed.isoformat()

    def _document_summary(self, row: dict[str, Any], entity_set: str) -> DocumentSummary:
        return DocumentSummary(
            id=int(row["Id"]),
            name=row.get("Name") or "",
            subject=row.get("Subject"),
            registration_number=row.get("RegistrationNumber"),
            registration_date=row.get("RegistrationDate"),
            url=self._document_url(entity_set, int(row["Id"])),
        )

    def _document_url(self, entity_set: str, document_id: int) -> str | None:
        if hasattr(self.client, "build_url"):
            return self.client.build_url(f"{entity_set}({document_id})")
        return None

    @staticmethod
    def _registration_sort_key(document: DocumentSummary) -> datetime:
        if document.registration_date is None:
            return datetime.min.replace(tzinfo=timezone.utc)
        value = document.registration_date
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value

    def _fallback_document_tokens(self, query: str) -> list[str]:
        tokens = [token.strip(EMPLOYEE_QUERY_STRIP_CHARS) for token in query.split()]
        ignored = {"для", "документы", "документ", "проверь", "проверить", "подготовь", "подготовьте", "подготовить"}
        fallback: list[str] = []
        for token in reversed(tokens):
            lowered = token.lower()
            if lowered == "мц":
                fallback.append("Минцифр")
                continue
            if len(token) <= 3 or lowered in ignored:
                continue
            fallback.append(token)
            if len(token) > 5 and lowered[-1:] in {"а", "е", "у", "ы", "я", "ю"}:
                fallback.append(token[:-1])
        return fallback

    def create_action_item(self, request: ActionItemCreateRequest) -> ActionItemCreateResult:
        payload = self._payload(request)
        if not request.confirm:
            return ActionItemCreateResult(
                mode="preview",
                payload=payload,
                success=True,
                directum_id=None,
                message="Preview generated; confirm to create the action item.",
            )

        document_id = request.document_id or self._resolve_document_id(request)
        if document_id is None:
            raise DirectumError("Action item creation requires document_id for Directum RX")

        payload = self._payload(request, document_id=document_id)
        response = self.client.post("RecordManagement/CreateActionItemExecution", payload)
        directum_id = self._directum_id(response)
        self.client.post("Docflow/StartTask", {"taskId": directum_id})
        url = self._action_item_url(directum_id)
        return ActionItemCreateResult(
            mode="created",
            payload=payload,
            success=True,
            directum_id=directum_id,
            url=url,
            message="Action item created.",
        )

    def _resolve_document_id(self, request: ActionItemCreateRequest) -> int | None:
        documents = self.search_documents(f"{request.subject} {request.action_text}", top=1)
        return documents[0].id if documents else None

    def _payload(self, request: ActionItemCreateRequest, document_id: int | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "documentId": request.document_id if document_id is None else document_id,
            "assigneeId": request.performer_id,
            "isUnderControl": False,
            "supervisorId": None,
            "coassigneeId": None,
            "deadline": None,
            "activeText": request.action_text,
        }
        if request.deadline is not None:
            if request.deadline.tzinfo is None or request.deadline.utcoffset() is None:
                raise DirectumError("Action item deadline must include timezone")
            payload["deadline"] = request.deadline.isoformat()
        return payload

    def _directum_id(self, response: dict[str, Any]) -> int:
        raw_id = response.get("value", response.get("Id"))
        if isinstance(raw_id, int):
            return raw_id
        if isinstance(raw_id, str) and raw_id.isdecimal():
            return int(raw_id)
        raise DirectumError("Directum returned an invalid action item id")

    def _action_item_url(self, directum_id: int) -> str | None:
        entity_path = f"IActionItemExecutionTasks({directum_id})"
        if hasattr(self.client, "build_client_card_url"):
            return self.client.build_client_card_url(entity_path)
        if hasattr(self.client, "build_url"):
            return self.client.build_url(entity_path)
        return None

    def create_task(self, request: TaskCreateRequest) -> TaskCreateResult:
        payload = self._task_payload(request)
        if not request.confirm:
            return TaskCreateResult(
                mode="preview",
                payload=payload,
                success=True,
                directum_id=None,
                message="Preview generated; confirm to create the task.",
            )

        response = self.client.post("Docflow/CreateSimpleTask", payload)
        directum_id = self._directum_id(response)
        self.client.post("Docflow/StartTask", {"taskId": directum_id})
        url = self._task_url(directum_id)
        return TaskCreateResult(
            mode="created",
            payload=payload,
            success=True,
            directum_id=directum_id,
            url=url,
            message="Task created.",
        )

    def _task_payload(self, request: TaskCreateRequest) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "assignmentType": "Assignment",
            "subject": request.subject,
            "deadline": None,
            "importance": "Normal",
            "text": request.action_text,
            "performerIds": [request.performer_id],
            "observerIds": [],
            "documentIds": [],
        }
        if request.deadline is not None:
            if request.deadline.tzinfo is None or request.deadline.utcoffset() is None:
                raise DirectumError("Task deadline must include timezone")
            payload["deadline"] = request.deadline.isoformat()
        return payload

    def _task_url(self, directum_id: int) -> str | None:
        entity_path = f"ISimpleTasks({directum_id})"
        if hasattr(self.client, "build_client_card_url"):
            return self.client.build_client_card_url(entity_path)
        if hasattr(self.client, "build_url"):
            return self.client.build_url(entity_path)
        return None
