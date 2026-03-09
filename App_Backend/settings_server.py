from pathlib import Path
import base64
import json
import os
import re
import shutil
from datetime import datetime
from urllib import error as urllib_error
from urllib import parse as urllib_parse
from urllib import request as urllib_request
from azure.identity import ClientSecretCredential
from azure.ai.projects import AIProjectClient
from Agents.AzureAIFoundry.foundry_bootstrap import (
    FoundryConfigurationError,
    FoundryRequestError,
    ensure_default_agent_and_thread,
    ensure_project_thread_for_project,
)
from Agents.AzureAIFoundry.foundry_description import (
    evaluate_description_with_architect,
    improve_description_with_architect,
)
from Agents.AzureAIFoundry.foundry_messages import (
    list_thread_messages,
    post_project_created_message,
    post_project_deleted_message,
)
from Agents.AzureMCP.cloudarchitect_chat_agent import (
    AzureMcpChatConfigurationError,
    AzureMcpChatRequestError,
    get_cloudarchitect_chat_status,
    run_cloudarchitect_chat_agent,
)
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

app = FastAPI()

WORKSPACE_ROOT = Path("/workspace")
APP_STATE_DIR = WORKSPACE_ROOT / "App_State"
PROJECTS_DIR = WORKSPACE_ROOT / "Projects"
DEFAULT_TEMPLATE_DIR = PROJECTS_DIR / "Default"
FRONTEND_DIR = Path("/app/App_Frontend")

DEFAULT_APP_SETTINGS = {
    "modelProvider": "ollama-local",
    "azureTenantId": "",
    "azureClientId": "",
    "azureClientSecret": "",
    "azureSubscriptionId": "",
    "azureResourceGroup": "",
    "aiFoundryProjectName": "",
    "aiFoundryEndpoint": "",
    "foundryApiVersion": "2024-05-01-preview",
    "ollamaBaseUrl": "http://host.docker.internal:11434",
    "foundryModelCoding": "",
    "foundryModelReasoning": "",
    "foundryModelFast": "",
    "foundryDefaultAgentId": "",
    "foundryDefaultThreadId": "",
    "ollamaModelPathCoding": "",
    "ollamaModelPathReasoning": "",
    "ollamaModelPathFast": "",
}


class AppSettingsPayload(BaseModel):
    settings: dict


class VerifySettingsPayload(BaseModel):
    settings: dict


class ProjectMeta(BaseModel):
    id: str
    name: str
    cloud: str
    applicationType: str | None = None
    applicationDescription: str | None = None
    applicationDescriptionQuality: str | None = None
    applicationDescriptionQualityIndex: int | None = None
    applicationDescriptionQualityScore: int | None = None
    iacLanguage: str | None = None
    foundryThreadId: str | None = None
    lastSaved: int | None = None


class ProjectSettingsPayload(BaseModel):
    project: ProjectMeta
    settings: dict


class ProjectSavePayload(BaseModel):
    project: ProjectMeta
    canvasState: dict = {}
    create: bool | None = None


class DiagramExportPayload(BaseModel):
    projectId: str
    projectName: str
    format: str
    imageData: str


class DescriptionEvaluatePayload(BaseModel):
    description: str
    appType: str | None = None
    cloud: str | None = None


class DescriptionImprovePayload(BaseModel):
    description: str
    appType: str | None = None
    cloud: str | None = None


class ProjectDescriptionPayload(BaseModel):
    projectId: str
    description: str
    appType: str | None = None
    cloud: str | None = None


class ArchitectureChatPayload(BaseModel):
    message: str
    projectId: str | None = None
    agentState: dict | None = None


def read_json_file(path: Path, default):
    try:
        if not path.exists():
            return default
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def collect_project_entries() -> list[dict]:
    entries = []
    if not PROJECTS_DIR.exists():
        return entries

    for project_dir in PROJECTS_DIR.iterdir():
        if not project_dir.is_dir():
            continue

        architecture_dir = project_dir / "Architecture"
        metadata_path = architecture_dir / "project.metadata.json"
        state_path = architecture_dir / "canvas.state.json"

        metadata = read_json_file(metadata_path, {})
        if not isinstance(metadata, dict):
            metadata = {}

        project_id = str(metadata.get("id") or "").strip()
        name = str(metadata.get("name") or "").strip()
        cloud = str(metadata.get("cloud") or "").strip()

        if not project_id or not name or cloud not in {"Azure", "AWS", "GCP"}:
            continue

        state_payload = read_json_file(state_path, {})
        if not isinstance(state_payload, dict):
            state_payload = {}

        last_saved = metadata.get("lastSaved")
        if not isinstance(last_saved, (int, float)):
            project_meta = state_payload.get("project") if isinstance(state_payload.get("project"), dict) else {}
            candidate = project_meta.get("lastSaved")
            if isinstance(candidate, (int, float)):
                last_saved = candidate
            elif state_path.exists():
                last_saved = int(state_path.stat().st_mtime * 1000)
            else:
                last_saved = int(metadata_path.stat().st_mtime * 1000) if metadata_path.exists() else 0

        entries.append(
            {
                "id": project_id,
                "name": name,
                "cloud": cloud,
                "lastSaved": int(last_saved),
                "projectDir": project_dir,
                "metadataPath": metadata_path,
                "statePath": state_path,
            }
        )

    entries.sort(key=lambda item: item["lastSaved"], reverse=True)
    return entries


def find_project_entry(project_id: str):
    project_id = str(project_id or "").strip()
    if not project_id:
        return None

    for entry in collect_project_entries():
        if entry["id"] == project_id:
            return entry
    return None


def list_iac_files(iac_dir: Path) -> list[dict]:
    if not iac_dir.exists():
        return []

    files: list[dict] = []
    for path in sorted(iac_dir.rglob("*")):
        if not path.is_file():
            continue

        try:
            rel = path.relative_to(iac_dir)
        except ValueError:
            continue

        if any(part.startswith(".") for part in rel.parts):
            continue

        stat = path.stat()
        files.append(
            {
                "path": rel.as_posix(),
                "name": path.name,
                "size": int(stat.st_size),
                "updated": int(stat.st_mtime * 1000),
            }
        )

    return files


def resolve_iac_file(iac_dir: Path, relative_path: str) -> Path:
    if not relative_path:
        raise HTTPException(status_code=400, detail="File path is required")

    requested = Path(str(relative_path))
    if requested.is_absolute():
        raise HTTPException(status_code=400, detail="Invalid file path")

    iac_root = iac_dir.resolve()
    target = (iac_dir / requested).resolve()
    try:
        target.relative_to(iac_root)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid file path") from exc

    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    return target


def sanitize_segment(value: str, fallback: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "-", value.strip())
    cleaned = cleaned.strip("-._")
    return cleaned or fallback


def format_diagram_timestamp(now: datetime | None = None) -> str:
    dt = now or datetime.now()
    return dt.strftime("%Y-%m-%d-%H-%M-%S")


def resolve_project_dir_for_write(project_id: str, project_name: str) -> Path:
    PROJECTS_DIR.mkdir(parents=True, exist_ok=True)

    fallback_folder_name = sanitize_segment(project_name, sanitize_segment(project_id, "project"))
    desired_dir = PROJECTS_DIR / fallback_folder_name

    existing_entry = find_project_entry(project_id)
    if not existing_entry:
        return desired_dir

    current_dir = existing_entry["projectDir"]
    desired_folder_name = sanitize_segment(project_name, current_dir.name or "project")
    desired_dir = PROJECTS_DIR / desired_folder_name

    if desired_dir == current_dir:
        return current_dir

    if desired_dir.exists():
        desired_metadata = read_json_file(desired_dir / "Architecture" / "project.metadata.json", {})
        desired_id = str(desired_metadata.get("id") or "").strip() if isinstance(desired_metadata, dict) else ""
        if desired_id and desired_id != project_id:
            raise HTTPException(
                status_code=409,
                detail=f"A project folder named '{desired_folder_name}' already exists.",
            )
        return desired_dir

    current_dir.rename(desired_dir)
    return desired_dir


def to_env_lines(payload: dict) -> str:
    lines = []
    for key, value in payload.items():
        key_text = str(key)
        snake = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", key_text)
        env_key = re.sub(r"[^a-zA-Z0-9_]", "_", snake).upper()
        env_value = "" if value is None else str(value)
        lines.append(f"{env_key}={env_value}")
    return "\n".join(lines) + "\n"


def snake_to_camel(value: str) -> str:
    parts = str(value or "").lower().split("_")
    if not parts:
        return ""
    return parts[0] + "".join(part.capitalize() for part in parts[1:])


def parse_env_file(path: Path) -> dict:
    if not path.exists():
        return {}

    payload = {}
    lines = path.read_text(encoding="utf-8").splitlines()
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        payload[snake_to_camel(key.strip())] = value.strip()

    return payload


def load_project_settings_file(project_dir: Path) -> dict:
    target = project_dir / "project.settings.env"
    settings = parse_env_file(target)
    return settings if isinstance(settings, dict) else {}


def merge_project_settings(existing: dict, incoming: dict) -> dict:
    existing = existing if isinstance(existing, dict) else {}
    incoming = incoming if isinstance(incoming, dict) else {}
    merged = {**existing, **incoming}

    existing_thread = str(existing.get("projectThreadId") or "").strip()
    incoming_thread = str(incoming.get("projectThreadId") or "").strip()
    if incoming_thread:
        merged["projectThreadId"] = incoming_thread
    elif existing_thread:
        merged["projectThreadId"] = existing_thread
    else:
        merged.pop("projectThreadId", None)

    return merged


def build_project_settings_payload(
    project_id: str,
    project_name: str,
    project_cloud: str,
    settings: dict,
) -> dict:
    payload = dict(settings or {})
    payload["projectId"] = str(project_id or "").strip()
    payload["projectName"] = str(project_name or "").strip()
    payload["projectCloud"] = str(project_cloud or "").strip()
    return payload


def persist_project_settings(
    project_dir: Path,
    project_id: str,
    project_name: str,
    project_cloud: str,
    settings: dict,
) -> Path:
    target = project_dir / "project.settings.env"
    payload = build_project_settings_payload(project_id, project_name, project_cloud, settings)
    target.write_text(to_env_lines(payload), encoding="utf-8")
    return target


def load_app_settings() -> dict:
    target = APP_STATE_DIR / "app.settings.env"
    loaded = {
        **DEFAULT_APP_SETTINGS,
        **parse_env_file(target),
    }

    if not loaded.get("aiFoundryEndpoint") and loaded.get("foundryEndpoint"):
        loaded["aiFoundryEndpoint"] = loaded.get("foundryEndpoint")
    if not loaded.get("aiFoundryEndpoint") and loaded.get("azureFoundryEndpoint"):
        loaded["aiFoundryEndpoint"] = loaded.get("azureFoundryEndpoint")

    if not loaded.get("azureTenantId") and loaded.get("foundryTenantId"):
        loaded["azureTenantId"] = loaded.get("foundryTenantId")
    if not loaded.get("azureClientId") and loaded.get("foundryClientId"):
        loaded["azureClientId"] = loaded.get("foundryClientId")
    if not loaded.get("azureClientSecret") and loaded.get("foundryClientSecret"):
        loaded["azureClientSecret"] = loaded.get("foundryClientSecret")
    if not loaded.get("foundryApiVersion") and loaded.get("azureFoundryApiVersion"):
        loaded["foundryApiVersion"] = loaded.get("azureFoundryApiVersion")

    if not loaded.get("modelCoding") and loaded.get("azureFoundryChatModelCoding"):
        loaded["modelCoding"] = loaded.get("azureFoundryChatModelCoding")
    if not loaded.get("modelReasoning") and loaded.get("azureFoundryChatModelReasoning"):
        loaded["modelReasoning"] = loaded.get("azureFoundryChatModelReasoning")
    if not loaded.get("modelFast") and loaded.get("azureFoundryChatModelFast"):
        loaded["modelFast"] = loaded.get("azureFoundryChatModelFast")

    if not loaded.get("foundryModelCoding") and loaded.get("modelCoding"):
        loaded["foundryModelCoding"] = loaded.get("modelCoding")
    if not loaded.get("foundryModelReasoning") and loaded.get("modelReasoning"):
        loaded["foundryModelReasoning"] = loaded.get("modelReasoning")
    if not loaded.get("foundryModelFast") and loaded.get("modelFast"):
        loaded["foundryModelFast"] = loaded.get("modelFast")

    for key in (
        "azureTenantId",
        "azureClientId",
        "azureSubscriptionId",
        "azureResourceGroup",
        "aiFoundryProjectName",
        "aiFoundryEndpoint",
    ):
        if not loaded.get(key):
            loaded[key] = DEFAULT_APP_SETTINGS.get(key, "")

    return loaded


def resolve_model_by_purpose(settings: dict, purpose: str) -> tuple[str, str]:
    purpose_value = str(purpose or "fast").strip().lower()
    provider = str(settings.get("modelProvider") or "ollama-local").strip().lower()
    if provider == "ollama-local":
        purpose_to_var = {
            "coding": "ollamaModelPathCoding",
            "code": "ollamaModelPathCoding",
            "reasoning": "ollamaModelPathReasoning",
            "fast": "ollamaModelPathFast",
            "chat": "ollamaModelPathReasoning",
        }
    else:
        purpose_to_var = {
            "coding": "foundryModelCoding",
            "code": "foundryModelCoding",
            "reasoning": "foundryModelReasoning",
            "fast": "foundryModelFast",
            "chat": "foundryModelReasoning",
        }
    variable = purpose_to_var.get(purpose_value, "modelFast")
    if variable == "modelFast":
        variable = "ollamaModelPathFast" if provider == "ollama-local" else "foundryModelFast"
    model_name = str(settings.get(variable) or "").strip()
    return variable, model_name


def sanitize_app_settings_for_provider(settings: dict) -> dict:
    incoming = settings if isinstance(settings, dict) else {}
    provider = str(incoming.get("modelProvider") or "ollama-local").strip().lower()
    normalized_provider = "azure-foundry" if provider == "azure-foundry" else "ollama-local"

    merged = {
        **DEFAULT_APP_SETTINGS,
        **incoming,
        "modelProvider": normalized_provider,
    }

    if normalized_provider == "azure-foundry":
        merged["ollamaBaseUrl"] = ""
        merged["ollamaModelPathCoding"] = ""
        merged["ollamaModelPathReasoning"] = ""
        merged["ollamaModelPathFast"] = ""
    else:
        merged["azureSubscriptionId"] = ""
        merged["azureResourceGroup"] = ""
        merged["aiFoundryProjectName"] = ""
        merged["aiFoundryEndpoint"] = ""
        merged["foundryModelCoding"] = ""
        merged["foundryModelReasoning"] = ""
        merged["foundryModelFast"] = ""

    return merged


def build_persistable_app_settings(settings: dict) -> dict:
    sanitized = sanitize_app_settings_for_provider(settings)
    provider = str(sanitized.get("modelProvider") or "ollama-local").strip().lower()

    if provider == "azure-foundry":
        keys = (
            "modelProvider",
            "azureTenantId",
            "azureClientId",
            "azureClientSecret",
            "azureSubscriptionId",
            "azureResourceGroup",
            "aiFoundryProjectName",
            "aiFoundryEndpoint",
            "foundryApiVersion",
            "foundryModelCoding",
            "foundryModelReasoning",
            "foundryModelFast",
            "foundryDefaultAgentId",
            "foundryDefaultThreadId",
        )
    else:
        keys = (
            "modelProvider",
            "azureTenantId",
            "azureClientId",
            "azureClientSecret",
            "ollamaBaseUrl",
            "ollamaModelPathCoding",
            "ollamaModelPathReasoning",
            "ollamaModelPathFast",
        )

    return {key: sanitized.get(key, "") for key in keys}


def is_azure_foundry_provider(settings: dict) -> bool:
    return str(settings.get("modelProvider") or "ollama-local").strip().lower() == "azure-foundry"


def write_app_settings_file(settings: dict) -> Path:
    APP_STATE_DIR.mkdir(parents=True, exist_ok=True)
    target = APP_STATE_DIR / "app.settings.env"
    persistable = build_persistable_app_settings(settings)
    target.write_text(to_env_lines(persistable), encoding="utf-8")
    return target


def bootstrap_default_foundry_resources(settings: dict) -> dict:
    if not is_azure_foundry_provider(settings):
        return {
            "ok": True,
            "skipped": True,
            "reason": "provider-not-azure-foundry",
        }

    known_agent_id = str(settings.get("foundryDefaultAgentId") or "").strip() or None
    known_thread_id = str(settings.get("foundryDefaultThreadId") or "").strip() or None

    try:
        result = ensure_default_agent_and_thread(
            settings,
            known_agent_id=known_agent_id,
            known_thread_id=known_thread_id,
        )
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

    settings_patch = result.settings_patch
    changed = False
    for key, value in settings_patch.items():
        if str(settings.get(key) or "") != str(value or ""):
            changed = True
            settings[key] = value

    return {
        "ok": True,
        "skipped": False,
        "agentId": result.agent_id,
        "threadId": result.thread_id,
        "createdAgent": result.created_agent,
        "createdThread": result.created_thread,
        "settingsUpdated": changed,
    }


def ensure_project_foundry_thread(settings: dict, project_id: str, known_thread_id: str | None = None) -> dict:
    if not is_azure_foundry_provider(settings):
        return {
            "ok": True,
            "skipped": True,
            "reason": "provider-not-azure-foundry",
        }

    safe_project_id = str(project_id or "").strip()
    if not safe_project_id:
        return {
            "ok": False,
            "skipped": False,
            "reason": "project-id-missing",
            "detail": "project_id is required",
        }

    try:
        thread_result = ensure_project_thread_for_project(
            settings,
            project_id=safe_project_id,
            known_thread_id=known_thread_id,
        )
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

    return {
        "ok": True,
        "skipped": False,
        "threadId": thread_result.thread_id,
        "created": thread_result.created,
    }


def resolve_project_foundry_thread_id(entry: dict, app_settings: dict) -> str | None:
    project_dir = entry["projectDir"]
    project_settings = load_project_settings_file(project_dir)

    metadata = read_json_file(entry["metadataPath"], {})
    if not isinstance(metadata, dict):
        metadata = {}

    known_thread_id = str(
        project_settings.get("projectThreadId")
        or metadata.get("foundryThreadId")
        or ""
    ).strip() or None

    thread_result = ensure_project_foundry_thread(
        app_settings,
        project_id=entry["id"],
        known_thread_id=known_thread_id,
    )

    resolved_thread_id = known_thread_id
    if thread_result.get("threadId"):
        resolved_thread_id = str(thread_result["threadId"]).strip() or resolved_thread_id

    if not resolved_thread_id:
        return None

    if str(project_settings.get("projectThreadId") or "").strip() != resolved_thread_id:
        project_settings = merge_project_settings(project_settings, {"projectThreadId": resolved_thread_id})
        persist_project_settings(
            project_dir,
            entry["id"],
            entry["name"],
            entry["cloud"],
            project_settings,
        )

    if str(metadata.get("foundryThreadId") or "").strip() != resolved_thread_id:
        metadata["foundryThreadId"] = resolved_thread_id
        entry["metadataPath"].write_text(json.dumps(metadata, indent=2), encoding="utf-8")

    return resolved_thread_id


def verify_foundry_settings(settings: dict) -> tuple[str, list[str]]:
    endpoint = str(settings.get("aiFoundryEndpoint") or settings.get("foundryEndpoint") or "").strip().rstrip("/")
    tenant_id = str(settings.get("azureTenantId") or settings.get("foundryTenantId") or "").strip()
    client_id = str(settings.get("azureClientId") or settings.get("foundryClientId") or "").strip()
    client_secret = str(settings.get("azureClientSecret") or settings.get("foundryClientSecret") or "").strip()
    subscription_id = str(settings.get("azureSubscriptionId") or "").strip()
    resource_group = str(settings.get("azureResourceGroup") or "").strip()
    project_name = str(settings.get("aiFoundryProjectName") or "").strip()
    api_version = str(settings.get("foundryApiVersion") or "").strip()

    if not endpoint:
        raise HTTPException(status_code=400, detail="AI_FOUNDRY_ENDPOINT is required.")
    if not tenant_id:
        raise HTTPException(status_code=400, detail="AZURE_TENANT_ID is required.")
    if not client_id:
        raise HTTPException(status_code=400, detail="AZURE_CLIENT_ID is required.")
    if not client_secret:
        raise HTTPException(status_code=400, detail="AZURE_CLIENT_SECRET is required.")
    if not subscription_id:
        raise HTTPException(status_code=400, detail="AZURE_SUBSCRIPTION_ID is required.")
    if not resource_group:
        raise HTTPException(status_code=400, detail="AZURE_RESOURCE_GROUP is required.")
    if not project_name:
        raise HTTPException(status_code=400, detail="AI_FOUNDRY_PROJECT_NAME is required.")

    is_project_endpoint = "/api/projects/" in endpoint.lower()

    def mask_value(value: str) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        if len(text) <= 8:
            return "*" * len(text)
        return f"{text[:4]}...{text[-4:]}"

    debug_context = {
        "endpoint": endpoint,
        "tenantId": tenant_id,
        "clientId": mask_value(client_id),
        "subscriptionId": mask_value(subscription_id),
        "resourceGroup": resource_group,
        "projectName": project_name,
        "apiVersion": api_version,
        "isProjectEndpoint": is_project_endpoint,
        "tokenScope": "",
        "sdkStatus": "not-run",
        "sdkDeploymentCount": 0,
    }

    def build_debug_detail(base_message: str, *, extra: str = "") -> str:
        return base_message

    try:
        credential = ClientSecretCredential(
            tenant_id=tenant_id,
            client_id=client_id,
            client_secret=client_secret,
        )
        project_client = AIProjectClient(endpoint=endpoint, credential=credential)
        sdk_deployments = list(project_client.deployments.list())
        sdk_names_set: set[str] = set()
        for item in sdk_deployments:
            candidate_name = str(
                getattr(item, "name", "")
                or getattr(item, "id", "")
                or getattr(item, "deployment_name", "")
                or getattr(item, "model_deployment_name", "")
                or ""
            ).strip()
            if candidate_name:
                if "/" in candidate_name:
                    candidate_name = candidate_name.split("/")[-1]
                sdk_names_set.add(candidate_name)

        sdk_names = sorted(sdk_names_set)
        debug_context["verificationMode"] = "sdk"
        debug_context["sdkStatus"] = "ok"
        debug_context["sdkDeploymentCount"] = len(sdk_names)
        if sdk_names:
            return (
                f"Foundry connection verified for project '{project_name}'.",
                sdk_names,
            )
    except Exception as sdk_exc:
        debug_context["verificationMode"] = "rest-fallback"
        debug_context["sdkStatus"] = "error"
        debug_context["sdkError"] = str(sdk_exc)[:400]

    token_url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
    candidate_scopes = (
        ["https://ai.azure.com/.default", "https://cognitiveservices.azure.com/.default"]
        if is_project_endpoint
        else ["https://cognitiveservices.azure.com/.default", "https://ai.azure.com/.default"]
    )

    access_token = ""
    token_failures: list[str] = []
    for scope in candidate_scopes:
        token_body = urllib_parse.urlencode(
            {
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": client_secret,
                "scope": scope,
            }
        ).encode("utf-8")
        token_request = urllib_request.Request(
            token_url,
            method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
            data=token_body,
        )

        try:
            with urllib_request.urlopen(token_request, timeout=10) as token_response:
                token_payload = json.loads(token_response.read().decode("utf-8") or "{}")
                access_token = str(token_payload.get("access_token") or "").strip()
                if access_token:
                    debug_context["tokenScope"] = scope
                    break
                token_failures.append(f"scope={scope}; token_response_missing_access_token=true")
        except urllib_error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore") if exc.fp else ""
            token_failures.append(f"scope={scope}; token_http={exc.code}; body={detail[:300] or '(empty)'}")
        except Exception as exc:
            token_failures.append(f"scope={scope}; error={exc}")

    if not access_token:
        raise HTTPException(
            status_code=400,
            detail=build_debug_detail(
                "Failed to acquire access token from Microsoft Entra ID.",
                extra=" | ".join(token_failures)[:1200] or "no token failure details captured",
            ),
        )

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
    }

    last_error_message = ""
    deployment_names: set[str] = set()

    def parse_deployments(payload_text: str) -> list[str]:
        try:
            parsed = json.loads(payload_text or "{}")
        except Exception:
            return []

        def get_name_from_item(item: dict) -> str:
            if not isinstance(item, dict):
                return ""
            properties = item.get("properties") if isinstance(item.get("properties"), dict) else {}
            deployment_name = str(
                item.get("id")
                or item.get("name")
                or item.get("deployment")
                or item.get("deploymentName")
                or item.get("modelDeploymentName")
                or properties.get("deploymentName")
                or properties.get("modelDeploymentName")
                or properties.get("name")
                or ""
            ).strip()
            if "/" in deployment_name:
                deployment_name = deployment_name.split("/")[-1]
            return deployment_name

        def extract_lists(payload) -> list[list]:
            if isinstance(payload, list):
                return [payload]
            if not isinstance(payload, dict):
                return []

            list_candidates = []
            for key in ("data", "value", "items", "deployments", "resources", "models", "results"):
                candidate = payload.get(key)
                if isinstance(candidate, list):
                    list_candidates.append(candidate)

            for nested_key in ("result", "body", "response"):
                nested = payload.get(nested_key)
                if isinstance(nested, dict):
                    list_candidates.extend(extract_lists(nested))

            return list_candidates

        names = []
        for raw_items in extract_lists(parsed):
            for item in raw_items:
                deployment_name = get_name_from_item(item)
                if deployment_name:
                    names.append(deployment_name)
        return names

    def probe_deployments(candidate_urls: list[str]) -> bool:
        nonlocal last_error_message
        found_any_route = False

        for url in candidate_urls:
            request = urllib_request.Request(url, method="GET", headers=headers)
            try:
                with urllib_request.urlopen(request, timeout=10) as response:
                    if 200 <= response.status < 300:
                        found_any_route = True
                        payload_text = response.read().decode("utf-8", errors="ignore")
                        for item in parse_deployments(payload_text):
                            deployment_names.add(item)
                    else:
                        last_error_message = f"Foundry verification failed with HTTP {response.status}."
            except urllib_error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="ignore") if exc.fp else ""
                detail_lower = detail.lower()

                if exc.code in {401, 403}:
                    raise HTTPException(
                        status_code=400,
                        detail=build_debug_detail(
                            "App Registration credentials are invalid or unauthorized for Foundry.",
                            extra=f"request_url={url}; http={exc.code}; body={detail[:800] or '(empty)'}",
                        ),
                    ) from exc

                if "api version not supported" in detail_lower:
                    if is_project_endpoint:
                        if "api-version=" in url:
                            last_error_message = build_debug_detail(
                                "Foundry API Version is not supported for this project endpoint route; retrying without api-version.",
                                extra=f"request_url={url}; http={exc.code}; body={detail[:800] or '(empty)'}",
                            )
                            continue
                        raise HTTPException(
                            status_code=400,
                            detail=build_debug_detail(
                                "Foundry project endpoint rejected deployment route without api-version.",
                                extra=f"request_url={url}; http={exc.code}; body={detail[:800] or '(empty)'}",
                            ),
                        ) from exc
                    raise HTTPException(
                        status_code=400,
                        detail=build_debug_detail(
                            "Foundry API Version is not supported for this endpoint. Use a supported api-version.",
                            extra=f"request_url={url}; http={exc.code}; body={detail[:800] or '(empty)'}",
                        ),
                    ) from exc

                if exc.code == 404:
                    continue

                if exc.code in {400, 405, 422}:
                    found_any_route = True
                    continue

                last_error_message = build_debug_detail(
                    f"Foundry verification failed with HTTP {exc.code}.",
                    extra=f"request_url={url}; body={detail[:800] or '(empty)'}",
                )
                continue
            except Exception as exc:
                last_error_message = build_debug_detail(
                    "Unable to reach Foundry endpoint.",
                    extra=f"request_url={url}; error={exc}",
                )
                continue

        return found_any_route

    if is_project_endpoint:
        candidate_urls = []
        if api_version:
            query = urllib_parse.urlencode({"api-version": api_version})
            candidate_urls.append(f"{endpoint}/openai/deployments?{query}")
            candidate_urls.append(f"{endpoint}/deployments?{query}")
        candidate_urls.append(f"{endpoint}/openai/deployments")
        candidate_urls.append(f"{endpoint}/deployments")

        found_any_route = probe_deployments(candidate_urls)
        if found_any_route:
            if deployment_names:
                return (
                    f"Foundry connection verified for project '{project_name}'.",
                    sorted(deployment_names),
                )
            return (
                build_debug_detail(
                    f"Foundry endpoint is reachable for project '{project_name}', but no deployments were returned.",
                    extra="SDK and REST probes returned zero deployments.",
                ),
                [],
            )

        raise HTTPException(
            status_code=400,
            detail=last_error_message
            or build_debug_detail("Foundry verification failed. Check endpoint and API version."),
        )

    if not api_version:
        raise HTTPException(
            status_code=400,
            detail="Foundry API Version is required for non-project endpoints.",
        )

    query = urllib_parse.urlencode({"api-version": api_version})
    candidate_urls = [f"{endpoint}/openai/deployments?{query}"]
    found_any_route = probe_deployments(candidate_urls)
    if found_any_route:
        if deployment_names:
            return (
                f"Foundry connection verified for project '{project_name}'.",
                sorted(deployment_names),
            )
        return (
            build_debug_detail(
                f"Foundry endpoint is reachable for project '{project_name}', but no deployments were returned.",
                extra="SDK and REST probes returned zero deployments.",
            ),
            [],
        )

    raise HTTPException(
        status_code=400,
        detail=last_error_message
        or build_debug_detail("Foundry verification failed. Check endpoint and API version."),
    )


def verify_ollama_settings(settings: dict) -> tuple[str, list[str]]:
    base_url = str(settings.get("ollamaBaseUrl") or "").strip().rstrip("/")
    if not base_url:
        raise HTTPException(status_code=400, detail="Ollama Base URL is required.")

    request = urllib_request.Request(
        f"{base_url}/api/tags",
        method="GET",
        headers={"Accept": "application/json"},
    )

    try:
        with urllib_request.urlopen(request, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8") or "{}")
            available_models = set()
            for item in payload.get("models", []):
                if not isinstance(item, dict):
                    continue
                model_name = str(item.get("name") or item.get("model") or "").strip()
                if model_name:
                    available_models.add(model_name)

            if not available_models:
                raise HTTPException(status_code=400, detail="URL reachable but no models found.")

            expected_models = {
                str(settings.get("ollamaModelPathCoding") or "").strip(),
                str(settings.get("ollamaModelPathReasoning") or "").strip(),
                str(settings.get("ollamaModelPathFast") or "").strip(),
            }
            expected_models = {item for item in expected_models if item}

            missing = []
            for model in expected_models:
                if model in available_models:
                    continue
                if any(name.startswith(f"{model}:") for name in available_models):
                    continue
                missing.append(model)

            if missing:
                raise HTTPException(status_code=400, detail=f"Ollama connected, but models not found: {', '.join(missing)}")

        return "Ollama connection verified.", sorted(available_models)
    except HTTPException:
        raise
    except urllib_error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore") if exc.fp else ""
        message = detail or f"Ollama verification failed with HTTP {exc.code}."
        raise HTTPException(status_code=400, detail=message) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Unable to reach Ollama endpoint: {exc}") from exc


def ensure_project_structure(project_dir: Path) -> None:
    if not DEFAULT_TEMPLATE_DIR.exists():
        project_dir.mkdir(parents=True, exist_ok=True)
        return

    if not project_dir.exists():
        shutil.copytree(DEFAULT_TEMPLATE_DIR, project_dir)
        return

    for root, dirs, files in os.walk(DEFAULT_TEMPLATE_DIR):
        rel_root = Path(root).relative_to(DEFAULT_TEMPLATE_DIR)
        target_root = project_dir / rel_root
        target_root.mkdir(parents=True, exist_ok=True)

        for dirname in dirs:
            (target_root / dirname).mkdir(parents=True, exist_ok=True)

        for filename in files:
            source_path = Path(root) / filename
            target_path = target_root / filename
            if not target_path.exists():
                shutil.copy2(source_path, target_path)


@app.post("/api/settings/app")
def save_app_settings(body: AppSettingsPayload):
    try:
        incoming_settings = body.settings if isinstance(body.settings, dict) else {}
        effective_settings = {
            **DEFAULT_APP_SETTINGS,
            **incoming_settings,
        }

        target = write_app_settings_file(effective_settings)
        bootstrap_result = bootstrap_default_foundry_resources(effective_settings)

        if bootstrap_result.get("settingsUpdated"):
            target = write_app_settings_file(effective_settings)

        return {
            "ok": True,
            "path": str(target.relative_to(WORKSPACE_ROOT)),
            "foundryBootstrap": bootstrap_result,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to save app settings: {exc}") from exc


@app.get("/api/settings/app")
def get_app_settings():
    target = APP_STATE_DIR / "app.settings.env"
    return {
        "settings": load_app_settings(),
        "path": str(target.relative_to(WORKSPACE_ROOT)) if target.exists() else None,
    }


@app.post("/api/foundry/bootstrap-default")
def bootstrap_foundry_defaults():
    try:
        settings = load_app_settings()
        result = bootstrap_default_foundry_resources(settings)

        path = None
        if result.get("settingsUpdated"):
            target = write_app_settings_file(settings)
            path = str(target.relative_to(WORKSPACE_ROOT))
        else:
            target = APP_STATE_DIR / "app.settings.env"
            if target.exists():
                path = str(target.relative_to(WORKSPACE_ROOT))

        return {
            **result,
            "path": path,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to bootstrap Foundry defaults: {exc}") from exc


@app.post("/api/description/evaluate")
def evaluate_description(body: DescriptionEvaluatePayload):
    settings = load_app_settings()
    bootstrap_result = bootstrap_default_foundry_resources(settings)
    if bootstrap_result.get("settingsUpdated"):
        write_app_settings_file(settings)

    agent_id = str(settings.get("foundryDefaultAgentId") or "").strip()
    thread_id = str(settings.get("foundryDefaultThreadId") or "").strip()
    if not agent_id or not thread_id:
        return {
            "ok": False,
            "skipped": True,
            "reason": "default-agent-or-thread-missing",
        }

    return evaluate_description_with_architect(
        settings,
        description=body.description,
        assistant_id=agent_id,
        thread_id=thread_id,
        app_type=body.appType,
        cloud=body.cloud,
    )


@app.post("/api/description/improve")
def improve_description(body: DescriptionImprovePayload):
    settings = load_app_settings()
    bootstrap_result = bootstrap_default_foundry_resources(settings)
    if bootstrap_result.get("settingsUpdated"):
        write_app_settings_file(settings)

    agent_id = str(settings.get("foundryDefaultAgentId") or "").strip()
    thread_id = str(settings.get("foundryDefaultThreadId") or "").strip()
    if not agent_id or not thread_id:
        return {
            "ok": False,
            "skipped": True,
            "reason": "default-agent-or-thread-missing",
        }

    return improve_description_with_architect(
        settings,
        description=body.description,
        assistant_id=agent_id,
        thread_id=thread_id,
        app_type=body.appType,
        cloud=body.cloud,
    )


@app.post("/api/description/project/evaluate")
def evaluate_project_description(body: ProjectDescriptionPayload):
    entry = find_project_entry(body.projectId)
    if not entry:
        raise HTTPException(status_code=404, detail="Project not found")

    settings = load_app_settings()
    bootstrap_result = bootstrap_default_foundry_resources(settings)
    if bootstrap_result.get("settingsUpdated"):
        write_app_settings_file(settings)

    agent_id = str(settings.get("foundryDefaultAgentId") or "").strip()
    if not agent_id:
        return {
            "ok": False,
            "skipped": True,
            "reason": "default-agent-missing",
        }

    project_dir = entry["projectDir"]
    project_settings = load_project_settings_file(project_dir)
    metadata = read_json_file(entry["metadataPath"], {})
    if not isinstance(metadata, dict):
        metadata = {}

    known_thread_id = str(
        project_settings.get("projectThreadId")
        or metadata.get("foundryThreadId")
        or ""
    ).strip() or None

    thread_result = ensure_project_foundry_thread(
        settings,
        project_id=entry["id"],
        known_thread_id=known_thread_id,
    )

    resolved_thread_id = known_thread_id
    if thread_result.get("threadId"):
        resolved_thread_id = str(thread_result["threadId"]).strip() or resolved_thread_id

    if not resolved_thread_id:
        return {
            "ok": False,
            "skipped": True,
            "reason": "project-thread-missing",
        }

    if str(project_settings.get("projectThreadId") or "").strip() != resolved_thread_id:
        project_settings = merge_project_settings(project_settings, {"projectThreadId": resolved_thread_id})
        persist_project_settings(
            project_dir,
            entry["id"],
            entry["name"],
            entry["cloud"],
            project_settings,
        )

    if str(metadata.get("foundryThreadId") or "").strip() != resolved_thread_id:
        metadata["foundryThreadId"] = resolved_thread_id
        entry["metadataPath"].write_text(json.dumps(metadata, indent=2), encoding="utf-8")

    result = evaluate_description_with_architect(
        settings,
        description=body.description,
        assistant_id=agent_id,
        thread_id=resolved_thread_id,
        app_type=body.appType,
        cloud=body.cloud,
    )

    if result.get("ok") and not result.get("skipped"):
        project_settings = merge_project_settings(
            project_settings,
            {
                "projectDescription": str(body.description or "").strip(),
                "projectApplicationType": str(body.appType or "").strip(),
                "projectDescriptionQuality": str(result.get("level") or "").strip(),
                "projectDescriptionQualityIndex": str(result.get("levelIndex") or "").strip(),
                "projectDescriptionQualityScore": str(result.get("score") or "").strip(),
            },
        )
        persist_project_settings(
            project_dir,
            entry["id"],
            entry["name"],
            entry["cloud"],
            project_settings,
        )

        metadata["applicationDescription"] = str(body.description or "").strip()
        if body.appType:
            metadata["applicationType"] = str(body.appType or "").strip()
        entry["metadataPath"].write_text(json.dumps(metadata, indent=2), encoding="utf-8")

    return result


@app.post("/api/description/project/improve")
def improve_project_description(body: ProjectDescriptionPayload):
    entry = find_project_entry(body.projectId)
    if not entry:
        raise HTTPException(status_code=404, detail="Project not found")

    settings = load_app_settings()
    bootstrap_result = bootstrap_default_foundry_resources(settings)
    if bootstrap_result.get("settingsUpdated"):
        write_app_settings_file(settings)

    agent_id = str(settings.get("foundryDefaultAgentId") or "").strip()
    if not agent_id:
        return {
            "ok": False,
            "skipped": True,
            "reason": "default-agent-missing",
        }

    project_dir = entry["projectDir"]
    project_settings = load_project_settings_file(project_dir)
    metadata = read_json_file(entry["metadataPath"], {})
    if not isinstance(metadata, dict):
        metadata = {}

    known_thread_id = str(
        project_settings.get("projectThreadId")
        or metadata.get("foundryThreadId")
        or ""
    ).strip() or None

    thread_result = ensure_project_foundry_thread(
        settings,
        project_id=entry["id"],
        known_thread_id=known_thread_id,
    )

    resolved_thread_id = known_thread_id
    if thread_result.get("threadId"):
        resolved_thread_id = str(thread_result["threadId"]).strip() or resolved_thread_id

    if not resolved_thread_id:
        return {
            "ok": False,
            "skipped": True,
            "reason": "project-thread-missing",
        }

    if str(project_settings.get("projectThreadId") or "").strip() != resolved_thread_id:
        project_settings = merge_project_settings(project_settings, {"projectThreadId": resolved_thread_id})
        persist_project_settings(
            project_dir,
            entry["id"],
            entry["name"],
            entry["cloud"],
            project_settings,
        )

    if str(metadata.get("foundryThreadId") or "").strip() != resolved_thread_id:
        metadata["foundryThreadId"] = resolved_thread_id
        entry["metadataPath"].write_text(json.dumps(metadata, indent=2), encoding="utf-8")

    return improve_description_with_architect(
        settings,
        description=body.description,
        assistant_id=agent_id,
        thread_id=resolved_thread_id,
        app_type=body.appType,
        cloud=body.cloud,
    )


@app.get("/api/settings/app/model")
def get_app_model(purpose: str = "chat", profile: str | None = None):
    settings = load_app_settings()
    purpose_value = str(purpose or "chat").strip().lower()

    if purpose_value in {"chat"} and profile:
        purpose_value = str(profile).strip().lower()

    variable, model = resolve_model_by_purpose(settings, purpose_value)
    return {
        "purpose": purpose_value,
        "provider": str(settings.get("modelProvider") or "azure-foundry").strip(),
        "region": str(settings.get("foundryProjectRegion") or "").strip(),
        "variable": variable,
        "model": model,
    }


@app.post("/api/chat/architecture")
def architecture_chat(body: ArchitectureChatPayload):
    message = str(body.message or "").strip()
    if not message:
        raise HTTPException(status_code=400, detail="message is required")

    settings = load_app_settings()
    bootstrap_result = bootstrap_default_foundry_resources(settings)
    if bootstrap_result.get("settingsUpdated"):
        write_app_settings_file(settings)

    foundry_agent_id = str(settings.get("foundryDefaultAgentId") or "").strip() or None
    foundry_thread_id = str(settings.get("foundryDefaultThreadId") or "").strip() or None

    project_context = None
    if body.projectId:
        entry = find_project_entry(body.projectId)
        if entry:
            metadata = read_json_file(entry["metadataPath"], {})
            if not isinstance(metadata, dict):
                metadata = {}

            project_settings = load_project_settings_file(entry["projectDir"])
            project_description = str(
                project_settings.get("projectDescription")
                or metadata.get("applicationDescription")
                or ""
            ).strip()

            project_context = {
                "id": entry["id"],
                "name": str(metadata.get("name") or entry["name"]),
                "cloud": str(metadata.get("cloud") or entry["cloud"]),
                "applicationType": str(metadata.get("applicationType") or project_settings.get("projectApplicationType") or ""),
                "applicationDescription": str(metadata.get("applicationDescription") or ""),
                "projectDescription": project_description,
            }

            resolved_thread_id = resolve_project_foundry_thread_id(entry, settings)
            if resolved_thread_id:
                foundry_thread_id = resolved_thread_id

    if not foundry_agent_id:
        raise HTTPException(status_code=400, detail="Azure AI Foundry default agent is not configured.")
    if not foundry_thread_id:
        raise HTTPException(status_code=400, detail="Azure AI Foundry project thread is not configured.")

    try:
        return run_cloudarchitect_chat_agent(
            app_settings=settings,
            user_message=message,
            agent_state=body.agentState if isinstance(body.agentState, dict) else None,
            project_context=project_context,
            foundry_thread_id=foundry_thread_id,
            foundry_agent_id=foundry_agent_id,
        )
    except AzureMcpChatConfigurationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except AzureMcpChatRequestError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Architecture chat failed: {exc}") from exc


@app.get("/api/chat/architecture/status")
def architecture_chat_status(projectId: str | None = None):
    settings = load_app_settings()
    bootstrap_result = bootstrap_default_foundry_resources(settings)
    if bootstrap_result.get("settingsUpdated"):
        write_app_settings_file(settings)

    foundry_agent_id = str(settings.get("foundryDefaultAgentId") or "").strip() or None
    foundry_thread_id = str(settings.get("foundryDefaultThreadId") or "").strip() or None

    if projectId:
        entry = find_project_entry(projectId)
        if entry:
            resolved_thread_id = resolve_project_foundry_thread_id(entry, settings)
            if resolved_thread_id:
                foundry_thread_id = resolved_thread_id

    return get_cloudarchitect_chat_status(
        settings,
        foundry_thread_id=foundry_thread_id,
        foundry_agent_id=foundry_agent_id,
    )


@app.get("/api/chat/architecture/history")
def architecture_chat_history(projectId: str, limit: int = 300):
    safe_project_id = str(projectId or "").strip()
    if not safe_project_id:
        raise HTTPException(status_code=400, detail="projectId is required")

    entry = find_project_entry(safe_project_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Project not found")

    settings = load_app_settings()
    bootstrap_result = bootstrap_default_foundry_resources(settings)
    if bootstrap_result.get("settingsUpdated"):
        write_app_settings_file(settings)

    thread_id = resolve_project_foundry_thread_id(entry, settings)
    if not thread_id:
        raise HTTPException(status_code=400, detail="Azure AI Foundry project thread is not configured.")

    safe_limit = max(50, min(int(limit or 300), 1200))
    result = list_thread_messages(settings, thread_id=thread_id, limit=safe_limit)

    if result.get("ok") and not result.get("skipped"):
        return {
            "projectId": entry["id"],
            "threadId": thread_id,
            "messages": result.get("messages", []),
        }

    reason = str(result.get("reason") or "").strip().lower()
    detail = str(result.get("detail") or "").strip()
    if reason == "configuration-incomplete":
        raise HTTPException(status_code=400, detail=detail or "Azure AI Foundry configuration is incomplete.")
    if reason == "request-failed":
        raise HTTPException(status_code=502, detail=detail or "Azure AI Foundry history request failed.")

    return {
        "projectId": entry["id"],
        "threadId": thread_id,
        "messages": [],
    }


@app.post("/api/settings/app/verify")
def verify_app_settings(body: VerifySettingsPayload):
    settings = body.settings or {}

    provider = str(settings.get("modelProvider") or "ollama-local").strip().lower()
    if provider == "ollama-local":
        message, models = verify_ollama_settings(settings)
        return {"ok": True, "provider": provider, "message": message, "models": models}
    else:
        message, models = verify_foundry_settings(settings)

    return {"ok": True, "provider": provider, "message": message, "models": models}


@app.post("/api/settings/project")
def save_project_settings(body: ProjectSettingsPayload):
    try:
        if not find_project_entry(body.project.id):
            raise HTTPException(status_code=404, detail="Project not found")

        target_dir = resolve_project_dir_for_write(body.project.id, body.project.name)
        ensure_project_structure(target_dir)

        existing_settings = load_project_settings_file(target_dir)
        incoming_settings = body.settings if isinstance(body.settings, dict) else {}
        merged_settings = merge_project_settings(existing_settings, incoming_settings)

        metadata_path = target_dir / "Architecture" / "project.metadata.json"
        metadata = read_json_file(metadata_path, {})
        if not isinstance(metadata, dict):
            metadata = {}

        description_text = str(merged_settings.get("projectDescription") or "").strip()
        application_type = str(merged_settings.get("projectApplicationType") or "").strip()
        if description_text:
            metadata["applicationDescription"] = description_text
        if application_type:
            metadata["applicationType"] = application_type
        if metadata:
            metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")

        target = persist_project_settings(
            target_dir,
            body.project.id,
            body.project.name,
            body.project.cloud,
            merged_settings,
        )
        return {"ok": True, "path": str(target.relative_to(WORKSPACE_ROOT))}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to save project settings: {exc}") from exc


@app.get("/api/settings/project/{project_id}")
def get_project_settings(project_id: str):
    entry = find_project_entry(project_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Project not found")

    project_dir = entry["projectDir"]
    settings = load_project_settings_file(project_dir)

    metadata = read_json_file(entry["metadataPath"], {})
    if not isinstance(metadata, dict):
        metadata = {}

    known_thread_id = str(
        settings.get("projectThreadId")
        or metadata.get("foundryThreadId")
        or ""
    ).strip() or None

    thread_result = ensure_project_foundry_thread(
        load_app_settings(),
        project_id=entry["id"],
        known_thread_id=known_thread_id,
    )

    resolved_thread_id = known_thread_id
    if thread_result.get("threadId"):
        resolved_thread_id = str(thread_result["threadId"]).strip() or resolved_thread_id

    if resolved_thread_id:
        if str(settings.get("projectThreadId") or "").strip() != resolved_thread_id:
            settings = merge_project_settings(settings, {"projectThreadId": resolved_thread_id})
            persist_project_settings(
                project_dir,
                entry["id"],
                entry["name"],
                entry["cloud"],
                settings,
            )

        if str(metadata.get("foundryThreadId") or "").strip() != resolved_thread_id:
            metadata["foundryThreadId"] = resolved_thread_id
            entry["metadataPath"].write_text(json.dumps(metadata, indent=2), encoding="utf-8")

    target = project_dir / "project.settings.env"
    return {
        "settings": settings,
        "path": str(target.relative_to(WORKSPACE_ROOT)) if target.exists() else None,
    }


@app.post("/api/project/save")
def save_project_snapshot(body: ProjectSavePayload):
    try:
        existing_entry = find_project_entry(body.project.id)
        is_create_request = bool(body.create)
        if not existing_entry and not is_create_request:
            raise HTTPException(
                status_code=409,
                detail="Project does not exist. Create a project from the landing page first.",
            )

        project_dir = resolve_project_dir_for_write(body.project.id, body.project.name)
        architecture_dir = project_dir / "Architecture"
        metadata_path = architecture_dir / "project.metadata.json"

        ensure_project_structure(project_dir)
        architecture_dir.mkdir(parents=True, exist_ok=True)

        existing_metadata = read_json_file(metadata_path, {})
        if not isinstance(existing_metadata, dict):
            existing_metadata = {}

        project_settings = load_project_settings_file(project_dir)

        known_foundry_thread_id = str(
            project_settings.get("projectThreadId")
            or body.project.foundryThreadId
            or existing_metadata.get("foundryThreadId")
            or ""
        ).strip() or None

        app_settings = load_app_settings()
        foundry_thread_result = ensure_project_foundry_thread(
            app_settings,
            project_id=body.project.id,
            known_thread_id=known_foundry_thread_id,
        )

        foundry_thread_id = known_foundry_thread_id
        if foundry_thread_result.get("threadId"):
            foundry_thread_id = str(foundry_thread_result["threadId"]).strip() or foundry_thread_id

        project_settings = merge_project_settings(
            project_settings,
            {
                "projectDescription": str(body.project.applicationDescription or "").strip(),
                "projectApplicationType": str(body.project.applicationType or "").strip(),
                "projectDescriptionQuality": str(body.project.applicationDescriptionQuality or "").strip(),
                "projectDescriptionQualityIndex": str(body.project.applicationDescriptionQualityIndex or "").strip(),
                "projectDescriptionQualityScore": str(body.project.applicationDescriptionQualityScore or "").strip(),
            },
        )

        iac_language = str(body.project.iacLanguage or "").strip()
        if iac_language:
            project_settings = merge_project_settings(
                project_settings,
                {"iacLanguage": iac_language},
            )

        metadata_payload = {
            "id": body.project.id,
            "name": body.project.name,
            "cloud": body.project.cloud,
            "applicationType": str(body.project.applicationType or "").strip(),
            "applicationDescription": str(body.project.applicationDescription or "").strip(),
            "lastSaved": int(body.project.lastSaved) if isinstance(body.project.lastSaved, (int, float)) else 0,
        }

        if foundry_thread_id:
            metadata_payload["foundryThreadId"] = foundry_thread_id
            project_settings = merge_project_settings(
                project_settings,
                {"projectThreadId": foundry_thread_id},
            )
            persist_project_settings(
                project_dir,
                body.project.id,
                body.project.name,
                body.project.cloud,
                project_settings,
            )
        else:
            persist_project_settings(
                project_dir,
                body.project.id,
                body.project.name,
                body.project.cloud,
                project_settings,
            )

        if is_create_request and foundry_thread_id:
            post_project_created_message(
                app_settings,
                thread_id=foundry_thread_id,
                project_name=body.project.name,
                project_id=body.project.id,
                created_at=datetime.utcnow(),
            )

        metadata_path.write_text(
            json.dumps(metadata_payload, indent=2),
            encoding="utf-8",
        )

        (architecture_dir / "canvas.state.json").write_text(
            json.dumps(body.canvasState or {}, indent=2),
            encoding="utf-8",
        )

        return {
            "ok": True,
            "projectPath": str(project_dir.relative_to(WORKSPACE_ROOT)),
            "foundryThread": foundry_thread_result,
            "files": [
                str(metadata_path.relative_to(WORKSPACE_ROOT)),
                str((architecture_dir / "canvas.state.json").relative_to(WORKSPACE_ROOT)),
            ],
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to save project snapshot: {exc}") from exc


@app.post("/api/project/export-diagram")
def export_project_diagram(body: DiagramExportPayload):
    entry = find_project_entry(body.projectId)
    if not entry:
        raise HTTPException(status_code=404, detail="Project not found")

    fmt = str(body.format or "png").strip().lower()
    if fmt not in {"png", "jpeg", "jpg"}:
        raise HTTPException(status_code=400, detail="Unsupported format. Use png or jpeg.")

    image_data = str(body.imageData or "").strip()
    data_url_match = re.match(r"^data:image\/(png|jpeg);base64,(.+)$", image_data, flags=re.IGNORECASE | re.DOTALL)
    if not data_url_match:
        raise HTTPException(status_code=400, detail="Invalid image payload.")

    data_url_format = data_url_match.group(1).lower()
    payload_b64 = data_url_match.group(2)
    normalized_format = "jpeg" if fmt in {"jpeg", "jpg"} else "png"

    if normalized_format != data_url_format:
        raise HTTPException(status_code=400, detail="Image format does not match payload.")

    try:
        image_bytes = base64.b64decode(payload_b64, validate=True)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Failed to decode image payload.") from exc

    extension = "jpg" if normalized_format == "jpeg" else "png"
    project_name = sanitize_segment(body.projectName, "project")
    file_name = f"{project_name}-{format_diagram_timestamp()}.{extension}"

    diagram_dir = entry["projectDir"] / "Diagram"
    diagram_dir.mkdir(parents=True, exist_ok=True)
    target = diagram_dir / file_name
    target.write_bytes(image_bytes)

    return {
        "ok": True,
        "fileName": file_name,
        "path": str(target.relative_to(WORKSPACE_ROOT)),
    }


@app.get("/api/projects")
def list_projects():
    entries = collect_project_entries()
    return {
        "projects": [
            {
                "id": entry["id"],
                "name": entry["name"],
                "cloud": entry["cloud"],
                "lastSaved": entry["lastSaved"],
            }
            for entry in entries
        ]
    }


@app.get("/api/project/{project_id}")
def get_project_snapshot(project_id: str):
    entry = find_project_entry(project_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Project not found")

    metadata = read_json_file(entry["metadataPath"], {})
    if not isinstance(metadata, dict):
        metadata = {}

    canvas_state = read_json_file(entry["statePath"], {})
    if not isinstance(canvas_state, dict):
        canvas_state = {}

    project_settings = load_project_settings_file(entry["projectDir"])
    known_thread_id = str(
        project_settings.get("projectThreadId")
        or metadata.get("foundryThreadId")
        or ""
    ).strip() or None

    thread_result = ensure_project_foundry_thread(
        load_app_settings(),
        project_id=entry["id"],
        known_thread_id=known_thread_id,
    )

    resolved_thread_id = known_thread_id
    if thread_result.get("threadId"):
        resolved_thread_id = str(thread_result["threadId"]).strip() or resolved_thread_id

    if resolved_thread_id:
        if str(project_settings.get("projectThreadId") or "").strip() != resolved_thread_id:
            project_settings = merge_project_settings(project_settings, {"projectThreadId": resolved_thread_id})
            persist_project_settings(
                entry["projectDir"],
                entry["id"],
                entry["name"],
                entry["cloud"],
                project_settings,
            )

        if str(metadata.get("foundryThreadId") or "").strip() != resolved_thread_id:
            metadata["foundryThreadId"] = resolved_thread_id
            entry["metadataPath"].write_text(json.dumps(metadata, indent=2), encoding="utf-8")

    return {
        "project": {
            "id": entry["id"],
            "name": str(metadata.get("name") or entry["name"]),
            "cloud": str(metadata.get("cloud") or entry["cloud"]),
            "applicationType": str(metadata.get("applicationType") or ""),
            "applicationDescription": str(metadata.get("applicationDescription") or ""),
            "foundryThreadId": str(resolved_thread_id or metadata.get("foundryThreadId") or ""),
            "lastSaved": int(entry["lastSaved"]),
        },
        "canvasState": canvas_state,
    }


@app.get("/api/project/{project_id}/iac/files")
def list_project_iac_files(project_id: str):
    entry = find_project_entry(project_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Project not found")

    iac_dir = entry["projectDir"] / "IaC"
    files = list_iac_files(iac_dir)
    return {
        "root": str(iac_dir.relative_to(WORKSPACE_ROOT)),
        "files": files,
    }


@app.get("/api/project/{project_id}/iac/file")
def get_project_iac_file(project_id: str, path: str):
    entry = find_project_entry(project_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Project not found")

    iac_dir = entry["projectDir"] / "IaC"
    target = resolve_iac_file(iac_dir, path)
    try:
        content = target.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        content = target.read_text(encoding="utf-8", errors="replace")

    return {
        "path": str(target.relative_to(iac_dir)),
        "name": target.name,
        "content": content,
    }


@app.delete("/api/project/{project_id}")
def delete_project_snapshot(project_id: str):
    entry = find_project_entry(project_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Project not found")

    try:
        project_settings = load_project_settings_file(entry["projectDir"])
        metadata = read_json_file(entry["metadataPath"], {})
        if not isinstance(metadata, dict):
            metadata = {}

        thread_id = str(
            project_settings.get("projectThreadId")
            or metadata.get("foundryThreadId")
            or ""
        ).strip()

        if thread_id:
            post_project_deleted_message(
                load_app_settings(),
                thread_id=thread_id,
                project_name=str(metadata.get("name") or entry["name"]),
                project_id=entry["id"],
                deleted_at=datetime.utcnow(),
            )

        shutil.rmtree(entry["projectDir"])
        return {"ok": True}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to delete project: {exc}") from exc


@app.get("/", include_in_schema=False)
def frontend_root():
    return RedirectResponse(url="/landing.html")


@app.get("/landing.html", include_in_schema=False)
def landing_page():
    return FileResponse(FRONTEND_DIR / "landing.html")


@app.get("/canvas.html", include_in_schema=False)
def canvas_page():
    return FileResponse(FRONTEND_DIR / "canvas.html")


@app.get("/settings.html", include_in_schema=False)
def settings_page():
    return FileResponse(FRONTEND_DIR / "settings.html")


app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=False), name="frontend-static")
