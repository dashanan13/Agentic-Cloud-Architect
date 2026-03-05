from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from typing import Any, Mapping
from urllib import error as urllib_error
from urllib import parse as urllib_parse
from urllib import request as urllib_request

from azure.identity import ClientSecretCredential

from Agents.AzureAIFoundry.foundry_bootstrap import (
    FoundryConfigurationError,
    FoundryConnectionSettings,
    FoundryRequestError,
    DEFAULT_FOUNDRY_API_VERSION,
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
        self._credential = ClientSecretCredential(
            tenant_id=settings.tenant_id,
            client_id=settings.client_id,
            client_secret=settings.client_secret,
        )

    def post_message(self, thread_id: str, content: str, role: str = DEFAULT_THREAD_MESSAGE_ROLE) -> ThreadMessageResult:
        safe_thread_id = str(thread_id or "").strip()
        if not safe_thread_id:
            raise FoundryConfigurationError("thread_id is required")

        message = str(content or "").strip()
        if not message:
            raise FoundryConfigurationError("message content is required")

        payload = {
            "role": str(role or DEFAULT_THREAD_MESSAGE_ROLE).strip() or DEFAULT_THREAD_MESSAGE_ROLE,
            "content": message,
        }

        response = self._request_json(
            "POST",
            f"/threads/{urllib_parse.quote(safe_thread_id)}/messages",
            payload=payload,
            expected_status=(200, 201),
        )

        message_id = str(response.get("id") or "").strip()
        if not message_id:
            raise FoundryRequestError("Foundry message create response did not include id")

        return ThreadMessageResult(thread_id=safe_thread_id, message_id=message_id)

    def _request_json(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        expected_status: tuple[int, ...] = (200,),
    ) -> dict[str, Any]:
        preferred_version = str(self.settings.api_version or "").strip() or DEFAULT_FOUNDRY_API_VERSION
        versions_to_try = [preferred_version]
        for candidate in (DEFAULT_FOUNDRY_API_VERSION, "2025-05-15-preview"):
            if candidate not in versions_to_try:
                versions_to_try.append(candidate)

        for index, api_version in enumerate(versions_to_try):
            query = urllib_parse.urlencode({"api-version": api_version})
            url = f"{self.settings.endpoint}{path}?{query}"
            headers = {
                "Authorization": f"Bearer {self._get_access_token()}",
                "Accept": "application/json",
            }

            body: bytes | None = None
            if payload is not None:
                headers["Content-Type"] = "application/json"
                body = json.dumps(payload).encode("utf-8")

            request = urllib_request.Request(url, method=method, headers=headers, data=body)
            try:
                with urllib_request.urlopen(request, timeout=self.timeout_seconds) as response:
                    if response.status not in expected_status:
                        raise FoundryRequestError(
                            f"Unexpected status {response.status} for {method} {path}",
                            status_code=response.status,
                        )

                    text = response.read().decode("utf-8", errors="ignore").strip()
                    if not text:
                        return {}

                    try:
                        parsed = json.loads(text)
                        if isinstance(parsed, dict):
                            return parsed
                        return {"data": parsed}
                    except json.JSONDecodeError:
                        return {}
            except urllib_error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="ignore") if exc.fp else ""
                if (
                    exc.code == 400
                    and "api version not supported" in detail.lower()
                    and index < len(versions_to_try) - 1
                ):
                    continue

                raise FoundryRequestError(
                    f"Foundry request failed for {method} {path}: HTTP {exc.code}",
                    status_code=exc.code,
                    detail=detail,
                ) from exc
            except urllib_error.URLError as exc:
                raise FoundryRequestError(f"Foundry request failed for {method} {path}: {exc}") from exc

        raise FoundryRequestError(f"Foundry request failed for {method} {path}: unsupported API version")

    def _get_access_token(self) -> str:
        token = self._credential.get_token("https://ai.azure.com/.default")
        value = str(token.token or "").strip()
        if not value:
            raise FoundryRequestError("Failed to acquire Azure AI token")
        return value


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


def _is_azure_foundry_provider(settings: Mapping[str, Any]) -> bool:
    provider = str(settings.get("modelProvider") or "ollama-local").strip().lower()
    return provider == "azure-foundry"
