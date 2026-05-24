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
        direct_response = self._direct_rx_response(message)
        if direct_response is not None:
            yield direct_response
            return

        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        messages.extend(history or [])
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

    def _direct_rx_response(self, message: str) -> str | None:
        create_response = self._direct_action_item_create_response(message)
        if create_response is not None:
            return create_response

        normalized = message.lower()
        direct_tool: str | None = None
        if "\u043f\u0440\u043e\u0441\u0440\u043e\u0447" in normalized:
            direct_tool = "get_overdue_assignments"
        elif (
            "\u043c\u043e\u0438 \u0437\u0430\u0434\u0430\u043d\u0438\u044f" in normalized
            or "\u043c\u043e\u0438 \u0437\u0430\u0434\u0430\u0447\u0438" in normalized
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

    def _direct_action_item_create_response(self, message: str) -> str | None:
        match = re.search(
            (
                r"\u0441\u043e\u0437\u0434\u0430\u0439\s+"
                r"(?:\u0437\u0430\u0434\u0430\u043d\u0438\u0435|\u043f\u043e\u0440\u0443\u0447\u0435\u043d\u0438\u0435)\s+"
                r"\u0434\u043b\u044f\s+([^,]+),\s*(.+?)"
                r"(?:,\s*\u0441\u0440\u043e\u043a\s*[-\u2013\u2014:]?\s*(.+))?$"
            ),
            message,
            flags=re.IGNORECASE,
        )
        if match is None:
            return None

        employee_query = match.group(1).strip()
        subject = match.group(2).strip()
        deadline_text = (match.group(3) or "").strip().lower()
        if not employee_query or not subject:
            return None

        try:
            employees = self.tool_registry.call("search_employee", {"query": employee_query})
            if not employees:
                return f"\u0421\u043e\u0442\u0440\u0443\u0434\u043d\u0438\u043a '{employee_query}' \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d."
            employee = employees[0]
            performer_id = employee["id"] if isinstance(employee, dict) else employee.id
            performer_name = employee["name"] if isinstance(employee, dict) else employee.name
            arguments: dict[str, Any] = {
                "subject": subject,
                "performer_id": performer_id,
                "action_text": subject,
            }
            if "\u0437\u0430\u0432\u0442\u0440\u0430" in deadline_text:
                deadline = (datetime.now(timezone.utc) + timedelta(days=1)).replace(
                    hour=23,
                    minute=59,
                    second=0,
                    microsecond=0,
                )
                arguments["deadline"] = deadline.isoformat()

            self.tool_registry.call("create_action_item", arguments)
            return (
                f"\u041f\u043e\u0434\u0433\u043e\u0442\u043e\u0432\u043b\u0435\u043d preview "
                f"\u043f\u043e\u0440\u0443\u0447\u0435\u043d\u0438\u044f \u0434\u043b\u044f {performer_name}: {subject}. "
                f"\u0414\u043b\u044f \u0444\u0430\u043a\u0442\u0438\u0447\u0435\u0441\u043a\u043e\u0433\u043e "
                f"\u0441\u043e\u0437\u0434\u0430\u043d\u0438\u044f \u043d\u0443\u0436\u043d\u043e "
                f"\u043f\u043e\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0435\u043d\u0438\u0435."
            )
        except Exception as exc:
            return self._safe_directum_error_message(exc)

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
