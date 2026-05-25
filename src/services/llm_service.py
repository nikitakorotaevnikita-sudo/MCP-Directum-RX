from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
import json
import re
from typing import Any
from urllib.parse import urlparse

import httpx
from openai import OpenAI


SYSTEM_PROMPT = (
    "You are an assistant for Directum RX work items. Russian tool routing is strict: "
    "'поручение'/'поручения' means create_action_item; 'задача'/'задание' means create_task. "
    "Never use create_action_item for 'задача' or 'задание'. "
    "Do not invent subject, action_text, deadline, or document_id when the user did not provide them. "
    "If the user gives only a performer, ask for the concrete task text.\n"
    "Final action item or task confirmation requires an explicit external, user-confirmed endpoint; do not confirm or "
    "create items yourself.\n"
    "When calling search_employee, normalize the query first: remove punctuation (.,;:!?\"'), strip extra spaces, "
    "and pass only the most stable part of the name — usually last name + first name. "
    "Do not include words like 'для', 'исполнитель', 'срок', or grammatical suffixes. "
    "If a full name search returns nothing, the tool will automatically try shorter tokens; "
    "if that also returns nothing, tell the user the employee was not found and suggest a shorter name variant.\n"
    "Format final answers in Markdown. When showing lists of Directum items, use a numbered Markdown list instead "
    "of a semicolon-separated line."
)

ACTION_ITEM_PREVIEW_MARKER = "DIRECTUM_ACTION_ITEM_PREVIEW"
EMPLOYEE_QUERY_STRIP_CHARS = " \t\r\n.,;:!?\"'\u00ab\u00bb"
CREATE_VERB_PATTERN = r"(?:создай|подготовь|поставь|сформируй|выдай)"


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
        employee_names_by_id: dict[int, str] = {}
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
                tool_name = function["name"]
                try:
                    result = self.tool_registry.call(tool_name, json.loads(function["arguments"] or "{}"))
                except ValueError as exc:
                    if self._is_direct_confirmation_error(exc):
                        yield self._confirmation_requires_button_message()
                        return
                    if self._is_vague_create_error(exc):
                        yield self._create_request_needs_text_message(tool_name)
                        return
                    raise
                self._remember_employee_names(tool_name, result, employee_names_by_id)
                preview_response = self._tool_preview_response(tool_name, result, employee_names_by_id)
                if preview_response is not None:
                    yield preview_response
                    return
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call["id"],
                        "content": json.dumps(result, ensure_ascii=False, default=str),
                    }
                )

        yield "Tool processing stopped after too many steps."

    def _is_direct_confirmation_error(self, exc: ValueError) -> bool:
        message = str(exc)
        return "cannot confirm creation directly" in message

    def _is_vague_create_error(self, exc: ValueError) -> bool:
        message = str(exc)
        return "needs concrete user-provided subject and action_text" in message

    def _create_request_needs_text_message(self, tool_name: str) -> str:
        entity_label = "задачи" if tool_name == "create_task" else "поручения"
        return f"Для подготовки {entity_label} нужен конкретный текст: что именно должен сделать исполнитель?"

    def _confirmation_requires_button_message(self) -> str:
        return (
            "Подтверждение создания через чат отключено. "
            "Нажмите кнопку создания в preview-карточке; если карточки нет, заново опишите поручение с исполнителем и текстом."
        )

    def _remember_employee_names(self, tool_name: str, result: Any, employee_names_by_id: dict[int, str]) -> None:
        if tool_name != "search_employee":
            return
        employees = result if isinstance(result, list) else []
        for employee in employees:
            if not isinstance(employee, dict):
                continue
            employee_id = employee.get("id")
            employee_name = employee.get("name")
            if isinstance(employee_id, int) and isinstance(employee_name, str) and employee_name.strip():
                employee_names_by_id[employee_id] = employee_name.strip()

    def _tool_preview_response(
        self,
        tool_name: str,
        result: Any,
        employee_names_by_id: dict[int, str],
    ) -> str | None:
        if tool_name not in {"create_action_item", "create_task"}:
            return None
        if not isinstance(result, dict) or result.get("mode") != "preview":
            return None
        payload = result.get("confirmation_payload")
        if not isinstance(payload, dict):
            return None

        performer_id = payload.get("performer_id")
        performer_name = employee_names_by_id.get(performer_id) if isinstance(performer_id, int) else None
        performer_name = performer_name or str(performer_id or "исполнитель")
        preview_type = "task" if tool_name == "create_task" else "action_item"
        entity_label = "задачи" if preview_type == "task" else "поручения"
        subject = payload.get("subject") or payload.get("action_text") or ""
        visible_response = (
            f"Подготовлен preview {entity_label} для {performer_name}: {subject}. "
            "Для фактического создания нажмите кнопку подтверждения."
        )
        return visible_response + "\n" + self._action_item_preview_marker(
            payload,
            performer_name,
            preview_type,
            correct_text=False,
        )

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
        confirmation_response = self._direct_confirmation_response(message, history)
        if confirmation_response is not None:
            return confirmation_response

        create_response = self._direct_action_item_create_response(message, history)
        if create_response is not None:
            return create_response

        normalized = message.lower()
        direct_tool: str | None = None
        wants_outgoing_action_item_analytics = (
            "\u0430\u043d\u0430\u043b\u0438\u0442" in normalized
            and "\u0438\u0441\u0445\u043e\u0434\u044f\u0449" in normalized
            and "\u043f\u043e\u0440\u0443\u0447\u0435\u043d" in normalized
        )
        if wants_outgoing_action_item_analytics:
            direct_tool = "get_action_items_created_by_me"
        elif "\u043f\u0440\u043e\u0441\u0440\u043e\u0447" in normalized:
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
        elif (
            "\u043d\u0430\u0437\u043d\u0430\u0447\u0435\u043d\u043d\u044b\u0435 \u043c\u043d\u0435" in normalized
            or "\u043f\u043e\u0440\u0443\u0447\u0435\u043d\u0438\u044f \u043c\u043d\u0435" in normalized
            or (
                "\u0432\u0445\u043e\u0434\u044f\u0449" in normalized
                and "\u043f\u043e\u0440\u0443\u0447\u0435\u043d" in normalized
            )
            or "assigned to me" in normalized
        ):
            direct_tool = "get_action_items_assigned_to_me"
        elif (
            "\u0441\u043e\u0437\u0434\u0430\u043d\u043d\u044b\u0435 \u043c\u043d\u043e\u0439" in normalized
            or "\u043f\u043e\u0440\u0443\u0447\u0435\u043d\u0438\u044f \u043e\u0442 \u043c\u0435\u043d\u044f" in normalized
            or "\u043f\u043e\u0440\u0443\u0447\u0435\u043d\u0438\u0439 \u043e\u0442 \u043c\u0435\u043d\u044f" in normalized
            or (
                "\u0438\u0441\u0445\u043e\u0434\u044f\u0449" in normalized
                and "\u043f\u043e\u0440\u0443\u0447\u0435\u043d" in normalized
            )
            or "created by me" in normalized
        ):
            direct_tool = "get_action_items_created_by_me"
        if direct_tool is None:
            return None

        try:
            result = self.tool_registry.call(direct_tool, {})
            if wants_outgoing_action_item_analytics:
                return self._format_outgoing_action_item_analytics(result)
            return self._format_tool_result(result)
        except Exception as exc:
            return self._safe_directum_error_message(exc)

    def _direct_confirmation_response(
        self,
        message: str,
        history: Iterable[dict[str, str]] | None = None,
    ) -> str | None:
        normalized = message.strip().lower()
        if normalized not in {"да", "подтверждаю", "создать", "подтвердить", "ok", "okay", "yes"}:
            return None
        for item in reversed(list(history or [])[-4:]):
            if item.get("role") != "assistant":
                continue
            content = item.get("content", "").lower()
            if (
                "preview" in content
                or "предваритель" in content
                or "подтверд" in content
                or "черновик" in content
            ) and ("поручен" in content or "задач" in content):
                return self._confirmation_requires_button_message()
        return None

    def _direct_action_item_create_response(
        self,
        message: str,
        history: Iterable[dict[str, str]] | None = None,
    ) -> str | None:
        llm_draft = self._extract_create_draft_via_llm(message)
        fallback_draft = self._parse_followup_create_draft(message, history) or self._parse_action_item_draft(message)
        if llm_draft is not None and not llm_draft.get("missing_fields"):
            draft = llm_draft
        else:
            draft = fallback_draft or llm_draft
        draft = self._apply_context_to_create_draft(message, draft, history)
        action_text = self._parse_action_text_update(message)
        if draft is None and action_text:
            draft = self._latest_action_item_draft(history)
            if draft is not None:
                draft["action_text"] = action_text
        if draft is None:
            return None

        missing_message = self._missing_create_draft_message(draft)
        if missing_message is not None:
            return missing_message

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
            deadline = self._normalize_draft_deadline(draft)
            if deadline:
                arguments["deadline"] = deadline

            preview_type = draft.get("type", "action_item")
            is_task = preview_type == "task"
            tool_name = "create_task" if is_task else "create_action_item"
            self.tool_registry.call(tool_name, arguments)
            entity_label = "задачи" if is_task else "поручения"
            visible_response = (
                f"\u041f\u043e\u0434\u0433\u043e\u0442\u043e\u0432\u043b\u0435\u043d preview "
                f"{entity_label} \u0434\u043b\u044f {performer_name}: "
                f"{draft['subject']}. "
                f"\u0414\u043b\u044f \u0444\u0430\u043a\u0442\u0438\u0447\u0435\u0441\u043a\u043e\u0433\u043e "
                f"\u0441\u043e\u0437\u0434\u0430\u043d\u0438\u044f \u043d\u0443\u0436\u043d\u043e "
                f"\u043f\u043e\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0435\u043d\u0438\u0435."
            )
            return visible_response + "\n" + self._action_item_preview_marker(
                arguments,
                performer_name,
                preview_type,
                correct_text=draft.get("source") != "llm",
            )
        except Exception as exc:
            return self._safe_directum_error_message(exc)

    def _extract_create_draft_via_llm(self, message: str) -> dict[str, Any] | None:
        if not self._looks_like_create_request(message):
            return None

        prompt = (
            "Извлеки черновик создания задачи или поручения Directum RX из сообщения пользователя.\n"
            "Верни только JSON без markdown.\n"
            "Правила:\n"
            "- entity_type: task для слов 'задача', 'задачу', 'задание', 'задания'; action_item для 'поручение', 'поручения'.\n"
            "- Не придумывай subject, action_text, deadline или employee_query, если их нет в сообщении.\n"
            "- Если есть только исполнитель без текста действия, action_text=null и добавь 'action_text' в missing_fields.\n"
            "- employee_query должен быть коротким поисковым именем сотрудника, без слов 'для', 'на', 'срок'.\n"
            "- subject: краткое существительное/фраза по смыслу действия, без даты.\n"
            "- action_text: конкретное действие для исполнителя, без даты.\n"
            "- deadline: если указана явная дата, верни ISO 8601 с timezone +00:00 и временем 23:59:00; "
            "для dd.mm.yy используй 20yy. Если даты нет, null.\n"
            "JSON schema: {"
            "\"entity_type\":\"task|action_item|null\","
            "\"employee_query\":\"string|null\","
            "\"subject\":\"string|null\","
            "\"action_text\":\"string|null\","
            "\"deadline\":\"string|null\","
            "\"missing_fields\":[\"employee_query\"|\"action_text\"|\"entity_type\"]"
            "}\n"
            f"Сообщение: {message}"
        )
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "Ты возвращаешь только валидный JSON без markdown-разметки."},
                    {"role": "user", "content": prompt},
                ],
                temperature=0,
                max_tokens=400,
            )
            raw = response.choices[0].message.content or "{}"
            data = self._parse_json_object(raw)
        except Exception:
            return None
        return self._normalize_llm_create_draft(data, message)

    def _looks_like_create_request(self, message: str) -> bool:
        normalized = message.lower()
        has_create_verb = any(verb in normalized for verb in ("создай", "подготовь", "поставь", "сформируй", "выдай"))
        has_entity = any(entity in normalized for entity in ("поручен", "задач", "задан"))
        return has_create_verb and has_entity

    def _parse_json_object(self, raw: str) -> dict[str, Any]:
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
            cleaned = re.sub(r"\s*```$", "", cleaned)
        if not cleaned.startswith("{"):
            match = re.search(r"\{[\s\S]*\}", cleaned)
            if match is None:
                return {}
            cleaned = match.group(0)
        data = json.loads(cleaned)
        return data if isinstance(data, dict) else {}

    def _normalize_llm_create_draft(self, data: dict[str, Any], message: str) -> dict[str, Any] | None:
        entity_type = self._entity_type_from_message(message) or data.get("entity_type")
        if entity_type not in {"task", "action_item"}:
            return None

        employee_query = self._clean_optional_text(data.get("employee_query"))
        subject = self._clean_optional_text(data.get("subject"))
        action_text = self._clean_optional_text(data.get("action_text"))
        deadline = self._clean_optional_text(data.get("deadline"))
        missing_fields = data.get("missing_fields") if isinstance(data.get("missing_fields"), list) else []
        missing = {str(field) for field in missing_fields}

        if action_text and not subject:
            subject = action_text
        if self._is_vague_generated_text(subject) or self._is_vague_generated_text(action_text):
            action_text = ""
            missing.add("action_text")
        if not employee_query:
            missing.add("employee_query")
        if not action_text:
            missing.add("action_text")

        return {
            "type": entity_type,
            "employee_query": employee_query or "",
            "subject": subject or action_text or ("Задача" if entity_type == "task" else "Поручение"),
            "action_text": action_text or "",
            "deadline": deadline or "",
            "missing_fields": sorted(missing),
            "source": "llm",
        }

    def _entity_type_from_message(self, message: str) -> str | None:
        lowered = message.lower()
        if re.search(r"\b(?:задач\w*|задани\w*)\b", lowered):
            return "task"
        if re.search(r"\bпоручен\w*\b", lowered):
            return "action_item"
        return None

    def _clean_optional_text(self, value: Any) -> str:
        if not isinstance(value, str):
            return ""
        return re.sub(r"\s+", " ", value).strip(" \t\r\n.,;:!?\"'«»")

    def _is_vague_generated_text(self, value: str | None) -> bool:
        normalized = self._clean_optional_text(value).lower()
        normalized = normalized.rstrip(".")
        return normalized in {
            "",
            "подготовить поручение",
            "поручение",
            "подготовить задание",
            "задание",
            "задача",
            "необходимо выполнить задачу согласно заданию",
            "выполнить задачу согласно заданию",
        }

    def _missing_create_draft_message(self, draft: dict[str, Any]) -> str | None:
        missing = set(draft.get("missing_fields") or [])
        entity_label = "задачи" if draft.get("type") == "task" else "поручения"
        if "employee_query" in missing:
            return f"Для подготовки {entity_label} нужен исполнитель."
        if "action_text" in missing or not draft.get("action_text"):
            return f"Для подготовки {entity_label} нужен конкретный текст: что именно должен сделать исполнитель?"
        return None

    def _apply_context_to_create_draft(
        self,
        message: str,
        draft: dict[str, Any] | None,
        history: Iterable[dict[str, str]] | None,
    ) -> dict[str, Any] | None:
        if draft is None:
            return None
        if not self._needs_context_for_create_draft(message, draft):
            return draft
        previous = self._latest_create_context(history)
        if previous is None:
            return draft

        merged = {**draft}
        if not merged.get("employee_query") or self._uses_pronoun_employee(message):
            merged["employee_query"] = previous.get("employee_query", "")
        if not merged.get("action_text"):
            merged["action_text"] = previous.get("action_text", "")
        if self._is_vague_generated_text(merged.get("subject")) and previous.get("subject"):
            merged["subject"] = previous["subject"]
        if not merged.get("deadline") and not merged.get("deadline_text"):
            merged["deadline"] = previous.get("deadline") or previous.get("deadline_text") or ""
        missing = set(merged.get("missing_fields") or [])
        if merged.get("employee_query"):
            missing.discard("employee_query")
        if merged.get("action_text"):
            missing.discard("action_text")
        merged["missing_fields"] = sorted(missing)
        return merged

    def _needs_context_for_create_draft(self, message: str, draft: dict[str, Any]) -> bool:
        normalized = message.lower()
        return (
            "такое же" in normalized
            or self._uses_pronoun_employee(message)
            or not draft.get("employee_query")
            or not draft.get("action_text")
        )

    def _uses_pronoun_employee(self, message: str) -> bool:
        return re.search(r"\b(?:нее|ней|него|нему|ним|её|его)\b", message.lower()) is not None

    def _latest_create_context(self, history: Iterable[dict[str, str]] | None) -> dict[str, Any] | None:
        items = list(history or [])
        for item in reversed(items):
            if item.get("role") == "assistant":
                draft = self._preview_draft_from_assistant_content(item.get("content", ""))
                if draft is not None:
                    return draft
        for item in reversed(items):
            if item.get("role") == "user":
                draft = self._parse_action_item_draft(item.get("content", ""))
                if draft is not None and draft.get("action_text"):
                    return draft
        return None

    def _preview_draft_from_assistant_content(self, content: str) -> dict[str, Any] | None:
        marker = self._preview_marker_payload(content)
        if marker is not None:
            payload = marker.get("payload") if isinstance(marker.get("payload"), dict) else {}
            display = marker.get("display") if isinstance(marker.get("display"), dict) else {}
            return {
                "type": marker.get("type") or "action_item",
                "employee_query": display.get("performer_name") or str(payload.get("performer_id") or ""),
                "subject": payload.get("subject") or payload.get("action_text") or "",
                "action_text": payload.get("action_text") or payload.get("subject") or "",
                "deadline": payload.get("deadline") or "",
                "source": "history",
            }

        match = re.search(
            r"preview\s+(задачи|поручения)\s+для\s+(.+?):\s+(.+?)(?:\.\s+Для|\s*$)",
            content,
            flags=re.IGNORECASE,
        )
        if match is None:
            return None
        return {
            "type": "task" if "задач" in match.group(1).lower() else "action_item",
            "employee_query": self._clean_employee_query(match.group(2)),
            "subject": match.group(3).strip(" ."),
            "action_text": match.group(3).strip(" ."),
            "deadline": "",
            "source": "history",
        }

    def _preview_marker_payload(self, content: str) -> dict[str, Any] | None:
        match = re.search(r"\[\[DIRECTUM_ACTION_ITEM_PREVIEW:([\s\S]*?)\]\]\s*$", content)
        if match is None:
            return None
        try:
            data = json.loads(match.group(1))
        except ValueError:
            return None
        return data if isinstance(data, dict) else None

    def _parse_followup_create_draft(
        self,
        message: str,
        history: Iterable[dict[str, str]] | None,
    ) -> dict[str, Any] | None:
        draft_type = self._pending_create_type_from_history(history)
        if draft_type is None:
            return None

        employee_match = re.search(
            r"(?:исполн?итель|испонитель)\s*[-–—:]?\s*(.+?)(?=\s*,?\s*(?:срок|$))",
            message,
            flags=re.IGNORECASE,
        )
        if employee_match is None:
            return None
        employee_query = re.split(r"\bсрок", employee_match.group(1), maxsplit=1, flags=re.IGNORECASE)[0]
        employee_query = self._clean_employee_query(employee_query)

        deadline_text = ""
        deadline_match = re.search(r"\bсрок(?:ом)?\s*[-–—:]?\s*(.+?)\s*$", message, flags=re.IGNORECASE)
        if deadline_match is not None:
            deadline_text = deadline_match.group(1).strip()

        theme_match = re.search(
            r"\bтема\s*[-–—:]?\s*(.+?)(?=\s*,?\s*(?:исполн?итель|испонитель|срок|$))",
            message,
            flags=re.IGNORECASE,
        )
        if theme_match is not None:
            subject = theme_match.group(1).strip(" .,;")
        else:
            subject = re.split(r"\b(?:исполн?итель|испонитель)\b", message, maxsplit=1, flags=re.IGNORECASE)[0]
            subject = subject.strip(" .,;:-–—")

        subject, inline_deadline = self._split_trailing_deadline(subject, deadline_text)
        return {
            "type": draft_type,
            "employee_query": employee_query,
            "subject": subject,
            "action_text": subject,
            "deadline_text": inline_deadline,
            "source": "followup",
        }

    def _pending_create_type_from_history(self, history: Iterable[dict[str, str]] | None) -> str | None:
        for item in reversed(list(history or [])[-6:]):
            if item.get("role") != "assistant":
                continue
            content = item.get("content", "").lower()
            if "нужен конкретный текст" in content or "укажите тему" in content:
                if "поручен" in content:
                    return "action_item"
                if "задач" in content or "задан" in content:
                    return "task"
        return None

    def _normalize_draft_deadline(self, draft: dict[str, Any]) -> str | None:
        raw_deadline = draft.get("deadline") or draft.get("deadline_text") or ""
        if not isinstance(raw_deadline, str) or not raw_deadline.strip():
            return None
        parsed = self._parse_deadline(raw_deadline)
        if parsed is not None:
            return parsed.isoformat()
        return raw_deadline.strip()

    def _action_item_preview_marker(
        self,
        payload: dict[str, Any],
        performer_name: str,
        preview_type: str = "action_item",
        correct_text: bool = True,
    ) -> str:
        corrected = self._action_item_text_to_imperative(payload) if correct_text else payload
        preview = {
            "type": preview_type,
            "payload": corrected,
            "display": {"performer_name": performer_name},
        }
        return f"[[{ACTION_ITEM_PREVIEW_MARKER}:{json.dumps(preview, ensure_ascii=False, default=str)}]]"

    def _action_item_text_to_imperative(self, payload: dict[str, Any]) -> dict[str, Any]:
        subject = payload.get("subject", "")
        action_text = payload.get("action_text", "")
        deadline = payload.get("deadline")

        corrected = self._correct_text_via_llm(subject, action_text, deadline)

        final_deadline = corrected.get("deadline") or deadline

        return {
            **payload,
            "subject": corrected["subject"],
            "action_text": corrected["action_text"],
            "deadline": final_deadline,
        }

    def _correct_text_via_llm(self, subject: str, action_text: str, deadline: str | None = None) -> dict[str, str]:
        deadline_hint = f"\nСрок (ISO 8601 с timezone, например '2026-06-26T23:59:00+00:00'): {deadline}" if deadline else ""
        correction_prompt = (
            "Ты — эксперт по деловой переписке на русском языке. Преобразуй текст поручения в правильную форму.\n"
            "Правила:\n"
            "- Тема: существительное (отглагольное) с большой буквы, без точки в конце, БЕЗ даты в тексте темы, максимум 100 символов. "
            "Пример: 'Подготовка документов для Аппарата правительства'\n"
            "- Текст поручения: глагол в повелительном наклонении (множественное число) с большой буквы, с точкой в конце, БЕЗ даты в тексте, максимум 500 символов. "
            "Пример: 'Подготовьте документы для Аппарата правительства.'\n"
            "- Срок: если в тексте есть конкретная дата (например '26.06' или '26.06.2026'), извлеки её в формат ISO 8601 с timezone (например '2026-06-26T23:59:00+00:00'). Если срок указан словами ('завтра', 'послезавтра', 'через неделю') или отсутствует — верни null.\n"
            f"Исходная тема: {subject}\n"
            f"Исходный текст: {action_text}\n"
            f"{deadline_hint}\n"
            "Верни ТОЛЬКО JSON: {\"subject\": \"...\", \"action_text\": \"...\", \"deadline\": \"...\"}"
        )
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "Ты возвращаешь только валидный JSON без markdown-разметки."},
                    {"role": "user", "content": correction_prompt},
                ],
                temperature=0.1,
                max_tokens=300,
            )
            raw = response.choices[0].message.content or "{}"
            raw = raw.strip()
            if raw.startswith("```"):
                raw = re.sub(r"```json\s*", "", raw)
                raw = re.sub(r"\s*```", "", raw)
            result = json.loads(raw)
            return {
                "subject": str(result.get("subject", subject)),
                "action_text": str(result.get("action_text", action_text)),
                "deadline": result.get("deadline"),
            }
        except Exception:
            return {"subject": subject, "action_text": action_text, "deadline": deadline}

    def _clean_employee_query(self, value: str) -> str:
        return value.strip(EMPLOYEE_QUERY_STRIP_CHARS)

    def _split_trailing_deadline(self, text: str, deadline_text: str = "") -> tuple[str, str]:
        cleaned_text = text.strip()
        cleaned_deadline = deadline_text.strip()
        if cleaned_deadline:
            return cleaned_text.strip(" ."), cleaned_deadline
        match = re.search(
            (
                r"^(.+?)(?:[.,;]\s+|\s+)"
                r"\u0441\u0440\u043e\u043a(?:\u043e\u043c)?\s*[-\u2013\u2014:]?\s*(.+?)\s*$"
            ),
            cleaned_text,
            flags=re.IGNORECASE,
        )
        if match is None:
            return cleaned_text.strip(" ."), ""
        return match.group(1).strip(" ."), match.group(2).strip()

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
                CREATE_VERB_PATTERN + r"\s+"
                r"(\u0437\u0430\u0434\u0430\u0447\u0443|\u0437\u0430\u0434\u0430\u043d\u0438[ея]|\u043f\u043e\u0440\u0443\u0447\u0435\u043d\u0438[ея])\s+"
                r"(?:\u0434\u043b\u044f|\u043d\u0430)\s+(.+?)\s+[\u0022\u00ab](.+?)[\u0022\u00bb]\s*"
                r"(?:,?\s*\u0441\u0440\u043e\u043a\s*[-\u2013\u2014:]?\s*(.+))?$"
            ),
            message,
            flags=re.IGNORECASE,
        )
        if quoted_match is not None:
            subject, deadline_text = self._split_trailing_deadline(
                quoted_match.group(3),
                quoted_match.group(4) or "",
            )
            return {
                "type": self._draft_type(quoted_match.group(1)),
                "employee_query": self._clean_employee_query(quoted_match.group(2)),
                "subject": subject,
                "action_text": subject,
                "deadline_text": deadline_text,
            }

        natural_match = re.search(
            (
                CREATE_VERB_PATTERN + r"\s+"
                r"(\u0437\u0430\u0434\u0430\u0447\u0443|\u0437\u0430\u0434\u0430\u043d\u0438[ея]|\u043f\u043e\u0440\u0443\u0447\u0435\u043d\u0438[ея])\s+"
                r"(?:\u0434\u043b\u044f|\u043d\u0430)\s+([^,]+?),?\s+"
                r"\u0447\u0442\u043e\u0431\u044b\s+(?:\u043e\u043d|"
                r"\u043e\u043d\u0430)\s+(.+?)\s+"
                r"(?:(?:\u0441\u043e\s+)?\u0441\u0440\u043e\u043a(?:\u043e\u043c)?\s*[-\u2013\u2014:]?|\u043a)\s*(.+)$"
            ),
            message,
            flags=re.IGNORECASE,
        )
        if natural_match is not None:
            subject, deadline_text = self._split_trailing_deadline(natural_match.group(3), natural_match.group(4))
            return {
                "type": self._draft_type(natural_match.group(1)),
                "employee_query": self._clean_employee_query(natural_match.group(2)),
                "subject": subject,
                "action_text": subject,
                "deadline_text": deadline_text,
            }

        theme_match = re.search(
            (
                CREATE_VERB_PATTERN + r"\s+"
                r"(\u0437\u0430\u0434\u0430\u0447\u0443|\u0437\u0430\u0434\u0430\u043d\u0438[ея]|\u043f\u043e\u0440\u0443\u0447\u0435\u043d\u0438[ея])\s+"
                r"(?:\u0434\u043b\u044f|\u043d\u0430)\s+([^,]+),\s*"
                r"\u0442\u0435\u043c\u0430\s*[-\u2013\u2014:]?\s*(.+?)"
                r"(?:,\s*\u0441\u0440\u043e\u043a\s*[-\u2013\u2014:]?\s*(.+))?$"
            ),
            message,
            flags=re.IGNORECASE,
        )
        if theme_match is not None:
            subject, deadline_text = self._split_trailing_deadline(
                theme_match.group(3),
                theme_match.group(4) or "",
            )
            return {
                "type": self._draft_type(theme_match.group(1)),
                "employee_query": self._clean_employee_query(theme_match.group(2)),
                "subject": subject,
                "deadline_text": deadline_text,
            }

        create_match = re.search(
            (
                CREATE_VERB_PATTERN + r"\s+"
                r"(\u0437\u0430\u0434\u0430\u0447\u0443|\u0437\u0430\u0434\u0430\u043d\u0438[ея]|\u043f\u043e\u0440\u0443\u0447\u0435\u043d\u0438[ея])\s+"
                r"(?:\u0434\u043b\u044f|\u043d\u0430)\s+([^,]+),\s*(.+?)"
                r"(?:,\s*\u0441\u0440\u043e\u043a\s*[-\u2013\u2014:]?\s*(.+))?$"
            ),
            message,
            flags=re.IGNORECASE,
        )
        if create_match is not None:
            subject, deadline_text = self._split_trailing_deadline(
                create_match.group(3),
                create_match.group(4) or "",
            )
            return {
                "type": self._draft_type(create_match.group(1)),
                "employee_query": self._clean_employee_query(create_match.group(2)),
                "subject": subject,
                "action_text": subject,
                "deadline_text": deadline_text,
            }

        performer_only_match = re.search(
            (
                CREATE_VERB_PATTERN + r"\s+"
                r"(\u0437\u0430\u0434\u0430\u0447\u0443|\u0437\u0430\u0434\u0430\u043d\u0438[ея]|\u043f\u043e\u0440\u0443\u0447\u0435\u043d\u0438[ея])\s+"
                r"(?:\u0434\u043b\u044f|\u043d\u0430)\s+([^,]+?)\s*$"
            ),
            message,
            flags=re.IGNORECASE,
        )
        if performer_only_match is not None:
            return {
                "type": self._draft_type(performer_only_match.group(1)),
                "employee_query": self._clean_employee_query(performer_only_match.group(2)),
                "subject": "\u041f\u043e\u0440\u0443\u0447\u0435\u043d\u0438\u0435",
                "deadline_text": "",
            }

        performer_match = re.search(
            r"^\s*(.+?)[.!?]?\s+\u0438\u0441\u043f\u043e\u043b\u043d\u0438\u0442\u0435\u043b\u044c\s+(.+?)\s*$",
            message,
            flags=re.IGNORECASE,
        )
        if performer_match is not None:
            action_text = performer_match.group(1).strip()
            return {
                "type": "task",
                "employee_query": self._clean_employee_query(performer_match.group(2)),
                "subject": action_text,
                "action_text": action_text,
                "deadline_text": "",
            }

        return None

    def _draft_type(self, entity_word: str) -> str:
        lowered = entity_word.lower()
        return "task" if "\u0437\u0430\u0434\u0430\u0447" in lowered or "\u0437\u0430\u0434\u0430\u043d" in lowered else "action_item"

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

        lines: list[dict[str, str]] = []
        for item in items:
            if hasattr(item, "model_dump"):
                item = item.model_dump(mode="json")
            if isinstance(item, dict):
                title = item.get("subject") or item.get("name") or item.get("message") or str(item)
                status = item.get("status") or item.get("mode") or item.get("entity_type")
                deadline = item.get("deadline")
                lines.append(
                    {
                        "title": str(title),
                        "status": str(status) if status else "",
                        "deadline": self._format_deadline_for_display(deadline),
                        "url": item.get("url") or "",
                    }
                )
            else:
                lines.append({"title": str(item), "status": "", "deadline": "", "url": ""})

        if len(lines) == 1:
            line = lines[0]
            suffix = f" ({line['status']})" if line["status"] else ""
            return f"\u041d\u0430\u0439\u0434\u0435\u043d\u043e 1: {line['title']}{suffix}."

        markdown_lines = [f"\u041d\u0430\u0439\u0434\u0435\u043d\u043e {len(lines)}:"]
        for index, line in enumerate(lines, start=1):
            details = []
            if line["status"]:
                details.append(f"\u0441\u0442\u0430\u0442\u0443\u0441: {line['status']}")
            if line["deadline"]:
                details.append(f"\u0441\u0440\u043e\u043a: {line['deadline']}")
            details_text = f" — {', '.join(details)}" if details else ""
            markdown_lines.append(f"{index}. {self._markdown_item_title(line['title'], line.get('url'))}{details_text}")
        return "\n\n".join([markdown_lines[0], "\n".join(markdown_lines[1:])])

    def _format_deadline_for_display(self, value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, datetime):
            return value.strftime("%d.%m.%Y")
        if isinstance(value, str) and value.strip():
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00")).strftime("%d.%m.%Y")
            except ValueError:
                pass
            parsed = self._parse_deadline(value)
            if parsed is not None:
                return parsed.strftime("%d.%m.%Y")
            return value.strip()
        return ""

    def _escape_markdown_text(self, value: str) -> str:
        return re.sub(r"([\\`*_{}\[\]()#])", r"\\\1", value)

    def _markdown_item_title(self, title: str, url: Any = None) -> str:
        escaped_title = self._escape_markdown_text(title)
        if isinstance(url, str) and url.strip():
            return f"[**{escaped_title}**](<{url.strip()}>)"
        return f"**{escaped_title}**"

    def _format_outgoing_action_item_analytics(self, result: Any) -> str:
        items = self._normalize_tool_items(result)
        now = datetime.now(timezone.utc)
        due_soon_limit = now + timedelta(days=1)
        categories: dict[str, list[dict[str, Any]]] = {
            "work": [],
            "due_soon": [],
            "overdue": [],
        }

        for item in items:
            deadline = self._deadline_from_item(item)
            if deadline is not None and deadline < now:
                categories["overdue"].append(item)
            elif deadline is not None and deadline <= due_soon_limit:
                categories["due_soon"].append(item)
            else:
                categories["work"].append(item)

        sections = [
            "## Аналитика по исходящим поручениям",
            f"Всего исходящих поручений: **{len(items)}**.",
            self._analytics_section("Поручения в работе", categories["work"], "work"),
            self._analytics_section("Срок подходит к концу (остался один день)", categories["due_soon"], "due-soon"),
            self._analytics_section("Просроченные поручения", categories["overdue"], "overdue"),
        ]
        return "\n\n".join(sections)

    def _normalize_tool_items(self, result: Any) -> list[dict[str, Any]]:
        raw_items = result if isinstance(result, list) else [result]
        items: list[dict[str, Any]] = []
        for item in raw_items:
            if hasattr(item, "model_dump"):
                item = item.model_dump(mode="json")
            if isinstance(item, dict):
                items.append(item)
            else:
                items.append({"subject": str(item)})
        return items

    def _analytics_section(self, title: str, items: list[dict[str, Any]], tone: str) -> str:
        heading = f'### <span class="analytics-heading analytics-heading-{tone}">{title}</span>'
        if not items:
            return f"{heading}\nНет поручений."
        lines = [heading]
        for index, item in enumerate(items, start=1):
            lines.append(f"{index}. {self._format_analytics_item(item)}")
        return "\n".join(lines)

    def _format_analytics_item(self, item: dict[str, Any]) -> str:
        title = str(item.get("subject") or item.get("name") or item.get("message") or item)
        details = []
        status = item.get("status") or item.get("mode") or item.get("entity_type")
        if status:
            details.append(f"статус: {status}")
        deadline = self._format_deadline_for_display(item.get("deadline"))
        details.append(f"срок: {deadline or 'не указан'}")
        return f"{self._markdown_item_title(title, item.get('url'))} — {', '.join(details)}"

    def _deadline_from_item(self, item: dict[str, Any]) -> datetime | None:
        raw = item.get("deadline")
        if raw is None:
            return None
        if isinstance(raw, datetime):
            return raw if raw.tzinfo is not None and raw.utcoffset() is not None else raw.replace(tzinfo=timezone.utc)
        if not isinstance(raw, str) or not raw.strip():
            return None
        try:
            parsed = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
        except ValueError:
            parsed = self._parse_deadline(raw)
        if parsed is None:
            return None
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed

    def _safe_directum_error_message(self, exc: Exception) -> str:
        message = str(exc)
        message = re.sub(r"Basic [A-Za-z0-9+/=]{8,}", "Basic [redacted]", message)
        return f"Directum RX request failed: {message}"
