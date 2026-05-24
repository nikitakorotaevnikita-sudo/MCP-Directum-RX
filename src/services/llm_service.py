from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
import json
import re
from typing import Any
from urllib.parse import urlparse

import httpx
from openai import OpenAI


SYSTEM_PROMPT = (
    "You are an assistant for Directum RX assignments. You may use tools to inspect assignments and preview action item "
    "creation. Final action item confirmation requires an explicit external, user-confirmed endpoint; do not confirm or "
    "create action items yourself."
)

ACTION_ITEM_PREVIEW_MARKER = "DIRECTUM_ACTION_ITEM_PREVIEW"
EMPLOYEE_QUERY_STRIP_CHARS = " \t\r\n.,;:!?\"'\u00ab\u00bb"


class LLMService:
    def __init__(
        self,
        provider: str,
        base_url: str,
        api_key: str,
        model: str,
        tool_calling: str,
        tool_registry: Any,
    ):
        self.provider = provider
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.tool_calling = tool_calling
        self.tool_registry = tool_registry
        self._api_key = api_key
        self.client = OpenAI(
            api_key=api_key,
            base_url=self.base_url,
            http_client=httpx.Client(trust_env=not self._uses_local_base_url()),
        )

    def _uses_local_base_url(self) -> bool:
        host = urlparse(self.base_url).hostname
        return host in {"localhost", "127.0.0.1", "::1"}

    def status(self) -> dict[str, str]:
        return {
            "provider": self.provider,
            "base_url": self.base_url,
            "model": self.model,
            "tool_calling": self.tool_calling,
        }

    def tools_for_request(self) -> list[dict[str, Any]]:
        if self.tool_calling == "disabled":
            return []
        return self.tool_registry.openai_tools()

    def stream_chat(self, message: str, history: Iterable[dict[str, str]] | None = None) -> Iterable[str]:
        history_items = list(history or [])
        direct_response = self._direct_rx_response(message, history_items)
        if direct_response is not None:
            yield direct_response
            return

        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        messages.extend(history_items)
        messages.append({"role": "user", "content": message})

        try:
            yield from self._stream_chat_with_tools(messages)
        except Exception as exc:
            yield self._safe_error_message(exc)

    def _stream_chat_with_tools(self, messages: list[dict[str, Any]], max_tool_rounds: int = 4) -> Iterable[str]:
        tools = self.tools_for_request()
        for _ in range(max_tool_rounds):
            chunks, tool_calls = self._collect_stream(messages, tools)
            if chunks:
                yield from chunks
            if not tool_calls:
                return

            messages.append(
                {
                    "role": "assistant",
                    "content": "".join(chunks) or None,
                    "tool_calls": tool_calls,
                }
            )
            for tool_call in tool_calls:
                function = tool_call["function"]
                result = self.tool_registry.call(function["name"], json.loads(function["arguments"] or "{}"))
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call["id"],
                        "content": json.dumps(result, ensure_ascii=False, default=str),
                    }
                )

        yield "Tool processing stopped after too many steps."

    def _collect_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> tuple[list[str], list[dict[str, Any]]]:
        stream = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            tools=tools or None,
            stream=True,
        )
        chunks: list[str] = []
        tool_calls_by_index: dict[int, dict[str, Any]] = {}

        for chunk in stream:
            choices = getattr(chunk, "choices", None) or []
            if not choices:
                continue
            delta = getattr(choices[0], "delta", None)
            content = getattr(delta, "content", None)
            if content:
                chunks.append(content)
            for tool_call_delta in getattr(delta, "tool_calls", None) or []:
                index = getattr(tool_call_delta, "index", 0)
                tool_call = tool_calls_by_index.setdefault(
                    index,
                    {"id": "", "type": "function", "function": {"name": "", "arguments": ""}},
                )
                tool_call["id"] += getattr(tool_call_delta, "id", None) or ""
                tool_call["type"] = getattr(tool_call_delta, "type", None) or tool_call["type"]
                function = getattr(tool_call_delta, "function", None)
                if function is not None:
                    tool_call["function"]["name"] += getattr(function, "name", None) or ""
                    tool_call["function"]["arguments"] += getattr(function, "arguments", None) or ""

        return chunks, [tool_calls_by_index[index] for index in sorted(tool_calls_by_index)]

    def _safe_error_message(self, exc: Exception) -> str:
        message = str(exc)
        if self._api_key:
            message = message.replace(self._api_key, "[redacted]")
        message = re.sub(r"sk-or-v1-[A-Za-z0-9]+", "[redacted]", message)
        return f"LLM request failed: {message}"

    def _direct_rx_response(self, message: str, history: Iterable[dict[str, str]] | None = None) -> str | None:
        create_response = self._direct_action_item_create_response(message, history)
        if create_response is not None:
            return create_response

        normalized = message.lower()
        direct_tool: str | None = None
        if "\u043f\u0440\u043e\u0441\u0440\u043e\u0447" in normalized:
            direct_tool = "get_overdue_assignments"
        elif (
            "\u043c\u043e\u0438 \u0437\u0430\u0434\u0430\u043d\u0438\u044f" in normalized
            or "\u043c\u043e\u0438 \u0437\u0430\u0434\u0430\u0447\u0438" in normalized
            or (
                "\u0443 \u043c\u0435\u043d\u044f" in normalized
                and "\u0437\u0430\u0434\u0430\u0447" in normalized
                and "\u0432 \u0440\u0430\u0431\u043e\u0442\u0435" in normalized
            )
            or "my assignments" in normalized
        ):
            direct_tool = "get_my_assignments"
        elif "\u043d\u0430\u0437\u043d\u0430\u0447\u0435\u043d\u043d\u044b\u0435 \u043c\u043d\u0435" in normalized or "assigned to me" in normalized:
            direct_tool = "get_action_items_assigned_to_me"
        elif "\u0441\u043e\u0437\u0434\u0430\u043d\u043d\u044b\u0435 \u043c\u043d\u043e\u0439" in normalized or "created by me" in normalized:
            direct_tool = "get_action_items_created_by_me"
        if direct_tool is None:
            return None

        try:
            result = self.tool_registry.call(direct_tool, {})
            return self._format_tool_result(result)
        except Exception as exc:
            return self._safe_directum_error_message(exc)

    def _direct_action_item_create_response(
        self,
        message: str,
        history: Iterable[dict[str, str]] | None = None,
    ) -> str | None:
        draft = self._parse_action_item_draft(message)
        action_text = self._parse_action_text_update(message)
        if draft is None and action_text:
            draft = self._latest_action_item_draft(history)
            if draft is not None:
                draft["action_text"] = action_text
        if draft is None:
            return None

        if not draft.get("action_text"):
            return (
                f"\u0414\u043b\u044f \u0441\u043e\u0437\u0434\u0430\u043d\u0438\u044f "
                f"\u043f\u043e\u0440\u0443\u0447\u0435\u043d\u0438\u044f "
                f"\u00ab{draft['subject']}\u00bb \u043c\u043d\u0435 \u043d\u0443\u0436\u0435\u043d "
                f"\u0442\u0435\u043a\u0441\u0442 \u0441\u0430\u043c\u043e\u0439 "
                f"\u0437\u0430\u0434\u0430\u0447\u0438."
            )

        try:
            employees = self.tool_registry.call("search_employee", {"query": draft["employee_query"]})
            if not employees:
                return (
                    f"\u0421\u043e\u0442\u0440\u0443\u0434\u043d\u0438\u043a "
                    f"'{draft['employee_query']}' \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d."
                )
            employee = employees[0]
            performer_id = employee["id"] if isinstance(employee, dict) else employee.id
            performer_name = employee["name"] if isinstance(employee, dict) else employee.name
            arguments: dict[str, Any] = {
                "subject": draft["subject"],
                "performer_id": performer_id,
                "action_text": draft["action_text"],
            }
            deadline = self._parse_deadline(draft.get("deadline_text") or "")
            if deadline is not None:
                arguments["deadline"] = deadline.isoformat()

            self.tool_registry.call("create_action_item", arguments)
            visible_response = (
                f"\u041f\u043e\u0434\u0433\u043e\u0442\u043e\u0432\u043b\u0435\u043d preview "
                f"\u043f\u043e\u0440\u0443\u0447\u0435\u043d\u0438\u044f \u0434\u043b\u044f {performer_name}: "
                f"{draft['subject']}. "
                f"\u0414\u043b\u044f \u0444\u0430\u043a\u0442\u0438\u0447\u0435\u0441\u043a\u043e\u0433\u043e "
                f"\u0441\u043e\u0437\u0434\u0430\u043d\u0438\u044f \u043d\u0443\u0436\u043d\u043e "
                f"\u043f\u043e\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0435\u043d\u0438\u0435."
            )
            return visible_response + "\n" + self._action_item_preview_marker(arguments, performer_name)
        except Exception as exc:
            return self._safe_directum_error_message(exc)

    def _action_item_preview_marker(self, payload: dict[str, Any], performer_name: str) -> str:
        preview = {
            "type": "action_item",
            "payload": payload,
            "display": {"performer_name": performer_name},
        }
        return f"[[{ACTION_ITEM_PREVIEW_MARKER}:{json.dumps(preview, ensure_ascii=False, default=str)}]]"

    def _clean_employee_query(self, value: str) -> str:
        return value.strip(EMPLOYEE_QUERY_STRIP_CHARS)

    def _latest_action_item_draft(self, history: Iterable[dict[str, str]] | None) -> dict[str, str] | None:
        for item in reversed(list(history or [])):
            if item.get("role") != "user":
                continue
            draft = self._parse_action_item_draft(item.get("content", ""))
            if draft is not None:
                return draft
        return None

    def _parse_action_item_draft(self, message: str) -> dict[str, str] | None:
        quoted_match = re.search(
            (
                r"(?:\u0441\u043e\u0437\u0434\u0430\u0439|\u043f\u043e\u0434\u0433\u043e\u0442\u043e\u0432\u044c)\s+"
                r"(?:\u0437\u0430\u0434\u0430\u043d\u0438\u0435|\u043f\u043e\u0440\u0443\u0447\u0435\u043d\u0438\u0435)\s+"
                r"\u0434\u043b\u044f\s+(.+?)\s+[\u0022\u00ab](.+?)[\u0022\u00bb]\s*"
                r"(?:,?\s*\u0441\u0440\u043e\u043a\s*[-\u2013\u2014:]?\s*(.+))?$"
            ),
            message,
            flags=re.IGNORECASE,
        )
        if quoted_match is not None:
            subject = quoted_match.group(2).strip()
            return {
                "employee_query": self._clean_employee_query(quoted_match.group(1)),
                "subject": subject,
                "action_text": subject,
                "deadline_text": (quoted_match.group(3) or "").strip(),
            }

        natural_match = re.search(
            (
                r"(?:\u0441\u043e\u0437\u0434\u0430\u0439|\u043f\u043e\u0434\u0433\u043e\u0442\u043e\u0432\u044c)\s+"
                r"(?:\u0437\u0430\u0434\u0430\u0447\u0443|\u0437\u0430\u0434\u0430\u043d\u0438\u0435|\u043f\u043e\u0440\u0443\u0447\u0435\u043d\u0438\u0435)\s+"
                r"\u0434\u043b\u044f\s+([^,]+?)\s+"
                r"\u0447\u0442\u043e\u0431\u044b\s+(?:\u043e\u043d|"
                r"\u043e\u043d\u0430)\s+(.+?)\s+"
                r"(?:\u0441\u043e\s+)?\u0441\u0440\u043e\u043a(?:\u043e\u043c)?\s*[-\u2013\u2014:]?\s*(.+)$"
            ),
            message,
            flags=re.IGNORECASE,
        )
        if natural_match is not None:
            subject = natural_match.group(2).strip(" .")
            return {
                "employee_query": self._clean_employee_query(natural_match.group(1)),
                "subject": subject,
                "action_text": subject,
                "deadline_text": natural_match.group(3).strip(),
            }

        theme_match = re.search(
            (
                r"(?:\u0441\u043e\u0437\u0434\u0430\u0439|\u043f\u043e\u0434\u0433\u043e\u0442\u043e\u0432\u044c)\s+"
                r"(?:\u0437\u0430\u0434\u0430\u043d\u0438\u0435|\u043f\u043e\u0440\u0443\u0447\u0435\u043d\u0438\u0435)\s+"
                r"\u0434\u043b\u044f\s+([^,]+),\s*"
                r"\u0442\u0435\u043c\u0430\s*[-\u2013\u2014:]?\s*(.+?)"
                r"(?:,\s*\u0441\u0440\u043e\u043a\s*[-\u2013\u2014:]?\s*(.+))?$"
            ),
            message,
            flags=re.IGNORECASE,
        )
        if theme_match is not None:
            return {
                "employee_query": self._clean_employee_query(theme_match.group(1)),
                "subject": theme_match.group(2).strip(),
                "deadline_text": (theme_match.group(3) or "").strip(),
            }

        create_match = re.search(
            (
                r"(?:\u0441\u043e\u0437\u0434\u0430\u0439|\u043f\u043e\u0434\u0433\u043e\u0442\u043e\u0432\u044c)\s+"
                r"(?:\u0437\u0430\u0434\u0430\u043d\u0438\u0435|\u043f\u043e\u0440\u0443\u0447\u0435\u043d\u0438\u0435)\s+"
                r"\u0434\u043b\u044f\s+([^,]+),\s*(.+?)"
                r"(?:,\s*\u0441\u0440\u043e\u043a\s*[-\u2013\u2014:]?\s*(.+))?$"
            ),
            message,
            flags=re.IGNORECASE,
        )
        if create_match is not None:
            subject = create_match.group(2).strip()
            return {
                "employee_query": self._clean_employee_query(create_match.group(1)),
                "subject": subject,
                "action_text": subject,
                "deadline_text": (create_match.group(3) or "").strip(),
            }

        performer_match = re.search(
            r"^\s*(.+?)[.!?]?\s+\u0438\u0441\u043f\u043e\u043b\u043d\u0438\u0442\u0435\u043b\u044c\s+(.+?)\s*$",
            message,
            flags=re.IGNORECASE,
        )
        if performer_match is not None:
            action_text = performer_match.group(1).strip()
            return {
                "employee_query": self._clean_employee_query(performer_match.group(2)),
                "subject": action_text,
                "action_text": action_text,
                "deadline_text": "",
            }

        return None

    def _parse_action_text_update(self, message: str) -> str | None:
        match = re.search(
            r"^\s*\u0442\u0435\u043a\u0441\u0442\s+(?:\u043f\u043e\u0440\u0443\u0447\u0435\u043d\u0438\u044f|\u0437\u0430\u0434\u0430\u0447\u0438)\s*[-\u2013\u2014:]?\s*(.+?)\s*$",
            message,
            flags=re.IGNORECASE,
        )
        return match.group(1).strip() if match is not None else None

    def _parse_deadline(self, deadline_text: str) -> datetime | None:
        normalized = deadline_text.strip().lower()
        if not normalized:
            return None
        if "\u0437\u0430\u0432\u0442\u0440\u0430" in normalized:
            return (datetime.now(timezone.utc) + timedelta(days=1)).replace(
                hour=23,
                minute=59,
                second=0,
                microsecond=0,
            )
        date_match = re.search(r"\b(\d{1,2})\.(\d{1,2})(?:\.(\d{2,4}))?\b", normalized)
        if date_match is None:
            return None
        day = int(date_match.group(1))
        month = int(date_match.group(2))
        year = int(date_match.group(3) or datetime.now(timezone.utc).year)
        if year < 100:
            year += 2000
        return datetime(year, month, day, 23, 59, tzinfo=timezone.utc)

    def _format_tool_result(self, result: Any) -> str:
        items = result if isinstance(result, list) else [result]
        if not items:
            return "\u041d\u0438\u0447\u0435\u0433\u043e \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d\u043e."

        lines = []
        for item in items:
            if hasattr(item, "model_dump"):
                item = item.model_dump(mode="json")
            if isinstance(item, dict):
                title = item.get("subject") or item.get("name") or item.get("message") or str(item)
                status = item.get("status") or item.get("mode") or item.get("entity_type")
                lines.append(f"{title} ({status})" if status else str(title))
            else:
                lines.append(str(item))
        return f"\u041d\u0430\u0439\u0434\u0435\u043d\u043e {len(lines)}: " + "; ".join(lines) + "."

    def _safe_directum_error_message(self, exc: Exception) -> str:
        message = str(exc)
        message = re.sub(r"Basic [A-Za-z0-9+/=]{8,}", "Basic [redacted]", message)
        return f"Directum RX request failed: {message}"
