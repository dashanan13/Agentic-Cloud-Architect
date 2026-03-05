from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Mapping
from urllib import error as urllib_error
from urllib import parse as urllib_parse
from urllib import request as urllib_request

from azure.identity import ClientSecretCredential

DEFAULT_AGENT_NAME = "architect-agent"
DEFAULT_THREAD_NAME = "architect-thread"
DEFAULT_FOUNDRY_API_VERSION = "2025-05-01"
DEFAULT_AGENT_INSTRUCTIONS = (
    "You are an enterprise cloud architecture assistant. "
    "Use project context to improve and refine architecture descriptions."
)


class FoundryConfigurationError(ValueError):
    pass


class FoundryRequestError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None, detail: str = ""):
        super().__init__(message)
        self.status_code = status_code
        self.detail = detail


@dataclass(frozen=True)
class FoundryConnectionSettings:
    endpoint: str
    tenant_id: str
    client_id: str
    client_secret: str
    model_deployment: str
    api_version: str = DEFAULT_FOUNDRY_API_VERSION

    @classmethod
    def from_app_settings(cls, settings: Mapping[str, Any]) -> "FoundryConnectionSettings":
        if not isinstance(settings, Mapping):
            raise FoundryConfigurationError("settings must be a mapping")

        endpoint = _first_non_empty(settings, "aiFoundryEndpoint", "foundryEndpoint", "azureFoundryEndpoint")
        tenant_id = _first_non_empty(settings, "azureTenantId", "foundryTenantId")
        client_id = _first_non_empty(settings, "azureClientId", "foundryClientId")
        client_secret = _first_non_empty(settings, "azureClientSecret", "foundryClientSecret")
        model_deployment = _first_non_empty(
            settings,
            "foundryModelReasoning",
            "foundryModelFast",
            "foundryModelCoding",
            "modelReasoning",
            "modelFast",
            "modelCoding",
        )
        api_version = _first_non_empty(settings, "foundryApiVersion") or DEFAULT_FOUNDRY_API_VERSION

        if not endpoint:
            raise FoundryConfigurationError("aiFoundryEndpoint is required")
        if not tenant_id:
            raise FoundryConfigurationError("azureTenantId is required")
        if not client_id:
            raise FoundryConfigurationError("azureClientId is required")
        if not client_secret:
            raise FoundryConfigurationError("azureClientSecret is required")
        if not model_deployment:
            raise FoundryConfigurationError("A Foundry model deployment is required")

        endpoint_text = str(endpoint).strip().rstrip("/")
        if not endpoint_text.startswith("http"):
            raise FoundryConfigurationError("aiFoundryEndpoint must be a valid URL")

        return cls(
            endpoint=endpoint_text,
            tenant_id=str(tenant_id).strip(),
            client_id=str(client_id).strip(),
            client_secret=str(client_secret).strip(),
            model_deployment=str(model_deployment).strip(),
            api_version=str(api_version).strip() or DEFAULT_FOUNDRY_API_VERSION,
        )


@dataclass(frozen=True)
class DefaultResourcesResult:
    agent_id: str
    thread_id: str
    created_agent: bool
    created_thread: bool

    @property
    def settings_patch(self) -> dict[str, str]:
        return {
            "foundryDefaultAgentId": self.agent_id,
            "foundryDefaultThreadId": self.thread_id,
        }


@dataclass(frozen=True)
class ThreadResult:
    thread_id: str
    created: bool


class FoundryBootstrapClient:
    def __init__(self, settings: FoundryConnectionSettings, timeout_seconds: int = 20):
        self.settings = settings
        self.timeout_seconds = timeout_seconds
        self._credential = ClientSecretCredential(
            tenant_id=settings.tenant_id,
            client_id=settings.client_id,
            client_secret=settings.client_secret,
        )

    def ensure_default_agent_and_thread(
        self,
        default_agent_name: str = DEFAULT_AGENT_NAME,
        default_thread_name: str = DEFAULT_THREAD_NAME,
        agent_instructions: str = DEFAULT_AGENT_INSTRUCTIONS,
        known_agent_id: str | None = None,
        known_thread_id: str | None = None,
    ) -> DefaultResourcesResult:
        agent_id, created_agent = self._ensure_agent(
            name=default_agent_name,
            instructions=agent_instructions,
            known_agent_id=known_agent_id,
        )
        thread_result = self.ensure_named_thread(default_thread_name, known_thread_id=known_thread_id)
        return DefaultResourcesResult(
            agent_id=agent_id,
            thread_id=thread_result.thread_id,
            created_agent=created_agent,
            created_thread=thread_result.created,
        )

    def ensure_project_thread(self, project_id: str, known_thread_id: str | None = None) -> ThreadResult:
        project_name = str(project_id or "").strip()
        if not project_name:
            raise FoundryConfigurationError("project_id is required")
        return self.ensure_named_thread(project_name, known_thread_id=known_thread_id)

    def ensure_named_thread(self, thread_name: str | None, known_thread_id: str | None = None) -> ThreadResult:
        normalized_name = str(thread_name or "").strip()

        if known_thread_id and self._thread_exists(known_thread_id):
            return ThreadResult(thread_id=known_thread_id, created=False)

        if normalized_name:
            existing_thread_id = self._find_thread_id_by_name(normalized_name)
            if existing_thread_id:
                return ThreadResult(thread_id=existing_thread_id, created=False)

        created_id = self._create_thread()
        return ThreadResult(thread_id=created_id, created=True)

    def _ensure_agent(
        self,
        name: str,
        instructions: str,
        known_agent_id: str | None,
    ) -> tuple[str, bool]:
        if known_agent_id and self._assistant_exists(known_agent_id):
            return known_agent_id, False

        existing_agent_id = self._find_assistant_id_by_name(name)
        if existing_agent_id:
            return existing_agent_id, False

        created_id = self._create_assistant(name=name, instructions=instructions)
        return created_id, True

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

    def _assistant_exists(self, assistant_id: str) -> bool:
        safe_id = str(assistant_id or "").strip()
        if not safe_id:
            return False

        try:
            payload = self._request_json("GET", f"/assistants/{urllib_parse.quote(safe_id)}")
            return bool(str(payload.get("id") or "").strip())
        except FoundryRequestError as exc:
            if exc.status_code in {401, 403}:
                return False
            if exc.status_code in {400, 404}:
                return self._assistant_exists_in_list(safe_id)
            raise

    def _assistant_exists_in_list(self, assistant_id: str) -> bool:
        try:
            payload = self._request_json("GET", "/assistants")
        except FoundryRequestError:
            return False

        items = payload.get("data") if isinstance(payload.get("data"), list) else []
        for item in items:
            if not isinstance(item, dict):
                continue
            if str(item.get("id") or "").strip() == assistant_id:
                return True
        return False

    def _find_assistant_id_by_name(self, assistant_name: str) -> str | None:
        payload = self._request_json("GET", "/assistants")
        items = payload.get("data") if isinstance(payload.get("data"), list) else []

        for item in items:
            if not isinstance(item, dict):
                continue
            if str(item.get("name") or "").strip() == assistant_name:
                candidate = str(item.get("id") or "").strip()
                if candidate:
                    return candidate
        return None

    def _create_assistant(self, name: str, instructions: str) -> str:
        payload = self._request_json(
            "POST",
            "/assistants",
            payload={
                "name": name,
                "instructions": instructions,
                "model": self.settings.model_deployment,
            },
            expected_status=(200, 201),
        )

        assistant_id = str(payload.get("id") or "").strip()
        if not assistant_id:
            raise FoundryRequestError("Foundry assistant create response did not include id")
        return assistant_id

    def _thread_exists(self, thread_id: str) -> bool:
        safe_id = str(thread_id or "").strip()
        if not safe_id:
            return False

        try:
            payload = self._request_json("GET", f"/threads/{urllib_parse.quote(safe_id)}")
            return bool(str(payload.get("id") or "").strip())
        except FoundryRequestError as exc:
            if exc.status_code in {401, 403}:
                return False
            if exc.status_code in {400, 404}:
                return self._thread_exists_in_list(safe_id)
            raise

    def _thread_exists_in_list(self, thread_id: str) -> bool:
        for thread in self._list_threads():
            if str(thread.get("id") or "").strip() == thread_id:
                return True
        return False

    def _list_threads(self) -> list[dict[str, Any]]:
        try:
            payload = self._request_json("GET", "/threads")
        except FoundryRequestError as exc:
            if exc.status_code in {404, 405}:
                return []
            raise

        items = payload.get("data") if isinstance(payload.get("data"), list) else []
        return [item for item in items if isinstance(item, dict)]

    def _find_thread_id_by_name(self, thread_name: str) -> str | None:
        for thread in self._list_threads():
            metadata = thread.get("metadata") if isinstance(thread.get("metadata"), dict) else {}
            candidates = {
                str(thread.get("name") or "").strip(),
                str(metadata.get("name") or "").strip(),
                str(metadata.get("threadName") or "").strip(),
                str(metadata.get("displayName") or "").strip(),
                str(metadata.get("projectId") or "").strip(),
            }
            if thread_name in candidates:
                candidate = str(thread.get("id") or "").strip()
                if candidate:
                    return candidate
        return None

    def _create_thread(self) -> str:
        payload = self._request_json(
            "POST",
            "/threads",
            payload={},
            expected_status=(200, 201),
        )

        thread_id = str(payload.get("id") or "").strip()
        if not thread_id:
            raise FoundryRequestError("Foundry thread create response did not include id")
        return thread_id


def ensure_default_agent_and_thread(
    app_settings: Mapping[str, Any],
    known_agent_id: str | None = None,
    known_thread_id: str | None = None,
) -> DefaultResourcesResult:
    connection = FoundryConnectionSettings.from_app_settings(app_settings)
    client = FoundryBootstrapClient(connection)
    return client.ensure_default_agent_and_thread(
        known_agent_id=known_agent_id,
        known_thread_id=known_thread_id,
    )


def ensure_project_thread_for_project(
    app_settings: Mapping[str, Any],
    project_id: str,
    known_thread_id: str | None = None,
) -> ThreadResult:
    connection = FoundryConnectionSettings.from_app_settings(app_settings)
    client = FoundryBootstrapClient(connection)
    return client.ensure_project_thread(project_id=project_id, known_thread_id=known_thread_id)


def _first_non_empty(settings: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = settings.get(key)
        text = str(value).strip() if value is not None else ""
        if text:
            return text
    return ""
