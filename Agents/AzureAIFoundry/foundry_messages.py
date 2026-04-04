from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping
from azure.ai.agents.aio import AgentsClient
from azure.ai.agents.models import ListSortOrder
from azure.core.exceptions import HttpResponseError
from azure.identity.aio import ClientSecretCredential as AsyncClientSecretCredential

from Agents.AzureAIFoundry.foundry_bootstrap import (
    FoundryConfigurationError,
    FoundryConnectionSettings,
    FoundryRequestError,
)

DEFAULT_THREAD_MESSAGE_ROLE = "user"
DEFAULT_THREAD_MESSAGE_AUTHOR = "architect-agent"


@dataclass(frozen=True)
class ThreadMessageResult:
    thread_id: str
    message_id: str


class FoundryThreadMessenger:
    def __init__(self, settings: FoundryConnectionSettings, timeout_seconds: int = 20):
        self.settings = settings
        self.timeout_seconds = timeout_seconds

    def post_message(self, thread_id: str, content: str, role: str = DEFAULT_THREAD_MESSAGE_ROLE) -> ThreadMessageResult:
        safe_thread_id = str(thread_id or "").strip()
        if not safe_thread_id:
            raise FoundryConfigurationError("thread_id is required")

        message = str(content or "").strip()
        if not message:
            raise FoundryConfigurationError("message content is required")

        return _run_sync(
            self._post_message_async(
                thread_id=safe_thread_id,
                content=message,
                role=str(role or DEFAULT_THREAD_MESSAGE_ROLE).strip() or DEFAULT_THREAD_MESSAGE_ROLE,
            )
        )

    def list_messages(self, thread_id: str, limit: int | None = None) -> list[dict[str, Any]]:
        safe_thread_id = str(thread_id or "").strip()
        if not safe_thread_id:
            raise FoundryConfigurationError("thread_id is required")

        safe_limit = limit if isinstance(limit, int) and limit > 0 else None
        return _run_sync(self._list_messages_async(safe_thread_id, safe_limit))

    async def _post_message_async(self, thread_id: str, content: str, role: str) -> ThreadMessageResult:
        async with self._agents_client_context() as agents_client:
            try:
                message = await agents_client.messages.create(
                    thread_id=thread_id,
                    role=role,
                    content=content,
                )
            except HttpResponseError as exc:
                self._raise_request_error(exc, "messages.create")
            except Exception as exc:
                raise FoundryRequestError(f"Foundry request failed for messages.create: {exc}") from exc

            message_id = self._as_text(self._model_get(message, "id"))
            if not message_id:
                raise FoundryRequestError("Foundry message create response did not include id")

            return ThreadMessageResult(thread_id=thread_id, message_id=message_id)

    async def _list_messages_async(self, thread_id: str, limit: int | None) -> list[dict[str, Any]]:
        async with self._agents_client_context() as agents_client:
            try:
                pageable = agents_client.messages.list(
                    thread_id=thread_id,
                    order=ListSortOrder.ASCENDING,
                )
                raw_messages: list[Any] = []
                async for item in pageable:
                    raw_messages.append(item)
                    if limit and len(raw_messages) >= limit:
                        break
            except HttpResponseError as exc:
                self._raise_request_error(exc, "messages.list")
            except Exception as exc:
                raise FoundryRequestError(f"Foundry request failed for messages.list: {exc}") from exc

        normalized: list[dict[str, Any]] = []
        has_timestamp = False
        last_role = ""
        last_content = ""
        for index, item in enumerate(raw_messages):
            role = self._normalize_role(self._model_get(item, "role"))
            if role not in {"user", "assistant"}:
                continue

            content_text = _extract_message_text(item)
            content_text = _normalize_message_for_display(role, content_text)
            if not content_text:
                continue

            # Suppress consecutive duplicates (same role + same text)
            if role == last_role and content_text == last_content:
                continue
            last_role = role
            last_content = content_text

            created_at = _coerce_created_at(
                self._model_get(item, "created_at")
                or self._model_get(item, "createdAt")
                or self._model_get(item, "created_on")
            )
            has_timestamp = has_timestamp or created_at > 0

            normalized.append(
                {
                    "id": self._as_text(self._model_get(item, "id")) or f"msg-{index}",
                    "role": role,
                    "content": content_text,
                    "createdAt": created_at,
                    "_sequence": index,
                }
            )

        if has_timestamp:
            normalized.sort(key=lambda message: (int(message.get("createdAt") or 0), int(message.get("_sequence") or 0)))
        else:
            normalized = list(reversed(normalized))

        if limit and len(normalized) > limit:
            normalized = normalized[-limit:]

        for message in normalized:
            message.pop("_sequence", None)

        return normalized

    @asynccontextmanager
    async def _agents_client_context(self):
        async with AsyncClientSecretCredential(
            tenant_id=self.settings.tenant_id,
            client_id=self.settings.client_id,
            client_secret=self.settings.client_secret,
        ) as credential:
            async with AgentsClient(
                endpoint=self.settings.endpoint,
                credential=credential,
            ) as agents_client:
                yield agents_client

    def _model_get(self, model: Any, key: str) -> Any:
        if hasattr(model, "get"):
            try:
                value = model.get(key)
                if value is not None:
                    return value
            except Exception:
                pass

        value = getattr(model, key, None)
        if value is not None:
            return value

        as_dict = getattr(model, "as_dict", None)
        if callable(as_dict):
            try:
                payload = as_dict()
                if isinstance(payload, Mapping):
                    return payload.get(key)
            except Exception:
                pass

        return None

    def _normalize_role(self, value: Any) -> str:
        text = self._as_text(value).lower()
        if "." in text:
            text = text.split(".")[-1]
        return text

    def _as_text(self, value: Any) -> str:
        if value is None:
            return ""
        return str(value).strip()

    def _status_code(self, exc: HttpResponseError) -> int:
        code = getattr(exc, "status_code", None)
        try:
            return int(code or 0)
        except (TypeError, ValueError):
            return 0

    def _raise_request_error(self, exc: HttpResponseError, operation: str) -> None:
        status = self._status_code(exc)
        detail = str(exc)
        raise FoundryRequestError(
            f"Foundry request failed for {operation}: HTTP {status or 'unknown'}",
            status_code=status or None,
            detail=detail,
        ) from exc


def _run_sync(coro):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    raise FoundryRequestError("Synchronous Foundry operation called while an event loop is already running")


def post_project_created_message(
    app_settings: Mapping[str, Any],
    thread_id: str,
    project_name: str,
    project_id: str,
    created_at: datetime | None = None,
) -> dict:
    return _post_project_event_message(
        app_settings,
        thread_id=thread_id,
        project_name=project_name,
        project_id=project_id,
        timestamp=created_at,
        event="created",
    )


def post_project_deleted_message(
    app_settings: Mapping[str, Any],
    thread_id: str,
    project_name: str,
    project_id: str,
    deleted_at: datetime | None = None,
) -> dict:
    return _post_project_event_message(
        app_settings,
        thread_id=thread_id,
        project_name=project_name,
        project_id=project_id,
        timestamp=deleted_at,
        event="deleted",
    )


def _post_project_event_message(
    app_settings: Mapping[str, Any],
    thread_id: str,
    project_name: str,
    project_id: str,
    timestamp: datetime | None,
    event: str,
) -> dict:
    if not _is_azure_foundry_provider(app_settings):
        return {
            "ok": True,
            "skipped": True,
            "reason": "provider-not-azure-foundry",
        }

    safe_thread_id = str(thread_id or "").strip()
    if not safe_thread_id:
        return {
            "ok": True,
            "skipped": True,
            "reason": "thread-id-missing",
        }

    try:
        connection = FoundryConnectionSettings.from_app_settings(app_settings)
        messenger = FoundryThreadMessenger(connection)
        timestamp_text = _format_timestamp(timestamp)
        payload = _build_project_event_message(project_name, project_id, timestamp_text, event)
        result = messenger.post_message(safe_thread_id, payload)
        return {
            "ok": True,
            "skipped": False,
            "threadId": result.thread_id,
            "messageId": result.message_id,
        }
    except FoundryConfigurationError as exc:
        return {
            "ok": True,
            "skipped": True,
            "reason": "configuration-incomplete",
            "detail": str(exc),
        }
    except FoundryRequestError as exc:
        return {
            "ok": False,
            "skipped": False,
            "reason": "request-failed",
            "statusCode": exc.status_code,
            "detail": f"{exc} {str(exc.detail or '')[:800]}".strip(),
        }


def list_thread_messages(
    app_settings: Mapping[str, Any],
    thread_id: str,
    limit: int | None = 300,
) -> dict:
    if not _is_azure_foundry_provider(app_settings):
        return {
            "ok": True,
            "skipped": True,
            "reason": "provider-not-azure-foundry",
            "messages": [],
        }

    safe_thread_id = str(thread_id or "").strip()
    if not safe_thread_id:
        return {
            "ok": True,
            "skipped": True,
            "reason": "thread-id-missing",
            "messages": [],
        }

    safe_limit = limit if isinstance(limit, int) and limit > 0 else 300

    try:
        connection = FoundryConnectionSettings.from_app_settings(app_settings)
        messenger = FoundryThreadMessenger(connection)
        messages = messenger.list_messages(safe_thread_id, limit=safe_limit)
        return {
            "ok": True,
            "skipped": False,
            "threadId": safe_thread_id,
            "messages": messages,
        }
    except FoundryConfigurationError as exc:
        return {
            "ok": True,
            "skipped": True,
            "reason": "configuration-incomplete",
            "detail": str(exc),
            "messages": [],
        }
    except FoundryRequestError as exc:
        return {
            "ok": False,
            "skipped": False,
            "reason": "request-failed",
            "statusCode": exc.status_code,
            "detail": f"{exc} {str(exc.detail or '')[:800]}".strip(),
            "messages": [],
        }


def post_thread_activity_message(
    app_settings: Mapping[str, Any],
    thread_id: str,
    actor: str,
    activity_type: str,
    content: str,
    created_at: datetime | None = None,
) -> dict:
    if not _is_azure_foundry_provider(app_settings):
        return {
            "ok": True,
            "skipped": True,
            "reason": "provider-not-azure-foundry",
        }

    safe_thread_id = str(thread_id or "").strip()
    if not safe_thread_id:
        return {
            "ok": True,
            "skipped": True,
            "reason": "thread-id-missing",
        }

    safe_actor = str(actor or "activity").strip() or "activity"
    safe_type = str(activity_type or "event").strip() or "event"
    safe_content = str(content or "").strip()
    timestamp_text = _format_timestamp(created_at)

    message = "\n".join(
        [
            f"[{safe_actor}] {safe_type}",
            f"Timestamp (UTC): {timestamp_text}",
            safe_content,
        ]
    ).strip()

    try:
        connection = FoundryConnectionSettings.from_app_settings(app_settings)
        messenger = FoundryThreadMessenger(connection)
        result = messenger.post_message(safe_thread_id, message)
        return {
            "ok": True,
            "skipped": False,
            "threadId": result.thread_id,
            "messageId": result.message_id,
        }
    except FoundryConfigurationError as exc:
        return {
            "ok": True,
            "skipped": True,
            "reason": "configuration-incomplete",
            "detail": str(exc),
        }
    except FoundryRequestError as exc:
        return {
            "ok": False,
            "skipped": False,
            "reason": "request-failed",
            "statusCode": exc.status_code,
            "detail": f"{exc} {str(exc.detail or '')[:800]}".strip(),
        }


def _build_project_event_message(project_name: str, project_id: str, timestamp_text: str, event: str) -> str:
    safe_name = str(project_name or "").strip() or "(unknown)"
    safe_id = str(project_id or "").strip() or "(unknown)"
    event_text = "created" if str(event or "").strip().lower() == "created" else "deleted"
    return (
        f"[Architect Agent] Project {event_text}.\n"
        f"Project Name: {safe_name}\n"
        f"Project ID: {safe_id}\n"
        f"Timestamp (UTC): {timestamp_text}"
    )


def _format_timestamp(value: datetime | None) -> str:
    dt = value or datetime.utcnow()
    return dt.replace(microsecond=0).isoformat() + "Z"


def _extract_message_text(message: Any) -> str:
    text_messages = getattr(message, "text_messages", None)
    if isinstance(text_messages, list):
        parts: list[str] = []
        for item in text_messages:
            text_obj = getattr(item, "text", None)
            value = str(getattr(text_obj, "value", "") or "").strip()
            if value:
                parts.append(value)
        if parts:
            return "\n".join(parts).strip()

    if not isinstance(message, Mapping):
        as_dict = getattr(message, "as_dict", None)
        if callable(as_dict):
            try:
                payload = as_dict()
                if isinstance(payload, Mapping):
                    message = payload
            except Exception:
                return ""

    if not isinstance(message, Mapping):
        return ""

    content = message.get("content")
    if isinstance(content, str):
        return content.strip()

    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if not isinstance(part, dict):
                continue
            if str(part.get("type") or "").strip().lower() != "text":
                continue

            text_block = part.get("text")
            if isinstance(text_block, dict):
                value = str(text_block.get("value") or "").strip()
                if value:
                    parts.append(value)
                continue

            value = str(text_block or "").strip()
            if value:
                parts.append(value)

        return "\n".join(parts).strip()

    text_block = message.get("text")
    if isinstance(text_block, dict):
        value = str(text_block.get("value") or "").strip()
        if value:
            return value

    return ""


def _looks_like_activity_log_message(content: str) -> bool:
    """Return True for messages posted by post_thread_activity_message.

    Those messages follow the pattern:
        [<actor>] <event-type>
        Timestamp (UTC): <iso-timestamp>
        <json-or-text payload>

    They must not be shown as chat bubbles in the UI.
    """
    safe = str(content or "").strip()
    if not safe.startswith("["):
        return False
    # First line must be of the form  "[something] something"
    first_newline = safe.find("\n")
    if first_newline < 0:
        return False
    first_line = safe[:first_newline].strip()
    # Must open with "[" and contain "] " (closing bracket + space)
    bracket_end = first_line.find("] ")
    if bracket_end < 1:
        return False
    # Second line must start with "Timestamp (UTC):"
    rest = safe[first_newline + 1:]
    second_line = rest.lstrip("\n").split("\n")[0].strip()
    return second_line.startswith("Timestamp (UTC):")


def _normalize_message_for_display(role: str, content: str) -> str:
    """Return clean display text for a thread message, or '' to suppress it."""
    safe_role = str(role or "").strip().lower()
    safe_content = str(content or "").strip()
    if not safe_content:
        return ""

    # --- activity / project lifecycle logs ---
    if safe_content.startswith("[Architect Agent] Project "):
        return ""
    if _looks_like_activity_log_message(safe_content):
        return ""

    # --- classifier JSON responses (assistant role) ---
    if _looks_like_classifier_json(safe_content):
        return ""

    # --- any orchestrator / system / classifier prompt (user role) ---
    if _looks_like_orchestrator_prompt(safe_content):
        if safe_role == "user":
            extracted = _extract_user_text_from_orchestrator_prompt(safe_content)
            return extracted or ""
        return ""  # suppress assistant echoes of prompts

    return safe_content


# ---------------------------------------------------------------------------
# Classifier / orchestrator detection helpers
# ---------------------------------------------------------------------------

def _looks_like_classifier_json(text: str) -> bool:
    """Return True for JSON classifier responses like {"style":...} or {"scope":...}."""
    safe = str(text or "").strip()
    if not safe.startswith("{"):
        return False
    compact = safe.lower().replace(" ", "")
    if '"style"' in compact and '"confidence"' in compact and '"reason"' in compact:
        return True
    if '"scope"' in compact and '"confidence"' in compact and '"reason"' in compact:
        return True
    return False


# Prefixes that indicate an orchestrator / system prompt, NOT a real user message.
_ORCHESTRATOR_PROMPT_PREFIXES = (
    "You are a ",                # greeting, out-of-scope, classifier, familiarization
    "Respond as a ",             # concise / detailed prompts
    "Rewrite the following",     # summarizer prompt
    "[Project Context]",         # conversational prompt with context
    "[Conversation Context]",    # architect prompt with context
    "[Project Data]",            # architect prompt
)


def _looks_like_orchestrator_prompt(text: str) -> bool:
    """Return True for any orchestrator / system / classifier prompt, NOT a real user message."""
    safe = str(text or "").strip()
    if not safe:
        return False
    for prefix in _ORCHESTRATOR_PROMPT_PREFIXES:
        if safe.startswith(prefix):
            return True
    # Context blocks embedded mid-text
    if "[Project Context]" in safe and "[End Context]" in safe:
        return True
    if "[Conversation Context]" in safe and "[End Context]" in safe:
        return True
    # Legacy pattern
    if "You are an Azure cloud architect assistant." in safe:
        return True
    return False


def _extract_user_text_from_orchestrator_prompt(text: str) -> str:
    """Extract the real user question from an orchestrator prompt.

    Tries these strategies in order:
    1. Text after the last 'User message:' / 'User request:' label
    2. Text after '[End Context]' or '[End Project Data]'
    3. Empty string (suppress entirely)
    """
    safe = str(text or "").strip()

    # Strategy 1: explicit label
    labels = ("User message:", "User request:")
    best_index = -1
    best_label_len = 0
    for label in labels:
        idx = safe.rfind(label)
        if idx > best_index:
            best_index = idx
            best_label_len = len(label)
    if best_index >= 0:
        remainder = safe[best_index + best_label_len:].strip()
        first_line = remainder.splitlines()[0].strip() if remainder else ""
        if first_line:
            return first_line

    # Strategy 2: text after context end markers
    for marker in ("[End Context]", "[End Project Data]"):
        idx = safe.rfind(marker)
        if idx >= 0:
            remainder = safe[idx + len(marker):].strip()
            if remainder and not _looks_like_orchestrator_prompt(remainder) and not _looks_like_classifier_json(remainder):
                lines = [line.strip() for line in remainder.splitlines() if line.strip()]
                if lines:
                    return lines[-1]

    return ""


def _extract_tail_after_user_marker(text: str) -> str:
    safe_text = str(text or "")
    if not safe_text:
        return ""

    labels = ["User request:", "User message:"]
    last_index = -1
    for label in labels:
        index = safe_text.rfind(label)
        if index > last_index:
            last_index = index

    if last_index < 0:
        return ""

    line_end = safe_text.find("\n", last_index)
    if line_end < 0:
        return ""

    tail = safe_text[line_end + 1 :].strip()
    if not tail:
        return ""

    if _looks_like_internal_prompt(tail):
        nested = _extract_tail_after_user_marker(tail)
        return nested or ""

    return tail


def _coerce_created_at(value: Any) -> int:
    if isinstance(value, datetime):
        return max(int(value.timestamp()), 0)

    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return max(value, 0)
    if isinstance(value, float):
        return max(int(value), 0)

    text = str(value or "").strip()
    if text.isdigit():
        try:
            return max(int(text), 0)
        except Exception:
            return 0

    return 0


def _is_azure_foundry_provider(settings: Mapping[str, Any]) -> bool:
    provider = str(settings.get("modelProvider") or "ollama-local").strip().lower()
    return provider == "azure-foundry"
