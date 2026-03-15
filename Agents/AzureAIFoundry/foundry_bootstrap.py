from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
from azure.ai.agents.aio import AgentsClient
from azure.core.exceptions import HttpResponseError
from azure.identity.aio import ClientSecretCredential as AsyncClientSecretCredential

DEFAULT_AGENT_NAME = "architect-agent"
DEFAULT_THREAD_NAME = "architect-thread"
DEFAULT_FOUNDRY_API_VERSION = "2025-05-01"
DEFAULT_AGENT_DEFINITION_FILE = "architect"
DEFAULT_CHAT_AGENT_NAME = "architect-chat-agent"
DEFAULT_IAC_AGENT_NAME = "iac-generation-agent"
DEFAULT_VALIDATION_AGENT_NAME = "architecture-validation-agent"
DEFAULT_CHAT_AGENT_DEFINITION_FILE = "cloudarchitect_chat_agent.md"
DEFAULT_IAC_AGENT_DEFINITION_FILE = "iac_generation_agent.md"
DEFAULT_VALIDATION_AGENT_DEFINITION_FILE = "architecture_validation_agent.md"


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
            "foundryModelFast",
            "foundryModelReasoning",
            "foundryModelCoding",
            "modelFast",
            "modelReasoning",
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


@dataclass(frozen=True)
class AppFoundryResourcesResult:
    chat_agent_id: str
    iac_agent_id: str
    validation_agent_id: str
    thread_id: str
    created_chat_agent: bool
    created_iac_agent: bool
    created_validation_agent: bool
    created_thread: bool

    @property
    def settings_patch(self) -> dict[str, str]:
        return {
            "foundryChatAgentId": self.chat_agent_id,
            "foundryIacAgentId": self.iac_agent_id,
            "foundryValidationAgentId": self.validation_agent_id,
            "foundryDefaultAgentId": self.chat_agent_id,
            "foundryDefaultThreadId": self.thread_id,
        }


class FoundryBootstrapClient:
    def __init__(self, settings: FoundryConnectionSettings, timeout_seconds: int = 20):
        self.settings = settings
        self.timeout_seconds = timeout_seconds

    def ensure_app_agents_and_thread(
        self,
        *,
        chat_agent_name: str = DEFAULT_CHAT_AGENT_NAME,
        iac_agent_name: str = DEFAULT_IAC_AGENT_NAME,
        validation_agent_name: str = DEFAULT_VALIDATION_AGENT_NAME,
        app_thread_name: str = DEFAULT_THREAD_NAME,
        chat_agent_definition: str = DEFAULT_CHAT_AGENT_DEFINITION_FILE,
        iac_agent_definition: str = DEFAULT_IAC_AGENT_DEFINITION_FILE,
        validation_agent_definition: str = DEFAULT_VALIDATION_AGENT_DEFINITION_FILE,
        known_chat_agent_id: str | None = None,
        known_iac_agent_id: str | None = None,
        known_validation_agent_id: str | None = None,
        known_thread_id: str | None = None,
    ) -> AppFoundryResourcesResult:
        chat_instructions = _load_agent_instructions(chat_agent_definition)
        iac_instructions = _load_agent_instructions(iac_agent_definition)
        validation_instructions = _load_agent_instructions(validation_agent_definition)

        (
            chat_agent_id,
            created_chat_agent,
            iac_agent_id,
            created_iac_agent,
            validation_agent_id,
            created_validation_agent,
            thread_result,
        ) = _run_sync(
            self._ensure_app_agents_and_thread_async(
                chat_agent_name=str(chat_agent_name or DEFAULT_CHAT_AGENT_NAME).strip() or DEFAULT_CHAT_AGENT_NAME,
                iac_agent_name=str(iac_agent_name or DEFAULT_IAC_AGENT_NAME).strip() or DEFAULT_IAC_AGENT_NAME,
                validation_agent_name=str(validation_agent_name or DEFAULT_VALIDATION_AGENT_NAME).strip() or DEFAULT_VALIDATION_AGENT_NAME,
                app_thread_name=str(app_thread_name or DEFAULT_THREAD_NAME).strip() or DEFAULT_THREAD_NAME,
                chat_instructions=chat_instructions,
                iac_instructions=iac_instructions,
                validation_instructions=validation_instructions,
                known_chat_agent_id=known_chat_agent_id,
                known_iac_agent_id=known_iac_agent_id,
                known_validation_agent_id=known_validation_agent_id,
                known_thread_id=known_thread_id,
            )
        )

        return AppFoundryResourcesResult(
            chat_agent_id=chat_agent_id,
            iac_agent_id=iac_agent_id,
            validation_agent_id=validation_agent_id,
            thread_id=thread_result.thread_id,
            created_chat_agent=created_chat_agent,
            created_iac_agent=created_iac_agent,
            created_validation_agent=created_validation_agent,
            created_thread=thread_result.created,
        )

    def ensure_default_agent_and_thread(
        self,
        default_agent_name: str = DEFAULT_AGENT_NAME,
        default_thread_name: str = DEFAULT_THREAD_NAME,
        agent_instructions: str | None = None,
        known_agent_id: str | None = None,
        known_thread_id: str | None = None,
    ) -> DefaultResourcesResult:
        resolved_instructions = (
            _load_agent_instructions()
            if agent_instructions is None
            else str(agent_instructions).strip()
        )
        if not resolved_instructions:
            raise FoundryConfigurationError("Agent instructions are empty")

        agent_id, created_agent, thread_result = _run_sync(
            self._ensure_default_agent_and_thread_async(
                default_agent_name=default_agent_name,
                default_thread_name=default_thread_name,
                resolved_instructions=resolved_instructions,
                known_agent_id=known_agent_id,
                known_thread_id=known_thread_id,
            )
        )

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
        return _run_sync(self._ensure_named_thread_async(thread_name=thread_name, known_thread_id=known_thread_id))

    async def _ensure_default_agent_and_thread_async(
        self,
        default_agent_name: str,
        default_thread_name: str,
        resolved_instructions: str,
        known_agent_id: str | None,
        known_thread_id: str | None,
    ) -> tuple[str, bool, ThreadResult]:
        async with self._agents_client_context() as agents_client:
            agent_id, created_agent = await self._ensure_agent_async(
                agents_client=agents_client,
                name=default_agent_name,
                instructions=resolved_instructions,
                known_agent_id=known_agent_id,
            )
            thread_result = await self._ensure_named_thread_async(
                thread_name=default_thread_name,
                known_thread_id=known_thread_id,
                agents_client=agents_client,
            )
            return agent_id, created_agent, thread_result

    async def _ensure_app_agents_and_thread_async(
        self,
        *,
        chat_agent_name: str,
        iac_agent_name: str,
        validation_agent_name: str,
        app_thread_name: str,
        chat_instructions: str,
        iac_instructions: str,
        validation_instructions: str,
        known_chat_agent_id: str | None,
        known_iac_agent_id: str | None,
        known_validation_agent_id: str | None,
        known_thread_id: str | None,
    ) -> tuple[str, bool, str, bool, str, bool, ThreadResult]:
        async with self._agents_client_context() as agents_client:
            chat_agent_id, created_chat_agent = await self._ensure_agent_async(
                agents_client=agents_client,
                name=chat_agent_name,
                instructions=chat_instructions,
                known_agent_id=known_chat_agent_id,
            )
            iac_agent_id, created_iac_agent = await self._ensure_agent_async(
                agents_client=agents_client,
                name=iac_agent_name,
                instructions=iac_instructions,
                known_agent_id=known_iac_agent_id,
            )
            validation_agent_id, created_validation_agent = await self._ensure_agent_async(
                agents_client=agents_client,
                name=validation_agent_name,
                instructions=validation_instructions,
                known_agent_id=known_validation_agent_id,
            )
            thread_result = await self._ensure_named_thread_async(
                thread_name=app_thread_name,
                known_thread_id=known_thread_id,
                agents_client=agents_client,
            )
            return (
                chat_agent_id,
                created_chat_agent,
                iac_agent_id,
                created_iac_agent,
                validation_agent_id,
                created_validation_agent,
                thread_result,
            )

    async def _ensure_named_thread_async(
        self,
        thread_name: str | None,
        known_thread_id: str | None,
        agents_client: AgentsClient | None = None,
    ) -> ThreadResult:
        normalized_name = str(thread_name or "").strip()

        if agents_client is not None:
            return await self._ensure_named_thread_with_client_async(
                agents_client=agents_client,
                thread_name=normalized_name,
                known_thread_id=known_thread_id,
            )

        async with self._agents_client_context() as scoped_client:
            return await self._ensure_named_thread_with_client_async(
                agents_client=scoped_client,
                thread_name=normalized_name,
                known_thread_id=known_thread_id,
            )

    async def _ensure_named_thread_with_client_async(
        self,
        agents_client: AgentsClient,
        thread_name: str,
        known_thread_id: str | None,
    ) -> ThreadResult:
        if known_thread_id:
            exists = await self._thread_exists_async(agents_client, known_thread_id)
            if exists is not False:
                return ThreadResult(thread_id=known_thread_id, created=False)

        if thread_name:
            existing_thread_id = await self._find_thread_id_by_name_async(agents_client, thread_name)
            if existing_thread_id:
                return ThreadResult(thread_id=existing_thread_id, created=False)

        created_id = await self._create_thread_async(agents_client, thread_name)
        return ThreadResult(thread_id=created_id, created=True)

    async def _ensure_agent_async(
        self,
        agents_client: AgentsClient,
        name: str,
        instructions: str,
        known_agent_id: str | None,
    ) -> tuple[str, bool]:
        if known_agent_id and await self._assistant_exists_async(agents_client, known_agent_id):
            return known_agent_id, False

        existing_agent_id = await self._find_assistant_id_by_name_async(agents_client, name)
        if existing_agent_id:
            return existing_agent_id, False

        created_id = await self._create_assistant_async(agents_client=agents_client, name=name, instructions=instructions)
        return created_id, True

    async def _assistant_exists_async(self, agents_client: AgentsClient, assistant_id: str) -> bool:
        safe_id = str(assistant_id or "").strip()
        if not safe_id:
            return False

        try:
            agent = await agents_client.get_agent(safe_id)
            return bool(self._as_text(self._model_get(agent, "id")))
        except HttpResponseError as exc:
            status = self._status_code(exc)
            if status in {400, 401, 403, 404}:
                return False
            self._raise_request_error(exc, "get_agent")
        except Exception as exc:
            raise FoundryRequestError(f"Foundry request failed for get_agent: {exc}") from exc

    async def _assistant_exists_in_list_async(self, agents_client: AgentsClient, assistant_id: str) -> bool:
        agents = await self._list_agents_async(agents_client)
        for agent in agents:
            if self._as_text(self._model_get(agent, "id")) == assistant_id:
                return True
        return False

    async def _find_assistant_id_by_name_async(self, agents_client: AgentsClient, assistant_name: str) -> str | None:
        agents = await self._list_agents_async(agents_client)
        for agent in agents:
            if self._as_text(self._model_get(agent, "name")) == assistant_name:
                candidate = self._as_text(self._model_get(agent, "id"))
                if candidate and await self._assistant_exists_async(agents_client, candidate):
                    return candidate
        return None

    async def _create_assistant_async(self, agents_client: AgentsClient, name: str, instructions: str) -> str:
        try:
            agent = await agents_client.create_agent(
                model=self.settings.model_deployment,
                name=name,
                instructions=instructions,
            )
        except HttpResponseError as exc:
            self._raise_request_error(exc, "create_agent")
        except Exception as exc:
            raise FoundryRequestError(f"Foundry request failed for create_agent: {exc}") from exc

        assistant_id = self._as_text(self._model_get(agent, "id"))
        if not assistant_id:
            raise FoundryRequestError("Foundry assistant create response did not include id")
        return assistant_id

    async def _thread_exists_async(self, agents_client: AgentsClient, thread_id: str) -> bool | None:
        safe_id = str(thread_id or "").strip()
        if not safe_id:
            return False

        try:
            thread = await agents_client.threads.get(safe_id)
            return bool(self._as_text(self._model_get(thread, "id")))
        except HttpResponseError as exc:
            status = self._status_code(exc)
            if status in {401, 403}:
                return None
            if status in {400, 404}:
                return await self._thread_exists_in_list_async(agents_client, safe_id)
            self._raise_request_error(exc, "threads.get")
        except Exception as exc:
            raise FoundryRequestError(f"Foundry request failed for threads.get: {exc}") from exc

    async def _thread_exists_in_list_async(self, agents_client: AgentsClient, thread_id: str) -> bool | None:
        threads = await self._list_threads_async(agents_client)
        if threads is None:
            return None
        for thread in threads:
            if self._as_text(self._model_get(thread, "id")) == thread_id:
                return True
        return False

    async def _list_agents_async(self, agents_client: AgentsClient) -> list[Any]:
        try:
            items: list[Any] = []
            async for agent in agents_client.list_agents(limit=100):
                items.append(agent)
            return items
        except HttpResponseError as exc:
            self._raise_request_error(exc, "list_agents")
        except Exception as exc:
            raise FoundryRequestError(f"Foundry request failed for list_agents: {exc}") from exc

    async def _list_threads_async(self, agents_client: AgentsClient) -> list[Any] | None:
        try:
            items: list[Any] = []
            async for thread in agents_client.threads.list(limit=100):
                items.append(thread)
            return items
        except HttpResponseError as exc:
            if self._status_code(exc) in {404, 405}:
                return None
            self._raise_request_error(exc, "threads.list")
        except Exception as exc:
            raise FoundryRequestError(f"Foundry request failed for threads.list: {exc}") from exc

    async def _find_thread_id_by_name_async(self, agents_client: AgentsClient, thread_name: str) -> str | None:
        threads = await self._list_threads_async(agents_client)
        if threads is None:
            return None

        for thread in threads:
            metadata = self._coerce_mapping(self._model_get(thread, "metadata"))
            candidates = {
                self._as_text(self._model_get(thread, "name")),
                self._as_text(metadata.get("name")),
                self._as_text(metadata.get("threadName")),
                self._as_text(metadata.get("displayName")),
                self._as_text(metadata.get("projectId")),
            }
            if thread_name in candidates:
                candidate = self._as_text(self._model_get(thread, "id"))
                if candidate:
                    return candidate
        return None

    async def _create_thread_async(self, agents_client: AgentsClient, thread_name: str | None) -> str:
        metadata: dict[str, str] | None = None
        if thread_name:
            metadata = {
                "name": thread_name,
                "threadName": thread_name,
                "displayName": thread_name,
                "projectId": thread_name,
            }

        try:
            if metadata:
                thread = await agents_client.threads.create(metadata=metadata)
            else:
                thread = await agents_client.threads.create()
        except HttpResponseError as exc:
            self._raise_request_error(exc, "threads.create")
        except Exception as exc:
            raise FoundryRequestError(f"Foundry request failed for threads.create: {exc}") from exc

        thread_id = self._as_text(self._model_get(thread, "id"))
        if not thread_id:
            raise FoundryRequestError("Foundry thread create response did not include id")
        return thread_id

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

    def _coerce_mapping(self, value: Any) -> dict[str, Any]:
        if isinstance(value, Mapping):
            return dict(value)
        return {}

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


    async def _get_agent_description_async(self, agent_id: str) -> tuple[bool, str | None]:
        """Get agent description from Azure AI Foundry. Returns (exists, description)."""
        safe_id = str(agent_id or "").strip()
        if not safe_id:
            return False, None

        async with self._agents_client_context() as agents_client:
            try:
                agent = await agents_client.get_agent(safe_id)
                description = self._as_text(self._model_get(agent, "instructions"))
                return True, description
            except HttpResponseError as exc:
                status = self._status_code(exc)
                if status in {401, 403}:
                    return False, None
                if status in {400, 404}:
                    return False, None
                self._raise_request_error(exc, "get_agent")
            except Exception as exc:
                raise FoundryRequestError(f"Foundry request failed for get_agent: {exc}") from exc

    async def _update_agent_description_async(self, agent_id: str, new_instructions: str) -> bool:
        """Update agent description on Azure AI Foundry."""
        safe_id = str(agent_id or "").strip()
        if not safe_id:
            return False

        async with self._agents_client_context() as agents_client:
            try:
                await agents_client.update_agent(
                    agent_id=safe_id,
                    instructions=new_instructions,
                )
                return True
            except HttpResponseError as exc:
                status = self._status_code(exc)
                if status in {401, 403, 404}:
                    return False
                self._raise_request_error(exc, "update_agent")
            except Exception as exc:
                raise FoundryRequestError(f"Foundry request failed for update_agent: {exc}") from exc


def _run_sync(coro):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    raise FoundryRequestError("Synchronous Foundry operation called while an event loop is already running")


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


def ensure_app_agents_and_thread(
    app_settings: Mapping[str, Any],
    *,
    known_chat_agent_id: str | None = None,
    known_iac_agent_id: str | None = None,
    known_validation_agent_id: str | None = None,
    known_thread_id: str | None = None,
) -> AppFoundryResourcesResult:
    connection = FoundryConnectionSettings.from_app_settings(app_settings)
    client = FoundryBootstrapClient(connection)
    return client.ensure_app_agents_and_thread(
        known_chat_agent_id=known_chat_agent_id,
        known_iac_agent_id=known_iac_agent_id,
        known_validation_agent_id=known_validation_agent_id,
        known_thread_id=known_thread_id,
    )


@dataclass(frozen=True)
class AgentStatusResult:
    is_present: bool
    description_matches: bool | None = None
    was_updated: bool = False


@dataclass(frozen=True)
class VerifyAgentsAndThreadsResult:
    chat_agent: AgentStatusResult
    iac_agent: AgentStatusResult
    validation_agent: AgentStatusResult
    chat_thread: bool | None
    iac_thread: bool | None
    validation_thread: bool | None
    has_errors: bool


def verify_agents_and_threads(app_settings: Mapping[str, Any]) -> VerifyAgentsAndThreadsResult:
    """
    Verification and update: check if agents and threads exist in Azure AI Foundry.
    If agents exist but descriptions don't match .md files, update them on Azure Foundry.
    Returns overall status with detection of changes.
    """
    try:
        connection = FoundryConnectionSettings.from_app_settings(app_settings)
    except FoundryConfigurationError:
        return VerifyAgentsAndThreadsResult(
            chat_agent=AgentStatusResult(is_present=False),
            iac_agent=AgentStatusResult(is_present=False),
            validation_agent=AgentStatusResult(is_present=False),
            chat_thread=None,
            iac_thread=None,
            validation_thread=None,
            has_errors=True,
        )

    client = FoundryBootstrapClient(connection)

    chat_agent_id = str(app_settings.get("foundryChatAgentId") or "").strip() or None
    iac_agent_id = str(app_settings.get("foundryIacAgentId") or "").strip() or None
    validation_agent_id = str(app_settings.get("foundryValidationAgentId") or "").strip() or None
    chat_thread_id = str(app_settings.get("foundryDefaultThreadId") or "").strip() or None

    chat_agent_status = _verify_and_update_agent_async(client, chat_agent_id, DEFAULT_CHAT_AGENT_DEFINITION_FILE)
    iac_agent_status = _verify_and_update_agent_async(client, iac_agent_id, DEFAULT_IAC_AGENT_DEFINITION_FILE)
    validation_agent_status = _verify_and_update_agent_async(client, validation_agent_id, DEFAULT_VALIDATION_AGENT_DEFINITION_FILE)
    chat_thread_exists = _verify_thread_async(client, chat_thread_id)

    return VerifyAgentsAndThreadsResult(
        chat_agent=chat_agent_status,
        iac_agent=iac_agent_status,
        validation_agent=validation_agent_status,
        chat_thread=chat_thread_exists,
        iac_thread=chat_thread_exists,
        validation_thread=chat_thread_exists,
        has_errors=False,
    )


def _definition_file_to_agent_name(definition_file: str) -> str:
    """Convert definition file name to agent name."""
    base = str(definition_file or "").strip()
    if base.endswith(".md"):
        base = base[:-3]
    return base.replace("_", "-")


def _verify_and_update_agent_async(client: FoundryBootstrapClient, agent_id: str | None, definition_file: str) -> AgentStatusResult:
    """Check if agent exists and verify/update description matches .md file."""
    try:
        current_instructions = _load_agent_instructions(definition_file)
    except Exception:
        return AgentStatusResult(is_present=False)

    try:
        resolved_agent_id = _run_sync(
            _ensure_agent_id_for_verification_async(
                client=client,
                known_agent_id=agent_id,
                definition_file=definition_file,
                instructions=current_instructions,
            )
        )
    except Exception:
        resolved_agent_id = None

    if not resolved_agent_id:
        return AgentStatusResult(is_present=False)

    try:
        agent_exists_with_desc, current_description = _run_sync(client._get_agent_description_async(resolved_agent_id))
    except Exception:
        agent_exists_with_desc, current_description = False, None

    if not agent_exists_with_desc or current_description is None:
        try:
            update_success = _run_sync(client._update_agent_description_async(resolved_agent_id, current_instructions))
        except Exception:
            update_success = False
        if update_success:
            return AgentStatusResult(is_present=True, description_matches=True, was_updated=True)
        return AgentStatusResult(is_present=True, description_matches=None)

    description_matches = str(current_description or "").strip() == str(current_instructions).strip()
    if description_matches:
        return AgentStatusResult(is_present=True, description_matches=True)

    try:
        update_success = _run_sync(client._update_agent_description_async(resolved_agent_id, current_instructions))
    except Exception:
        update_success = False
    if update_success:
        return AgentStatusResult(is_present=True, description_matches=True, was_updated=True)
    return AgentStatusResult(is_present=True, description_matches=False)


def _agent_name_for_definition_file(definition_file: str) -> str:
    normalized = str(definition_file or "").strip()
    if normalized == DEFAULT_CHAT_AGENT_DEFINITION_FILE:
        return DEFAULT_CHAT_AGENT_NAME
    if normalized == DEFAULT_IAC_AGENT_DEFINITION_FILE:
        return DEFAULT_IAC_AGENT_NAME
    if normalized == DEFAULT_VALIDATION_AGENT_DEFINITION_FILE:
        return DEFAULT_VALIDATION_AGENT_NAME
    return _definition_file_to_agent_name(normalized)


async def _ensure_agent_id_for_verification_async(
    client: FoundryBootstrapClient,
    known_agent_id: str | None,
    definition_file: str,
    instructions: str,
) -> str | None:
    known_id = str(known_agent_id or "").strip() or None
    expected_name = _agent_name_for_definition_file(definition_file)

    async with client._agents_client_context() as agents_client:
        resolved_id, _ = await client._ensure_agent_async(
            agents_client=agents_client,
            name=expected_name,
            instructions=instructions,
            known_agent_id=known_id,
        )
        return resolved_id


def _verify_thread_async(client: FoundryBootstrapClient, thread_id: str | None) -> bool | None:
    """Check if thread exists."""
    if not thread_id:
        return None

    try:
        thread_exists = _run_sync(_verify_thread_exists_async(client, thread_id))
        return thread_exists
    except Exception:
        return None


async def _verify_thread_exists_async(client: FoundryBootstrapClient, thread_id: str) -> bool | None:
    async with client._agents_client_context() as agents_client:
        return await client._thread_exists_async(agents_client, thread_id)


def _load_agent_instructions(agent_definition: str = DEFAULT_AGENT_DEFINITION_FILE) -> str:
    agent_key = str(agent_definition or "").strip()
    if not agent_key:
        raise FoundryConfigurationError("agent_definition is required")

    agents_dir = Path(__file__).resolve().parents[1]
    agent_path = agents_dir / agent_key
    try:
        content = agent_path.read_text(encoding="utf-8").strip()
    except FileNotFoundError as exc:
        raise FoundryConfigurationError(f"Missing agent definition file: {agent_path}") from exc

    if not content:
        raise FoundryConfigurationError(f"Agent definition file is empty: {agent_path}")
    return content


def _first_non_empty(settings: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = settings.get(key)
        text = str(value).strip() if value is not None else ""
        if text:
            return text
    return ""
