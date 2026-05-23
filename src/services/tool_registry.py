from typing import Any, Callable

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
            "create_action_item": lambda args: self.action_item_service.create_action_item(ActionItemCreateRequest(**args)),
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
                    "confirm": {"type": "boolean"},
                },
                required=["subject", "performer_id", "action_text"],
            ),
        ]

    def call(self, name: str, arguments: dict[str, Any]) -> Any:
        if name not in self._handlers:
            raise KeyError(f"Unknown tool: {name}")
        result = self._handlers[name](arguments)
        if hasattr(result, "model_dump"):
            return result.model_dump(mode="json")
        if isinstance(result, list):
            return [item.model_dump(mode="json") if hasattr(item, "model_dump") else item for item in result]
        return result

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
