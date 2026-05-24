from datetime import datetime, timezone
import re
from typing import Any, Callable

from pydantic import ValidationError

from src.models.schemas import ActionItemCreateRequest
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
            "search_employee": lambda args: self.action_item_service.search_employee(args["query"]),
            "create_action_item": self._create_action_item,
        }
        self._required_arguments: dict[str, list[str]] = {
            "search_employee": ["query"],
            "create_action_item": ["subject", "performer_id", "action_text"],
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
                "create_action_item",
                "Preview or create an action item. Use confirm=false first.",
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
        self._validate_required_arguments(name, arguments)
        result = self._handlers[name](arguments)
        if hasattr(result, "model_dump"):
            return result.model_dump(mode="json")
        if isinstance(result, list):
            return [item.model_dump(mode="json") if hasattr(item, "model_dump") else item for item in result]
        return result

    def _create_action_item(self, arguments: dict[str, Any]) -> Any:
        if arguments.get("confirm") is True:
            raise ValueError("Tool 'create_action_item' cannot confirm creation directly; use preview mode first")
        safe_arguments = self._normalize_create_action_item_arguments(arguments)
        try:
            request = ActionItemCreateRequest(**safe_arguments)
        except ValidationError as exc:
            raise ValueError(f"Tool 'create_action_item' invalid arguments: {exc}") from exc
        return self.action_item_service.create_action_item(request)

    def _normalize_create_action_item_arguments(self, arguments: dict[str, Any]) -> dict[str, Any]:
        safe_arguments = {**arguments, "confirm": False}
        deadline = safe_arguments.get("deadline")
        if isinstance(deadline, datetime) and (deadline.tzinfo is None or deadline.utcoffset() is None):
            safe_arguments["deadline"] = deadline.replace(tzinfo=timezone.utc)
        if isinstance(deadline, str):
            parsed_deadline = self._parse_deadline(deadline)
            if parsed_deadline is not None:
                safe_arguments["deadline"] = parsed_deadline
        return safe_arguments

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
