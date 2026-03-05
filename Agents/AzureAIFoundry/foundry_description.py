from __future__ import annotations

from dataclasses import dataclass
import json
import re
import time
from typing import Any, Mapping
from urllib import error as urllib_error
from urllib import parse as urllib_parse
from urllib import request as urllib_request

from azure.identity import ClientSecretCredential

from Agents.AzureAIFoundry.foundry_bootstrap import (
    DEFAULT_FOUNDRY_API_VERSION,
    FoundryConfigurationError,
    FoundryConnectionSettings,
    FoundryRequestError,
)

QUALITY_LEVELS = ["Poor", "Minimal", "Adequate", "Informative", "Rich", "Perfect"]
QUALITY_SIGNALS = ["purpose", "users", "components", "data", "scale", "nonFunctional"]
MIN_WORDS_ADEQUATE = 25


@dataclass(frozen=True)
class AssistantRunResult:
    thread_id: str
    run_id: str
    message_id: str
    response_text: str


class FoundryAssistantRunner:
    def __init__(self, settings: FoundryConnectionSettings, timeout_seconds: int = 20):
        self.settings = settings
        self.timeout_seconds = timeout_seconds
        self._credential = ClientSecretCredential(
            tenant_id=settings.tenant_id,
            client_id=settings.client_id,
            client_secret=settings.client_secret,
        )

    def run_assistant(
        self,
        assistant_id: str,
        thread_id: str,
        content: str,
        role: str = "user",
        poll_interval: float = 0.6,
    ) -> AssistantRunResult:
        safe_assistant_id = str(assistant_id or "").strip()
        safe_thread_id = str(thread_id or "").strip()
        message_text = str(content or "").strip()

        if not safe_assistant_id:
            raise FoundryConfigurationError("assistant_id is required")
        if not safe_thread_id:
            raise FoundryConfigurationError("thread_id is required")
        if not message_text:
            raise FoundryConfigurationError("message content is required")

        message_payload = {
            "role": str(role or "user").strip() or "user",
            "content": message_text,
        }
        message_response = self._request_json(
            "POST",
            f"/threads/{urllib_parse.quote(safe_thread_id)}/messages",
            payload=message_payload,
            expected_status=(200, 201),
        )
        message_id = str(message_response.get("id") or "").strip()
        if not message_id:
            raise FoundryRequestError("Foundry message create response did not include id")

        run_response = self._request_json(
            "POST",
            f"/threads/{urllib_parse.quote(safe_thread_id)}/runs",
            payload={"assistant_id": safe_assistant_id},
            expected_status=(200, 201),
        )
        run_id = str(run_response.get("id") or "").strip()
        if not run_id:
            raise FoundryRequestError("Foundry run create response did not include id")

        status = str(run_response.get("status") or "").strip().lower()
        deadline = time.time() + self.timeout_seconds
        while time.time() < deadline and status not in {"completed", "succeeded"}:
            if status in {"failed", "cancelled", "canceled", "expired"}:
                raise FoundryRequestError(f"Foundry run failed with status {status}")
            time.sleep(poll_interval)
            run_state = self._request_json(
                "GET",
                f"/threads/{urllib_parse.quote(safe_thread_id)}/runs/{urllib_parse.quote(run_id)}",
            )
            status = str(run_state.get("status") or "").strip().lower()

        if status not in {"completed", "succeeded"}:
            raise FoundryRequestError("Foundry run did not complete in time")

        messages_payload = self._request_json(
            "GET",
            f"/threads/{urllib_parse.quote(safe_thread_id)}/messages",
        )
        messages = messages_payload.get("data") if isinstance(messages_payload.get("data"), list) else []
        response_text = _extract_assistant_response(messages, run_id)

        if not response_text:
            raise FoundryRequestError("Foundry run completed, but no assistant response was found")

        return AssistantRunResult(
            thread_id=safe_thread_id,
            run_id=run_id,
            message_id=message_id,
            response_text=response_text,
        )

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


def evaluate_description_with_architect(
    app_settings: Mapping[str, Any],
    description: str,
    assistant_id: str,
    thread_id: str,
    app_type: str | None = None,
    cloud: str | None = None,
) -> dict:
    if not _is_azure_foundry_provider(app_settings):
        return {
            "ok": True,
            "skipped": True,
            "reason": "provider-not-azure-foundry",
        }

    safe_description = str(description or "").strip()
    if not safe_description:
        return {
            "ok": False,
            "skipped": False,
            "reason": "description-missing",
        }

    try:
        connection = FoundryConnectionSettings.from_app_settings(app_settings)
        runner = FoundryAssistantRunner(connection)
        prompt = _build_evaluation_prompt(safe_description, app_type, cloud)
        result = runner.run_assistant(assistant_id=assistant_id, thread_id=thread_id, content=prompt)
        parsed = _parse_json_payload(result.response_text)
        metrics = _compute_quality_metrics(parsed, safe_description)
        return {
            "ok": True,
            "skipped": False,
            **metrics,
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
    except ValueError as exc:
        return {
            "ok": False,
            "skipped": False,
            "reason": "parse-failed",
            "detail": str(exc),
        }


def improve_description_with_architect(
    app_settings: Mapping[str, Any],
    description: str,
    assistant_id: str,
    thread_id: str,
    app_type: str | None = None,
    cloud: str | None = None,
) -> dict:
    if not _is_azure_foundry_provider(app_settings):
        return {
            "ok": True,
            "skipped": True,
            "reason": "provider-not-azure-foundry",
        }

    safe_description = str(description or "").strip()
    if not safe_description:
        return {
            "ok": False,
            "skipped": False,
            "reason": "description-missing",
        }

    try:
        connection = FoundryConnectionSettings.from_app_settings(app_settings)
        runner = FoundryAssistantRunner(connection)
        prompt = _build_improvement_prompt(safe_description, app_type, cloud)
        result = runner.run_assistant(assistant_id=assistant_id, thread_id=thread_id, content=prompt)
        improved = _sanitize_improved_text(result.response_text)
        if not improved:
            raise ValueError("Assistant returned an empty improvement")
        return {
            "ok": True,
            "skipped": False,
            "improved": improved,
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
    except ValueError as exc:
        return {
            "ok": False,
            "skipped": False,
            "reason": "parse-failed",
            "detail": str(exc),
        }


def _build_evaluation_prompt(description: str, app_type: str | None, cloud: str | None) -> str:
    app_type_text = str(app_type or "").strip()
    cloud_text = str(cloud or "").strip()
    context_lines = []
    if app_type_text:
        context_lines.append(f"Application Type: {app_type_text}")
    if cloud_text:
        context_lines.append(f"Cloud: {cloud_text}")
    context = "\n".join(context_lines)

    return (
        "Evaluate the following project description for information richness.\n"
        "Return ONLY JSON with keys: signals (array), specifics (integer), notes (string).\n"
        "Signals must be chosen from: purpose, users, components, data, scale, nonFunctional.\n"
        "Specifics is the count of concrete details (numbers, regions, compliance names, quotas).\n"
        f"{context}\n"
        "Description:\n"
        f"{description}\n"
    )


def _build_improvement_prompt(description: str, app_type: str | None, cloud: str | None) -> str:
    app_type_text = str(app_type or "").strip()
    cloud_text = str(cloud or "").strip()
    context_lines = []
    if app_type_text:
        context_lines.append(f"Application Type: {app_type_text}")
    if cloud_text:
        context_lines.append(f"Cloud: {cloud_text}")
    context = "\n".join(context_lines)

    return (
        "Improve the project description so it is clear, structured, and at least Adequate.\n"
        "Keep it to 2-4 sentences. Do not invent facts.\n"
        f"{context}\n"
        "Original Description:\n"
        f"{description}\n"
        "Return ONLY the improved description text."
    )


def _parse_json_payload(text: str) -> dict:
    raw = str(text or "").strip()
    if not raw:
        raise ValueError("Empty response")

    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if match:
        try:
            parsed = json.loads(match.group(0))
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass

    raise ValueError("Unable to parse JSON from assistant response")


def _compute_quality_metrics(parsed: dict, description: str) -> dict:
    signals = parsed.get("signals") if isinstance(parsed.get("signals"), list) else []
    normalized_signals = []
    for item in signals:
        label = str(item or "").strip()
        if label in QUALITY_SIGNALS and label not in normalized_signals:
            normalized_signals.append(label)

    specifics = parsed.get("specifics")
    specifics_count = int(specifics) if isinstance(specifics, (int, float, str)) and str(specifics).strip().isdigit() else 0

    word_count = len(re.findall(r"\b\w+\b", description))
    level, level_index = _score_quality(len(normalized_signals), word_count, specifics_count)
    missing = [signal for signal in QUALITY_SIGNALS if signal not in normalized_signals]

    score = _score_for_level(level_index)
    notes = str(parsed.get("notes") or "").strip()

    return {
        "level": level,
        "levelIndex": level_index,
        "score": score,
        "signals": normalized_signals,
        "missing": missing,
        "specifics": specifics_count,
        "wordCount": word_count,
        "notes": notes,
    }


def _score_quality(signal_count: int, word_count: int, specifics_count: int) -> tuple[str, int]:
    if signal_count == 6 and specifics_count >= 2:
        return "Perfect", 5
    if signal_count >= 5:
        return "Rich", 4
    if signal_count >= 4:
        return "Informative", 3
    if signal_count >= 3 and word_count >= MIN_WORDS_ADEQUATE:
        return "Adequate", 2
    if signal_count >= 2:
        return "Minimal", 1
    return "Poor", 0


def _score_for_level(level_index: int) -> int:
    score_map = [10, 30, 55, 70, 85, 100]
    return score_map[min(max(level_index, 0), len(score_map) - 1)]


def _sanitize_improved_text(text: str) -> str:
    raw = str(text or "").strip()
    if not raw:
        return ""
    if raw.startswith("\"") and raw.endswith("\""):
        raw = raw[1:-1].strip()
    return raw


def _extract_assistant_response(messages: list[dict[str, Any]], run_id: str) -> str:
    run_match = str(run_id or "").strip()
    assistant_messages = []

    for message in messages:
        if not isinstance(message, dict):
            continue
        if str(message.get("role") or "").strip().lower() != "assistant":
            continue
        if run_match:
            message_run = str(message.get("run_id") or "").strip()
            if message_run and message_run != run_match:
                continue
        assistant_messages.append(message)

    if not assistant_messages:
        assistant_messages = [
            message for message in messages
            if isinstance(message, dict) and str(message.get("role") or "").strip().lower() == "assistant"
        ]

    for message in assistant_messages:
        text = _extract_message_text(message)
        if text:
            return text

    return ""


def _extract_message_text(message: dict[str, Any]) -> str:
    if not isinstance(message, dict):
        return ""

    content = message.get("content")
    if isinstance(content, str):
        return content.strip()

    if isinstance(content, list):
        parts = []
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "text":
                text_block = part.get("text")
                if isinstance(text_block, dict):
                    value = str(text_block.get("value") or "").strip()
                    if value:
                        parts.append(value)
                else:
                    value = str(text_block or "").strip()
                    if value:
                        parts.append(value)
        if parts:
            return "\n".join(parts).strip()

    text_block = message.get("text")
    if isinstance(text_block, dict):
        value = str(text_block.get("value") or "").strip()
        if value:
            return value

    return ""


def _is_azure_foundry_provider(settings: Mapping[str, Any]) -> bool:
    provider = str(settings.get("modelProvider") or "ollama-local").strip().lower()
    return provider == "azure-foundry"
