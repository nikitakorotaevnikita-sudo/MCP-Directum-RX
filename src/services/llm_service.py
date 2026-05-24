from collections.abc import Iterable
import re
from typing import Any

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
        self.client = OpenAI(api_key=api_key, base_url=self.base_url)

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
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        messages.extend(history or [])
        messages.append({"role": "user", "content": message})

        try:
            tools = self.tools_for_request()
            stream = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                tools=tools or None,
                stream=True,
            )

            for chunk in stream:
                choices = getattr(chunk, "choices", None) or []
                if not choices:
                    continue
                delta = getattr(choices[0], "delta", None)
                content = getattr(delta, "content", None)
                if content:
                    yield content
        except Exception as exc:
            yield self._safe_error_message(exc)

    def _safe_error_message(self, exc: Exception) -> str:
        message = str(exc)
        if self._api_key:
            message = message.replace(self._api_key, "[redacted]")
        message = re.sub(r"sk-or-v1-[A-Za-z0-9]+", "[redacted]", message)
        return f"LLM request failed: {message}"
