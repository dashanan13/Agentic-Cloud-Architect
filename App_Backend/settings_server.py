from pathlib import Path
import base64
import hashlib
import io
import json
import os
import re
import shutil
import time
import zipfile
from datetime import datetime
from threading import Lock, RLock, Thread
from typing import Any, Mapping
from urllib import error as urllib_error
from urllib import parse as urllib_parse
from urllib import request as urllib_request
from uuid import uuid4
from azure.identity import ClientSecretCredential
from azure.ai.projects import AIProjectClient
from Agents.AzureAIFoundry.foundry_bootstrap import (
    FoundryConfigurationError,
    FoundryRequestError,
    ensure_app_agents_and_thread,
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
    post_thread_activity_message,
)
from Agents.AzureMCP.cloudarchitect_chat_agent import (
    AzureMcpChatConfigurationError,
    AzureMcpChatRequestError,
    get_cloudarchitect_chat_status,
    run_cloudarchitect_chat_agent,
)
from Agents.AzureMCP.architecture_validation_agent import (
    get_architecture_validation_status,
    run_architecture_validation_agent,
)
from Agents.AzureMCP.iac_generation_agent import generate_bicep_iac_from_canvas
from Agents.common.activity_log import log_activity as write_activity_log
from Agents.common.activity_log import resolve_logs_dir as resolve_activity_logs_dir
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse
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
    "foundryChatAgentId": "",
    "foundryIacAgentId": "",
    "foundryValidationAgentId": "",
    "foundryDefaultAgentId": "",
    "foundryDefaultThreadId": "",
    "ollamaModelPathCoding": "",
    "ollamaModelPathReasoning": "",
    "ollamaModelPathFast": "",
    "iacLiveTemplateStrict": True,
}

IAC_STAGE_DEFINITIONS = [
    {"id": "cleanup_output", "label": "Clear existing IaC output"},
    {"id": "gather_properties", "label": "Gather resource properties"},
    {"id": "dependency_tree", "label": "Build dependency tree"},
    {"id": "render_templates", "label": "Render live Bicep templates"},
    {"id": "generate_parameters", "label": "Generate parameters and pipeline"},
    {"id": "guardrails_mcp", "label": "Run MCP guardrails"},
    {"id": "guardrails_model", "label": "Run coding-model guardrails"},
    {"id": "write_files", "label": "Write generated files"},
]
IAC_TASK_RETENTION_SECONDS = 60 * 60
IAC_TASK_STALE_RUNNING_SECONDS = 8 * 60
IAC_TASK_STALE_QUEUED_SECONDS = 2 * 60
IAC_TASKS: dict[str, dict] = {}
IAC_PROJECT_TASKS: dict[str, str] = {}
IAC_TASK_LOCK = Lock()
APP_LOG_DEFAULT_SOURCE = "backend.api"

FOUNDRY_RESOURCE_LOCK = RLock()
FOUNDRY_RESOURCE_CACHE_KEYS = (
    "foundryChatAgentId",
    "foundryIacAgentId",
    "foundryValidationAgentId",
    "foundryDefaultAgentId",
    "foundryDefaultThreadId",
)
FOUNDRY_RESOURCE_ID_CACHE: dict[str, str] = {}
FOUNDRY_THREAD_RUN_LOCK_GUARD = RLock()
FOUNDRY_THREAD_RUN_LOCKS: dict[str, RLock] = {}


def _derive_log_category(event_type: str) -> str:
    normalized = str(event_type or "").strip().lower()
    if not normalized:
        return "application"
    return normalized.split(".", 1)[0]


def _normalize_log_level(value: str | None) -> str:
    candidate = str(value or "info").strip().lower()
    if candidate in {"warning", "warn"}:
        return "warning"
    if candidate in {"error", "failed", "failure", "fatal"}:
        return "error"
    return "info"


def _append_app_activity(
    event_type: str,
    *,
    status: str = "info",
    project_id: str | None = None,
    details: Mapping[str, Any] | None = None,
    category: str | None = None,
    step: str | None = None,
    source: str = APP_LOG_DEFAULT_SOURCE,
) -> None:
    write_activity_log(
        app_state_dir=APP_STATE_DIR,
        event_type=str(event_type or "application.event").strip() or "application.event",
        category=str(category or _derive_log_category(event_type)).strip() or "application",
        level=_normalize_log_level(status),
        step=str(step or event_type or "application.event").strip() or "application.event",
        source=str(source or APP_LOG_DEFAULT_SOURCE).strip() or APP_LOG_DEFAULT_SOURCE,
        project_id=str(project_id or "").strip() or None,
        details=details if isinstance(details, Mapping) else None,
    )


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
    iacParameterFormat: str | None = None
    foundryThreadId: str | None = None
    lastSaved: int | None = None


class ProjectSettingsPayload(BaseModel):
    project: ProjectMeta
    settings: dict


class ProjectSavePayload(BaseModel):
    project: ProjectMeta
    canvasState: dict = {}
    create: bool | None = None
    baseStateHash: str | None = None
    saveTrigger: str | None = None


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


class ArchitectureValidatePayload(BaseModel):
    canvasState: dict = {}
    validationRunId: str | None = None
    projectDescription: str | None = None


class ArchitectureValidationFixAuditPayload(BaseModel):
    validationRunId: str
    findingId: str
    status: str
    suggestionTitle: str | None = None
    severity: str | None = None
    attemptedOperations: list[dict] | None = None
    beforeStateHash: str | None = None
    afterStateHash: str | None = None
    resultSummary: str | None = None


class IacGeneratePayload(BaseModel):
    parameterFormat: str | None = None


def _normalize_save_trigger(value: str | None) -> str:
    text = str(value or "").strip().lower()
    if text in {"auto", "autosave", "background"}:
        return "autosave"
    if text in {"manual", "user", "button"}:
        return "manual"
    return "unspecified"


def _canvas_entity_counts(canvas_state: Mapping[str, Any] | None) -> tuple[int, int]:
    payload = canvas_state if isinstance(canvas_state, Mapping) else {}
    items = payload.get("canvasItems") if isinstance(payload.get("canvasItems"), list) else []
    connections = payload.get("canvasConnections") if isinstance(payload.get("canvasConnections"), list) else []
    return len(items), len(connections)


@app.on_event("startup")
def initialize_application_activity_log() -> None:
    _append_app_activity(
        "application.startup.begin",
        status="info",
        category="startup",
        step="initialize",
        source="backend.startup",
        details={
            "workspaceRoot": str(WORKSPACE_ROOT),
            "appStateDir": str(APP_STATE_DIR),
            "logsDir": str(resolve_activity_logs_dir(APP_STATE_DIR)),
        },
    )

    try:
        APP_STATE_DIR.mkdir(parents=True, exist_ok=True)
        projects_count = len(collect_project_entries())
        provider = str(load_app_settings().get("modelProvider") or "ollama-local").strip().lower()
        _append_app_activity(
            "application.startup.ready",
            status="info",
            category="startup",
            step="ready",
            source="backend.startup",
            details={
                "modelProvider": provider,
                "projectsLoaded": projects_count,
            },
        )
    except Exception as exc:
        _append_app_activity(
            "application.startup.failed",
            status="error",
            category="startup",
            step="failed",
            source="backend.startup",
            details={"error": str(exc)},
        )


def read_json_file(path: Path, default):
    try:
        if not path.exists():
            return default
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def compute_canvas_state_hash(canvas_state: dict | None) -> str:
    normalized = canvas_state if isinstance(canvas_state, dict) else {}
    serialized = json.dumps(
        normalized,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def normalize_parameter_format(value: str | None) -> str:
    candidate = str(value or "bicepparam").strip().lower()
    if candidate in {"bicepparam", "json"}:
        return candidate
    return "bicepparam"


def _timestamp_ms() -> int:
    return int(time.time() * 1000)


def _timestamp_utc_text() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _truncate_log_text(value: Any, *, max_chars: int = 360) -> str:
    text = str(value or "").strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"


def _ensure_validation_log_file(
    project_dir: Path,
    *,
    project_id: str,
    project_name: str,
    validation_run_id: str,
) -> Path:
    """Initialize validation log file with header. Creates if doesn't exist."""
    documentation_dir = project_dir / "Documentation"
    documentation_dir.mkdir(parents=True, exist_ok=True)
    target_path = documentation_dir / "validation.log"

    header = f"timestamp={_timestamp_utc_text()} activity=validation.log.created details={json.dumps({'projectId': project_id, 'projectName': project_name, 'validationRunId': validation_run_id}, ensure_ascii=False)}\n"
    
    # Write header if file doesn't exist, otherwise just return path
    if not target_path.exists():
        target_path.write_text(header, encoding="utf-8")
    
    return target_path


def _append_validation_log_event(
    log_path: Path,
    *,
    activity: str,
    details: Any,
) -> None:
    """Append a single event to validation log file immediately (streaming write)."""
    if not log_path:
        return
    
    event_timestamp = _timestamp_utc_text()
    safe_activity = str(activity or "validation.event").strip() or "validation.event"
    
    if isinstance(details, (Mapping, list)):
        details_text = json.dumps(details, ensure_ascii=False)
    else:
        details_text = str(details or "").strip()
    
    line = f"timestamp={event_timestamp} activity={safe_activity} details={details_text}\n"
    
    try:
        # Append to file immediately for real-time logging
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(line)
            f.flush()  # Force flush to disk
    except Exception:
        pass  # Silently ignore write errors


def _write_project_validation_text_log(
    project_dir: Path,
    *,
    project_id: str,
    project_name: str,
    validation_run_id: str,
    events: list[dict[str, Any]],
) -> Path:
    """Legacy function for backward compatibility. Now just returns the log path."""
    # The log is already being written in real-time via _append_validation_log_event
    # This function is kept for backward compatibility but no longer does bulk writes
    documentation_dir = project_dir / "Documentation"
    documentation_dir.mkdir(parents=True, exist_ok=True)
    target_path = documentation_dir / "validation.log"
    return target_path


def _sanitize_iac_stage_detail_items(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []

    normalized: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue

        label = str(item.get("label") or "Step").strip() or "Step"
        text_value = str(item.get("value") or "").strip()
        if not text_value:
            continue

        status = str(item.get("status") or "info").strip().lower()
        if status not in {"info", "pass", "fail", "warning", "skipped"}:
            status = "info"

        normalized.append(
            {
                "label": label,
                "value": text_value,
                "status": status,
            }
        )
        if len(normalized) >= 20:
            break

    return normalized


def _new_iac_stage_payload() -> list[dict]:
    return [
        {
            "id": stage["id"],
            "label": stage["label"],
            "status": "pending",
            "message": "",
            "detailSummary": "",
            "detailItems": [],
            "startedAt": None,
            "completedAt": None,
        }
        for stage in IAC_STAGE_DEFINITIONS
    ]


def _clamp_progress(value: int | float | str | None, fallback: int = 0) -> int:
    try:
        numeric = int(float(value))
    except Exception:
        numeric = int(fallback)
    if numeric < 0:
        return 0
    if numeric > 100:
        return 100
    return numeric


def _cleanup_iac_tasks() -> None:
    now_seconds = time.time()
    now_ms = int(now_seconds * 1000)
    removable: list[str] = []
    for task_id, task in IAC_TASKS.items():
        status = str(task.get("status") or "").strip().lower()
        if status in {"queued", "running"}:
            updated_at = task.get("updatedAt")
            updated_seconds = float(updated_at) / 1000.0 if isinstance(updated_at, (int, float)) else None
            if updated_seconds is not None:
                age_seconds = now_seconds - updated_seconds
                if status == "running" and age_seconds > IAC_TASK_STALE_RUNNING_SECONDS:
                    task["status"] = "error"
                    task["message"] = "Generation task timed out. Start generation again."
                    task["error"] = task["message"]
                    task["updatedAt"] = now_ms
                    task["finishedAt"] = now_ms
                elif status == "queued" and age_seconds > IAC_TASK_STALE_QUEUED_SECONDS:
                    task["status"] = "error"
                    task["message"] = "Queued generation task expired. Start generation again."
                    task["error"] = task["message"]
                    task["updatedAt"] = now_ms
                    task["finishedAt"] = now_ms
            continue
        finished_at = task.get("finishedAt")
        if isinstance(finished_at, (int, float)):
            age_seconds = now_seconds - (float(finished_at) / 1000.0)
            if age_seconds > IAC_TASK_RETENTION_SECONDS:
                removable.append(task_id)

    for task_id in removable:
        IAC_TASKS.pop(task_id, None)

    stale_project_keys: list[str] = []
    for project_id, task_id in IAC_PROJECT_TASKS.items():
        if task_id not in IAC_TASKS:
            stale_project_keys.append(project_id)

    for project_id in stale_project_keys:
        IAC_PROJECT_TASKS.pop(project_id, None)


def _serialize_iac_task(task: dict) -> dict:
    response = {
        "taskId": str(task.get("taskId") or "").strip(),
        "projectId": str(task.get("projectId") or "").strip(),
        "status": str(task.get("status") or "queued").strip().lower(),
        "message": str(task.get("message") or "").strip(),
        "progress": _clamp_progress(task.get("progress"), 0),
        "parameterFormat": normalize_parameter_format(task.get("parameterFormat") or "bicepparam"),
        "createdAt": task.get("createdAt"),
        "updatedAt": task.get("updatedAt"),
        "startedAt": task.get("startedAt"),
        "finishedAt": task.get("finishedAt"),
        "stages": [],
    }

    for stage in task.get("stages") if isinstance(task.get("stages"), list) else []:
        if not isinstance(stage, dict):
            continue
        response["stages"].append(
            {
                "id": str(stage.get("id") or "").strip(),
                "label": str(stage.get("label") or "").strip(),
                "status": str(stage.get("status") or "pending").strip().lower(),
                "message": str(stage.get("message") or "").strip(),
                "detailSummary": str(stage.get("detailSummary") or "").strip(),
                "detailItems": _sanitize_iac_stage_detail_items(stage.get("detailItems")),
                "startedAt": stage.get("startedAt"),
                "completedAt": stage.get("completedAt"),
            }
        )

    if task.get("result") is not None:
        response["result"] = task.get("result")
    if task.get("error"):
        response["error"] = str(task.get("error") or "").strip()

    return response


def _create_iac_task(project_id: str, parameter_format: str) -> dict:
    now = _timestamp_ms()
    task_id = uuid4().hex
    payload = {
        "taskId": task_id,
        "projectId": str(project_id or "").strip(),
        "status": "queued",
        "message": "Generation queued",
        "progress": 1,
        "parameterFormat": normalize_parameter_format(parameter_format),
        "createdAt": now,
        "updatedAt": now,
        "startedAt": None,
        "finishedAt": None,
        "stages": _new_iac_stage_payload(),
        "result": None,
        "error": None,
    }
    IAC_TASKS[task_id] = payload
    IAC_PROJECT_TASKS[payload["projectId"]] = task_id

    _append_app_activity(
        "codegen.task",
        status="info",
        project_id=payload["projectId"],
        category="codegen",
        step="queued",
        source="backend.codegen",
        details={
            "taskId": task_id,
            "parameterFormat": payload["parameterFormat"],
        },
    )
    return payload


def _mark_iac_task_running(task_id: str, message: str = "IaC generation started") -> None:
    now = _timestamp_ms()
    task = IAC_TASKS.get(task_id)
    if not task:
        return

    task["status"] = "running"
    task["message"] = str(message or "IaC generation started").strip()
    task["updatedAt"] = now
    if not isinstance(task.get("startedAt"), (int, float)):
        task["startedAt"] = now
    task["progress"] = max(1, _clamp_progress(task.get("progress"), 1))

    _append_app_activity(
        "codegen.task",
        status="info",
        project_id=str(task.get("projectId") or "").strip() or None,
        category="codegen",
        step="running",
        source="backend.codegen",
        details={
            "taskId": str(task.get("taskId") or task_id),
            "message": task["message"],
            "progress": task["progress"],
        },
    )


def _update_iac_task_stage(
    task_id: str,
    stage_id: str,
    *,
    status: str,
    message: str = "",
    progress: int | float | str | None = None,
    detail_items: Any = None,
    detail_summary: Any = None,
) -> None:
    now = _timestamp_ms()
    task = IAC_TASKS.get(task_id)
    if not task:
        return

    normalized_status = str(status or "pending").strip().lower()
    if normalized_status not in {"pending", "running", "completed", "error"}:
        normalized_status = "running"

    stages = task.get("stages") if isinstance(task.get("stages"), list) else []
    stage = None
    for candidate in stages:
        if isinstance(candidate, dict) and str(candidate.get("id") or "").strip() == stage_id:
            stage = candidate
            break

    if not stage:
        return

    stage["status"] = normalized_status
    stage["message"] = str(message or "").strip()
    if detail_items is not None:
        stage["detailItems"] = _sanitize_iac_stage_detail_items(detail_items)
    if detail_summary is not None:
        stage["detailSummary"] = str(detail_summary or "").strip()
    if normalized_status == "running":
        if not isinstance(stage.get("startedAt"), (int, float)):
            stage["startedAt"] = now
        stage["completedAt"] = None
    elif normalized_status in {"completed", "error"}:
        if not isinstance(stage.get("startedAt"), (int, float)):
            stage["startedAt"] = now
        stage["completedAt"] = now

    task["updatedAt"] = now
    if normalized_status == "error":
        task["status"] = "error"
        task["message"] = stage["message"] or "IaC generation failed"
        task["error"] = task["message"]
        task["finishedAt"] = now
    else:
        task["status"] = "running"
        task["message"] = stage["message"] or task.get("message") or "IaC generation in progress"

    if progress is not None:
        task["progress"] = _clamp_progress(progress, task.get("progress") or 0)

    _append_app_activity(
        "codegen.stage",
        status="error" if normalized_status == "error" else "info",
        project_id=str(task.get("projectId") or "").strip() or None,
        category="codegen",
        step=normalized_status,
        source="backend.codegen",
        details={
            "taskId": str(task.get("taskId") or task_id),
            "stageId": str(stage.get("id") or stage_id).strip(),
            "stageLabel": str(stage.get("label") or "").strip(),
            "message": stage.get("message"),
            "progress": task.get("progress"),
        },
    )


def _complete_iac_task(task_id: str, result: dict | None = None) -> None:
    now = _timestamp_ms()
    task = IAC_TASKS.get(task_id)
    if not task:
        return

    for stage in task.get("stages") if isinstance(task.get("stages"), list) else []:
        if not isinstance(stage, dict):
            continue
        if str(stage.get("status") or "pending").strip().lower() == "running":
            stage["status"] = "completed"
            stage["completedAt"] = now
            if not str(stage.get("message") or "").strip():
                stage["message"] = "Completed"

    task["status"] = "completed"
    task["message"] = "IaC generation completed"
    task["progress"] = 100
    task["updatedAt"] = now
    task["finishedAt"] = now
    task["result"] = result if isinstance(result, dict) else result
    task["error"] = None

    _append_app_activity(
        "codegen.task",
        status="info",
        project_id=str(task.get("projectId") or "").strip() or None,
        category="codegen",
        step="completed",
        source="backend.codegen",
        details={
            "taskId": str(task.get("taskId") or task_id),
            "progress": task.get("progress"),
            "resultOk": bool(isinstance(result, Mapping) and result.get("ok")),
        },
    )


def _fail_iac_task(task_id: str, message: str) -> None:
    now = _timestamp_ms()
    task = IAC_TASKS.get(task_id)
    if not task:
        return

    failure_message = str(message or "IaC generation failed").strip() or "IaC generation failed"
    marked_stage = False
    for stage in task.get("stages") if isinstance(task.get("stages"), list) else []:
        if not isinstance(stage, dict):
            continue
        if str(stage.get("status") or "pending").strip().lower() == "running":
            stage["status"] = "error"
            stage["message"] = failure_message
            if not isinstance(stage.get("startedAt"), (int, float)):
                stage["startedAt"] = now
            stage["completedAt"] = now
            marked_stage = True
            break

    if not marked_stage:
        stages = task.get("stages") if isinstance(task.get("stages"), list) else []
        if stages and isinstance(stages[0], dict):
            stages[0]["status"] = "error"
            stages[0]["message"] = failure_message
            stages[0]["startedAt"] = stages[0].get("startedAt") or now
            stages[0]["completedAt"] = now

    task["status"] = "error"
    task["message"] = failure_message
    task["error"] = failure_message
    task["updatedAt"] = now
    task["finishedAt"] = now

    _append_app_activity(
        "codegen.task",
        status="error",
        project_id=str(task.get("projectId") or "").strip() or None,
        category="codegen",
        step="failed",
        source="backend.codegen",
        details={
            "taskId": str(task.get("taskId") or task_id),
            "error": failure_message,
            "progress": task.get("progress"),
        },
    )


def _record_iac_progress_event(task_id: str, event: dict) -> None:
    if not isinstance(event, dict):
        return

    stage_id = str(event.get("stage") or "").strip()
    if not stage_id:
        return

    _update_iac_task_stage(
        task_id,
        stage_id,
        status=str(event.get("status") or "running"),
        message=str(event.get("message") or "").strip(),
        progress=event.get("progress"),
        detail_items=event.get("detailItems"),
        detail_summary=event.get("detailSummary"),
    )


def _get_latest_project_iac_task(project_id: str) -> dict | None:
    task_id = IAC_PROJECT_TASKS.get(str(project_id or "").strip())
    if not task_id:
        return None
    task = IAC_TASKS.get(task_id)
    if not isinstance(task, dict):
        return None
    return task


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

    if not loaded.get("foundryChatAgentId") and loaded.get("foundryDefaultAgentId"):
        loaded["foundryChatAgentId"] = loaded.get("foundryDefaultAgentId")
    if not loaded.get("foundryDefaultAgentId") and loaded.get("foundryChatAgentId"):
        loaded["foundryDefaultAgentId"] = loaded.get("foundryChatAgentId")

    loaded.pop("foundryMasterAgentId", None)

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
            "foundryChatAgentId",
            "foundryIacAgentId",
            "foundryValidationAgentId",
            "foundryDefaultAgentId",
            "foundryDefaultThreadId",
            "iacLiveTemplateStrict",
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
            "iacLiveTemplateStrict",
        )

    return {key: sanitized.get(key, "") for key in keys}


def is_azure_foundry_provider(settings: dict) -> bool:
    return str(settings.get("modelProvider") or "ollama-local").strip().lower() == "azure-foundry"


def _non_empty_text(value: Any) -> str:
    return str(value or "").strip()


def _merge_cached_foundry_resource_ids(settings: dict) -> None:
    if not isinstance(settings, dict):
        return
    for key in FOUNDRY_RESOURCE_CACHE_KEYS:
        current_value = _non_empty_text(settings.get(key))
        if current_value:
            continue
        cached_value = _non_empty_text(FOUNDRY_RESOURCE_ID_CACHE.get(key))
        if cached_value:
            settings[key] = cached_value


def _update_cached_foundry_resource_ids(settings: Mapping[str, Any]) -> None:
    if not isinstance(settings, Mapping):
        return
    for key in FOUNDRY_RESOURCE_CACHE_KEYS:
        value = _non_empty_text(settings.get(key))
        if value:
            FOUNDRY_RESOURCE_ID_CACHE[key] = value
        elif key in settings:
            FOUNDRY_RESOURCE_ID_CACHE.pop(key, None)


def _resolve_foundry_thread_run_lock(thread_id: str | None) -> RLock:
    safe_thread_id = _non_empty_text(thread_id)
    if not safe_thread_id:
        return FOUNDRY_RESOURCE_LOCK

    with FOUNDRY_THREAD_RUN_LOCK_GUARD:
        existing = FOUNDRY_THREAD_RUN_LOCKS.get(safe_thread_id)
        if existing is not None:
            return existing

        created = RLock()
        FOUNDRY_THREAD_RUN_LOCKS[safe_thread_id] = created
        return created


def _resolve_foundry_chat_agent_id(settings: Mapping[str, Any]) -> str:
    return str(settings.get("foundryChatAgentId") or settings.get("foundryDefaultAgentId") or "").strip()


def _resolve_foundry_iac_agent_id(settings: Mapping[str, Any]) -> str:
    return str(
        settings.get("foundryIacAgentId")
        or settings.get("foundryChatAgentId")
        or settings.get("foundryDefaultAgentId")
        or ""
    ).strip()


def _resolve_foundry_validation_agent_id(settings: Mapping[str, Any]) -> str:
    return str(settings.get("foundryValidationAgentId") or "").strip()


def write_app_settings_file(settings: dict) -> Path:
    APP_STATE_DIR.mkdir(parents=True, exist_ok=True)
    target = APP_STATE_DIR / "app.settings.env"
    persistable = build_persistable_app_settings(settings)
    target.write_text(to_env_lines(persistable), encoding="utf-8")
    return target


def bootstrap_default_foundry_resources(settings: dict) -> dict:
    with FOUNDRY_RESOURCE_LOCK:
        if not is_azure_foundry_provider(settings):
            _append_app_activity(
                "foundry.bootstrap",
                status="info",
                category="foundry",
                step="skipped",
                source="backend.foundry",
                details={"reason": "provider-not-azure-foundry"},
            )
            return {
                "ok": True,
                "skipped": True,
                "reason": "provider-not-azure-foundry",
            }

        _merge_cached_foundry_resource_ids(settings)

        known_chat_agent_id = _resolve_foundry_chat_agent_id(settings) or None
        known_iac_agent_id = _non_empty_text(settings.get("foundryIacAgentId")) or None
        known_default_agent_id = _non_empty_text(settings.get("foundryDefaultAgentId")) or None
        known_validation_agent_id = _non_empty_text(settings.get("foundryValidationAgentId")) or None

        shared_validation_agent_id = bool(
            known_validation_agent_id
            and (
                known_validation_agent_id == known_chat_agent_id
                or known_validation_agent_id == known_default_agent_id
            )
        )
        if shared_validation_agent_id:
            known_validation_agent_id = None
            settings["foundryValidationAgentId"] = ""

            _append_app_activity(
                "foundry.bootstrap",
                status="info",
                category="foundry",
                step="migration-reset-validation-agent",
                source="backend.foundry",
                details={
                    "reason": "validation-agent-shared-with-chat",
                    "chatAgentId": known_chat_agent_id,
                    "defaultAgentId": known_default_agent_id,
                },
            )
        known_thread_id = _non_empty_text(settings.get("foundryDefaultThreadId")) or None

        try:
            result = ensure_app_agents_and_thread(
                settings,
                known_chat_agent_id=known_chat_agent_id,
                known_iac_agent_id=known_iac_agent_id,
                known_validation_agent_id=known_validation_agent_id,
                known_thread_id=known_thread_id,
            )
        except FoundryConfigurationError as exc:
            _append_app_activity(
                "foundry.bootstrap",
                status="warning",
                category="foundry",
                step="skipped",
                source="backend.foundry",
                details={
                    "reason": "configuration-incomplete",
                    "detail": str(exc),
                },
            )
            return {
                "ok": True,
                "skipped": True,
                "reason": "configuration-incomplete",
                "detail": str(exc),
            }
        except FoundryRequestError as exc:
            _append_app_activity(
                "foundry.bootstrap",
                status="error",
                category="foundry",
                step="failed",
                source="backend.foundry",
                details={
                    "reason": "request-failed",
                    "statusCode": exc.status_code,
                    "detail": f"{exc} {str(exc.detail or '')[:400]}".strip(),
                },
            )
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

        _update_cached_foundry_resource_ids(settings)

        _append_app_activity(
            "foundry.bootstrap",
            status="info",
            category="foundry",
            step="completed",
            source="backend.foundry",
            details={
                "chatAgentId": result.chat_agent_id,
                "iacAgentId": result.iac_agent_id,
                "validationAgentId": result.validation_agent_id,
                "threadId": result.thread_id,
                "createdChatAgent": result.created_chat_agent,
                "createdIacAgent": result.created_iac_agent,
                "createdValidationAgent": result.created_validation_agent,
                "createdThread": result.created_thread,
                "settingsUpdated": changed,
            },
        )

        return {
            "ok": True,
            "skipped": False,
            "agentId": result.chat_agent_id,
            "chatAgentId": result.chat_agent_id,
            "iacAgentId": result.iac_agent_id,
            "validationAgentId": result.validation_agent_id,
            "threadId": result.thread_id,
            "createdAgent": result.created_chat_agent,
            "createdChatAgent": result.created_chat_agent,
            "createdIacAgent": result.created_iac_agent,
            "createdValidationAgent": result.created_validation_agent,
            "createdThread": result.created_thread,
            "settingsUpdated": changed,
        }


def _record_orchestration_event(
    settings: Mapping[str, Any],
    *,
    thread_id: str | None,
    workflow: str,
    status: str,
    project_id: str | None = None,
    project_name: str | None = None,
    detail: str = "",
    child_agent_id: str | None = None,
) -> None:
    if not is_azure_foundry_provider(dict(settings)):
        return

    safe_thread_id = str(thread_id or "").strip()
    safe_workflow = str(workflow or "").strip() or "workflow"
    safe_status = str(status or "").strip() or "event"
    safe_project_id = str(project_id or "").strip()
    safe_project_name = str(project_name or "").strip()
    safe_child_agent_id = str(child_agent_id or "").strip()
    safe_detail = str(detail or "").strip()
    if len(safe_detail) > 240:
        safe_detail = safe_detail[:240] + "..."

    if not safe_thread_id:
        _append_app_activity(
            "orchestration.event",
            status="warning",
            project_id=safe_project_id,
            category="orchestration",
            step="thread-missing",
            source="backend.orchestrator",
            details={
                "workflow": safe_workflow,
                "status": safe_status,
                "projectName": safe_project_name,
                "childAgentId": safe_child_agent_id,
                "reason": "thread-missing",
            },
        )
        return

    payload = {
        "workflow": safe_workflow,
        "status": safe_status,
        "projectId": safe_project_id,
        "projectName": safe_project_name,
        "childAgentId": safe_child_agent_id,
        "detail": safe_detail,
        "timestampUtc": datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
    }
    serialized_payload = json.dumps(payload, ensure_ascii=False)

    try:
        post_thread_activity_message(
            settings,
            thread_id=safe_thread_id,
            actor="orchestrator",
            activity_type=f"{safe_workflow}.{safe_status}",
            content=serialized_payload,
        )
    finally:
        _append_app_activity(
            "orchestration.event",
            status="error" if safe_status.lower() == "failed" else "info",
            project_id=safe_project_id,
            category="orchestration",
            step=safe_status,
            source="backend.orchestrator",
            details={
                "workflow": safe_workflow,
                "status": safe_status,
                "projectName": safe_project_name,
                "childAgentId": safe_child_agent_id,
                "threadId": safe_thread_id,
                "detail": safe_detail,
            },
        )


def _record_validation_thread_payload(
    settings: Mapping[str, Any],
    *,
    thread_id: str | None,
    activity_type: str,
    payload: Mapping[str, Any],
    project_id: str | None = None,
    project_name: str | None = None,
) -> None:
    if not is_azure_foundry_provider(dict(settings)):
        return

    safe_thread_id = str(thread_id or "").strip()
    safe_project_id = str(project_id or "").strip()
    safe_project_name = str(project_name or "").strip()
    safe_activity_type = str(activity_type or "validation.event").strip() or "validation.event"
    serialized_payload = json.dumps(payload if isinstance(payload, Mapping) else {}, ensure_ascii=False)

    if not safe_thread_id:
        _append_app_activity(
            "validation.audit",
            status="warning",
            project_id=safe_project_id,
            category="validation",
            step="thread-missing",
            source="backend.validation",
            details={
                "projectName": safe_project_name,
                "activityType": safe_activity_type,
                "reason": "thread-missing",
            },
        )
        return

    result = post_thread_activity_message(
        settings,
        thread_id=safe_thread_id,
        actor="validation-agent",
        activity_type=safe_activity_type,
        content=serialized_payload,
    )

    status_text = "error"
    if bool(result.get("ok")) and not bool(result.get("skipped")):
        status_text = "info"
    elif bool(result.get("ok")) and bool(result.get("skipped")):
        status_text = "warning"

    _append_app_activity(
        "validation.audit",
        status=status_text,
        project_id=safe_project_id,
        category="validation",
        step=safe_activity_type,
        source="backend.validation",
        details={
            "projectName": safe_project_name,
            "threadId": safe_thread_id,
            "activityType": safe_activity_type,
            "threadPost": result,
        },
    )


def ensure_project_foundry_thread(settings: dict, project_id: str, known_thread_id: str | None = None) -> dict:
    if not is_azure_foundry_provider(settings):
        _append_app_activity(
            "foundry.thread",
            status="info",
            project_id=str(project_id or "").strip() or None,
            category="foundry",
            step="skipped",
            source="backend.foundry",
            details={"reason": "provider-not-azure-foundry"},
        )
        return {
            "ok": True,
            "skipped": True,
            "reason": "provider-not-azure-foundry",
        }

    safe_project_id = str(project_id or "").strip()
    if not safe_project_id:
        _append_app_activity(
            "foundry.thread",
            status="warning",
            category="foundry",
            step="failed",
            source="backend.foundry",
            details={
                "reason": "project-id-missing",
            },
        )
        return {
            "ok": False,
            "skipped": False,
            "reason": "project-id-missing",
            "detail": "project_id is required",
        }

    try:
        with FOUNDRY_RESOURCE_LOCK:
            thread_result = ensure_project_thread_for_project(
                settings,
                project_id=safe_project_id,
                known_thread_id=known_thread_id,
            )
    except FoundryConfigurationError as exc:
        _append_app_activity(
            "foundry.thread",
            status="warning",
            project_id=safe_project_id,
            category="foundry",
            step="skipped",
            source="backend.foundry",
            details={
                "reason": "configuration-incomplete",
                "detail": str(exc),
            },
        )
        return {
            "ok": True,
            "skipped": True,
            "reason": "configuration-incomplete",
            "detail": str(exc),
        }
    except FoundryRequestError as exc:
        _append_app_activity(
            "foundry.thread",
            status="error",
            project_id=safe_project_id,
            category="foundry",
            step="failed",
            source="backend.foundry",
            details={
                "reason": "request-failed",
                "statusCode": exc.status_code,
                "detail": f"{exc} {str(exc.detail or '')[:400]}".strip(),
            },
        )
        return {
            "ok": False,
            "skipped": False,
            "reason": "request-failed",
            "statusCode": exc.status_code,
            "detail": f"{exc} {str(exc.detail or '')[:800]}".strip(),
        }

    _append_app_activity(
        "foundry.thread",
        status="info",
        project_id=safe_project_id,
        category="foundry",
        step="completed",
        source="backend.foundry",
        details={
            "threadId": thread_result.thread_id,
            "created": bool(thread_result.created),
        },
    )

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
        provider = str(effective_settings.get("modelProvider") or "ollama-local").strip().lower()

        _append_app_activity(
            "settings.app.save",
            status="info",
            category="settings",
            step="requested",
            source="backend.api",
            details={
                "provider": provider,
                "requestedKeyCount": len(incoming_settings),
                "requestedKeys": sorted([str(key) for key in incoming_settings.keys()])[:50],
            },
        )

        target = write_app_settings_file(effective_settings)
        bootstrap_result = bootstrap_default_foundry_resources(effective_settings)

        if bootstrap_result.get("settingsUpdated"):
            target = write_app_settings_file(effective_settings)

        _append_app_activity(
            "settings.app.save",
            status="info",
            category="settings",
            step="completed",
            source="backend.api",
            details={
                "provider": provider,
                "path": str(target.relative_to(WORKSPACE_ROOT)),
                "foundryBootstrap": bootstrap_result,
            },
        )

        return {
            "ok": True,
            "path": str(target.relative_to(WORKSPACE_ROOT)),
            "foundryBootstrap": bootstrap_result,
        }
    except Exception as exc:
        _append_app_activity(
            "settings.app.save",
            status="error",
            category="settings",
            step="failed",
            source="backend.api",
            details={"error": str(exc)},
        )
        raise HTTPException(status_code=500, detail=f"Failed to save app settings: {exc}") from exc


@app.get("/api/settings/app")
def get_app_settings():
    target = APP_STATE_DIR / "app.settings.env"
    settings = load_app_settings()
    _append_app_activity(
        "settings.app.load",
        status="info",
        category="settings",
        step="completed",
        source="backend.api",
        details={
            "provider": str(settings.get("modelProvider") or "ollama-local").strip().lower(),
            "hasFoundryEndpoint": bool(str(settings.get("aiFoundryEndpoint") or "").strip()),
        },
    )
    return {
        "settings": settings,
        "path": str(target.relative_to(WORKSPACE_ROOT)) if target.exists() else None,
    }


@app.post("/api/foundry/bootstrap-default")
def bootstrap_foundry_defaults():
    try:
        _append_app_activity(
            "foundry.bootstrap",
            status="info",
            category="foundry",
            step="requested",
            source="backend.api",
        )
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

        _append_app_activity(
            "foundry.bootstrap",
            status="info",
            category="foundry",
            step="completed",
            source="backend.api",
            details={
                "path": path,
                "result": result,
            },
        )

        return {
            **result,
            "path": path,
        }
    except Exception as exc:
        _append_app_activity(
            "foundry.bootstrap",
            status="error",
            category="foundry",
            step="failed",
            source="backend.api",
            details={"error": str(exc)},
        )
        raise HTTPException(status_code=500, detail=f"Failed to bootstrap Foundry defaults: {exc}") from exc


@app.post("/api/foundry/verify-agents-and-threads")
def verify_agents_and_threads_endpoint():
    """
    Periodic verification (called every 30s) to check if agents and threads exist.
    Returns simple status: agent presence and thread presence.
    """
    try:
        from Agents.AzureAIFoundry.foundry_bootstrap import verify_agents_and_threads
        
        with FOUNDRY_RESOURCE_LOCK:
            settings = load_app_settings()

            # Ensure agents/thread exist on every verification cycle.
            bootstrap_result = bootstrap_default_foundry_resources(settings)
            if bootstrap_result.get("settingsUpdated"):
                write_app_settings_file(settings)

            result = verify_agents_and_threads(settings)
        
        # Log only if there are issues (agents missing or errors) or agents were updated
        agents_updated = any([
            getattr(result.chat_agent, 'was_updated', False),
            getattr(result.iac_agent, 'was_updated', False),
            getattr(result.validation_agent, 'was_updated', False),
        ])
        
        if result.has_errors or not all([
            result.chat_agent.is_present,
            result.iac_agent.is_present,
            result.validation_agent.is_present,
            result.chat_thread,
        ]) or agents_updated:
            _append_app_activity(
                "foundry.verify",
                status="info" if agents_updated else ("warning" if result.has_errors else "info"),
                category="foundry",
                step="agents-updated" if agents_updated else ("issues-detected" if not result.has_errors else "failed"),
                source="backend.api",
                details={
                    "chatAgentPresent": result.chat_agent.is_present,
                    "iacAgentPresent": result.iac_agent.is_present,
                    "validationAgentPresent": result.validation_agent.is_present,
                    "chatThreadPresent": result.chat_thread,
                    "hasErrors": result.has_errors,
                    "agentsUpdated": agents_updated,
                    "chatAgentUpdated": getattr(result.chat_agent, 'was_updated', False),
                    "iacAgentUpdated": getattr(result.iac_agent, 'was_updated', False),
                    "validationAgentUpdated": getattr(result.validation_agent, 'was_updated', False),
                },
            )
        
        chat_agent_id = str(settings.get("foundryChatAgentId") or "").strip() or None
        iac_agent_id = str(settings.get("foundryIacAgentId") or "").strip() or None
        validation_agent_id = str(settings.get("foundryValidationAgentId") or "").strip() or None
        
        return {
            "ok": not result.has_errors,
            "agentIds": {
                "chat": chat_agent_id,
                "iac": iac_agent_id,
                "validation": validation_agent_id,
            },
            "agents": {
                "chat": {
                    "present": result.chat_agent.is_present,
                    "descriptionMatches": result.chat_agent.description_matches,
                    "wasUpdated": getattr(result.chat_agent, 'was_updated', False),
                },
                "iac": {
                    "present": result.iac_agent.is_present,
                    "descriptionMatches": result.iac_agent.description_matches,
                    "wasUpdated": getattr(result.iac_agent, 'was_updated', False),
                },
                "validation": {
                    "present": result.validation_agent.is_present,
                    "descriptionMatches": result.validation_agent.description_matches,
                    "wasUpdated": getattr(result.validation_agent, 'was_updated', False),
                },
            },
            "threads": {
                "chat": result.chat_thread,
                "iac": result.iac_thread,
                "validation": result.validation_thread,
            },
        }
    except Exception as exc:
        _append_app_activity(
            "foundry.verify",
            status="error",
            category="foundry",
            step="failed",
            source="backend.api",
            details={"error": str(exc)[:200]},
        )
        return {
            "ok": False,
            "error": str(exc)[:200],
            "agents": {
                "chat": {"present": False},
                "iac": {"present": False},
                "validation": {"present": False},
            },
            "threads": {
                "chat": None,
                "iac": None,
                "validation": None,
            },
        }


@app.post("/api/description/evaluate")
def evaluate_description(body: DescriptionEvaluatePayload):
    settings = load_app_settings()
    bootstrap_result = bootstrap_default_foundry_resources(settings)
    if bootstrap_result.get("settingsUpdated"):
        write_app_settings_file(settings)

    agent_id = _resolve_foundry_chat_agent_id(settings)
    thread_id = str(settings.get("foundryDefaultThreadId") or "").strip()
    if not agent_id or not thread_id:
        return {
            "ok": False,
            "skipped": True,
            "reason": "chat-agent-or-thread-missing",
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

    agent_id = _resolve_foundry_chat_agent_id(settings)
    thread_id = str(settings.get("foundryDefaultThreadId") or "").strip()
    if not agent_id or not thread_id:
        return {
            "ok": False,
            "skipped": True,
            "reason": "chat-agent-or-thread-missing",
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

    agent_id = _resolve_foundry_chat_agent_id(settings)
    if not agent_id:
        return {
            "ok": False,
            "skipped": True,
            "reason": "chat-agent-missing",
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

    agent_id = _resolve_foundry_chat_agent_id(settings)
    if not agent_id:
        return {
            "ok": False,
            "skipped": True,
            "reason": "chat-agent-missing",
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
    _append_app_activity(
        "models.resolve",
        status="info",
        category="model",
        step="resolved",
        source="backend.api",
        details={
            "purpose": purpose_value,
            "provider": str(settings.get("modelProvider") or "azure-foundry").strip(),
            "variable": variable,
            "model": model,
        },
    )
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

    foundry_agent_id = _resolve_foundry_chat_agent_id(settings) or None
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

            # Read canvas state directly from Architecture/canvas.state.json on disk
            canvas_state_raw = read_json_file(entry["statePath"], {})
            if not isinstance(canvas_state_raw, dict):
                canvas_state_raw = {}
            raw_items = canvas_state_raw.get("canvasItems") if isinstance(canvas_state_raw.get("canvasItems"), list) else []
            raw_conns = canvas_state_raw.get("canvasConnections") if isinstance(canvas_state_raw.get("canvasConnections"), list) else []
            # Build id→name map for connection resolution
            id_to_name = {str(i.get("id") or ""): str(i.get("name") or "") for i in raw_items if isinstance(i, dict)}
            canvas_items_slim = [
                {"name": str(i.get("name") or ""), "resourceType": str(i.get("resourceType") or ""), "category": str(i.get("category") or "")}
                for i in raw_items if isinstance(i, dict)
            ]
            canvas_connections_slim = [
                {
                    "from": id_to_name.get(str(c.get("fromId") or ""), str(c.get("fromId") or "")),
                    "to":   id_to_name.get(str(c.get("toId") or ""), str(c.get("toId") or "")),
                    "direction": str(c.get("direction") or "one-way"),
                }
                for c in raw_conns if isinstance(c, dict)
            ]
            canvas_context = {"items": canvas_items_slim, "connections": canvas_connections_slim} if canvas_items_slim else None

            project_context = {
                "id": entry["id"],
                "name": str(metadata.get("name") or entry["name"]),
                "cloud": str(metadata.get("cloud") or entry["cloud"]),
                "applicationType": str(metadata.get("applicationType") or project_settings.get("projectApplicationType") or ""),
                "applicationDescription": str(metadata.get("applicationDescription") or ""),
                "projectDescription": project_description,
                "canvasContext": canvas_context,
            }

            resolved_thread_id = resolve_project_foundry_thread_id(entry, settings)
            if resolved_thread_id:
                foundry_thread_id = resolved_thread_id

    if not foundry_agent_id:
        raise HTTPException(status_code=400, detail="Azure AI Foundry chat agent is not configured.")
    if not foundry_thread_id:
        raise HTTPException(status_code=400, detail="Azure AI Foundry project thread is not configured.")

    thread_run_lock = _resolve_foundry_thread_run_lock(foundry_thread_id)

    project_name_for_log = ""
    project_id_for_log = ""
    if isinstance(project_context, dict):
        project_name_for_log = str(project_context.get("name") or "").strip()
        project_id_for_log = str(project_context.get("id") or body.projectId or "").strip()

    try:
        _record_orchestration_event(
            settings,
            thread_id=foundry_thread_id,
            workflow="chat",
            status="dispatch",
            project_id=project_id_for_log,
            project_name=project_name_for_log,
            detail="Routing user architecture message to chat agent.",
            child_agent_id=foundry_agent_id,
        )

        with thread_run_lock:
            response = run_cloudarchitect_chat_agent(
                app_settings=settings,
                user_message=message,
                agent_state=body.agentState if isinstance(body.agentState, dict) else None,
                project_context=project_context,
                foundry_thread_id=foundry_thread_id,
                foundry_agent_id=foundry_agent_id,
            )

        _record_orchestration_event(
            settings,
            thread_id=foundry_thread_id,
            workflow="chat",
            status="completed",
            project_id=project_id_for_log,
            project_name=project_name_for_log,
            detail="Architecture chat response completed.",
            child_agent_id=foundry_agent_id,
        )
        return response
    except AzureMcpChatConfigurationError as exc:
        _record_orchestration_event(
            settings,
            thread_id=foundry_thread_id,
            workflow="chat",
            status="failed",
            project_id=project_id_for_log,
            project_name=project_name_for_log,
            detail=str(exc),
            child_agent_id=foundry_agent_id,
        )
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except AzureMcpChatRequestError as exc:
        _record_orchestration_event(
            settings,
            thread_id=foundry_thread_id,
            workflow="chat",
            status="failed",
            project_id=project_id_for_log,
            project_name=project_name_for_log,
            detail=str(exc),
            child_agent_id=foundry_agent_id,
        )
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        _record_orchestration_event(
            settings,
            thread_id=foundry_thread_id,
            workflow="chat",
            status="failed",
            project_id=project_id_for_log,
            project_name=project_name_for_log,
            detail=str(exc),
            child_agent_id=foundry_agent_id,
        )
        raise HTTPException(status_code=500, detail=f"Architecture chat failed: {exc}") from exc


@app.get("/api/chat/architecture/status")
def architecture_chat_status(projectId: str | None = None):
    settings = load_app_settings()
    bootstrap_result = bootstrap_default_foundry_resources(settings)
    if bootstrap_result.get("settingsUpdated"):
        write_app_settings_file(settings)

    foundry_agent_id = _resolve_foundry_chat_agent_id(settings) or None
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


@app.get("/api/validation/architecture/status")
def architecture_validation_status(projectId: str | None = None):
    settings = load_app_settings()
    bootstrap_result = bootstrap_default_foundry_resources(settings)
    if bootstrap_result.get("settingsUpdated"):
        write_app_settings_file(settings)

    foundry_agent_id = _resolve_foundry_validation_agent_id(settings) or None
    foundry_thread_id = str(settings.get("foundryDefaultThreadId") or "").strip() or None

    if projectId:
        entry = find_project_entry(projectId)
        if entry:
            resolved_thread_id = resolve_project_foundry_thread_id(entry, settings)
            if resolved_thread_id:
                foundry_thread_id = resolved_thread_id

    return get_architecture_validation_status(
        settings,
        foundry_thread_id=foundry_thread_id,
        foundry_agent_id=foundry_agent_id,
    )


@app.post("/api/project/{project_id}/architecture/validation/run")
def run_project_architecture_validation(project_id: str, body: ArchitectureValidatePayload | None = None):
    entry = find_project_entry(project_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Project not found")

    metadata = read_json_file(entry["metadataPath"], {})
    if not isinstance(metadata, dict):
        metadata = {}
    project_name = str(metadata.get("name") or entry["name"] or "").strip() or entry["id"]
    project_description = str(body.projectDescription or "").strip() if body else ""
    if not project_description:
        project_description = str(metadata.get("applicationDescription") or "").strip()

    settings = load_app_settings()
    bootstrap_result = bootstrap_default_foundry_resources(settings)
    if bootstrap_result.get("settingsUpdated"):
        write_app_settings_file(settings)

    foundry_agent_id = _resolve_foundry_validation_agent_id(settings) or None
    foundry_thread_id = resolve_project_foundry_thread_id(entry, settings)
    if not foundry_thread_id:
        raise HTTPException(status_code=400, detail="Azure AI Foundry project thread is not configured for validation.")

    thread_run_lock = _resolve_foundry_thread_run_lock(foundry_thread_id)

    payload_canvas_state = body.canvasState if body and isinstance(body.canvasState, dict) else {}
    canvas_state = payload_canvas_state if payload_canvas_state else read_json_file(entry["statePath"], {})
    if not isinstance(canvas_state, dict):
        canvas_state = {}

    validation_run_id = ""
    if body:
        validation_run_id = str(body.validationRunId or "").strip()
    if not validation_run_id:
        validation_run_id = f"val-{_timestamp_ms()}-{uuid4().hex[:6]}"

    resource_count, connection_count = _canvas_entity_counts(canvas_state)
    state_hash = compute_canvas_state_hash(canvas_state)

    # Initialize validation log file for real-time streaming
    validation_log_path = _ensure_validation_log_file(
        entry["projectDir"],
        project_id=entry["id"],
        project_name=project_name,
        validation_run_id=validation_run_id,
    )

    # For backward compatibility, keep list but don't use it (logging is now real-time)
    validation_text_events: list[dict[str, Any]] = []

    def _record_validation_text_event(activity: str, details: Any) -> None:
        """Record validation event with real-time file streaming."""
        safe_activity = str(activity or "validation.event").strip() or "validation.event"
        if isinstance(details, (Mapping, list)):
            safe_details: Any = details
        else:
            safe_details = {"value": str(details or "").strip()}
        
        # Write to log file immediately (streaming)
        _append_validation_log_event(
            validation_log_path,
            activity=safe_activity,
            details=safe_details,
        )
        
        # Keep in list for backward compatibility (optional)
        validation_text_events.append(
            {
                "timestamp": _timestamp_utc_text(),
                "activity": safe_activity,
                "details": safe_details,
            }
        )

    _record_validation_text_event(
        "validation.run.requested",
        {
            "projectName": project_name,
            "projectDescriptionLength": len(project_description),
            "validationRunId": validation_run_id,
            "resourceCount": resource_count,
            "connectionCount": connection_count,
            "stateHash": state_hash,
            "foundryAgentId": foundry_agent_id or "",
            "foundryThreadId": foundry_thread_id or "",
        },
    )

    _append_app_activity(
        "validation.run",
        status="info",
        project_id=entry["id"],
        category="validation",
        step="requested",
        source="backend.validation",
        details={
            "projectName": project_name,
            "projectDescriptionLength": len(project_description),
            "validationRunId": validation_run_id,
            "resourceCount": resource_count,
            "connectionCount": connection_count,
            "stateHash": state_hash,
            "foundryAgentId": foundry_agent_id or "",
            "foundryThreadId": foundry_thread_id or "",
        },
    )

    _record_orchestration_event(
        settings,
        thread_id=foundry_thread_id,
        workflow="architecture-validation",
        status="dispatch",
        project_id=entry["id"],
        project_name=project_name,
        detail="Dispatching current canvas to validation agent.",
        child_agent_id=foundry_agent_id,
    )

    _record_validation_text_event(
        "validation.run.dispatch",
        {
            "message": "Dispatching current canvas to validation agent.",
            "foundryThreadId": foundry_thread_id,
            "foundryAgentId": foundry_agent_id or "",
        },
    )

    try:
        with thread_run_lock:
            _record_validation_thread_payload(
                settings,
                thread_id=foundry_thread_id,
                activity_type="validation.input",
                project_id=entry["id"],
                project_name=project_name,
                payload={
                    "validationRunId": validation_run_id,
                    "projectId": entry["id"],
                    "projectName": project_name,
                    "projectDescription": project_description,
                    "stateHash": state_hash,
                    "resourceCount": resource_count,
                    "connectionCount": connection_count,
                    "canvas": {
                        "canvasItems": canvas_state.get("canvasItems") if isinstance(canvas_state.get("canvasItems"), list) else [],
                        "canvasConnections": canvas_state.get("canvasConnections") if isinstance(canvas_state.get("canvasConnections"), list) else [],
                    },
                },
            )

            result = run_architecture_validation_agent(
                app_settings=settings,
                canvas_state=canvas_state,
                project_name=project_name,
                project_id=entry["id"],
                project_description=project_description,
                foundry_agent_id=foundry_agent_id,
                foundry_thread_id=foundry_thread_id,
                validation_run_id=validation_run_id,
            )

            _record_validation_thread_payload(
                settings,
                thread_id=foundry_thread_id,
                activity_type="validation.output",
                project_id=entry["id"],
                project_name=project_name,
                payload={
                    "validationRunId": str(result.get("runId") or validation_run_id),
                    "projectId": entry["id"],
                    "projectName": project_name,
                    "summary": result.get("summary") if isinstance(result.get("summary"), Mapping) else {},
                    "sources": result.get("sources") if isinstance(result.get("sources"), Mapping) else {},
                    "findings": result.get("findings") if isinstance(result.get("findings"), list) else [],
                },
            )
    except Exception as exc:
        _record_orchestration_event(
            settings,
            thread_id=foundry_thread_id,
            workflow="architecture-validation",
            status="failed",
            project_id=entry["id"],
            project_name=project_name,
            detail=str(exc),
            child_agent_id=foundry_agent_id,
        )
        _append_app_activity(
            "validation.run",
            status="error",
            project_id=entry["id"],
            category="validation",
            step="failed",
            source="backend.validation",
            details={
                "projectName": project_name,
                "validationRunId": validation_run_id,
                "error": str(exc),
            },
        )

        _record_validation_text_event(
            "validation.run.failed",
            {
                "projectName": project_name,
                "validationRunId": validation_run_id,
                "error": str(exc),
            },
        )

        validation_log_path = _write_project_validation_text_log(
            entry["projectDir"],
            project_id=entry["id"],
            project_name=project_name,
            validation_run_id=validation_run_id,
            events=validation_text_events,
        )

        safe_log_ref = str(validation_log_path)
        try:
            safe_log_ref = str(validation_log_path.relative_to(WORKSPACE_ROOT))
        except Exception:
            safe_log_ref = str(validation_log_path)

        raise HTTPException(
            status_code=500,
            detail=f"Architecture validation failed: {exc}. Validation log: {safe_log_ref}",
        ) from exc

    _record_orchestration_event(
        settings,
        thread_id=foundry_thread_id,
        workflow="architecture-validation",
        status="completed",
        project_id=entry["id"],
        project_name=project_name,
        detail="Architecture validation completed.",
        child_agent_id=foundry_agent_id,
    )

    summary_payload = result.get("summary") if isinstance(result.get("summary"), Mapping) else {}
    evaluation_payload = result.get("evaluation") if isinstance(result.get("evaluation"), Mapping) else {}
    evaluation_steps = evaluation_payload.get("steps") if isinstance(evaluation_payload.get("steps"), list) else []

    for raw_step in evaluation_steps:
        if not isinstance(raw_step, Mapping):
            continue

        raw_step_name = str(raw_step.get("name") or "step").strip().lower()
        safe_step_name = re.sub(r"[^a-z0-9_-]+", "-", raw_step_name).strip("-") or "step"
        step_details: dict[str, Any] = {
            "projectName": project_name,
            "validationRunId": str(result.get("runId") or validation_run_id),
            "step": safe_step_name,
            "state": str(raw_step.get("state") or "").strip(),
            "findingCount": int(raw_step.get("findingCount") or 0),
            "durationMs": int(raw_step.get("durationMs") or 0),
            "explanation": _truncate_log_text(raw_step.get("explanation") or ""),
        }

        if "usedMcpContext" in raw_step:
            step_details["usedMcpContext"] = bool(raw_step.get("usedMcpContext"))

        _record_validation_text_event(
            f"validation.step.{safe_step_name}.completed",
            step_details,
        )

        step_tools = raw_step.get("tools") if isinstance(raw_step.get("tools"), list) else []
        for tool in step_tools:
            if not isinstance(tool, Mapping):
                continue

            tool_label = str(tool.get("label") or tool.get("selectedTool") or "tool").strip()
            _record_validation_text_event(
                f"validation.step.{safe_step_name}.tool.completed",
                {
                    "projectName": project_name,
                    "validationRunId": str(result.get("runId") or validation_run_id),
                    "step": safe_step_name,
                    "label": tool_label,
                    "selectedTool": str(tool.get("selectedTool") or "").strip(),
                    "status": str(tool.get("status") or "").strip(),
                    "findingCount": int(tool.get("findingCount") or 0),
                    "attemptCount": int(tool.get("attemptCount") or 0),
                    "durationMs": int(tool.get("durationMs") or 0),
                    "error": _truncate_log_text(tool.get("error") or "", max_chars=240),
                },
            )

            attempts = tool.get("attempts") if isinstance(tool.get("attempts"), list) else []
            for attempt in attempts:
                if not isinstance(attempt, Mapping):
                    continue

                _record_validation_text_event(
                    f"validation.step.{safe_step_name}.tool.attempt",
                    {
                        "projectName": project_name,
                        "validationRunId": str(result.get("runId") or validation_run_id),
                        "step": safe_step_name,
                        "label": tool_label,
                        "tool": str(attempt.get("tool") or "").strip(),
                        "variantIndex": int(attempt.get("variantIndex") or 0),
                        "status": str(attempt.get("status") or "").strip(),
                        "durationMs": int(attempt.get("durationMs") or 0),
                        "argKeys": attempt.get("argKeys") if isinstance(attempt.get("argKeys"), list) else [],
                        "payloadType": str(attempt.get("payloadType") or "").strip(),
                        "error": _truncate_log_text(attempt.get("error") or "", max_chars=220),
                    },
                )

        if isinstance(raw_step.get("details"), Mapping):
            _record_validation_text_event(
                f"validation.step.{safe_step_name}.details",
                {
                    "projectName": project_name,
                    "validationRunId": str(result.get("runId") or validation_run_id),
                    "step": safe_step_name,
                    "details": raw_step.get("details"),
                },
            )

    aggregation_payload = result.get("aggregation") if isinstance(result.get("aggregation"), Mapping) else {}
    if aggregation_payload:
        _record_validation_text_event(
            "validation.aggregation.completed",
            {
                "projectName": project_name,
                "validationRunId": str(result.get("runId") or validation_run_id),
                "counts": aggregation_payload,
            },
        )

    timing_payload = result.get("timing") if isinstance(result.get("timing"), Mapping) else {}
    if timing_payload:
        _record_validation_text_event(
            "validation.timing.completed",
            {
                "projectName": project_name,
                "validationRunId": str(result.get("runId") or validation_run_id),
                "durationsMs": timing_payload,
            },
        )

    _append_app_activity(
        "validation.run",
        status="info",
        project_id=entry["id"],
        category="validation",
        step="completed",
        source="backend.validation",
        details={
            "projectName": project_name,
            "validationRunId": str(result.get("runId") or validation_run_id),
            "failureCount": int(summary_payload.get("failure") or 0),
            "warningCount": int(summary_payload.get("warning") or 0),
            "infoCount": int(summary_payload.get("info") or 0),
            "total": int(summary_payload.get("total") or 0),
        },
    )

    sources_payload = result.get("sources") if isinstance(result.get("sources"), Mapping) else {}
    deterministic_source = sources_payload.get("deterministic") if isinstance(sources_payload.get("deterministic"), Mapping) else {}
    azure_mcp_source = sources_payload.get("azureMcp") if isinstance(sources_payload.get("azureMcp"), Mapping) else {}
    reasoning_source = sources_payload.get("reasoningModel") if isinstance(sources_payload.get("reasoningModel"), Mapping) else {}

    _record_validation_text_event(
        "validation.run.completed",
        {
            "projectName": project_name,
            "validationRunId": str(result.get("runId") or validation_run_id),
            "summary": {
                "failure": int(summary_payload.get("failure") or 0),
                "warning": int(summary_payload.get("warning") or 0),
                "info": int(summary_payload.get("info") or 0),
                "total": int(summary_payload.get("total") or 0),
            },
            "channels": {
                "deterministic": {
                    "state": str(deterministic_source.get("connectionState") or ""),
                    "findingCount": int(deterministic_source.get("findingCount") or 0),
                },
                "azureMcp": {
                    "state": str(azure_mcp_source.get("connectionState") or ""),
                    "findingCount": int(azure_mcp_source.get("findingCount") or 0),
                    "explanation": str(azure_mcp_source.get("explanation") or ""),
                },
                "reasoningModel": {
                    "state": str(reasoning_source.get("connectionState") or ""),
                    "findingCount": int(reasoning_source.get("findingCount") or 0),
                    "explanation": str(reasoning_source.get("explanation") or ""),
                },
            },
        },
    )

    validation_log_path = _write_project_validation_text_log(
        entry["projectDir"],
        project_id=entry["id"],
        project_name=project_name,
        validation_run_id=str(result.get("runId") or validation_run_id),
        events=validation_text_events,
    )

    validation_log_ref = str(validation_log_path)
    try:
        validation_log_ref = str(validation_log_path.relative_to(WORKSPACE_ROOT))
    except Exception:
        validation_log_ref = str(validation_log_path)

    return {
        **result,
        "projectId": entry["id"],
        "projectName": project_name,
        "threadId": foundry_thread_id,
        "agentId": foundry_agent_id,
        "validationLogPath": validation_log_ref,
    }


@app.post("/api/project/{project_id}/architecture/validation/fix-audit")
def audit_project_architecture_validation_fix(project_id: str, body: ArchitectureValidationFixAuditPayload):
    entry = find_project_entry(project_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Project not found")

    metadata = read_json_file(entry["metadataPath"], {})
    if not isinstance(metadata, dict):
        metadata = {}
    project_name = str(metadata.get("name") or entry["name"] or "").strip() or entry["id"]

    settings = load_app_settings()
    bootstrap_result = bootstrap_default_foundry_resources(settings)
    if bootstrap_result.get("settingsUpdated"):
        write_app_settings_file(settings)

    foundry_thread_id = resolve_project_foundry_thread_id(entry, settings)
    if not foundry_thread_id:
        raise HTTPException(status_code=400, detail="Azure AI Foundry project thread is not configured for fix audit.")

    safe_status = str(body.status or "").strip().lower()
    if safe_status not in {"attempted", "applied", "failed"}:
        safe_status = "attempted"

    attempted_operations = body.attemptedOperations if isinstance(body.attemptedOperations, list) else []

    audit_payload = {
        "validationRunId": str(body.validationRunId or "").strip(),
        "findingId": str(body.findingId or "").strip(),
        "status": safe_status,
        "suggestionTitle": str(body.suggestionTitle or "").strip(),
        "severity": str(body.severity or "").strip(),
        "attemptedOperations": attempted_operations,
        "beforeStateHash": str(body.beforeStateHash or "").strip(),
        "afterStateHash": str(body.afterStateHash or "").strip(),
        "resultSummary": str(body.resultSummary or "").strip(),
        "timestampUtc": datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
    }

    _record_validation_thread_payload(
        settings,
        thread_id=foundry_thread_id,
        activity_type=f"validation.fix.{safe_status}",
        project_id=entry["id"],
        project_name=project_name,
        payload=audit_payload,
    )

    _append_app_activity(
        "validation.fix",
        status="warning" if safe_status == "failed" else "info",
        project_id=entry["id"],
        category="validation",
        step=safe_status,
        source="backend.validation",
        details={
            "projectName": project_name,
            "validationRunId": audit_payload["validationRunId"],
            "findingId": audit_payload["findingId"],
            "severity": audit_payload["severity"],
            "operationCount": len(attempted_operations),
            "threadId": foundry_thread_id,
        },
    )

    return {
        "ok": True,
        "projectId": entry["id"],
        "threadId": foundry_thread_id,
        "status": safe_status,
    }


@app.post("/api/settings/app/verify")
def verify_app_settings(body: VerifySettingsPayload):
    settings = body.settings or {}

    provider = str(settings.get("modelProvider") or "ollama-local").strip().lower()
    try:
        _append_app_activity(
            "settings.app.verify",
            status="info",
            category="settings",
            step="requested",
            source="backend.api",
            details={"provider": provider},
        )

        if provider == "ollama-local":
            message, models = verify_ollama_settings(settings)
        else:
            message, models = verify_foundry_settings(settings)

        _append_app_activity(
            "settings.app.verify",
            status="info",
            category="settings",
            step="completed",
            source="backend.api",
            details={
                "provider": provider,
                "message": message,
                "modelCount": len(models),
                "models": models,
            },
        )
        return {"ok": True, "provider": provider, "message": message, "models": models}
    except HTTPException as exc:
        _append_app_activity(
            "settings.app.verify",
            status="warning",
            category="settings",
            step="failed",
            source="backend.api",
            details={
                "provider": provider,
                "error": str(exc.detail or exc),
            },
        )
        raise
    except Exception as exc:
        _append_app_activity(
            "settings.app.verify",
            status="error",
            category="settings",
            step="failed",
            source="backend.api",
            details={
                "provider": provider,
                "error": str(exc),
            },
        )
        raise


@app.post("/api/settings/project")
def save_project_settings(body: ProjectSettingsPayload):
    try:
        _append_app_activity(
            "settings.project.save",
            status="info",
            project_id=body.project.id,
            category="settings",
            step="requested",
            source="backend.api",
            details={
                "projectName": body.project.name,
                "cloud": body.project.cloud,
            },
        )

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

        _append_app_activity(
            "settings.project.save",
            status="info",
            project_id=body.project.id,
            category="settings",
            step="completed",
            source="backend.api",
            details={
                "projectName": body.project.name,
                "cloud": body.project.cloud,
                "path": str(target.relative_to(WORKSPACE_ROOT)),
            },
        )
        return {"ok": True, "path": str(target.relative_to(WORKSPACE_ROOT))}
    except HTTPException as exc:
        _append_app_activity(
            "settings.project.save",
            status="warning",
            project_id=body.project.id,
            category="settings",
            step="failed",
            source="backend.api",
            details={
                "projectName": body.project.name,
                "cloud": body.project.cloud,
                "error": str(exc.detail or exc),
            },
        )
        raise
    except Exception as exc:
        _append_app_activity(
            "settings.project.save",
            status="error",
            project_id=body.project.id,
            category="settings",
            step="failed",
            source="backend.api",
            details={
                "projectName": body.project.name,
                "cloud": body.project.cloud,
                "error": str(exc),
            },
        )
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
    safe_project_id = str(body.project.id or "").strip()
    safe_project_name = str(body.project.name or "").strip()
    safe_project_cloud = str(body.project.cloud or "").strip()
    save_trigger = _normalize_save_trigger(body.saveTrigger)

    try:
        existing_entry = find_project_entry(body.project.id)
        is_create_request = bool(body.create)
        if not existing_entry and not is_create_request:
            raise HTTPException(
                status_code=409,
                detail="Project does not exist. Create a project from the landing page first.",
            )

        _append_app_activity(
            "canvas.save",
            status="info",
            project_id=safe_project_id,
            category="canvas",
            step="requested",
            source="backend.api",
            details={
                "projectName": safe_project_name,
                "cloud": safe_project_cloud,
                "create": is_create_request,
                "saveTrigger": save_trigger,
            },
        )

        project_dir = resolve_project_dir_for_write(body.project.id, body.project.name)
        architecture_dir = project_dir / "Architecture"
        metadata_path = architecture_dir / "project.metadata.json"
        canvas_state_path = architecture_dir / "canvas.state.json"

        ensure_project_structure(project_dir)
        architecture_dir.mkdir(parents=True, exist_ok=True)

        existing_canvas_state = read_json_file(canvas_state_path, {})
        if not isinstance(existing_canvas_state, dict):
            existing_canvas_state = {}
        current_state_hash = compute_canvas_state_hash(existing_canvas_state)

        incoming_base_state_hash = str(body.baseStateHash or "").strip()
        if not is_create_request:
            if not incoming_base_state_hash:
                raise HTTPException(
                    status_code=409,
                    detail="Save blocked: stale editor session detected. Refresh the canvas page and retry.",
                )
            if incoming_base_state_hash != current_state_hash:
                raise HTTPException(
                    status_code=409,
                    detail="Save blocked: newer canvas data already exists. Refresh to load the latest canvas state.",
                )

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
        bootstrap_result = bootstrap_default_foundry_resources(app_settings)
        if bootstrap_result.get("settingsUpdated"):
            write_app_settings_file(app_settings)

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

        iac_parameter_format = str(body.project.iacParameterFormat or "").strip()
        if iac_parameter_format:
            project_settings = merge_project_settings(
                project_settings,
                {"iacParameterFormat": normalize_parameter_format(iac_parameter_format)},
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
            _record_orchestration_event(
                app_settings,
                thread_id=foundry_thread_id,
                workflow="project-lifecycle",
                status="created",
                project_id=body.project.id,
                project_name=body.project.name,
                detail="Project created and project thread initialized.",
            )
            post_project_created_message(
                app_settings,
                thread_id=foundry_thread_id,
                project_name=body.project.name,
                project_id=body.project.id,
                created_at=datetime.utcnow(),
            )

        if is_create_request:
            _append_app_activity(
                "project.lifecycle",
                status="info",
                project_id=safe_project_id,
                category="project",
                step="created",
                source="backend.api",
                details={
                    "projectName": safe_project_name,
                    "cloud": safe_project_cloud,
                    "threadCreated": bool(foundry_thread_result.get("created")),
                    "threadId": foundry_thread_id or "",
                },
            )

        metadata_path.write_text(
            json.dumps(metadata_payload, indent=2),
            encoding="utf-8",
        )

        canvas_state_payload = body.canvasState if isinstance(body.canvasState, dict) else {}
        resource_count, connection_count = _canvas_entity_counts(canvas_state_payload)
        canvas_state_path.write_text(
            json.dumps(canvas_state_payload, indent=2),
            encoding="utf-8",
        )
        saved_state_hash = compute_canvas_state_hash(canvas_state_payload)

        _append_app_activity(
            "canvas.save",
            status="info",
            project_id=safe_project_id,
            category="canvas",
            step="completed",
            source="backend.api",
            details={
                "projectName": safe_project_name,
                "cloud": safe_project_cloud,
                "create": is_create_request,
                "saveTrigger": save_trigger,
                "resourceCount": resource_count,
                "connectionCount": connection_count,
                "stateHash": saved_state_hash,
                "foundryBootstrap": bootstrap_result,
                "foundryThread": foundry_thread_result,
            },
        )

        return {
            "ok": True,
            "projectPath": str(project_dir.relative_to(WORKSPACE_ROOT)),
            "foundryBootstrap": bootstrap_result,
            "foundryThread": foundry_thread_result,
            "stateHash": saved_state_hash,
            "files": [
                str(metadata_path.relative_to(WORKSPACE_ROOT)),
                str(canvas_state_path.relative_to(WORKSPACE_ROOT)),
            ],
        }
    except HTTPException as exc:
        _append_app_activity(
            "canvas.save",
            status="warning",
            project_id=safe_project_id,
            category="canvas",
            step="failed",
            source="backend.api",
            details={
                "projectName": safe_project_name,
                "cloud": safe_project_cloud,
                "saveTrigger": save_trigger,
                "error": str(exc.detail or exc),
            },
        )
        raise
    except Exception as exc:
        _append_app_activity(
            "canvas.save",
            status="error",
            project_id=safe_project_id,
            category="canvas",
            step="failed",
            source="backend.api",
            details={
                "projectName": safe_project_name,
                "cloud": safe_project_cloud,
                "saveTrigger": save_trigger,
                "error": str(exc),
            },
        )
        raise HTTPException(status_code=500, detail=f"Failed to save project snapshot: {exc}") from exc


@app.post("/api/project/export-diagram")
def export_project_diagram(body: DiagramExportPayload):
    safe_project_id = str(body.projectId or "").strip()
    safe_project_name = str(body.projectName or "").strip()
    fmt = str(body.format or "png").strip().lower()

    _append_app_activity(
        "canvas.export",
        status="info",
        project_id=safe_project_id,
        category="canvas",
        step="requested",
        source="backend.api",
        details={
            "projectName": safe_project_name,
            "format": fmt,
        },
    )

    try:
        entry = find_project_entry(body.projectId)
        if not entry:
            raise HTTPException(status_code=404, detail="Project not found")

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

        _append_app_activity(
            "canvas.export",
            status="info",
            project_id=safe_project_id,
            category="canvas",
            step="completed",
            source="backend.api",
            details={
                "projectName": safe_project_name,
                "format": fmt,
                "bytes": len(image_bytes),
                "path": str(target.relative_to(WORKSPACE_ROOT)),
            },
        )

        return {
            "ok": True,
            "fileName": file_name,
            "path": str(target.relative_to(WORKSPACE_ROOT)),
        }
    except HTTPException as exc:
        _append_app_activity(
            "canvas.export",
            status="warning",
            project_id=safe_project_id,
            category="canvas",
            step="failed",
            source="backend.api",
            details={
                "projectName": safe_project_name,
                "format": fmt,
                "error": str(exc.detail or exc),
            },
        )
        raise
    except Exception as exc:
        _append_app_activity(
            "canvas.export",
            status="error",
            project_id=safe_project_id,
            category="canvas",
            step="failed",
            source="backend.api",
            details={
                "projectName": safe_project_name,
                "format": fmt,
                "error": str(exc),
            },
        )
        raise


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
            "iacLanguage": str(project_settings.get("iacLanguage") or "bicep").strip().lower(),
            "iacParameterFormat": normalize_parameter_format(project_settings.get("iacParameterFormat") or "bicepparam"),
        },
        "canvasState": canvas_state,
        "stateHash": compute_canvas_state_hash(canvas_state),
    }


def _prepare_iac_generation_context(project_id: str, requested_parameter_format: str | None = None):
    entry = find_project_entry(project_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Project not found")

    metadata = read_json_file(entry["metadataPath"], {})
    if not isinstance(metadata, dict):
        metadata = {}

    project_cloud = str(metadata.get("cloud") or entry["cloud"] or "").strip()
    if project_cloud.lower() != "azure":
        raise HTTPException(status_code=400, detail="IaC generation is currently supported only for Azure projects.")

    project_settings = load_project_settings_file(entry["projectDir"])
    iac_language = str(project_settings.get("iacLanguage") or "bicep").strip().lower()
    if iac_language != "bicep":
        raise HTTPException(status_code=400, detail="Only Bicep generation is currently supported for Generate Code.")

    requested = str(requested_parameter_format or "").strip()
    if requested:
        parameter_format = normalize_parameter_format(requested)
    else:
        parameter_format = normalize_parameter_format(project_settings.get("iacParameterFormat") or "bicepparam")

    current_format = normalize_parameter_format(project_settings.get("iacParameterFormat") or "bicepparam")
    if current_format != parameter_format:
        project_settings = merge_project_settings(
            project_settings,
            {"iacParameterFormat": parameter_format},
        )
        persist_project_settings(
            entry["projectDir"],
            entry["id"],
            entry["name"],
            entry["cloud"],
            project_settings,
        )

    return entry, metadata, project_settings, iac_language, parameter_format


def _resolve_iac_language_folder_name(iac_language: str) -> str:
    normalized = str(iac_language or "bicep").strip().lower()
    if normalized == "bicep":
        return "Bicep"
    if normalized == "terraform":
        return "Terraform"
    if normalized in {"opentofu", "tofu"}:
        return "OpenTofu"
    if not normalized:
        return "Bicep"
    return normalized[:1].upper() + normalized[1:]


def _reset_iac_language_output_dir(project_dir: Path, iac_language: str) -> Path:
    iac_root = project_dir / "IaC"
    iac_root.mkdir(parents=True, exist_ok=True)

    language_folder = _resolve_iac_language_folder_name(iac_language)
    target_dir = iac_root / language_folder

    if target_dir.exists():
        shutil.rmtree(target_dir)

    target_dir.mkdir(parents=True, exist_ok=True)
    return target_dir


def _generate_project_iac_payload(
    project_id: str,
    requested_parameter_format: str | None = None,
    progress_callback=None,
) -> dict:
    entry, metadata, _project_settings, iac_language, parameter_format = _prepare_iac_generation_context(
        project_id,
        requested_parameter_format=requested_parameter_format,
    )

    if callable(progress_callback):
        try:
            progress_callback(
                {
                    "stage": "cleanup_output",
                    "status": "running",
                    "message": "Removing existing IaC output folder",
                    "progress": 1,
                }
            )
        except Exception:
            pass

    try:
        iac_dir = _reset_iac_language_output_dir(entry["projectDir"], iac_language)
    except Exception as exc:
        if callable(progress_callback):
            try:
                progress_callback(
                    {
                        "stage": "cleanup_output",
                        "status": "error",
                        "message": f"Failed to reset IaC output folder: {exc}",
                        "progress": 1,
                    }
                )
            except Exception:
                pass
        _append_app_activity(
            "codegen.cleanup",
            status="error",
            project_id=entry["id"],
            category="codegen",
            step="failed",
            source="backend.codegen",
            details={
                "projectName": str(metadata.get("name") or entry["name"]),
                "iacLanguage": iac_language,
                "error": str(exc),
            },
        )
        raise HTTPException(status_code=500, detail=f"Failed to reset IaC output folder: {exc}") from exc

    _append_app_activity(
        "codegen.cleanup",
        status="info",
        project_id=entry["id"],
        category="codegen",
        step="completed",
        source="backend.codegen",
        details={
            "projectName": str(metadata.get("name") or entry["name"]),
            "iacLanguage": iac_language,
            "outputDir": str(iac_dir.relative_to(WORKSPACE_ROOT)),
        },
    )

    if callable(progress_callback):
        try:
            progress_callback(
                {
                    "stage": "cleanup_output",
                    "status": "completed",
                    "message": "Cleared existing IaC output folder",
                    "progress": 4,
                }
            )
        except Exception:
            pass

    canvas_state = read_json_file(entry["statePath"], {})
    if not isinstance(canvas_state, dict):
        canvas_state = {}
    resource_count, connection_count = _canvas_entity_counts(canvas_state)

    app_settings = load_app_settings()
    bootstrap_result = bootstrap_default_foundry_resources(app_settings)
    if bootstrap_result.get("settingsUpdated"):
        write_app_settings_file(app_settings)

    foundry_agent_id = _resolve_foundry_iac_agent_id(app_settings) or None
    foundry_thread_id = resolve_project_foundry_thread_id(entry, app_settings)

    _append_app_activity(
        "codegen.request",
        status="info",
        project_id=entry["id"],
        category="codegen",
        step="requested",
        source="backend.codegen",
        details={
            "projectName": str(metadata.get("name") or entry["name"]),
            "parameterFormat": parameter_format,
            "resourceCount": resource_count,
            "connectionCount": connection_count,
            "foundryAgentId": foundry_agent_id or "",
            "foundryThreadId": foundry_thread_id or "",
        },
    )

    if is_azure_foundry_provider(app_settings):
        if not foundry_thread_id:
            raise HTTPException(status_code=400, detail="Azure AI Foundry project thread is not configured for IaC generation.")
        if not foundry_agent_id:
            raise HTTPException(status_code=400, detail="Azure AI Foundry IaC agent is not configured.")

    _record_orchestration_event(
        app_settings,
        thread_id=foundry_thread_id,
        workflow="iac-generation",
        status="dispatch",
        project_id=entry["id"],
        project_name=str(metadata.get("name") or entry["name"]),
        detail="Dispatching canvas to IaC generation agent.",
        child_agent_id=foundry_agent_id,
    )

    try:
        result = generate_bicep_iac_from_canvas(
            app_settings=app_settings,
            canvas_state=canvas_state,
            output_dir=iac_dir,
            project_name=str(metadata.get("name") or entry["name"]),
            project_id=entry["id"],
            parameter_format=parameter_format,
            foundry_agent_id=foundry_agent_id,
            foundry_thread_id=foundry_thread_id,
            progress_callback=progress_callback,
        )
        _record_orchestration_event(
            app_settings,
            thread_id=foundry_thread_id,
            workflow="iac-generation",
            status="completed",
            project_id=entry["id"],
            project_name=str(metadata.get("name") or entry["name"]),
            detail="IaC generation completed successfully.",
            child_agent_id=foundry_agent_id,
        )
        _append_app_activity(
            "codegen.result",
            status="info",
            project_id=entry["id"],
            category="codegen",
            step="completed",
            source="backend.codegen",
            details={
                "projectName": str(metadata.get("name") or entry["name"]),
                "parameterFormat": parameter_format,
                "fileCount": len(result.get("files") if isinstance(result.get("files"), list) else []),
                "warningCount": len(result.get("warnings") if isinstance(result.get("warnings"), list) else []),
            },
        )
    except ValueError as exc:
        _record_orchestration_event(
            app_settings,
            thread_id=foundry_thread_id,
            workflow="iac-generation",
            status="failed",
            project_id=entry["id"],
            project_name=str(metadata.get("name") or entry["name"]),
            detail=str(exc),
            child_agent_id=foundry_agent_id,
        )
        _append_app_activity(
            "codegen.result",
            status="warning",
            project_id=entry["id"],
            category="codegen",
            step="failed",
            source="backend.codegen",
            details={
                "projectName": str(metadata.get("name") or entry["name"]),
                "parameterFormat": parameter_format,
                "error": str(exc),
            },
        )
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        _record_orchestration_event(
            app_settings,
            thread_id=foundry_thread_id,
            workflow="iac-generation",
            status="failed",
            project_id=entry["id"],
            project_name=str(metadata.get("name") or entry["name"]),
            detail=str(exc),
            child_agent_id=foundry_agent_id,
        )
        _append_app_activity(
            "codegen.result",
            status="error",
            project_id=entry["id"],
            category="codegen",
            step="failed",
            source="backend.codegen",
            details={
                "projectName": str(metadata.get("name") or entry["name"]),
                "parameterFormat": parameter_format,
                "error": str(exc),
            },
        )
        raise HTTPException(status_code=500, detail=f"IaC generation failed: {exc}") from exc

    return {
        "ok": True,
        "projectId": entry["id"],
        "root": str((entry["projectDir"] / "IaC").relative_to(WORKSPACE_ROOT)),
        "iacLanguage": iac_language,
        "parameterFormat": parameter_format,
        "generation": result,
    }


def _run_iac_generation_task(task_id: str, project_id: str, parameter_format: str) -> None:
    with IAC_TASK_LOCK:
        _mark_iac_task_running(task_id)

    def on_progress(event: dict) -> None:
        with IAC_TASK_LOCK:
            _record_iac_progress_event(task_id, event)

    try:
        payload = _generate_project_iac_payload(
            project_id,
            requested_parameter_format=parameter_format,
            progress_callback=on_progress,
        )
    except HTTPException as exc:
        detail = exc.detail
        if isinstance(detail, (dict, list)):
            detail_text = json.dumps(detail, ensure_ascii=False)
        else:
            detail_text = str(detail or str(exc)).strip()
        with IAC_TASK_LOCK:
            _fail_iac_task(task_id, detail_text)
        return
    except Exception as exc:
        with IAC_TASK_LOCK:
            _fail_iac_task(task_id, f"IaC generation failed: {exc}")
        return

    with IAC_TASK_LOCK:
        _complete_iac_task(task_id, result=payload)


@app.post("/api/project/{project_id}/iac/task/start")
def start_project_iac_task(project_id: str, body: IacGeneratePayload | None = None):
    requested_format_raw = ""
    if body and body.parameterFormat is not None:
        requested_format_raw = str(body.parameterFormat or "").strip()

    entry, _metadata, _project_settings, _iac_language, parameter_format = _prepare_iac_generation_context(
        project_id,
        requested_parameter_format=requested_format_raw,
    )

    with IAC_TASK_LOCK:
        _cleanup_iac_tasks()
        existing_task = _get_latest_project_iac_task(entry["id"])
        if existing_task and str(existing_task.get("status") or "").strip().lower() in {"queued", "running"}:
            return {
                "ok": True,
                "existing": True,
                "task": _serialize_iac_task(existing_task),
            }

        created_task = _create_iac_task(entry["id"], parameter_format)

    worker = Thread(
        target=_run_iac_generation_task,
        args=(created_task["taskId"], entry["id"], parameter_format),
        daemon=True,
    )
    worker.start()

    return {
        "ok": True,
        "existing": False,
        "task": _serialize_iac_task(created_task),
    }


@app.get("/api/project/{project_id}/iac/task/latest")
def get_latest_project_iac_task(project_id: str):
    entry = find_project_entry(project_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Project not found")

    with IAC_TASK_LOCK:
        _cleanup_iac_tasks()
        task = _get_latest_project_iac_task(entry["id"])

    return {
        "ok": True,
        "task": _serialize_iac_task(task) if isinstance(task, dict) else None,
    }


@app.get("/api/project/{project_id}/iac/task/{task_id}")
def get_project_iac_task(project_id: str, task_id: str):
    with IAC_TASK_LOCK:
        _cleanup_iac_tasks()
        task = IAC_TASKS.get(str(task_id or "").strip())
        if not isinstance(task, dict) or str(task.get("projectId") or "").strip() != str(project_id or "").strip():
            raise HTTPException(status_code=404, detail="IaC generation task not found")

        payload = _serialize_iac_task(task)

    return {
        "ok": True,
        "task": payload,
    }


@app.post("/api/project/{project_id}/iac/generate")
def generate_project_iac(project_id: str, body: IacGeneratePayload | None = None):
    requested_format_raw = ""
    if body and body.parameterFormat is not None:
        requested_format_raw = str(body.parameterFormat or "").strip()

    return _generate_project_iac_payload(
        project_id,
        requested_parameter_format=requested_format_raw,
        progress_callback=None,
    )


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


@app.get("/api/project/{project_id}/iac/download")
def download_project_iac_archive(project_id: str):
    entry = find_project_entry(project_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Project not found")

    iac_dir = entry["projectDir"] / "IaC"
    files = list_iac_files(iac_dir)
    if not files:
        raise HTTPException(status_code=404, detail="No IaC files available for download")

    archive_buffer = io.BytesIO()
    with zipfile.ZipFile(archive_buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        for file_entry in files:
            rel_path = str(file_entry.get("path") or "").strip()
            if not rel_path:
                continue

            source_path = iac_dir / Path(rel_path)
            if not source_path.exists() or not source_path.is_file():
                continue

            archive.write(source_path, arcname=rel_path)

    if archive_buffer.getbuffer().nbytes == 0:
        raise HTTPException(status_code=404, detail="No IaC files available for download")

    archive_buffer.seek(0)
    file_stem = sanitize_segment(str(entry.get("name") or entry["id"]), "project")
    archive_name = f"{file_stem}-iac.zip"

    return StreamingResponse(
        archive_buffer,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{archive_name}"',
        },
    )


@app.delete("/api/project/{project_id}")
def delete_project_snapshot(project_id: str):
    entry = find_project_entry(project_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Project not found")

    _append_app_activity(
        "project.lifecycle",
        status="info",
        project_id=entry["id"],
        category="project",
        step="delete-requested",
        source="backend.api",
        details={
            "projectName": entry["name"],
            "cloud": entry["cloud"],
        },
    )

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
            app_settings = load_app_settings()
            _record_orchestration_event(
                app_settings,
                thread_id=thread_id,
                workflow="project-lifecycle",
                status="deleted",
                project_id=entry["id"],
                project_name=str(metadata.get("name") or entry["name"]),
                detail="Project deletion requested.",
            )
            post_project_deleted_message(
                app_settings,
                thread_id=thread_id,
                project_name=str(metadata.get("name") or entry["name"]),
                project_id=entry["id"],
                deleted_at=datetime.utcnow(),
            )

        shutil.rmtree(entry["projectDir"])

        _append_app_activity(
            "project.lifecycle",
            status="info",
            project_id=entry["id"],
            category="project",
            step="deleted",
            source="backend.api",
            details={
                "projectName": entry["name"],
                "cloud": entry["cloud"],
            },
        )
        return {"ok": True}
    except Exception as exc:
        _append_app_activity(
            "project.lifecycle",
            status="error",
            project_id=entry["id"],
            category="project",
            step="delete-failed",
            source="backend.api",
            details={
                "projectName": entry["name"],
                "cloud": entry["cloud"],
                "error": str(exc),
            },
        )
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
