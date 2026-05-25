from datetime import datetime, timezone
import re
from typing import Any, Callable

from pydantic import ValidationError

from src.models.schemas import ActionItemCreateRequest, TaskCreateRequest
from src.services.action_items import ActionItemService
from src.services.assignments import AssignmentsService
from src.services.current_user import CurrentUserService


class ToolRegistry:
    def __init__(
        self,
        current_user_service: CurrentUserService,
        assignments_service: AssignmentsService,
        action_item_service: ActionItemService,
    ):
        self.current_user_service = current_user_service
        self.assignments_service = assignments_service
        self.action_item_service = action_item_service
        self._handlers: dict[str, Callable[[dict[str, Any]], Any]] = {
            "get_current_user": lambda args: self.current_user_service.get_current_user(),
            "get_my_assignments": lambda args: self.assignments_service.get_my_assignments(),
            "get_overdue_assignments": lambda args: self.assignments_service.get_overdue_assignments(),
            "get_action_items_assigned_to_me": lambda args: self.assignments_service.get_action_items_assigned_to_me(),
            "get_action_items_created_by_me": lambda args: self.assignments_service.get_action_items_created_by_me(),
            "search_employee": lambda args: self.action_item_service.search_employee(
                self._normalize_search_query(args.get("query", ""))
            ),
            "search_documents": lambda args: self.action_item_service.search_documents(args.get("query", "")),
            "create_action_item": self._create_action_item,
            "create_task": self._create_task,
        }
        self._required_arguments: dict[str, list[str]] = {
            "search_employee": ["query"],
            "search_documents": ["query"],
            "create_action_item": ["subject", "performer_id", "action_text"],
            "create_task": ["subject", "performer_id", "action_text"],
        }

    def openai_tools(self) -> list[dict[str, Any]]:
        return [
            self._tool("get_current_user", "Get current Directum RX user.", {}),
            self._tool("get_my_assignments", "Get my in-process assignments.", {}),
            self._tool("get_overdue_assignments", "Get my overdue assignments.", {}),
            self._tool("get_action_items_assigned_to_me", "Get action items assigned to me.", {}),
            self._tool("get_action_items_created_by_me", "Get action items created by me.", {}),
            self._tool(
                "search_employee",
                "Search Directum employees by name.",
                {"query": {"type": "string", "description": "Employee name or part of name"}},
                required=["query"],
            ),
            self._tool(
                "search_documents",
                "Search Directum official documents by name or subject.",
                {"query": {"type": "string", "description": "Document name, subject, number, or keyword"}},
                required=["query"],
            ),
            self._tool(
                "create_action_item",
                (
                    "Preview a document-bound Directum RX action item. Use this only for Russian requests that say "
                    "'поручение' or 'поручения'. Do not use it for 'задача' or 'задание'; use create_task for those. "
                    "Use only concrete user-provided subject/action_text; never invent placeholder text."
                ),
                {
                    "subject": {"type": "string"},
                    "performer_id": {"type": "integer"},
                    "action_text": {"type": "string"},
                    "deadline": {"type": "string"},
                    "document_id": {"type": "integer"},
                },
                required=["subject", "performer_id", "action_text"],
            ),
            self._tool(
                "create_task",
                (
                    "Preview a Directum RX simple task without a document. Use this for Russian requests that say "
                    "'задача' or 'задание'. Do not use create_action_item for 'задача' or 'задание'. "
                    "Use only concrete user-provided subject/action_text; never invent placeholder text."
                ),
                {
                    "subject": {"type": "string"},
                    "performer_id": {"type": "integer"},
                    "action_text": {"type": "string"},
                    "deadline": {"type": "string"},
                },
                required=["subject", "performer_id", "action_text"],
            ),
        ]

    def call(self, name: str, arguments: dict[str, Any]) -> Any:
        if name not in self._handlers:
            raise ValueError(f"Unknown tool: {name}")
        if not isinstance(arguments, dict):
            raise ValueError(f"Tool '{name}' arguments must be an object")
        if name not in {"create_action_item", "create_task"}:
            self._validate_required_arguments(name, arguments)
        result = self._handlers[name](arguments)
        if hasattr(result, "model_dump"):
            return result.model_dump(mode="json")
        if isinstance(result, list):
            return [item.model_dump(mode="json") if hasattr(item, "model_dump") else item for item in result]
        return result

    def _create_action_item(self, arguments: dict[str, Any]) -> Any:
        safe_arguments = self._unwrap_tool_arguments(arguments)
        if safe_arguments.get("confirm") is True:
            raise ValueError("Tool 'create_action_item' cannot confirm creation directly; use preview mode first")
        safe_arguments = self._normalize_create_action_item_arguments(safe_arguments)
        self._reject_vague_create_arguments("create_action_item", safe_arguments)
        self._validate_required_arguments("create_action_item", safe_arguments)
        try:
            request = ActionItemCreateRequest(**safe_arguments)
        except ValidationError as exc:
            raise ValueError(f"Tool 'create_action_item' invalid arguments: {exc}") from exc
        result = self.action_item_service.create_action_item(request)
        return self._with_confirmation_payload(result, request.model_dump(mode="json"))

    def _create_task(self, arguments: dict[str, Any]) -> Any:
        safe_arguments = self._unwrap_tool_arguments(arguments)
        if safe_arguments.get("confirm") is True:
            raise ValueError("Tool 'create_task' cannot confirm creation directly; use preview mode first")
        safe_arguments = self._normalize_create_task_arguments(safe_arguments)
        self._reject_vague_create_arguments("create_task", safe_arguments)
        self._validate_required_arguments("create_task", safe_arguments)
        try:
            request = TaskCreateRequest(**safe_arguments)
        except ValidationError as exc:
            raise ValueError(f"Tool 'create_task' invalid arguments: {exc}") from exc
        result = self.action_item_service.create_task(request)
        return self._with_confirmation_payload(result, request.model_dump(mode="json"))

    def _with_confirmation_payload(self, result: Any, payload: dict[str, Any]) -> Any:
        if hasattr(result, "model_dump"):
            data = result.model_dump(mode="json")
        elif isinstance(result, dict):
            data = result
        else:
            return result
        if data.get("mode") != "preview":
            return data
        return {**data, "confirmation_payload": payload}

    def _normalize_create_action_item_arguments(self, arguments: dict[str, Any]) -> dict[str, Any]:
        safe_arguments = {**self._unwrap_tool_arguments(arguments), "confirm": False}
        self._normalize_action_item_text_fields(safe_arguments)
        self._normalize_action_item_performer(safe_arguments)
        self._normalize_action_item_document(safe_arguments)
        deadline = safe_arguments.get("deadline")
        if isinstance(deadline, datetime) and (deadline.tzinfo is None or deadline.utcoffset() is None):
            safe_arguments["deadline"] = deadline.replace(tzinfo=timezone.utc)
        if isinstance(deadline, str):
            parsed_deadline = self._parse_deadline(deadline)
            if parsed_deadline is not None:
                safe_arguments["deadline"] = parsed_deadline
        return safe_arguments

    def _normalize_create_task_arguments(self, arguments: dict[str, Any]) -> dict[str, Any]:
        safe_arguments = {**self._unwrap_tool_arguments(arguments), "confirm": False}
        self._normalize_action_item_text_fields(safe_arguments)
        self._normalize_action_item_performer(safe_arguments)
        safe_arguments.pop("document_id", None)
        deadline = safe_arguments.get("deadline")
        if isinstance(deadline, datetime) and (deadline.tzinfo is None or deadline.utcoffset() is None):
            safe_arguments["deadline"] = deadline.replace(tzinfo=timezone.utc)
        if isinstance(deadline, str):
            parsed_deadline = self._parse_deadline(deadline)
            if parsed_deadline is not None:
                safe_arguments["deadline"] = parsed_deadline
        return safe_arguments

    def _unwrap_tool_arguments(self, arguments: dict[str, Any]) -> dict[str, Any]:
        parameters = arguments.get("parameters")
        if isinstance(parameters, dict):
            return parameters
        return arguments

    def _normalize_action_item_text_fields(self, arguments: dict[str, Any]) -> None:
        subject = arguments.get("subject")
        action_text = arguments.get("action_text")
        if not action_text and isinstance(subject, str) and subject.strip():
            arguments["action_text"] = subject
        if not subject and isinstance(action_text, str) and action_text.strip():
            arguments["subject"] = action_text

    def _reject_vague_create_arguments(self, name: str, arguments: dict[str, Any]) -> None:
        subject = self._normalize_vague_text(arguments.get("subject"))
        action_text = self._normalize_vague_text(arguments.get("action_text"))
        vague_values = {
            "подготовить поручение",
            "поручение",
            "подготовить задание",
            "задание",
            "задача",
            "необходимо выполнить задачу согласно заданию",
            "выполнить задачу согласно заданию",
        }
        if subject in vague_values or action_text in vague_values:
            raise ValueError(
                f"Tool '{name}' needs concrete user-provided subject and action_text; ask the user for the task text"
            )

    def _normalize_vague_text(self, value: Any) -> str:
        if not isinstance(value, str):
            return ""
        return re.sub(r"\s+", " ", value.strip().lower().rstrip(".")).strip()

    def _normalize_action_item_performer(self, arguments: dict[str, Any]) -> None:
        performer = arguments.get("performer_id")
        if isinstance(performer, str):
            cleaned = self._normalize_search_query(performer)
            if cleaned.isdecimal():
                arguments["performer_id"] = int(cleaned)
                return
            employees = self.action_item_service.search_employee(cleaned)
            if employees:
                first = employees[0]
                arguments["performer_id"] = first["id"] if isinstance(first, dict) else first.id

    def _normalize_action_item_document(self, arguments: dict[str, Any]) -> None:
        document_id = arguments.get("document_id")
        if document_id is None:
            return
        if isinstance(document_id, int):
            return
        if isinstance(document_id, str):
            cleaned = document_id.strip()
            if cleaned.isdecimal():
                arguments["document_id"] = int(cleaned)
                return
        arguments.pop("document_id", None)

    def _parse_deadline(self, value: str) -> datetime | None:
        parsed_iso = self._parse_iso_deadline(value)
        if parsed_iso is not None:
            return parsed_iso
        return self._parse_short_deadline(value)

    def _parse_iso_deadline(self, value: str) -> datetime | None:
        cleaned = value.strip()
        if not cleaned:
            return None
        try:
            parsed = datetime.fromisoformat(cleaned.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed

    def _parse_short_deadline(self, value: str) -> datetime | None:
        match = re.search(r"\b(\d{1,2})\.(\d{1,2})(?:\.(\d{2,4}))?\b", value.strip())
        if match is None:
            return None
        day = int(match.group(1))
        month = int(match.group(2))
        year = int(match.group(3) or datetime.now(timezone.utc).year)
        if year < 100:
            year += 2000
        return datetime(year, month, day, 23, 59, tzinfo=timezone.utc)

    def _validate_required_arguments(self, name: str, arguments: dict[str, Any]) -> None:
        for field in self._required_arguments.get(name, []):
            if field not in arguments:
                raise ValueError(f"Tool '{name}' missing required argument: {field}")

    def _normalize_search_query(self, query: str) -> str:
        stripped = query.strip(" \t\r\n.,;:!?\"'«»")
        return stripped

    def _tool(
        self,
        name: str,
        description: str,
        properties: dict[str, Any],
        required: list[str] | None = None,
    ) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required or [],
                },
            },
        }
