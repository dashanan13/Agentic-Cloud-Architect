from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import re
import time
from pathlib import Path
from typing import Any, Mapping

from Agents.AzureAIFoundry.foundry_bootstrap import (
    FoundryConfigurationError,
    FoundryConnectionSettings,
    FoundryRequestError,
)

QUALITY_LEVELS = ["Poor", "Minimal", "Adequate", "Informative", "Rich", "Perfect"]
QUALITY_SIGNALS = ["purpose", "users", "components", "data", "scale", "nonFunctional"]
MIN_WORDS_ADEQUATE = 25


def _load_agent_description(agent_name: str = "architecture-validation-agent") -> str:
    """Load agent description from markdown file. Converts hyphens to underscores for filename lookup."""
    try:
        # Convert agent name hyphens to underscores for file lookup
        # e.g., "architecture-validation-agent" -> "architecture_validation_agent.md"
        filename = str(agent_name or "architecture-validation-agent").replace("-", "_") + ".md"
        agent_description_file = Path(__file__).parent.parent / filename
        if agent_description_file.exists():
            content = agent_description_file.read_text(encoding="utf-8", errors="replace")
            if content.strip():
                return content.strip()
    except Exception:
        pass
    return "You are a helpful Azure cloud architecture assistant."


@dataclass(frozen=True)
class AssistantRunResult:
    thread_id: str
    run_id: str
    message_id: str
    response_text: str


class FoundryAssistantRunner:
    def __init__(self, settings: FoundryConnectionSettings, timeout_seconds: int = 20, agent_name: str = "architecture-validation-agent"):
        self.settings = settings
        self.timeout_seconds = timeout_seconds
        self.agent_name = str(agent_name or "architecture-validation-agent").strip() or "architecture-validation-agent"

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

        return _run_sync(
            self._run_assistant_with_framework(
                assistant_id=safe_assistant_id,
                thread_id=safe_thread_id,
                content=message_text,
                role=str(role or "user").strip() or "user",
                poll_interval=float(poll_interval or 0.6),
            )
        )

    async def _run_assistant_with_framework(
        self,
        assistant_id: str,
        thread_id: str,
        content: str,
        role: str,
        poll_interval: float,
    ) -> AssistantRunResult:
        del role
        del poll_interval

        try:
            from agent_framework import Agent
            from agent_framework import AgentSession
            from agent_framework.azure import AzureAIAgentClient
            from azure.identity.aio import ClientSecretCredential as AsyncClientSecretCredential
        except ModuleNotFoundError as exc:
            raise FoundryConfigurationError(
                "Python package 'agent-framework-azure-ai' is required for Foundry chat. "
                "Install backend dependencies and rebuild the app container."
            ) from exc

        try:
            async with AsyncClientSecretCredential(
                tenant_id=self.settings.tenant_id,
                client_id=self.settings.client_id,
                client_secret=self.settings.client_secret,
            ) as credential:
                chat_client = AzureAIAgentClient(
                    project_endpoint=self.settings.endpoint,
                    model_deployment_name=self.settings.model_deployment,
                    credential=credential,
                    agent_id=assistant_id,
                )

                try:
                    agent = self._build_framework_agent(Agent, chat_client)
                    async with agent:
                        response = await asyncio.wait_for(
                            self._run_framework_agent(agent, AgentSession, content, thread_id),
                            timeout=float(self.timeout_seconds),
                        )
                finally:
                    close_method = getattr(chat_client, "close", None)
                    if callable(close_method):
                        close_result = close_method()
                        if asyncio.iscoroutine(close_result):
                            await close_result
        except asyncio.TimeoutError as exc:
            raise FoundryRequestError("Foundry run did not complete in time") from exc
        except FoundryRequestError:
            raise
        except Exception as exc:
            raise FoundryRequestError(f"Foundry run failed via Agent Framework: {exc}") from exc

        response_text = self._extract_response_text(response)
        if not response_text:
            raise FoundryRequestError("Foundry run completed, but no assistant response was found")

        run_id = self._extract_response_id(response, "response_id", "run_id", "id")
        if not run_id:
            run_id = f"af-run-{int(time.time() * 1000)}"

        message_id = self._extract_response_id(response, "message_id", "id")
        if not message_id:
            message_id = run_id

        return AssistantRunResult(
            thread_id=thread_id,
            run_id=run_id,
            message_id=message_id,
            response_text=response_text,
        )

    def _build_framework_agent(self, agent_type: Any, chat_client: Any) -> Any:
        agent_instructions = _load_agent_description(self.agent_name)
        attempts = [
            {
                "chat_client": chat_client,
                "name": "architect-agent",
                "instructions": agent_instructions,
            },
            {
                "client": chat_client,
                "name": "architect-agent",
                "instructions": agent_instructions,
            },
        ]
        for kwargs in attempts:
            try:
                return agent_type(**kwargs)
            except TypeError:
                continue

        raise FoundryConfigurationError("Unable to initialize Agent Framework agent for Foundry.")

    async def _run_framework_agent(self, agent: Any, session_type: Any, content: str, thread_id: str) -> Any:
        try:
            return await agent.run(content, conversation_id=thread_id)
        except TypeError:
            session = session_type(service_session_id=thread_id)
            return await agent.run(content, session=session)

    def _extract_response_text(self, response: Any) -> str:
        direct_text = str(getattr(response, "text", "") or "").strip()
        if direct_text:
            return direct_text

        messages = getattr(response, "messages", None)
        if isinstance(messages, list):
            for message in reversed(messages):
                message_text = str(getattr(message, "text", "") or "").strip()
                if message_text:
                    return message_text

        rendered = str(response or "").strip()
        if rendered and rendered.lower() != "none":
            return rendered

        return ""

    def _extract_response_id(self, response: Any, *candidates: str) -> str:
        for candidate in candidates:
            value = str(getattr(response, candidate, "") or "").strip()
            if value:
                return value
        return ""


def _run_sync(coro):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    raise FoundryRequestError("Synchronous Foundry operation called while an event loop is already running")


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
        "Do not invent facts. Preserve user-provided detail; do not shorten if it reduces quality.\n"
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
