from pathlib import Path
import json
import os
import re
import shutil
from urllib import error as urllib_error
from urllib import parse as urllib_parse
from urllib import request as urllib_request
from azure.identity import ClientSecretCredential
from azure.ai.projects import AIProjectClient
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
    "modelProvider": "azure-foundry",
    "azureTenantId": "65f51067-7d65-4aa9-b996-4cc43a0d7111",
    "azureClientId": "8b450e6b-0ae2-4e1b-8597-774d2bc4e747",
    "azureClientSecret": "",
    "azureSubscriptionId": "68aa0317-df02-493d-b9c7-0fa97a84fde6",
    "azureResourceGroup": "mohitRG",
    "aiFoundryProjectName": "mohitfoundry-project",
    "aiFoundryEndpoint": "https://mohitfoundry.services.ai.azure.com/api/projects/mohitfoundry-project",
    "foundryApiVersion": "2024-05-01-preview",
    "ollamaBaseUrl": "http://host.docker.internal:11434",
    "foundryModelCoding": "",
    "foundryModelReasoning": "",
    "foundryModelFast": "",
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
    lastSaved: int | None = None


class ProjectSettingsPayload(BaseModel):
    project: ProjectMeta
    settings: dict


class ProjectSavePayload(BaseModel):
    project: ProjectMeta
    canvasState: dict = {}


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


def sanitize_segment(value: str, fallback: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "-", value.strip())
    cleaned = cleaned.strip("-._")
    return cleaned or fallback


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
    provider = str(settings.get("modelProvider") or "azure-foundry").strip().lower()
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
        context_blob = json.dumps(debug_context, ensure_ascii=False)
        if extra:
            return f"{base_message} | debug={context_blob} | extra={extra}"
        return f"{base_message} | debug={context_blob}"

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
        APP_STATE_DIR.mkdir(parents=True, exist_ok=True)
        target = APP_STATE_DIR / "app.settings.env"
        target.write_text(to_env_lines(body.settings), encoding="utf-8")
        return {"ok": True, "path": str(target.relative_to(WORKSPACE_ROOT))}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to save app settings: {exc}") from exc


@app.get("/api/settings/app")
def get_app_settings():
    target = APP_STATE_DIR / "app.settings.env"
    return {
        "settings": load_app_settings(),
        "path": str(target.relative_to(WORKSPACE_ROOT)) if target.exists() else None,
    }


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


@app.post("/api/settings/app/verify")
def verify_app_settings(body: VerifySettingsPayload):
    settings = body.settings or {}

    provider = str(settings.get("modelProvider") or "azure-foundry").strip().lower()
    if provider == "ollama-local":
        message, models = verify_ollama_settings(settings)
        return {"ok": True, "provider": provider, "message": message, "models": models}
    else:
        message, models = verify_foundry_settings(settings)

    return {"ok": True, "provider": provider, "message": message, "models": models}


@app.post("/api/settings/project")
def save_project_settings(body: ProjectSettingsPayload):
    try:
        project_folder_name = sanitize_segment(body.project.name, sanitize_segment(body.project.id, "project"))
        target_dir = PROJECTS_DIR / project_folder_name
        ensure_project_structure(target_dir)

        payload = {
            "project_id": body.project.id,
            "project_name": body.project.name,
            "project_cloud": body.project.cloud,
            **body.settings,
        }

        target = target_dir / "project.settings.env"
        target.write_text(to_env_lines(payload), encoding="utf-8")
        return {"ok": True, "path": str(target.relative_to(WORKSPACE_ROOT))}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to save project settings: {exc}") from exc


@app.get("/api/settings/project/{project_id}")
def get_project_settings(project_id: str):
    entry = find_project_entry(project_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Project not found")

    target = entry["projectDir"] / "project.settings.env"
    return {
        "settings": parse_env_file(target),
        "path": str(target.relative_to(WORKSPACE_ROOT)) if target.exists() else None,
    }


@app.post("/api/project/save")
def save_project_snapshot(body: ProjectSavePayload):
    try:
        project_folder_name = sanitize_segment(body.project.name, sanitize_segment(body.project.id, "project"))
        project_dir = PROJECTS_DIR / project_folder_name
        architecture_dir = project_dir / "Architecture"

        ensure_project_structure(project_dir)
        architecture_dir.mkdir(parents=True, exist_ok=True)

        metadata_payload = {
            "id": body.project.id,
            "name": body.project.name,
            "cloud": body.project.cloud,
            "lastSaved": int(body.project.lastSaved) if isinstance(body.project.lastSaved, (int, float)) else 0,
        }

        (architecture_dir / "project.metadata.json").write_text(
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
            "files": [
                str((architecture_dir / "project.metadata.json").relative_to(WORKSPACE_ROOT)),
                str((architecture_dir / "canvas.state.json").relative_to(WORKSPACE_ROOT)),
            ],
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to save project snapshot: {exc}") from exc


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

    return {
        "project": {
            "id": entry["id"],
            "name": str(metadata.get("name") or entry["name"]),
            "cloud": str(metadata.get("cloud") or entry["cloud"]),
            "lastSaved": int(entry["lastSaved"]),
        },
        "canvasState": canvas_state,
    }


@app.delete("/api/project/{project_id}")
def delete_project_snapshot(project_id: str):
    entry = find_project_entry(project_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Project not found")

    try:
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
