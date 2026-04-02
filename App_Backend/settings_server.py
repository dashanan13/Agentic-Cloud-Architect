from pathlib import Path
import asyncio
import base64
import hashlib
import importlib
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
    delete_project_thread,
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
    "modelProvider": "azure-foundry",
    "azureTenantId": "",
    "azureClientId": "",
    "azureClientSecret": "",
    "azureSubscriptionId": "",
    "azureResourceGroup": "",
    "aiFoundryProjectName": "",
    "aiFoundryEndpoint": "",
    "foundryApiVersion": "2024-05-01-preview",
    "foundryModelCoding": "",
    "foundryModelReasoning": "",
    "foundryModelFast": "",
    "foundryChatAgentId": "",
    "foundryIacAgentId": "",
    "foundryValidationAgentId": "",
    "foundryDefaultAgentId": "",
    "foundryDefaultThreadId": "",
    "iacLiveTemplateStrict": True,
}

PROJECT_FOUNDRY_THREAD_FIELDS = {
    "chat": {
        "settingsKey": "projectChatThreadId",
        "metadataKey": "foundryChatThreadId",
        "legacySettingsKey": "projectThreadId",
        "legacyMetadataKey": "foundryThreadId",
    },
    "validation": {
        "settingsKey": "projectValidationThreadId",
        "metadataKey": "foundryValidationThreadId",
        "legacySettingsKey": None,
        "legacyMetadataKey": None,
    },
}

CHAT_THREAD_VALIDATION_MARKERS = (
    "[orchestrator] architecture-validation",
    "[validation-agent]",
    '"workflow": "architecture-validation"',
    "validation.input",
    "validation.output",
    "you are a principal azure cloud architect reviewing an azure architecture for enterprise quality",
)

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
    foundryChatThreadId: str | None = None
    foundryValidationThreadId: str | None = None
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


class ValidationInputVerificationPayload(BaseModel):
    canvasState: Any = None


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
    allowWarnings: bool | None = None


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
        provider = str(load_app_settings().get("modelProvider") or "azure-foundry").strip().lower()
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


def _parse_validation_log_line(line: str) -> dict[str, Any] | None:
    raw_line = str(line or "").strip()
    if not raw_line.startswith("timestamp="):
        return None

    activity_marker = " activity="
    details_marker = " details="

    try:
        activity_index = raw_line.index(activity_marker)
        details_index = raw_line.index(details_marker, activity_index + len(activity_marker))
    except ValueError:
        return None

    timestamp_text = raw_line[len("timestamp="):activity_index].strip()
    activity_text = raw_line[activity_index + len(activity_marker):details_index].strip()
    details_text = raw_line[details_index + len(details_marker):].strip()

    details_payload: Any = details_text
    if details_text.startswith("{") or details_text.startswith("["):
        try:
            details_payload = json.loads(details_text)
        except Exception:
            details_payload = details_text

    return {
        "timestamp": timestamp_text,
        "activity": activity_text,
        "details": details_payload,
    }


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


def _reset_input_verification_log(project_dir: Path) -> Path:
    documentation_dir = project_dir / "Documentation"
    if documentation_dir.exists():
        shutil.rmtree(documentation_dir, ignore_errors=True)
    documentation_dir.mkdir(parents=True, exist_ok=True)
    log_path = documentation_dir / "validation.log"
    log_path.write_text("", encoding="utf-8")
    return log_path


def _append_input_verification_log(log_path: Path, level: str, message: str) -> None:
    safe_level = str(level or "INFO").strip().upper() or "INFO"
    safe_message = str(message or "").strip()
    if not safe_message:
        return
    try:
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(f"[{safe_level}] {safe_message}\n")
            handle.flush()
    except Exception:
        pass


def _ensure_validation_log_path(project_dir: Path) -> Path:
    documentation_dir = project_dir / "Documentation"
    documentation_dir.mkdir(parents=True, exist_ok=True)
    log_path = documentation_dir / "validation.log"
    if not log_path.exists():
        log_path.write_text("", encoding="utf-8")
    return log_path


def _gb_normalize_resource_type(raw_type: str) -> str:
    normalized = str(raw_type or "").strip().lower()
    alias_map = {
        "virtual networks": "Azure Virtual Network",
        "virtual network": "Azure Virtual Network",
        "vnet": "Azure Virtual Network",
        "subnet": "Azure Subnet",
        "subnets": "Azure Subnet",
        "network security group": "Azure Network Security Group",
        "network security groups": "Azure Network Security Group",
        "nsg": "Azure Network Security Group",
        "route table": "Azure Route Table",
        "route tables": "Azure Route Table",
        "public ip": "Azure Public IP Address",
        "public ip address": "Azure Public IP Address",
        "public ip addresses": "Azure Public IP Address",
    }
    if normalized in alias_map:
        return alias_map[normalized]
    if normalized.startswith("azure "):
        return str(raw_type or "").strip() or "Azure Resource"
    if not normalized:
        return "Azure Resource"
    return f"Azure {str(raw_type).strip()}"


def _gb_normalize_resource_types(canvas_items: list[Any]) -> list[dict[str, Any]]:
    normalized_items: list[dict[str, Any]] = []
    for item in canvas_items:
        if not isinstance(item, Mapping):
            continue
        copied = dict(item)
        copied["resourceType"] = _gb_normalize_resource_type(str(item.get("resourceType") or ""))
        normalized_items.append(copied)
    return normalized_items


def _gb_build_nodes(canvas_items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    nodes: dict[str, dict[str, Any]] = {}
    for item in canvas_items:
        item_id = str(item.get("id") or "").strip()
        if not item_id:
            continue
        properties = item.get("properties") if isinstance(item.get("properties"), Mapping) else {}
        nodes[item_id] = {
            "id": item_id,
            "name": str(item.get("name") or item.get("resourceName") or item_id),
            "type": str(item.get("resourceType") or "Azure Resource"),
            "category": str(item.get("category") or "other"),
            "properties": dict(properties),
            "parentId": (str(item.get("parentId") or "").strip() or None),
            "children": [],
        }
    return nodes


def _gb_resolve_hierarchy(nodes: dict[str, dict[str, Any]]) -> list[str]:
    warnings: list[str] = []
    for node in nodes.values():
        parent_id = node.get("parentId")
        if not parent_id:
            continue
        if parent_id not in nodes:
            warnings.append(f"Broken parentId reference: {node.get('id')} -> {parent_id}")
            node["parentId"] = None
            continue
        parent_children = nodes[parent_id].setdefault("children", [])
        if node["id"] not in parent_children:
            parent_children.append(node["id"])
    return warnings


def _gb_extract_ref_edges_from_properties(
    source_id: str,
    value: Any,
    *,
    path_key: str = "",
) -> list[dict[str, str]]:
    edges: list[dict[str, str]] = []
    if isinstance(value, Mapping):
        for key, child in value.items():
            key_text = str(key or "").strip()
            child_path = f"{path_key}.{key_text}" if path_key else key_text
            if key_text.endswith("Ref"):
                ref_value = str(child or "").strip()
                if ref_value:
                    edges.append({"from": source_id, "to": ref_value, "type": "reference"})
            edges.extend(_gb_extract_ref_edges_from_properties(source_id, child, path_key=child_path))
    elif isinstance(value, list):
        for child in value:
            edges.extend(_gb_extract_ref_edges_from_properties(source_id, child, path_key=path_key))
    return edges


def _gb_build_connections(
    canvas_connections: list[Any],
    nodes: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, str]], list[str]]:
    edges: list[dict[str, str]] = []
    warnings: list[str] = []

    for connection in canvas_connections:
        if not isinstance(connection, Mapping):
            continue
        from_id = str(connection.get("fromId") or "").strip()
        to_id = str(connection.get("toId") or "").strip()
        if not from_id or not to_id:
            continue
        if from_id not in nodes or to_id not in nodes:
            warnings.append(f"Broken connection edge: {from_id} -> {to_id}")
            continue
        edges.append({"from": from_id, "to": to_id})

    for node_id, node in nodes.items():
        properties = node.get("properties")
        if not isinstance(properties, Mapping):
            continue
        ref_edges = _gb_extract_ref_edges_from_properties(node_id, properties)
        for edge in ref_edges:
            if edge["to"] not in nodes:
                warnings.append(f"Broken property reference: {edge['from']} -> {edge['to']}")
                continue
            edges.append(edge)

    seen: set[tuple[str, str, str]] = set()
    unique_edges: list[dict[str, str]] = []
    for edge in edges:
        edge_type = str(edge.get("type") or "").strip()
        key = (str(edge.get("from") or "").strip(), str(edge.get("to") or "").strip(), edge_type)
        if not key[0] or not key[1] or key in seen:
            continue
        seen.add(key)
        if edge_type:
            unique_edges.append({"from": key[0], "to": key[1], "type": edge_type})
        else:
            unique_edges.append({"from": key[0], "to": key[1]})

    return unique_edges, warnings


def _gb_detect_relationships_and_anomalies(
    nodes: dict[str, dict[str, Any]],
    edges: list[dict[str, str]],
) -> dict[str, list[str]]:
    incoming: dict[str, int] = {node_id: 0 for node_id in nodes}
    outgoing: dict[str, int] = {node_id: 0 for node_id in nodes}
    broken_edges: list[str] = []

    for edge in edges:
        src = str(edge.get("from") or "").strip()
        dst = str(edge.get("to") or "").strip()
        if src not in nodes or dst not in nodes:
            broken_edges.append(f"{src}->{dst}")
            continue
        outgoing[src] += 1
        incoming[dst] += 1

    orphan_nodes = [
        node_id
        for node_id, node in nodes.items()
        if not node.get("parentId") and incoming.get(node_id, 0) == 0 and outgoing.get(node_id, 0) == 0
    ]

    unused_resources = [
        node_id
        for node_id in nodes
        if incoming.get(node_id, 0) == 0 and outgoing.get(node_id, 0) == 0
    ]

    return {
        "orphanNodes": orphan_nodes,
        "brokenReferences": broken_edges,
        "unusedResources": unused_resources,
    }


def _gb_finalize_graph(
    nodes: dict[str, dict[str, Any]],
    edges: list[dict[str, str]],
) -> dict[str, Any]:
    return {
        "nodes": nodes,
        "edges": edges,
    }


def _run_graph_builder_stage(
    *,
    project_dir: Path,
    canvas_state_input: Any,
) -> dict[str, Any]:
    log_path = _ensure_validation_log_path(project_dir)
    errors: list[str] = []
    warnings: list[str] = []
    step_results: list[dict[str, Any]] = []

    def _start(step_key: str, message: str) -> None:
        _append_input_verification_log(log_path, "INFO", message)
        step_results.append({"step": step_key, "status": "started"})

    def _done(step_key: str, message: str) -> None:
        _append_input_verification_log(log_path, "INFO", message)
        step_results.append({"step": step_key, "status": "completed"})

    def _fail(step_key: str, message: str) -> dict[str, Any]:
        _append_input_verification_log(log_path, "ERROR", message)
        step_results.append({"step": step_key, "status": "failed", "error": message})
        return {
            "ok": False,
            "errors": [message],
            "warnings": warnings,
            "stepResults": step_results,
            "artifactPath": "/Documentation/architecture-graph.json",
            "logFile": str(log_path),
        }

    _append_input_verification_log(log_path, "INFO", "Graph Builder started")

    parsed_json, iv_errors, iv_warnings = _iv_validate_json(canvas_state_input)
    if iv_errors:
        return _fail("normalize-types", f"Input Verification prerequisite failed: {iv_errors[0]}")
    warnings.extend(iv_warnings)

    iv_errors, _ = _iv_validate_structure(parsed_json)
    if iv_errors:
        return _fail("normalize-types", f"Input Verification prerequisite failed: {iv_errors[0]}")

    canvas_items = parsed_json.get("canvasItems") if isinstance(parsed_json.get("canvasItems"), list) else []
    canvas_connections = parsed_json.get("canvasConnections") if isinstance(parsed_json.get("canvasConnections"), list) else []

    iv_errors, _ = _iv_validate_resources(canvas_items)
    if iv_errors:
        return _fail("build-nodes", f"Input Verification prerequisite failed: {iv_errors[0]}")

    iv_errors, _ = _iv_validate_references(canvas_items)
    if iv_errors:
        return _fail("build-connections", f"Input Verification prerequisite failed: {iv_errors[0]}")

    resource_ids = {
        str(item.get("id") or "").strip()
        for item in canvas_items
        if isinstance(item, Mapping) and str(item.get("id") or "").strip()
    }
    iv_errors, _ = _iv_validate_connections(canvas_connections, resource_ids)
    if iv_errors:
        return _fail("build-connections", f"Input Verification prerequisite failed: {iv_errors[0]}")

    _start("normalize-types", "Graph Builder Normalizing Resource Types")
    normalized_items = _gb_normalize_resource_types(canvas_items)
    _done("normalize-types", "Graph Builder resource types normalized")

    _start("build-nodes", "Graph Builder Building Nodes")
    nodes = _gb_build_nodes(normalized_items)
    _append_input_verification_log(log_path, "INFO", f"Nodes created: {len(nodes)}")
    _done("build-nodes", "Graph Builder nodes built")

    _start("resolve-hierarchy", "Graph Builder Resolving Hierarchy")
    hierarchy_warnings = _gb_resolve_hierarchy(nodes)
    warnings.extend(hierarchy_warnings)
    for warn in hierarchy_warnings:
        _append_input_verification_log(log_path, "WARN", warn)
    _done("resolve-hierarchy", "Graph Builder hierarchy resolved")

    _start("build-connections", "Graph Builder Building Connections")
    edges, edge_warnings = _gb_build_connections(canvas_connections, nodes)
    warnings.extend(edge_warnings)
    for warn in edge_warnings:
        _append_input_verification_log(log_path, "WARN", warn)
    _append_input_verification_log(log_path, "INFO", f"Edges created: {len(edges)}")
    _done("build-connections", "Graph Builder connections built")

    _start("detect-relationships", "Graph Builder Detecting Relationships")
    anomalies = _gb_detect_relationships_and_anomalies(nodes, edges)
    for orphan in anomalies.get("orphanNodes", []):
        _append_input_verification_log(log_path, "WARN", f"Orphan node detected: {orphan}")
    _done("detect-relationships", "Graph Builder relationships detected")

    _start("finalize-graph", "Graph Builder Finalizing Graph")
    graph_payload = _gb_finalize_graph(nodes, edges)
    documentation_dir = project_dir / "Documentation"
    documentation_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = documentation_dir / "architecture-graph.json"
    artifact_path.write_text(json.dumps(graph_payload, indent=2, ensure_ascii=False), encoding="utf-8")
    _done("finalize-graph", "Graph Builder graph finalized")

    _append_input_verification_log(log_path, "INFO", "Graph Builder completed")

    return {
        "ok": True,
        "errors": errors,
        "warnings": warnings,
        "stepResults": step_results,
        "artifactPath": "/Documentation/architecture-graph.json",
        "logFile": str(log_path),
        "graph": {
            "nodeCount": len(nodes),
            "edgeCount": len(edges),
            "anomalies": anomalies,
        },
    }


def _iv_validate_json(input_json: Any) -> tuple[dict[str, Any], list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []

    if input_json is None:
        errors.append("Input JSON is missing.")
        return {}, errors, warnings

    if isinstance(input_json, str):
        try:
            parsed = json.loads(input_json)
        except Exception:
            errors.append("Input JSON is not parseable.")
            return {}, errors, warnings
    else:
        try:
            parsed = json.loads(json.dumps(input_json))
        except Exception:
            errors.append("Input JSON is not serializable.")
            return {}, errors, warnings

    if not isinstance(parsed, Mapping):
        errors.append("Input JSON root must be an object.")
        return {}, errors, warnings

    return dict(parsed), errors, warnings


def _iv_validate_structure(parsed_json: Mapping[str, Any]) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []

    canvas_items = parsed_json.get("canvasItems")
    canvas_connections = parsed_json.get("canvasConnections")

    if not isinstance(canvas_items, list):
        errors.append("canvasItems must exist and be an array.")
    if not isinstance(canvas_connections, list):
        errors.append("canvasConnections must exist and be an array.")

    return errors, warnings


def _iv_validate_resources(canvas_items: list[Any]) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []

    for index, item in enumerate(canvas_items):
        if not isinstance(item, Mapping):
            errors.append(f"canvasItems[{index}] must be an object.")
            continue

        item_id = str(item.get("id") or "").strip()
        resource_type = str(item.get("resourceType") or "").strip()
        properties = item.get("properties")

        if not item_id:
            errors.append(f"canvasItems[{index}] missing required field 'id'.")
        if not resource_type:
            errors.append(f"canvasItems[{index}] missing required field 'resourceType'.")
        if not isinstance(properties, Mapping):
            errors.append(f"canvasItems[{index}] missing required field 'properties'.")

    return errors, warnings


def _iv_validate_references(canvas_items: list[Any]) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    resource_ids = {
        str(item.get("id") or "").strip()
        for item in canvas_items
        if isinstance(item, Mapping) and str(item.get("id") or "").strip()
    }

    for index, item in enumerate(canvas_items):
        if not isinstance(item, Mapping):
            continue

        parent_id = item.get("parentId")
        if parent_id not in (None, ""):
            parent_id_text = str(parent_id).strip()
            if parent_id_text and parent_id_text not in resource_ids:
                errors.append(f"canvasItems[{index}].parentId '{parent_id_text}' does not reference a valid resource.")

        properties = item.get("properties")
        if not isinstance(properties, Mapping):
            continue

        for key, value in properties.items():
            key_text = str(key or "").strip()
            if not key_text.endswith("Ref"):
                continue
            ref_value = str(value or "").strip()
            if ref_value and ref_value not in resource_ids:
                errors.append(f"canvasItems[{index}].properties.{key_text} '{ref_value}' does not reference a valid resource.")

    return errors, warnings


def _iv_validate_connections(canvas_connections: list[Any], resource_ids: set[str]) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []

    for index, connection in enumerate(canvas_connections):
        if not isinstance(connection, Mapping):
            errors.append(f"canvasConnections[{index}] must be an object.")
            continue

        from_id = str(connection.get("fromId") or "").strip()
        to_id = str(connection.get("toId") or "").strip()

        if not from_id:
            errors.append(f"canvasConnections[{index}] missing required field 'fromId'.")
        elif from_id not in resource_ids:
            errors.append(f"canvasConnections[{index}].fromId '{from_id}' does not reference a valid resource.")

        if not to_id:
            errors.append(f"canvasConnections[{index}] missing required field 'toId'.")
        elif to_id not in resource_ids:
            errors.append(f"canvasConnections[{index}].toId '{to_id}' does not reference a valid resource.")

    return errors, warnings


def _iv_validate_environment(settings: Mapping[str, Any]) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []

    provider = str(settings.get("modelProvider") or "").strip()
    llm_model = str(
        settings.get("foundryModelReasoning")
        or settings.get("foundryModelFast")
        or settings.get("foundryModelCoding")
        or ""
    ).strip()

    if not provider:
        errors.append("LLM backend configuration is missing (modelProvider).")
    if not llm_model:
        errors.append("LLM backend model configuration is missing.")

    required_mcp_keys = [
        "azureTenantId",
        "azureClientId",
        "azureClientSecret",
        "azureSubscriptionId",
    ]
    missing_mcp_keys = [key for key in required_mcp_keys if not str(settings.get(key) or "").strip()]
    if missing_mcp_keys:
        errors.append(f"MCP server configuration is missing: {', '.join(missing_mcp_keys)}")

    return errors, warnings


def _run_input_verification_stage(
    *,
    project_dir: Path,
    canvas_state_input: Any,
    settings: Mapping[str, Any],
) -> dict[str, Any]:
    log_path = _reset_input_verification_log(project_dir)
    step_results: list[dict[str, Any]] = []
    errors: list[str] = []
    warnings: list[str] = []

    def _record_step_start(step_key: str, message: str) -> None:
        _append_input_verification_log(log_path, "INFO", message)
        step_results.append({"step": step_key, "status": "started"})

    def _record_step_complete(step_key: str, message: str) -> None:
        _append_input_verification_log(log_path, "INFO", message)
        step_results.append({"step": step_key, "status": "completed"})

    def _record_step_failed(step_key: str, message: str) -> None:
        _append_input_verification_log(log_path, "ERROR", message)
        step_results.append({"step": step_key, "status": "failed", "error": message})

    _append_input_verification_log(log_path, "INFO", "Input Verification started")

    _record_step_start("json", "Validating JSON")
    parsed_json, step_errors, step_warnings = _iv_validate_json(canvas_state_input)
    if step_errors:
        errors.extend(step_errors)
        for reason in step_errors:
            _record_step_failed("json", reason)
        _append_input_verification_log(log_path, "INFO", "Validation failed")
        return {
            "isValid": False,
            "errors": errors,
            "warnings": warnings,
            "stepResults": step_results,
            "logFile": str(log_path),
        }
    warnings.extend(step_warnings)
    _record_step_complete("json", "JSON is valid")

    _record_step_start("structure", "Validating Structure")
    step_errors, step_warnings = _iv_validate_structure(parsed_json)
    if step_errors:
        errors.extend(step_errors)
        for reason in step_errors:
            _record_step_failed("structure", reason)
        _append_input_verification_log(log_path, "INFO", "Validation failed")
        return {
            "isValid": False,
            "errors": errors,
            "warnings": warnings,
            "stepResults": step_results,
            "logFile": str(log_path),
        }
    warnings.extend(step_warnings)
    _record_step_complete("structure", "Structure is valid")

    canvas_items = parsed_json.get("canvasItems") if isinstance(parsed_json, Mapping) else []
    canvas_connections = parsed_json.get("canvasConnections") if isinstance(parsed_json, Mapping) else []

    _record_step_start("resources", "Validating Resources")
    step_errors, step_warnings = _iv_validate_resources(canvas_items if isinstance(canvas_items, list) else [])
    if step_errors:
        errors.extend(step_errors)
        for reason in step_errors:
            _record_step_failed("resources", reason)
        _append_input_verification_log(log_path, "INFO", "Validation failed")
        return {
            "isValid": False,
            "errors": errors,
            "warnings": warnings,
            "stepResults": step_results,
            "logFile": str(log_path),
        }
    warnings.extend(step_warnings)
    _record_step_complete("resources", "Resources are valid")

    _record_step_start("references", "Validating References")
    step_errors, step_warnings = _iv_validate_references(canvas_items if isinstance(canvas_items, list) else [])
    if step_errors:
        errors.extend(step_errors)
        for reason in step_errors:
            _record_step_failed("references", reason)
        _append_input_verification_log(log_path, "INFO", "Validation failed")
        return {
            "isValid": False,
            "errors": errors,
            "warnings": warnings,
            "stepResults": step_results,
            "logFile": str(log_path),
        }
    warnings.extend(step_warnings)
    _record_step_complete("references", "References are valid")

    _record_step_start("connections", "Validating Connections")
    resource_ids = {
        str(item.get("id") or "").strip()
        for item in (canvas_items if isinstance(canvas_items, list) else [])
        if isinstance(item, Mapping) and str(item.get("id") or "").strip()
    }
    step_errors, step_warnings = _iv_validate_connections(
        canvas_connections if isinstance(canvas_connections, list) else [],
        resource_ids,
    )
    if step_errors:
        errors.extend(step_errors)
        for reason in step_errors:
            _record_step_failed("connections", reason)
        _append_input_verification_log(log_path, "INFO", "Validation failed")
        return {
            "isValid": False,
            "errors": errors,
            "warnings": warnings,
            "stepResults": step_results,
            "logFile": str(log_path),
        }
    warnings.extend(step_warnings)
    _record_step_complete("connections", "Connections are valid")

    _record_step_start("environment", "Validating Environment")
    step_errors, step_warnings = _iv_validate_environment(settings)
    if step_errors:
        errors.extend(step_errors)
        for reason in step_errors:
            _record_step_failed("environment", reason)
        _append_input_verification_log(log_path, "INFO", "Validation failed")
        return {
            "isValid": False,
            "errors": errors,
            "warnings": warnings,
            "stepResults": step_results,
            "logFile": str(log_path),
        }
    warnings.extend(step_warnings)
    _record_step_complete("environment", "Environment is valid")

    _append_input_verification_log(log_path, "INFO", "Validation completed")
    return {
        "isValid": True,
        "errors": errors,
        "warnings": warnings,
        "stepResults": step_results,
        "logFile": str(log_path),
    }


@app.post("/api/project/{project_id}/validation/input-verification")
def run_input_verification(project_id: str, body: ValidationInputVerificationPayload | None = None):
    entry = find_project_entry(project_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Project not found")

    payload_canvas_state = body.canvasState if body else None
    if payload_canvas_state in (None, {}):
        payload_canvas_state = read_json_file(entry["statePath"], {})

    settings = load_app_settings()
    result = _run_input_verification_stage(
        project_dir=entry["projectDir"],
        canvas_state_input=payload_canvas_state,
        settings=settings,
    )
    return result


@app.post("/api/project/{project_id}/validation/graph-builder")
def run_graph_builder(project_id: str, body: ValidationInputVerificationPayload | None = None):
    entry = find_project_entry(project_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Project not found")

    payload_canvas_state = body.canvasState if body else None
    if payload_canvas_state in (None, {}):
        payload_canvas_state = read_json_file(entry["statePath"], {})

    result = _run_graph_builder_stage(
        project_dir=entry["projectDir"],
        canvas_state_input=payload_canvas_state,
    )
    return result


def _en_detect_security_risks(resource: Mapping[str, Any], graph_data: Mapping[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    risks: list[dict[str, Any]] = []
    insights: list[dict[str, Any]] = []
    resource_type = str(resource.get("resourceType") or "").lower()
    properties = resource.get("properties") if isinstance(resource.get("properties"), Mapping) else {}

    if "subnet" in resource_type:
        if not str(properties.get("nsgRef") or properties.get("networkSecurityGroupRef") or "").strip():
            risks.append({
                "type": "security",
                "message": "Subnet is missing Network Security Group association.",
            })

        subnet_private_value = properties.get("subnetPrivate")
        if subnet_private_value in (False, "false", "False", 0, "0", "no", "No"):
            risks.append({
                "type": "security",
                "message": "Subnet is configured as public.",
            })

    if "virtual network" in resource_type or resource_type.endswith("/virtualnetworks"):
        ddos_enabled = properties.get("ddosProtectionEnabled")
        if ddos_enabled in (None, False, "false", "False", 0, "0", "no", "No"):
            risks.append({
                "type": "security",
                "message": "DDoS protection is not enabled for VNet.",
            })

    return risks, insights


def _en_detect_networking_risks(resource: Mapping[str, Any], graph_data: Mapping[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    risks: list[dict[str, Any]] = []
    insights: list[dict[str, Any]] = []
    resource_type = str(resource.get("resourceType") or "").lower()
    properties = resource.get("properties") if isinstance(resource.get("properties"), Mapping) else {}

    if "virtual network" in resource_type or resource_type.endswith("/virtualnetworks"):
        address_space = properties.get("addressSpace")
        if not (isinstance(address_space, list) and address_space):
            risks.append({
                "type": "networking",
                "message": "Virtual network has no address space configured.",
            })

    if "subnet" in resource_type:
        if not str(properties.get("routeTableRef") or "").strip():
            risks.append({
                "type": "networking",
                "message": "Subnet has no route table association.",
            })

    if "route table" in resource_type or resource_type.endswith("/routetables"):
        routes = properties.get("routes")
        if not (isinstance(routes, list) and routes):
            risks.append({
                "type": "networking",
                "message": "Route table has no routes configured.",
            })

    return risks, insights


def _en_detect_connectivity_risks(resource: Mapping[str, Any], graph_data: Mapping[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    risks: list[dict[str, Any]] = []
    insights: list[dict[str, Any]] = []
    resource_type = str(resource.get("resourceType") or "").lower()
    resource_id = str(resource.get("id") or "").strip()
    properties = resource.get("properties") if isinstance(resource.get("properties"), Mapping) else {}

    nodes = graph_data.get("nodes") if isinstance(graph_data.get("nodes"), Mapping) else {}
    edges = graph_data.get("connections") if isinstance(graph_data.get("connections"), list) else []

    if "public ip" in resource_type or resource_type.endswith("/publicipaddresses"):
        has_association = any(
            str(edge.get("from") or "").strip() == resource_id or str(edge.get("to") or "").strip() == resource_id
            for edge in edges
            if isinstance(edge, Mapping)
        )
        if not has_association:
            risks.append({
                "type": "connectivity",
                "message": "Public IP is not associated with any resource.",
            })

    missing_refs: list[str] = []
    for key, value in properties.items():
        key_text = str(key or "").strip()
        if not key_text.endswith("Ref"):
            continue
        ref_value = str(value or "").strip()
        if ref_value and ref_value not in nodes:
            missing_refs.append(f"{key_text}={ref_value}")

    if missing_refs:
        risks.append({
            "type": "connectivity",
            "message": f"Resource has unresolved references: {', '.join(missing_refs)}.",
        })

    return risks, insights


def _en_detect_structure_risks(
    resource: Mapping[str, Any],
    graph_data: Mapping[str, Any],
    duplicate_names: set[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    risks: list[dict[str, Any]] = []
    insights: list[dict[str, Any]] = []
    resource_id = str(resource.get("id") or "").strip()
    resource_name = str(resource.get("name") or "").strip()

    nodes = graph_data.get("nodes") if isinstance(graph_data.get("nodes"), Mapping) else {}
    edges = graph_data.get("connections") if isinstance(graph_data.get("connections"), list) else []

    node = nodes.get(resource_id) if isinstance(nodes.get(resource_id), Mapping) else {}
    parent_id = str(node.get("parentId") or "").strip()
    has_edges = any(
        str(edge.get("from") or "").strip() == resource_id or str(edge.get("to") or "").strip() == resource_id
        for edge in edges
        if isinstance(edge, Mapping)
    )
    if not parent_id and not has_edges:
        risks.append({
            "type": "structure",
            "message": "Resource is an orphan node with no parent or connections.",
        })

    location = str(resource.get("location") or resource.get("region") or "").strip()
    if not location:
        risks.append({
            "type": "structure",
            "message": "Resource location is not defined.",
        })

    if resource_name and resource_name.lower() in duplicate_names:
        risks.append({
            "type": "structure",
            "message": "Duplicate resource name detected.",
        })

    return risks, insights


def _en_detect_configuration_risks(resource: Mapping[str, Any], graph_data: Mapping[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    risks: list[dict[str, Any]] = []
    insights: list[dict[str, Any]] = []
    resource_type = str(resource.get("resourceType") or "").lower()
    properties = resource.get("properties") if isinstance(resource.get("properties"), Mapping) else {}

    tags = resource.get("tags")
    if tags in (None, ""):
        risks.append({
            "type": "configuration",
            "message": "Resource has no tags configured.",
        })
    elif isinstance(tags, Mapping) and not tags:
        risks.append({
            "type": "configuration",
            "message": "Resource has empty tags.",
        })

    default_like_values = {"default", "standard", "basic"}
    used_defaults = [
        str(key)
        for key, value in properties.items()
        if str(value or "").strip().lower() in default_like_values
    ]
    if used_defaults:
        risks.append({
            "type": "configuration",
            "message": f"Default configuration values are used: {', '.join(used_defaults)}.",
        })

    if "dns" in resource_type:
        has_dns_config = any(
            str(key or "").lower().startswith("dns") or str(key or "").lower().endswith("dns")
            for key in properties.keys()
        )
        if not has_dns_config:
            risks.append({
                "type": "configuration",
                "message": "DNS configuration is missing.",
            })

    return risks, insights


def _en_detect_global_risks(resources: list[dict[str, Any]]) -> list[str]:
    global_risks: list[str] = []
    has_security_control = any(
        "network security group" in str(resource.get("resourceType") or "").lower()
        or "firewall" in str(resource.get("resourceType") or "").lower()
        or (
            isinstance(resource.get("properties"), Mapping)
            and resource["properties"].get("ddosProtectionEnabled") in (True, "true", "True", 1, "1", "yes", "Yes")
        )
        for resource in resources
    )
    if not has_security_control:
        global_risks.append("No explicit security controls found (NSG/Firewall/DDoS).")

    subnet_count = sum(
        1
        for resource in resources
        if "subnet" in str(resource.get("resourceType") or "").lower()
    )
    if subnet_count <= 1:
        global_risks.append("Network segmentation appears insufficient (single or no subnet).")

    return global_risks


def _en_generate_assumptions(resources: list[dict[str, Any]]) -> list[str]:
    assumptions: list[str] = []

    if all(not str(resource.get("location") or resource.get("region") or "").strip() for resource in resources):
        assumptions.append("Deployment region is assumed to be selected at deployment time.")

    has_scaling = any(
        isinstance(resource.get("properties"), Mapping)
        and any(
            "scale" in str(key or "").lower() or "sku" in str(key or "").lower()
            for key in resource["properties"].keys()
        )
        for resource in resources
    )
    if not has_scaling:
        assumptions.append("Workload is assumed to use baseline capacity with no autoscaling configured.")

    return assumptions


def _en_generate_unknowns(resources: list[dict[str, Any]]) -> list[str]:
    unknowns: list[str] = []

    has_availability = any(
        isinstance(resource.get("properties"), Mapping)
        and any("availability" in str(key or "").lower() for key in resource["properties"].keys())
        for resource in resources
    )
    if not has_availability:
        unknowns.append("Availability and disaster recovery requirements are not defined.")

    has_traffic = any(
        isinstance(resource.get("properties"), Mapping)
        and any("throughput" in str(key or "").lower() or "traffic" in str(key or "").lower() for key in resource["properties"].keys())
        for resource in resources
    )
    if not has_traffic:
        unknowns.append("Traffic profile and expected load are not specified.")

    return unknowns


def _run_enricher_stage(*, project_dir: Path) -> dict[str, Any]:
    log_path = _ensure_validation_log_path(project_dir)
    graph_path = project_dir / "Documentation" / "architecture-graph.json"
    output_path = project_dir / "Documentation" / "enriched-architecture.json"

    step_results: list[dict[str, Any]] = []

    def _record(step: str, status: str, message: str, *, error: str | None = None) -> None:
        level = "INFO" if status != "failed" else "ERROR"
        _append_input_verification_log(log_path, level, message)
        payload: dict[str, Any] = {"step": step, "status": status}
        if error:
            payload["error"] = error
        step_results.append(payload)

    _append_input_verification_log(log_path, "INFO", "Enricher started")

    _record("generate-summary", "started", "Generating Summary")
    if not graph_path.exists():
        reason = "Missing /Documentation/architecture-graph.json. Run Graph Builder first."
        _record("generate-summary", "failed", reason, error=reason)
        return {
            "ok": False,
            "error": reason,
            "stepResults": step_results,
            "artifactPath": str(output_path),
        }

    try:
        graph_data = read_json_file(graph_path, {})
    except Exception as exc:
        reason = f"Failed to read architecture graph: {exc}"
        _record("generate-summary", "failed", reason, error=reason)
        return {
            "ok": False,
            "error": reason,
            "stepResults": step_results,
            "artifactPath": str(output_path),
        }

    if not isinstance(graph_data, Mapping):
        reason = "Invalid architecture graph format."
        _record("generate-summary", "failed", reason, error=reason)
        return {
            "ok": False,
            "error": reason,
            "stepResults": step_results,
            "artifactPath": str(output_path),
        }

    nodes = graph_data.get("nodes") if isinstance(graph_data.get("nodes"), Mapping) else {}
    connections = graph_data.get("connections") if isinstance(graph_data.get("connections"), list) else []
    _record("generate-summary", "completed", "Summary Generated")

    _record("process-resources", "started", "Processing Resources")
    resources: list[dict[str, Any]] = []
    duplicate_name_count: dict[str, int] = {}
    for node_id, node_data in nodes.items():
        if not isinstance(node_data, Mapping):
            continue
        node_name = str(node_data.get("name") or "").strip()
        if node_name:
            key = node_name.lower()
            duplicate_name_count[key] = duplicate_name_count.get(key, 0) + 1
        resources.append(
            {
                "id": str(node_id),
                "name": node_name,
                "resourceType": str(node_data.get("resourceType") or node_data.get("type") or "").strip(),
                "location": str(node_data.get("location") or node_data.get("region") or "").strip(),
                "properties": node_data.get("properties") if isinstance(node_data.get("properties"), Mapping) else {},
                "tags": node_data.get("tags"),
            }
        )
    duplicate_names = {name for name, count in duplicate_name_count.items() if count > 1}
    _record("process-resources", "completed", f"Processed {len(resources)} resources")

    _record("run-detectors", "started", "Running Detectors")
    enriched_resources: list[dict[str, Any]] = []
    total_risks = 0
    detector_count = 0
    for resource in resources:
        all_risks: list[dict[str, Any]] = []
        all_insights: list[dict[str, Any]] = []

        detector_groups = [
            _en_detect_security_risks(resource, graph_data),
            _en_detect_networking_risks(resource, graph_data),
            _en_detect_connectivity_risks(resource, graph_data),
            _en_detect_structure_risks(resource, graph_data, duplicate_names),
            _en_detect_configuration_risks(resource, graph_data),
        ]

        for risks, insights in detector_groups:
            detector_count += 1
            all_risks.extend(risks)
            all_insights.extend(insights)

        total_risks += len(all_risks)
        enriched_resources.append(
            {
                "id": resource["id"],
                "name": resource["name"],
                "resourceType": resource["resourceType"],
                "insights": all_insights,
                "risks": all_risks,
            }
        )
    _record("run-detectors", "completed", f"Detectors completed: {detector_count} runs, {total_risks} risks found")

    _record("detect-global-risks", "started", "Detecting Global Risks")
    global_risks = _en_detect_global_risks(resources)
    _record("detect-global-risks", "completed", f"Global risks detected: {len(global_risks)}")

    _record("generate-assumptions", "started", "Generating Assumptions")
    assumptions = _en_generate_assumptions(resources)
    _record("generate-assumptions", "completed", f"Assumptions generated: {len(assumptions)}")

    _record("generate-unknowns", "started", "Generating Unknowns")
    unknowns = _en_generate_unknowns(resources)
    _record("generate-unknowns", "completed", f"Unknowns generated: {len(unknowns)}")

    _record("finalize-output", "started", "Finalizing Output")
    architecture_type = str(graph_data.get("architectureType") or "Unknown")
    summary = (
        f"Detected architecture with {len(resources)} resources, {len(connections)} connections, "
        f"{total_risks} resource-level risks, and {len(global_risks)} global risks."
    )

    enriched_output = {
        "summary": summary,
        "architectureType": architecture_type,
        "resources": enriched_resources,
        "globalRisks": global_risks,
        "assumptions": assumptions,
        "unknowns": unknowns,
        "generatedAt": datetime.utcnow().isoformat() + "Z",
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(enriched_output, indent=2), encoding="utf-8")
    _record("finalize-output", "completed", f"Enriched architecture written to {output_path}")

    _append_input_verification_log(log_path, "INFO", "Enricher completed")
    return {
        "ok": True,
        "summary": summary,
        "artifactPath": str(output_path),
        "stepResults": step_results,
    }


@app.post("/api/project/{project_id}/validation/enricher")
def run_enricher(project_id: str):
    entry = find_project_entry(project_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Project not found")

    result = _run_enricher_stage(project_dir=entry["projectDir"])
    return result


def _re_normalize_risk_messages(resource: Mapping[str, Any]) -> list[str]:
    risks = resource.get("risks") if isinstance(resource.get("risks"), list) else []
    messages: list[str] = []
    for risk in risks:
        if not isinstance(risk, Mapping):
            continue
        message = str(risk.get("message") or "").strip()
        if message:
            messages.append(message)
    return messages


def _re_resource_type_contains(resource: Mapping[str, Any], token: str) -> bool:
    resource_type = str(resource.get("resourceType") or "").strip().lower()
    return str(token or "").strip().lower() in resource_type


def _re_has_risk_message(resource: Mapping[str, Any], token: str) -> bool:
    token_text = str(token or "").strip().lower()
    if not token_text:
        return False
    risk_messages = resource.get("_riskMessages") if isinstance(resource.get("_riskMessages"), list) else []
    return any(token_text in str(message).lower() for message in risk_messages)


def _re_get_context_resources(ctx: Mapping[str, Any]) -> list[dict[str, Any]]:
    payload = ctx.get("allResources")
    return payload if isinstance(payload, list) else []


def _re_count_resources(ctx: Mapping[str, Any], token: str) -> int:
    token_text = str(token or "").strip().lower()
    if not token_text:
        return 0
    return sum(
        1
        for resource in _re_get_context_resources(ctx)
        if _re_resource_type_contains(resource, token_text)
    )


def _re_find_resource_by_id(ctx: Mapping[str, Any], resource_id: str) -> dict[str, Any] | None:
    safe_id = str(resource_id or "").strip()
    if not safe_id:
        return None
    for resource in _re_get_context_resources(ctx):
        if str(resource.get("id") or "").strip() == safe_id:
            return resource
    return None


def _re_get_resource_connections(ctx: Mapping[str, Any], resource_id: str) -> list[dict[str, Any]]:
    graph = ctx.get("graph") if isinstance(ctx.get("graph"), Mapping) else {}
    edges_raw = graph.get("edges") or graph.get("connections")
    edges = edges_raw if isinstance(edges_raw, list) else []
    safe_id = str(resource_id or "").strip()
    if not safe_id:
        return []
    return [
        edge
        for edge in edges
        if isinstance(edge, Mapping)
        and (
            str(edge.get("from") or "").strip() == safe_id
            or str(edge.get("to") or "").strip() == safe_id
        )
    ]


def _re_has_global_risk(ctx: Mapping[str, Any], token: str) -> bool:
    token_text = str(token or "").strip().lower()
    if not token_text:
        return False
    global_risks = ctx.get("globalRisks") if isinstance(ctx.get("globalRisks"), list) else []
    return any(token_text in str(item).lower() for item in global_risks)


def _re_get_properties(resource: Mapping[str, Any]) -> Mapping[str, Any]:
    properties = resource.get("properties")
    return properties if isinstance(properties, Mapping) else {}


def _re_value_is_disabled(value: Any) -> bool:
    return value in (False, "false", "False", 0, "0", "no", "No", "disabled", "Disabled")


def _re_contains_default_name(name: str) -> bool:
    return bool(re.fullmatch(r"resource\s+\d+", str(name or "").strip(), flags=re.IGNORECASE))


def _re_security_rules() -> list[dict[str, Any]]:
    return [
        {
            "id": "security-subnet-nsg-required",
            "category": "security",
            "severity": "high",
            "message": "Subnet is missing Network Security Group association.",
            "recommendation": "Associate a Network Security Group to each subnet.",
            "evaluate": lambda resource, _ctx: _re_resource_type_contains(resource, "subnet") and _re_has_risk_message(resource, "missing network security group association"),
            "scope": "resource",
        },
        {
            "id": "security-public-subnet-nsg",
            "category": "security",
            "severity": "high",
            "message": "Public subnet must have an NSG.",
            "recommendation": "Attach NSG rules to restrict public subnet traffic.",
            "evaluate": lambda resource, _ctx: _re_resource_type_contains(resource, "subnet") and _re_has_risk_message(resource, "subnet is configured as public") and _re_has_risk_message(resource, "missing network security group association"),
            "scope": "resource",
        },
        {
            "id": "security-nsg-unrestricted-inbound",
            "category": "security",
            "severity": "high",
            "message": "NSG has potentially unrestricted inbound access (0.0.0.0/0).",
            "recommendation": "Restrict inbound NSG rules to required source ranges only.",
            "evaluate": lambda resource, _ctx: _re_resource_type_contains(resource, "network security group") and (
                _re_has_risk_message(resource, "0.0.0.0/0")
                or "0.0.0.0/0" in json.dumps(_re_get_properties(resource), ensure_ascii=False)
            ),
            "scope": "resource",
        },
        {
            "id": "security-vnet-ddos-enabled",
            "category": "security",
            "severity": "medium",
            "message": "DDoS protection is not enabled for VNet.",
            "recommendation": "Enable DDoS protection for VNets hosting critical workloads.",
            "evaluate": lambda resource, _ctx: _re_resource_type_contains(resource, "virtual network") and _re_has_risk_message(resource, "ddos protection is not enabled"),
            "scope": "resource",
        },
        {
            "id": "security-public-ip-associated",
            "category": "security",
            "severity": "medium",
            "message": "Public IP should be associated to a protected endpoint.",
            "recommendation": "Associate Public IPs only with protected resources behind security controls.",
            "evaluate": lambda resource, _ctx: _re_resource_type_contains(resource, "public ip") and _re_has_risk_message(resource, "not associated with any resource"),
            "scope": "resource",
        },
        {
            "id": "security-private-endpoint-policy-review",
            "category": "security",
            "severity": "medium",
            "message": "Private endpoint network policies are disabled and require review.",
            "recommendation": "Review private endpoint policy settings and justify disabled state.",
            "evaluate": lambda resource, _ctx: _re_resource_type_contains(resource, "private endpoint") and _re_value_is_disabled(_re_get_properties(resource).get("privateEndpointNetworkPolicies")),
            "scope": "resource",
        },
        {
            "id": "security-no-network-segmentation",
            "category": "security",
            "severity": "high",
            "message": "Network segmentation is insufficient (single subnet architecture).",
            "recommendation": "Use separate subnets for application tiers and security boundaries.",
            "evaluate": lambda _resource, ctx: _re_count_resources(ctx, "subnet") <= 1,
            "scope": "global",
        },
        {
            "id": "security-public-exposure-without-protection",
            "category": "security",
            "severity": "high",
            "message": "Public exposure detected without adequate network protection.",
            "recommendation": "Add NSG/firewall controls before exposing workloads publicly.",
            "evaluate": lambda _resource, ctx: (
                any(_re_has_risk_message(resource, "subnet is configured as public") for resource in _re_get_context_resources(ctx))
                and not any(
                    _re_resource_type_contains(resource, "network security group")
                    or _re_resource_type_contains(resource, "firewall")
                    for resource in _re_get_context_resources(ctx)
                )
            ),
            "scope": "global",
        },
    ]


def _re_networking_rules() -> list[dict[str, Any]]:
    return [
        {
            "id": "networking-vnet-address-space",
            "category": "reliability",
            "severity": "high",
            "message": "VNet has no address space configured.",
            "recommendation": "Define valid CIDR address spaces for each VNet.",
            "evaluate": lambda resource, _ctx: _re_resource_type_contains(resource, "virtual network") and _re_has_risk_message(resource, "no address space configured"),
            "scope": "resource",
        },
        {
            "id": "networking-subnet-belongs-to-vnet",
            "category": "reliability",
            "severity": "high",
            "message": "Subnet is not associated with a virtual network.",
            "recommendation": "Assign the subnet to a parent VNet via parentId or vnetRef.",
            "evaluate": lambda resource, _ctx: _re_resource_type_contains(resource, "subnet") and not str(resource.get("parentId") or _re_get_properties(resource).get("vnetRef") or "").strip(),
            "scope": "resource",
        },
        {
            "id": "networking-subnet-route-table",
            "category": "reliability",
            "severity": "medium",
            "message": "Subnet has no route table association.",
            "recommendation": "Associate the subnet with an explicit route table.",
            "evaluate": lambda resource, _ctx: _re_resource_type_contains(resource, "subnet") and _re_has_risk_message(resource, "no route table association"),
            "scope": "resource",
        },
        {
            "id": "networking-route-table-has-routes",
            "category": "reliability",
            "severity": "medium",
            "message": "Route table has no routes configured.",
            "recommendation": "Configure required route entries in each route table.",
            "evaluate": lambda resource, _ctx: _re_resource_type_contains(resource, "route table") and _re_has_risk_message(resource, "no routes configured"),
            "scope": "resource",
        },
        {
            "id": "networking-multiple-subnets-recommended",
            "category": "performance",
            "severity": "low",
            "message": "Multiple subnets are recommended for tier isolation.",
            "recommendation": "Add separate subnets for frontend, application, and data tiers.",
            "evaluate": lambda _resource, ctx: _re_count_resources(ctx, "subnet") < 2,
            "scope": "global",
        },
        {
            "id": "networking-dns-missing",
            "category": "operations",
            "severity": "medium",
            "message": "DNS configuration is missing.",
            "recommendation": "Configure required DNS settings or DNS zones.",
            "evaluate": lambda resource, _ctx: _re_resource_type_contains(resource, "dns") and _re_has_risk_message(resource, "dns configuration is missing"),
            "scope": "resource",
        },
    ]


def _re_connectivity_rules() -> list[dict[str, Any]]:
    return [
        {
            "id": "connectivity-public-ip-unused",
            "category": "reliability",
            "severity": "medium",
            "message": "Public IP is unused.",
            "recommendation": "Associate the Public IP or remove it to reduce attack surface.",
            "evaluate": lambda resource, _ctx: _re_resource_type_contains(resource, "public ip") and _re_has_risk_message(resource, "not associated with any resource"),
            "scope": "resource",
        },
        {
            "id": "connectivity-broken-reference",
            "category": "reliability",
            "severity": "high",
            "message": "Resource has broken references.",
            "recommendation": "Resolve invalid references to existing dependent resources.",
            "evaluate": lambda resource, _ctx: _re_has_risk_message(resource, "unresolved references"),
            "scope": "resource",
        },
        {
            "id": "connectivity-route-association-inconsistent",
            "category": "reliability",
            "severity": "medium",
            "message": "Route table association is inconsistent.",
            "recommendation": "Ensure all routeTableRef values point to existing route tables.",
            "evaluate": lambda resource, ctx: (
                _re_resource_type_contains(resource, "subnet")
                and bool(str(_re_get_properties(resource).get("routeTableRef") or "").strip())
                and _re_find_resource_by_id(ctx, str(_re_get_properties(resource).get("routeTableRef") or "").strip()) is None
            ),
            "scope": "resource",
        },
        {
            "id": "connectivity-resource-not-connected",
            "category": "reliability",
            "severity": "medium",
            "message": "Resource is not connected to any other component.",
            "recommendation": "Validate dependency links and add missing connections.",
            "evaluate": lambda resource, ctx: (
                str(resource.get("id") or "").strip() not in {"", "global"}
                and len(_re_get_resource_connections(ctx, str(resource.get("id") or "").strip())) == 0
            ),
            "scope": "resource",
        },
    ]


def _re_operations_rules() -> list[dict[str, Any]]:
    return [
        {
            "id": "operations-missing-location",
            "category": "operations",
            "severity": "medium",
            "message": "Resource location is missing.",
            "recommendation": "Specify a deployment location/region for each resource.",
            "evaluate": lambda resource, _ctx: _re_has_risk_message(resource, "location is not defined"),
            "scope": "resource",
        },
        {
            "id": "operations-missing-tags",
            "category": "operations",
            "severity": "low",
            "message": "Resource tags are missing.",
            "recommendation": "Apply mandatory governance and ownership tags.",
            "evaluate": lambda resource, _ctx: _re_has_risk_message(resource, "no tags configured") or _re_has_risk_message(resource, "empty tags"),
            "scope": "resource",
        },
        {
            "id": "operations-rg-location-inconsistent",
            "category": "operations",
            "severity": "medium",
            "message": "Resource group location usage is inconsistent.",
            "recommendation": "Align resource deployments with regional governance standards.",
            "evaluate": lambda _resource, ctx: (
                len(
                    {
                        str(res.get("location") or "").strip().lower()
                        for res in _re_get_context_resources(ctx)
                        if _re_resource_type_contains(res, "resource group") and str(res.get("location") or "").strip()
                    }
                ) > 1
            ),
            "scope": "global",
        },
        {
            "id": "operations-default-naming",
            "category": "operations",
            "severity": "low",
            "message": "Default naming pattern detected.",
            "recommendation": "Use meaningful and standardized naming conventions.",
            "evaluate": lambda resource, _ctx: _re_contains_default_name(str(resource.get("name") or "")) or str(resource.get("name") or "").strip().lower() == "default",
            "scope": "resource",
        },
    ]


def _re_cost_rules() -> list[dict[str, Any]]:
    return [
        {
            "id": "cost-unused-resources",
            "category": "cost",
            "severity": "medium",
            "message": "Unused resource detected.",
            "recommendation": "Remove or repurpose unused resources to reduce cost.",
            "evaluate": lambda resource, _ctx: _re_has_risk_message(resource, "orphan node"),
            "scope": "resource",
        },
        {
            "id": "cost-standard-public-ip",
            "category": "cost",
            "severity": "low",
            "message": "Standard SKU Public IP has no clear associated need.",
            "recommendation": "Validate Standard SKU necessity or downgrade/remove if unused.",
            "evaluate": lambda resource, _ctx: (
                _re_resource_type_contains(resource, "public ip")
                and str(_re_get_properties(resource).get("sku") or "").strip().lower() == "standard"
                and _re_has_risk_message(resource, "not associated with any resource")
            ),
            "scope": "resource",
        },
        {
            "id": "cost-default-config-not-optimized",
            "category": "cost",
            "severity": "low",
            "message": "Default configuration values may not be cost-optimized.",
            "recommendation": "Review default settings and optimize for workload demand.",
            "evaluate": lambda resource, _ctx: _re_has_risk_message(resource, "default configuration values are used"),
            "scope": "resource",
        },
        {
            "id": "cost-overprovisioned-networking",
            "category": "cost",
            "severity": "medium",
            "message": "Networking components appear over-provisioned.",
            "recommendation": "Right-size network components based on actual usage patterns.",
            "evaluate": lambda _resource, ctx: (
                _re_count_resources(ctx, "public ip") > 2 and _re_count_resources(ctx, "subnet") <= 1
            ) or (
                _re_count_resources(ctx, "route table") > 2 and _re_count_resources(ctx, "subnet") <= 1
            ),
            "scope": "global",
        },
    ]


def _re_performance_reliability_rules() -> list[dict[str, Any]]:
    return [
        {
            "id": "performance-single-subnet-architecture",
            "category": "performance",
            "severity": "medium",
            "message": "Single-subnet architecture limits performance isolation.",
            "recommendation": "Introduce multi-subnet segmentation for workload tiers.",
            "evaluate": lambda _resource, ctx: _re_count_resources(ctx, "subnet") <= 1,
            "scope": "global",
        },
        {
            "id": "reliability-no-zone-redundancy",
            "category": "reliability",
            "severity": "medium",
            "message": "No zone redundancy detected for applicable resources.",
            "recommendation": "Enable zone redundancy or multi-zone deployment where supported.",
            "evaluate": lambda resource, _ctx: (
                (
                    _re_resource_type_contains(resource, "virtual machine")
                    or _re_resource_type_contains(resource, "scale set")
                    or _re_resource_type_contains(resource, "sql")
                    or _re_resource_type_contains(resource, "app service")
                )
                and not any(
                    str(_re_get_properties(resource).get(key) or "").strip()
                    for key in ("zone", "zones", "zoneRedundant", "availabilityZones")
                )
            ),
            "scope": "resource",
        },
        {
            "id": "performance-no-load-distribution",
            "category": "performance",
            "severity": "medium",
            "message": "No load distribution mechanism detected.",
            "recommendation": "Add load balancing components (Load Balancer/Application Gateway/Front Door).",
            "evaluate": lambda _resource, ctx: not any(
                _re_resource_type_contains(resource, "load balancer")
                or _re_resource_type_contains(resource, "application gateway")
                or _re_resource_type_contains(resource, "front door")
                for resource in _re_get_context_resources(ctx)
            ),
            "scope": "global",
        },
        {
            "id": "reliability-no-traffic-routing-strategy",
            "category": "reliability",
            "severity": "medium",
            "message": "No traffic routing strategy detected.",
            "recommendation": "Define routing using Route Tables, DNS, or traffic manager patterns.",
            "evaluate": lambda _resource, ctx: not any(
                _re_resource_type_contains(resource, "route table")
                or _re_resource_type_contains(resource, "dns")
                or _re_resource_type_contains(resource, "traffic manager")
                or _re_resource_type_contains(resource, "front door")
                for resource in _re_get_context_resources(ctx)
            ),
            "scope": "global",
        },
    ]


def _re_initialize_rules() -> list[dict[str, Any]]:
    rules = (
        _re_security_rules()
        + _re_networking_rules()
        + _re_connectivity_rules()
        + _re_operations_rules()
        + _re_cost_rules()
        + _re_performance_reliability_rules()
    )
    return rules


def _re_build_violation(*, rule: Mapping[str, Any], resource: Mapping[str, Any], sequence: int) -> dict[str, Any]:
    resource_id = str(resource.get("id") or "global") or "global"
    violation = {
        "id": f"{str(rule.get('id') or 'rule')}:{resource_id}:{sequence}",
        "resourceId": resource_id,
        "resourceName": str(resource.get("name") or "Architecture") or "Architecture",
        "resourceType": str(resource.get("resourceType") or "Global") or "Global",
        "message": str(rule.get("message") or "Rule violation detected."),
        "severity": str(rule.get("severity") or "low"),
        "category": str(rule.get("category") or "operations"),
        "recommendation": str(rule.get("recommendation") or "Review and remediate this rule violation.").strip() or "Review and remediate this rule violation.",
    }
    return violation


def _re_evaluate_rules(
    *,
    resources: list[dict[str, Any]],
    rules: list[dict[str, Any]],
    global_risks: list[str],
    graph: Mapping[str, Any],
) -> list[dict[str, Any]]:
    violations: list[dict[str, Any]] = []
    sequence = 1

    enriched_resources: list[dict[str, Any]] = []
    for resource in resources:
        if not isinstance(resource, Mapping):
            continue
        normalized = dict(resource)
        normalized["_riskMessages"] = _re_normalize_risk_messages(resource)
        enriched_resources.append(normalized)

    context = {
        "allResources": enriched_resources,
        "resources": enriched_resources,
        "globalRisks": global_risks,
        "graph": graph,
        "summary": {
            "resourceCount": len(enriched_resources),
            "subnetCount": sum(1 for resource in enriched_resources if _re_resource_type_contains(resource, "subnet")),
            "vnetCount": sum(1 for resource in enriched_resources if _re_resource_type_contains(resource, "virtual network")),
            "publicIpCount": sum(1 for resource in enriched_resources if _re_resource_type_contains(resource, "public ip")),
            "routeTableCount": sum(1 for resource in enriched_resources if _re_resource_type_contains(resource, "route table")),
        },
    }

    for resource in enriched_resources:
        for rule in rules:
            if str(rule.get("scope") or "resource") != "resource":
                continue
            evaluator = rule.get("evaluate")
            if not callable(evaluator):
                continue
            try:
                is_violation = bool(evaluator(resource, context))
            except Exception:
                is_violation = False
            if not is_violation:
                continue
            violations.append(_re_build_violation(rule=rule, resource=resource, sequence=sequence))
            sequence += 1

    global_resource = {
        "id": "global",
        "name": "Architecture",
        "resourceType": "Global",
    }
    for rule in rules:
        if str(rule.get("scope") or "resource") != "global":
            continue
        evaluator = rule.get("evaluate")
        if not callable(evaluator):
            continue
        try:
            is_violation = bool(evaluator(global_resource, context))
        except Exception:
            is_violation = False
        if not is_violation:
            continue
        violations.append(_re_build_violation(rule=rule, resource=global_resource, sequence=sequence))
        sequence += 1

    return violations


def _re_aggregate_violations(violations: list[dict[str, Any]]) -> dict[str, Any]:
    severity_keys = ["low", "medium", "high"]
    category_keys = ["security", "reliability", "cost", "performance", "operations"]

    by_severity = {key: 0 for key in severity_keys}
    by_category = {key: 0 for key in category_keys}

    for violation in violations:
        severity = str(violation.get("severity") or "").strip().lower()
        category = str(violation.get("category") or "").strip().lower()
        if severity in by_severity:
            by_severity[severity] += 1
        if category in by_category:
            by_category[category] += 1

    return {
        "total": len(violations),
        "bySeverity": by_severity,
        "byCategory": by_category,
    }


def _run_rule_engine_stage(*, project_dir: Path) -> dict[str, Any]:
    log_path = _ensure_validation_log_path(project_dir)
    input_path = project_dir / "Documentation" / "enriched-architecture.json"
    graph_path = project_dir / "Documentation" / "architecture-graph.json"
    output_path = project_dir / "Documentation" / "rule-results.json"
    output_artifact_ref = "/Documentation/rule-results.json"

    step_results: list[dict[str, Any]] = []

    def _record(step: str, status: str, message: str, *, error: str | None = None) -> None:
        level = "INFO" if status != "failed" else "ERROR"
        _append_input_verification_log(log_path, level, message)
        row: dict[str, Any] = {"step": step, "status": status}
        if error:
            row["error"] = error
        step_results.append(row)

    _append_input_verification_log(log_path, "INFO", "Rule Engine started")

    _record("load-enriched-data", "started", "Loading Enriched Data")
    if not input_path.exists():
        reason = "Missing /Documentation/enriched-architecture.json. Run Enricher first."
        _record("load-enriched-data", "failed", reason, error=reason)
        return {
            "ok": False,
            "error": reason,
            "stepResults": step_results,
            "artifactPath": output_artifact_ref,
        }

    enriched_payload = read_json_file(input_path, {})
    if not isinstance(enriched_payload, Mapping):
        reason = "Invalid enriched architecture structure."
        _record("load-enriched-data", "failed", reason, error=reason)
        return {
            "ok": False,
            "error": reason,
            "stepResults": step_results,
            "artifactPath": output_artifact_ref,
        }

    resources = enriched_payload.get("resources") if isinstance(enriched_payload.get("resources"), list) else None
    global_risks = enriched_payload.get("globalRisks") if isinstance(enriched_payload.get("globalRisks"), list) else []
    if resources is None:
        reason = "Invalid enriched architecture structure: resources array is required."
        _record("load-enriched-data", "failed", reason, error=reason)
        return {
            "ok": False,
            "error": reason,
            "stepResults": step_results,
            "artifactPath": output_artifact_ref,
        }

    normalized_resources: list[dict[str, Any]] = []
    for index, item in enumerate(resources):
        if not isinstance(item, Mapping):
            reason = f"Invalid enriched architecture structure: resources[{index}] must be an object."
            _record("load-enriched-data", "failed", reason, error=reason)
            return {
                "ok": False,
                "error": reason,
                "stepResults": step_results,
                "artifactPath": output_artifact_ref,
            }

        resource_id = str(item.get("id") or "").strip()
        resource_name = str(item.get("name") or "").strip()
        resource_type = str(item.get("resourceType") or "").strip()
        risks = item.get("risks")
        if not resource_id or not resource_name or not resource_type or not isinstance(risks, list):
            reason = (
                f"Invalid enriched architecture structure: resources[{index}] requires "
                "id, name, resourceType, and risks[]."
            )
            _record("load-enriched-data", "failed", reason, error=reason)
            return {
                "ok": False,
                "error": reason,
                "stepResults": step_results,
                "artifactPath": output_artifact_ref,
            }

        normalized_resources.append(dict(item))

    graph_payload = read_json_file(graph_path, {})
    if not isinstance(graph_payload, Mapping):
        graph_payload = {}

    _record("load-enriched-data", "completed", f"Loaded Enriched Data: {len(resources)} resources")

    _record("initialize-rules", "started", "Initializing Rules")
    rules = _re_initialize_rules()
    if len(rules) < 25:
        reason = f"Rule initialization failed: expected at least 25 rules, got {len(rules)}."
        _record("initialize-rules", "failed", reason, error=reason)
        return {
            "ok": False,
            "error": reason,
            "stepResults": step_results,
            "artifactPath": output_artifact_ref,
        }
    _append_input_verification_log(log_path, "INFO", f"Rules loaded: {len(rules)}")
    _record("initialize-rules", "completed", f"Initialized Rules: {len(rules)}")

    _record("evaluate-rules", "started", "Evaluating Rules")
    violations = _re_evaluate_rules(
        resources=normalized_resources,
        rules=rules,
        global_risks=[str(item) for item in global_risks],
        graph=graph_payload,
    )
    _record("evaluate-rules", "completed", f"Rules Evaluated: {len(violations)} violations")

    _record("aggregate-violations", "started", "Aggregating Violations")
    summary = _re_aggregate_violations(violations)
    _record("aggregate-violations", "completed", "Violations Aggregated")

    _record("finalize-output", "started", "Finalizing Output")
    result_payload = {
        "violations": violations,
        "summary": summary,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result_payload, indent=2, ensure_ascii=False), encoding="utf-8")
    _record("finalize-output", "completed", f"Rule results written to {output_artifact_ref}")

    _append_input_verification_log(log_path, "INFO", f"Violations detected: {summary['total']}")
    _append_input_verification_log(log_path, "INFO", f"High severity: {summary['bySeverity']['high']}")
    _append_input_verification_log(log_path, "INFO", "Rule Engine completed")

    return {
        "ok": True,
        "artifactPath": output_artifact_ref,
        "stepResults": step_results,
        "violations": violations,
        "summary": summary,
    }


@app.post("/api/project/{project_id}/validation/rule-engine")
def run_rule_engine(project_id: str):
    entry = find_project_entry(project_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Project not found")

    result = _run_rule_engine_stage(project_dir=entry["projectDir"])
    return result


def _sf_normalize_severity(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"high", "medium", "low"}:
        return normalized
    if normalized in {"critical"}:
        return "high"
    return "medium"


def _sf_severity_rank(value: str) -> int:
    order = {"low": 1, "medium": 2, "high": 3}
    return order.get(str(value or "").strip().lower(), 1)


def _sf_merge_severity(current: str, incoming: str) -> str:
    return incoming if _sf_severity_rank(incoming) > _sf_severity_rank(current) else current


def _sf_normalize_category(value: Any, *, message: str = "") -> str:
    normalized = str(value or "").strip().lower()
    allowed = {"security", "reliability", "cost", "performance", "operations", "networking", "connectivity", "configuration", "structure"}
    if normalized in allowed:
        if normalized == "networking" or normalized == "connectivity":
            return "reliability"
        if normalized in {"configuration", "structure"}:
            return "operations"
        return normalized

    text = str(message or "").strip().lower()
    if any(token in text for token in ("security", "nsg", "firewall", "ddos", "public")):
        return "security"
    if any(token in text for token in ("route", "connect", "reference", "network", "subnet", "vnet")):
        return "reliability"
    if any(token in text for token in ("cost", "unused", "optimiz", "sku")):
        return "cost"
    if any(token in text for token in ("latency", "throughput", "performance", "slow")):
        return "performance"
    return "operations"


def _sf_issue_token(issue_id: Any, message: Any) -> str:
    issue_id_value = str(issue_id or "").strip()
    if issue_id_value:
        return issue_id_value.split(":", 1)[0]

    words = re.findall(r"[a-z0-9]+", str(message or "").lower())
    if not words:
        return "issue"
    return "-".join(words[:6])


def _sf_risk_title(*, category: str, resource_name: str, related_issues: list[str]) -> str:
    title_map = {
        "security": "Unprotected public network surface",
        "reliability": "Network configuration reliability gaps",
        "operations": "Operational governance and configuration gaps",
        "cost": "Cost optimization and unused resource risk",
        "performance": "Performance and scaling risk",
    }
    base = title_map.get(category, "Architecture risk cluster")
    if resource_name:
        return f"{base} ({resource_name})"
    if related_issues:
        return base
    return "Architecture risk cluster"


def _run_structured_findings_stage(*, project_dir: Path) -> dict[str, Any]:
    log_path = _ensure_validation_log_path(project_dir)
    graph_path = project_dir / "Documentation" / "architecture-graph.json"
    enriched_path = project_dir / "Documentation" / "enriched-architecture.json"
    rule_results_path = project_dir / "Documentation" / "rule-results.json"
    output_path = project_dir / "Documentation" / "structured-findings.json"
    output_artifact_ref = "/Documentation/structured-findings.json"

    step_results: list[dict[str, Any]] = []

    def _record(step: str, status: str, message: str, *, error: str | None = None) -> None:
        level = "INFO" if status != "failed" else "ERROR"
        _append_input_verification_log(log_path, level, message)
        row: dict[str, Any] = {"step": step, "status": status}
        if error:
            row["error"] = error
        step_results.append(row)

    _append_input_verification_log(log_path, "INFO", "Structured Findings started")

    _record("load-inputs", "started", "Loading Inputs")
    if not graph_path.exists():
        reason = "Missing /Documentation/architecture-graph.json. Run Graph Builder first."
        _record("load-inputs", "failed", reason, error=reason)
        return {"ok": False, "error": reason, "stepResults": step_results, "artifactPath": output_artifact_ref}
    if not enriched_path.exists():
        reason = "Missing /Documentation/enriched-architecture.json. Run Enricher first."
        _record("load-inputs", "failed", reason, error=reason)
        return {"ok": False, "error": reason, "stepResults": step_results, "artifactPath": output_artifact_ref}
    if not rule_results_path.exists():
        reason = "Missing /Documentation/rule-results.json. Run Rule Engine first."
        _record("load-inputs", "failed", reason, error=reason)
        return {"ok": False, "error": reason, "stepResults": step_results, "artifactPath": output_artifact_ref}

    graph_payload = read_json_file(graph_path, {})
    enriched_payload = read_json_file(enriched_path, {})
    rules_payload = read_json_file(rule_results_path, {})

    if not isinstance(graph_payload, Mapping) or not isinstance(enriched_payload, Mapping) or not isinstance(rules_payload, Mapping):
        reason = "Invalid JSON structure in one or more input artifacts."
        _record("load-inputs", "failed", reason, error=reason)
        return {"ok": False, "error": reason, "stepResults": step_results, "artifactPath": output_artifact_ref}

    nodes = graph_payload.get("nodes") if isinstance(graph_payload.get("nodes"), Mapping) else {}
    edges = graph_payload.get("edges") if isinstance(graph_payload.get("edges"), list) else []
    enriched_resources = enriched_payload.get("resources") if isinstance(enriched_payload.get("resources"), list) else []
    rule_violations = rules_payload.get("violations") if isinstance(rules_payload.get("violations"), list) else []
    _record("load-inputs", "completed", "Inputs loaded")

    _record("normalize-data", "started", "Normalizing Data")
    resources: list[dict[str, Any]] = []
    resource_index: dict[str, dict[str, Any]] = {}
    for node_id in sorted(str(key) for key in nodes.keys()):
        node = nodes.get(node_id)
        if not isinstance(node, Mapping):
            continue
        row = {
            "id": node_id,
            "type": _gb_normalize_resource_type(str(node.get("type") or node.get("resourceType") or "")),
            "name": str(node.get("name") or node_id),
        }
        resources.append(row)
        resource_index[node_id] = row

    violation_seen: set[tuple[str, str]] = set()
    violations: list[dict[str, Any]] = []
    for violation in rule_violations:
        if not isinstance(violation, Mapping):
            continue
        resource_id = str(violation.get("resourceId") or "global").strip() or "global"
        message = str(violation.get("message") or "").strip()
        if not message:
            continue
        dedupe_key = (resource_id, message.lower())
        if dedupe_key in violation_seen:
            continue
        violation_seen.add(dedupe_key)
        violations.append(
            {
                "id": str(violation.get("id") or _sf_issue_token("", message)).strip() or _sf_issue_token("", message),
                "resourceId": resource_id,
                "message": message,
                "severity": _sf_normalize_severity(violation.get("severity")),
                "category": _sf_normalize_category(violation.get("category"), message=message),
            }
        )

    signal_seen: set[tuple[str, str, str]] = set()
    signals: list[dict[str, Any]] = []
    for resource in enriched_resources:
        if not isinstance(resource, Mapping):
            continue
        resource_id = str(resource.get("id") or "").strip() or "global"
        risks = resource.get("risks") if isinstance(resource.get("risks"), list) else []
        for risk in risks:
            if not isinstance(risk, Mapping):
                continue
            signal_type = str(risk.get("type") or "operations").strip().lower() or "operations"
            message = str(risk.get("message") or "").strip()
            if not message:
                continue
            dedupe_key = (signal_type, resource_id, message.lower())
            if dedupe_key in signal_seen:
                continue
            signal_seen.add(dedupe_key)
            signals.append({"type": signal_type, "resourceId": resource_id, "message": message})

    global_risks = enriched_payload.get("globalRisks") if isinstance(enriched_payload.get("globalRisks"), list) else []
    for item in global_risks:
        message = str(item or "").strip()
        if not message:
            continue
        dedupe_key = ("global", "global", message.lower())
        if dedupe_key in signal_seen:
            continue
        signal_seen.add(dedupe_key)
        signals.append({"type": "global", "resourceId": "global", "message": message})

    _record("normalize-data", "completed", "Data normalized")

    _record("group-issues", "started", "Grouping Issues")
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for violation in violations:
        resource_id = str(violation.get("resourceId") or "global")
        category = _sf_normalize_category(violation.get("category"), message=str(violation.get("message") or ""))
        key = (resource_id, category)
        group = grouped.setdefault(
            key,
            {
                "resourceId": resource_id,
                "category": category,
                "severity": "low",
                "related_issues": [],
                "resources": set(),
            },
        )
        group["severity"] = _sf_merge_severity(str(group.get("severity") or "low"), _sf_normalize_severity(violation.get("severity")))
        group["resources"].add(resource_id)
        token = _sf_issue_token(violation.get("id"), violation.get("message"))
        if token not in group["related_issues"]:
            group["related_issues"].append(token)

    for signal in signals:
        resource_id = str(signal.get("resourceId") or "global")
        category = _sf_normalize_category(signal.get("type"), message=str(signal.get("message") or ""))
        key = (resource_id, category)
        group = grouped.setdefault(
            key,
            {
                "resourceId": resource_id,
                "category": category,
                "severity": "low",
                "related_issues": [],
                "resources": set(),
            },
        )
        signal_severity = "medium" if category in {"security", "reliability"} else "low"
        group["severity"] = _sf_merge_severity(str(group.get("severity") or "low"), signal_severity)
        group["resources"].add(resource_id)
        token = _sf_issue_token("", signal.get("message"))
        if token not in group["related_issues"]:
            group["related_issues"].append(token)

    risks: list[dict[str, Any]] = []
    risk_index: dict[str, int] = {}
    for (resource_id, category), group in sorted(grouped.items(), key=lambda item: (item[0][1], item[0][0])):
        related_issues = [str(item) for item in group.get("related_issues") if str(item).strip()]
        if not related_issues:
            continue
        risk_index[category] = risk_index.get(category, 0) + 1
        resource_name = str(resource_index.get(resource_id, {}).get("name") or resource_id)
        risks.append(
            {
                "id": f"risk-{category}-{risk_index[category]}",
                "title": _sf_risk_title(category=category, resource_name=resource_name, related_issues=related_issues),
                "severity": _sf_normalize_severity(group.get("severity")),
                "category": category,
                "resources": sorted({str(item) for item in group.get("resources", set()) if str(item).strip()}),
                "related_issues": related_issues,
            }
        )
    _record("group-issues", "completed", "Issues grouped")

    _record("build-risk-summary", "started", "Building Risk Summary")
    risk_summary: dict[str, list[str]] = {"high": [], "medium": [], "low": []}
    for risk in risks:
        severity = _sf_normalize_severity(risk.get("severity"))
        risk_summary[severity].append(str(risk.get("id") or ""))
    _record("build-risk-summary", "completed", "Risk summary built")

    _record("finalize-output", "started", "Finalizing Output")
    resource_types = sorted({str(resource.get("type") or "Azure Resource") for resource in resources})
    assumptions = [str(item).strip() for item in (enriched_payload.get("assumptions") if isinstance(enriched_payload.get("assumptions"), list) else []) if str(item).strip()]
    unknowns = [str(item).strip() for item in (enriched_payload.get("unknowns") if isinstance(enriched_payload.get("unknowns"), list) else []) if str(item).strip()]

    architecture_notes: list[str] = []
    architecture_notes.append(f"Graph edges detected: {len(edges)}")
    if global_risks:
        architecture_notes.append(f"Global risks detected: {len(global_risks)}")

    output_payload = {
        "architecture_summary": {
            "resourceCount": len(resources),
            "resourceTypes": resource_types,
            "notes": architecture_notes,
        },
        "resources": resources,
        "violations": violations,
        "signals": signals,
        "risks": risks,
        "risk_summary": risk_summary,
        "assumptions": assumptions,
        "unknowns": unknowns,
    }

    if not resources and not violations and not signals and not risks:
        reason = "Structured findings output is empty."
        _record("finalize-output", "failed", reason, error=reason)
        return {"ok": False, "error": reason, "stepResults": step_results, "artifactPath": output_artifact_ref}

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output_payload, indent=2, ensure_ascii=False), encoding="utf-8")
    _record("finalize-output", "completed", f"Structured findings written to {output_artifact_ref}")

    _append_input_verification_log(log_path, "INFO", "Inputs loaded")
    _append_input_verification_log(log_path, "INFO", f"Violations processed: {len(violations)}")
    _append_input_verification_log(log_path, "INFO", f"Signals processed: {len(signals)}")
    _append_input_verification_log(log_path, "INFO", f"Risks generated: {len(risks)}")
    _append_input_verification_log(log_path, "INFO", "Structured Findings completed")

    return {
        "ok": True,
        "artifactPath": output_artifact_ref,
        "stepResults": step_results,
        "structuredFindings": output_payload,
    }


@app.post("/api/project/{project_id}/validation/structured-findings")
def run_structured_findings(project_id: str):
    entry = find_project_entry(project_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Project not found")

    result = _run_structured_findings_stage(project_dir=entry["projectDir"])
    return result


def _kr_extract_text(content: Any) -> str:
    if isinstance(content, list):
        return "\n".join(str(getattr(item, "text", item)) for item in content)
    return str(content or "")


def _kr_try_json(raw: str) -> Any:
    try:
        return json.loads(raw)
    except Exception:
        return None


def _kr_normalize_risk_title(risk: Mapping[str, Any]) -> str:
    title = str(risk.get("title") or "").strip()
    if title:
        return title
    risk_id = str(risk.get("id") or "").strip()
    if risk_id:
        return risk_id
    return "Azure architecture risk guidance"


def _kr_compact_terms(values: list[str], *, max_items: int = 3) -> list[str]:
    compact: list[str] = []
    seen: set[str] = set()
    for raw in values:
        value = re.sub(r"\s+", " ", str(raw or "").strip())
        if not value:
            continue
        key = value.lower()
        if key in seen:
            continue
        seen.add(key)
        compact.append(value)
        if len(compact) >= max_items:
            break
    return compact


def _kr_issue_terms(risk: Mapping[str, Any], *, max_items: int = 3) -> list[str]:
    related = risk.get("related_issues") if isinstance(risk.get("related_issues"), list) else []
    normalized: list[str] = []
    for item in related:
        token = str(item or "").strip().lower()
        if not token:
            continue
        token = re.sub(r"[-_:]+", " ", token)
        token = re.sub(r"\s+", " ", token).strip()
        if token:
            normalized.append(token)
    return _kr_compact_terms(normalized, max_items=max_items)


def _kr_resource_context(
    risk: Mapping[str, Any],
    *,
    resource_lookup: Mapping[str, Mapping[str, Any]],
    architecture_resource_types: list[str],
) -> tuple[list[str], list[str]]:
    resource_ids = risk.get("resources") if isinstance(risk.get("resources"), list) else []
    resource_types: list[str] = []
    resource_names: list[str] = []

    for resource_id in resource_ids:
        row = resource_lookup.get(str(resource_id)) if isinstance(resource_lookup, Mapping) else None
        if not isinstance(row, Mapping):
            continue
        resource_type = str(row.get("type") or "").strip()
        resource_name = str(row.get("name") or "").strip()
        if resource_type:
            resource_types.append(resource_type)
        if resource_name:
            resource_names.append(resource_name)

    compact_types = _kr_compact_terms(resource_types, max_items=3)
    compact_names = _kr_compact_terms(resource_names, max_items=2)

    if not compact_types:
        compact_types = _kr_compact_terms([str(item) for item in architecture_resource_types], max_items=2)

    return compact_types, compact_names


def _kr_build_query_for_risk(
    risk: Mapping[str, Any],
    *,
    resource_lookup: Mapping[str, Mapping[str, Any]],
    architecture_resource_types: list[str],
) -> str:
    title = _kr_normalize_risk_title(risk)
    category = str(risk.get("category") or "").strip().lower()
    severity = str(risk.get("severity") or "").strip().lower()
    issue_terms = _kr_issue_terms(risk, max_items=3)
    resource_types, resource_names = _kr_resource_context(
        risk,
        resource_lookup=resource_lookup,
        architecture_resource_types=architecture_resource_types,
    )

    base_by_category = {
        "security": "Azure security best practices for network exposure, identity access, and threat protection",
        "reliability": "Azure reliability best practices for resilient dependencies, routing, and service continuity",
        "cost": "Azure cost optimization best practices for right-sizing, idle resource cleanup, and SKU governance",
        "performance": "Azure performance and scalability best practices for latency, throughput, and scaling",
        "operations": "Azure operational excellence best practices for governance, tagging, monitoring, and policy",
    }

    base = base_by_category.get(category, "Azure architecture best practices for resilient and secure cloud design")
    detail_parts: list[str] = [f"risk: {title}"]
    if severity in {"high", "medium", "low"}:
        detail_parts.append(f"severity: {severity}")
    if resource_types:
        detail_parts.append(f"resource types: {', '.join(resource_types)}")
    if resource_names:
        detail_parts.append(f"resource names: {', '.join(resource_names)}")
    if issue_terms:
        detail_parts.append(f"related issues: {', '.join(issue_terms)}")

    query = f"{base}; {'; '.join(detail_parts)}"
    query = re.sub(r"\s+", " ", query).strip()
    return query[:320].rstrip("; ,")


def _kr_build_topics(
    risks: list[dict[str, Any]],
    *,
    resource_lookup: Mapping[str, Mapping[str, Any]],
    architecture_resource_types: list[str],
) -> list[dict[str, Any]]:
    dedupe: dict[str, dict[str, Any]] = {}
    sorted_risks = sorted(
        risks,
        key=lambda item: (
            -_sf_severity_rank(str(item.get("severity") or "low")),
            str(item.get("id") or ""),
        ),
    )
    for risk in sorted_risks:
        if not isinstance(risk, Mapping):
            continue
        query = _kr_build_query_for_risk(
            risk,
            resource_lookup=resource_lookup,
            architecture_resource_types=architecture_resource_types,
        )
        normalized_query = re.sub(r"\s+", " ", query).strip().lower()
        if not normalized_query:
            continue

        if normalized_query not in dedupe:
            dedupe[normalized_query] = {
                "riskId": str(risk.get("id") or "").strip() or "unknown-risk",
                "title": _kr_normalize_risk_title(risk),
                "query": query.strip(),
                "documents": [],
                "_riskIds": [str(risk.get("id") or "").strip() or "unknown-risk"],
            }
        else:
            dedupe[normalized_query]["_riskIds"].append(str(risk.get("id") or "").strip() or "unknown-risk")

    topics = list(dedupe.values())
    max_topics = 12
    return topics[:max_topics]


def _kr_parse_documents_from_payload(payload: Any, *, raw_text: str) -> list[dict[str, Any]]:
    documents: list[dict[str, Any]] = []

    def _append_document(source: str, content: str, relevance: Any = None) -> None:
        clean_source = str(source or "Azure Learn").strip() or "Azure Learn"
        clean_content = re.sub(r"\s+", " ", str(content or "")).strip()
        if not clean_content:
            return
        if len(clean_content) > 700:
            clean_content = clean_content[:697].rstrip() + "..."
        row: dict[str, Any] = {
            "source": clean_source,
            "content": clean_content,
        }
        if isinstance(relevance, (int, float)):
            row["relevanceScore"] = float(relevance)
        documents.append(row)

    candidate_results: list[Any] = []
    if isinstance(payload, Mapping):
        for key in ("results", "items", "value", "documents", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                candidate_results = value
                break
    elif isinstance(payload, list):
        candidate_results = payload

    if candidate_results:
        for item in candidate_results:
            if not isinstance(item, Mapping):
                continue
            source = str(item.get("source") or item.get("url") or item.get("title") or "Azure Learn").strip()
            content = (
                item.get("content")
                or item.get("snippet")
                or item.get("summary")
                or item.get("description")
                or ""
            )
            relevance = item.get("relevanceScore")
            _append_document(source, str(content), relevance)
            if len(documents) >= 3:
                break

    if not documents:
        _append_document("Azure Learn MCP", raw_text)

    return documents[:3]


def _kr_is_tool_not_found_text(raw_text: str) -> bool:
    text = str(raw_text or "").strip().lower()
    if not text:
        return False
    return "tool" in text and "not found" in text


async def _kr_query_azure_learn_mcp(
    *,
    queries: list[str],
    settings: Mapping[str, Any],
) -> tuple[dict[str, list[dict[str, Any]]], int, int]:
    try:
        mcp_module = importlib.import_module("mcp")
        mcp_http_module = importlib.import_module("mcp.client.streamable_http")
    except ModuleNotFoundError as exc:
        raise RuntimeError("Python package 'mcp' is required for Knowledge Retrieval stage") from exc

    ClientSession = getattr(mcp_module, "ClientSession")
    streamable_http_client = getattr(mcp_http_module, "streamable_http_client")

    endpoint = str(settings.get("azureLearnMcpEndpoint") or "").strip() or "https://learn.microsoft.com/api/mcp"

    results_by_query: dict[str, list[dict[str, Any]]] = {}
    executed_queries = 0
    warning_count = 0

    async with streamable_http_client(endpoint) as (read_stream, write_stream, _):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()

            tool_candidates: list[str] = []
            explicit = [
                "microsoft_docs_search",
                "microsoft_docs_fetch",
                "microsoft_code_sample_search",
            ]

            listed_tools = []
            try:
                listed = await session.list_tools()
                listed_tools = getattr(listed, "tools", []) if listed is not None else []
            except Exception:
                listed_tools = []

            discovered_names = [
                str(getattr(tool, "name", "") or "").strip()
                for tool in listed_tools
                if str(getattr(tool, "name", "") or "").strip()
            ]
            discovered_pref = [
                name
                for name in discovered_names
                if "search" in name.lower() and ("learn" in name.lower() or "docs" in name.lower() or "microsoft" in name.lower())
            ]

            for name in explicit + discovered_pref:
                if name and name not in tool_candidates:
                    tool_candidates.append(name)

            if not tool_candidates:
                raise RuntimeError("No Microsoft Learn search tool discovered in MCP session")

            for query in queries:
                executed_queries += 1
                successful = False
                last_error = ""
                for tool_name in tool_candidates:
                    arg_variants = [
                        {"query": query},
                        {"query": query, "top": 3},
                        {"searchQuery": query},
                        {"q": query},
                    ]
                    for args in arg_variants:
                        try:
                            response = await session.call_tool(tool_name, arguments=args)
                            raw_text = _kr_extract_text(getattr(response, "content", response))
                            if _kr_is_tool_not_found_text(raw_text):
                                last_error = raw_text
                                continue
                            payload = _kr_try_json(raw_text)
                            docs = _kr_parse_documents_from_payload(payload, raw_text=raw_text)
                            results_by_query[query] = docs
                            successful = True
                            break
                        except Exception as exc:
                            last_error = f"{tool_name}: {exc}"
                    if successful:
                        break

                if not successful:
                    warning_count += 1
                    results_by_query[query] = [
                        {
                            "source": "Azure Learn MCP",
                            "content": f"MCP query failed: {last_error or 'unknown error'}",
                        }
                    ]

    return results_by_query, executed_queries, warning_count


def _run_knowledge_retrieval_stage(*, project_dir: Path, settings: Mapping[str, Any]) -> dict[str, Any]:
    log_path = _ensure_validation_log_path(project_dir)
    structured_findings_path = project_dir / "Documentation" / "structured-findings.json"
    output_path = project_dir / "Documentation" / "knowledge-base.json"
    output_artifact_ref = "/Documentation/knowledge-base.json"

    step_results: list[dict[str, Any]] = []

    def _record(step: str, status: str, message: str, *, error: str | None = None) -> None:
        level = "INFO" if status != "failed" else "ERROR"
        _append_input_verification_log(log_path, level, message)
        item: dict[str, Any] = {"step": step, "status": status}
        if error:
            item["error"] = error
        step_results.append(item)

    _append_input_verification_log(log_path, "INFO", "Knowledge Retrieval started")

    if not structured_findings_path.exists():
        reason = "Missing /Documentation/structured-findings.json. Run Structured Findings first."
        _record("load-structured-findings", "started", "Loading Structured Findings")
        _record("load-structured-findings", "failed", reason, error=reason)
        return {
            "ok": False,
            "error": reason,
            "stepResults": step_results,
            "artifactPath": output_artifact_ref,
            "status": "mcp_available",
        }

    _record("check-mcp-availability", "started", "Checking MCP Availability")
    mcp_status = "mcp_available"
    try:
        mcp_probe_results, mcp_probe_executed, mcp_probe_warnings = asyncio.run(
            _kr_query_azure_learn_mcp(
                queries=["Azure architecture best practices"],
                settings=settings,
            )
        )
        probe_docs = mcp_probe_results.get("Azure architecture best practices") if isinstance(mcp_probe_results, Mapping) else []
        has_probe_docs = isinstance(probe_docs, list) and len(probe_docs) > 0
        if mcp_probe_executed <= 0 or mcp_probe_warnings > 0 or not has_probe_docs:
            mcp_status = "mcp_unavailable"
    except Exception as exc:
        mcp_status = "mcp_unavailable"
        _append_input_verification_log(log_path, "WARN", f"Knowledge Retrieval MCP connectivity check failed: {exc}")

    _append_input_verification_log(
        log_path,
        "INFO" if mcp_status == "mcp_available" else "WARN",
        f"MCP connectivity: {'available' if mcp_status == 'mcp_available' else 'unavailable'}",
    )
    _record(
        "check-mcp-availability",
        "completed",
        "MCP Availability Checked",
    )

    if mcp_status == "mcp_unavailable":
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps({"topics": [], "status": "mcp_unavailable"}, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        _append_input_verification_log(log_path, "INFO", "Knowledge Retrieval completed")
        return {
            "ok": True,
            "artifactPath": output_artifact_ref,
            "stepResults": step_results,
            "topics": [],
            "status": "mcp_unavailable",
        }

    _record("load-structured-findings", "started", "Loading Structured Findings")

    structured_findings = read_json_file(structured_findings_path, {})
    if not isinstance(structured_findings, Mapping):
        reason = "Invalid structured findings artifact structure."
        _record("load-structured-findings", "failed", reason, error=reason)
        return {
            "ok": False,
            "error": reason,
            "stepResults": step_results,
            "artifactPath": output_artifact_ref,
            "status": "mcp_available",
        }

    risks = structured_findings.get("risks") if isinstance(structured_findings.get("risks"), list) else []
    resources = structured_findings.get("resources") if isinstance(structured_findings.get("resources"), list) else []
    architecture_summary = structured_findings.get("architecture_summary") if isinstance(structured_findings.get("architecture_summary"), Mapping) else {}
    architecture_resource_types = architecture_summary.get("resourceTypes") if isinstance(architecture_summary.get("resourceTypes"), list) else []
    resource_lookup: dict[str, dict[str, Any]] = {}
    for item in resources:
        if not isinstance(item, Mapping):
            continue
        resource_id = str(item.get("id") or "").strip()
        if not resource_id:
            continue
        resource_lookup[resource_id] = {
            "type": str(item.get("type") or "").strip(),
            "name": str(item.get("name") or "").strip(),
        }
    _record("load-structured-findings", "completed", f"Loaded Structured Findings: {len(risks)} risks")

    _record("map-risks-to-topics", "started", "Mapping Risks to Topics")
    topics = _kr_build_topics(
        [dict(item) for item in risks if isinstance(item, Mapping)],
        resource_lookup=resource_lookup,
        architecture_resource_types=[str(item) for item in architecture_resource_types if str(item).strip()],
    )
    _append_input_verification_log(log_path, "INFO", f"Risks processed: {len(risks)}")
    _append_input_verification_log(log_path, "INFO", f"Topics generated: {len(topics)}")
    _record("map-risks-to-topics", "completed", f"Mapped Topics: {len(topics)}")

    _record("query-azure-learn-mcp", "started", "Querying Azure Learn MCP")
    query_list = [str(topic.get("query") or "").strip() for topic in topics if str(topic.get("query") or "").strip()]
    query_results: dict[str, list[dict[str, Any]]] = {}
    executed_queries = 0
    warning_count = 0
    if query_list:
        try:
            query_results, executed_queries, warning_count = asyncio.run(
                _kr_query_azure_learn_mcp(
                    queries=query_list,
                    settings=settings,
                )
            )
        except Exception as exc:
            warning_count = max(warning_count, 1)
            _append_input_verification_log(log_path, "WARN", f"Knowledge Retrieval MCP call failed: {exc}")
            for query in query_list:
                query_results[query] = [
                    {
                        "source": "Azure Learn MCP",
                        "content": f"MCP unavailable for this query: {exc}",
                    }
                ]
            executed_queries = len(query_list)

    _append_input_verification_log(log_path, "INFO", f"MCP queries executed: {executed_queries}")
    if warning_count > 0:
        _append_input_verification_log(log_path, "WARN", f"MCP query warnings: {warning_count}")
    _record("query-azure-learn-mcp", "completed", f"MCP Queries Executed: {executed_queries}")

    _record("process-results", "started", "Processing Results")
    output_topics: list[dict[str, Any]] = []
    for topic in topics:
        query = str(topic.get("query") or "").strip()
        documents = query_results.get(query) if isinstance(query_results.get(query), list) else []
        output_topics.append(
            {
                "riskId": str(topic.get("riskId") or "unknown-risk"),
                "title": str(topic.get("title") or "Azure guidance topic"),
                "query": query,
                "documents": documents[:3],
            }
        )
    _record("process-results", "completed", f"Processed Topics: {len(output_topics)}")

    _record("finalize-output", "started", "Finalizing Output")
    knowledge_payload = {
        "topics": output_topics,
        "status": "mcp_available",
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(knowledge_payload, indent=2, ensure_ascii=False), encoding="utf-8")
    _record("finalize-output", "completed", f"Knowledge base written to {output_artifact_ref}")

    _append_input_verification_log(log_path, "INFO", "Knowledge Retrieval completed")
    return {
        "ok": True,
        "artifactPath": output_artifact_ref,
        "stepResults": step_results,
        "topics": output_topics,
        "status": "mcp_available",
    }


def _manage_validation_thread_lifecycle(
    project_dir: Path,
    project_id: str,
    project_name: str,
    app_settings: dict,
) -> str:
    """
    Rolling thread manager for validation.
    
    Logic:
    1. Load project settings
    2. Get last validation thread ID from settings (if exists)
    3. Create a new validation thread
    4. If creation succeeded, delete the old thread
    5. Update project settings with new thread ID
    6. Return new thread ID
    
    Returns: New validation thread ID
    """
    # Load project settings
    project_settings = load_project_settings_file(project_dir)
    last_validation_thread_id = str(
        project_settings.get("projectValidationLastThreadId")
        or project_settings.get("projectValidationThreadId")
        or ""
    ).strip() or None
    log_path = _ensure_validation_log_path(project_dir)
    
    # Create new validation thread
    from uuid import uuid4
    new_thread_name = f"validation-{project_dir.name}-{uuid4().hex[:8]}"
    
    try:
        thread_result = ensure_project_thread_for_project(
            app_settings,
            project_id=project_id,
            known_thread_id=None,
            thread_name=new_thread_name,
        )
        new_thread_id = str(getattr(thread_result, "thread_id", "") or "").strip() or None
        if new_thread_id:
            _append_validation_log_event(
                log_path,
                activity="validation.thread.created",
                details={
                    "projectId": project_id,
                    "projectName": project_name,
                    "threadId": new_thread_id,
                    "previousThreadId": last_validation_thread_id or "",
                },
            )
    except Exception as exc:
        # If thread creation fails, continue with None (will use stateless) 
        _append_app_activity(
            "validation.thread",
            status="warning",
            project_id=project_id,
            category="validation",
            step="thread-creation-failed",
            source="backend.validation",
            details={"error": str(exc)[:200]},
        )
        _append_validation_log_event(
            log_path,
            activity="validation.thread.creation_failed",
            details={
                "projectId": project_id,
                "projectName": project_name,
                "error": str(exc)[:400],
            },
        )
        new_thread_id = None
    
    # Delete old thread if we successfully created a new one
    if new_thread_id and last_validation_thread_id:
        try:
            delete_success = delete_project_thread(app_settings, last_validation_thread_id)
            if delete_success:
                _append_app_activity(
                    "validation.thread",
                    status="info",
                    project_id=project_id,
                    category="validation",
                    step="old-thread-deleted",
                    source="backend.validation",
                    details={"oldThreadId": last_validation_thread_id},
                )
                _append_validation_log_event(
                    log_path,
                    activity="validation.thread.deleted",
                    details={
                        "projectId": project_id,
                        "projectName": project_name,
                        "threadId": last_validation_thread_id,
                    },
                )
        except Exception as exc:
            # Log cleanup failure but don't block validation
            _append_app_activity(
                "validation.thread",
                status="warning",
                project_id=project_id,
                category="validation",
                step="old-thread-deletion-failed",
                source="backend.validation",
                details={"oldThreadId": last_validation_thread_id, "error": str(exc)[:200]},
            )
            _append_validation_log_event(
                log_path,
                activity="validation.thread.delete_failed",
                details={
                    "projectId": project_id,
                    "projectName": project_name,
                    "threadId": last_validation_thread_id,
                    "error": str(exc)[:400],
                },
            )
    
    # Update project settings with new thread ID if we have one
    if new_thread_id:
        try:
            project_settings["projectValidationThreadId"] = new_thread_id
            project_settings["projectValidationLastThreadId"] = new_thread_id
            updated_settings_payload = build_project_settings_payload(
                str(project_settings.get("projectId") or project_id).strip() or project_id,
                str(project_settings.get("projectName") or project_name).strip() or project_name,
                str(project_settings.get("projectCloud") or "Azure").strip() or "Azure",
                project_settings,
            )
            settings_path = project_dir / "project.settings.env"
            settings_path.write_text(to_env_lines(updated_settings_payload), encoding="utf-8")
            _append_app_activity(
                "validation.thread",
                status="info",
                project_id=project_id,
                category="validation",
                step="settings-updated",
                source="backend.validation",
                details={"newThreadId": new_thread_id},
            )
            _append_validation_log_event(
                log_path,
                activity="validation.thread.active",
                details={
                    "projectId": project_id,
                    "projectName": project_name,
                    "threadId": new_thread_id,
                },
            )
        except Exception as exc:
            # Log settings update failure but continue with new thread
            _append_app_activity(
                "validation.thread",
                status="warning",
                project_id=project_id,
                category="validation",
                step="settings-update-failed",
                source="backend.validation",
                details={"newThreadId": new_thread_id, "error": str(exc)[:200]},
            )
            _append_validation_log_event(
                log_path,
                activity="validation.thread.settings_update_failed",
                details={
                    "projectId": project_id,
                    "projectName": project_name,
                    "threadId": new_thread_id,
                    "error": str(exc)[:400],
                },
            )
    
    return new_thread_id


@app.post("/api/project/{project_id}/validation/knowledge-retrieval")
def run_knowledge_retrieval(project_id: str):
    entry = find_project_entry(project_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Project not found")

    result = _run_knowledge_retrieval_stage(
        project_dir=entry["projectDir"],
        settings=load_app_settings(),
    )
    return result


@app.post("/api/project/{project_id}/validation/ai-validation-agent")
def run_ai_validation_agent_stage(project_id: str):
    entry = find_project_entry(project_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Project not found")

    settings = load_app_settings()
    bootstrap_result = bootstrap_default_foundry_resources(settings)
    if bootstrap_result.get("settingsUpdated"):
        write_app_settings_file(settings)

    metadata = read_json_file(entry["metadataPath"], {})
    if not isinstance(metadata, Mapping):
        metadata = {}

    project_name = str(metadata.get("name") or entry.get("name") or entry.get("id") or "Project").strip() or "Project"
    project_description = str(metadata.get("applicationDescription") or "").strip()
    canvas_state = read_json_file(entry["statePath"], {})
    if not isinstance(canvas_state, Mapping):
        canvas_state = {}

    foundry_agent_id = _resolve_foundry_validation_agent_id(settings) or None
    # Use rolling thread manager: creates fresh thread, deletes old one, updates settings
    foundry_thread_id = _manage_validation_thread_lifecycle(
        entry["projectDir"],
        entry["id"],
        project_name,
        settings,
    )

    try:
        result = run_architecture_validation_agent(
            app_settings=settings,
            canvas_state=canvas_state,
            project_name=project_name,
            project_id=entry["id"],
            project_description=project_description,
            foundry_agent_id=foundry_agent_id,
            foundry_thread_id=foundry_thread_id,
            validation_run_id=f"ai-stage-{_timestamp_ms()}-{uuid4().hex[:6]}",
            project_dir=entry["projectDir"],
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"AI Validation Agent failed: {exc}") from exc

    sources = result.get("sources") if isinstance(result.get("sources"), Mapping) else {}
    azure_mcp_state = str((sources.get("azureMcp") or {}).get("connectionState") or "partial").strip().lower()
    learn_mcp_state = str((sources.get("learnMcp") or {}).get("connectionState") or "partial").strip().lower()
    reasoning_state = str((sources.get("reasoningModel") or {}).get("connectionState") or "partial").strip().lower()

    def _step_status(connection_state: str) -> str:
        safe = str(connection_state or "").strip().lower()
        if safe in {"failed", "error"}:
            return "failed"
        return "completed"

    azure_mcp_explanation = str((sources.get("azureMcp") or {}).get("explanation") or "").strip()
    learn_mcp_explanation = str((sources.get("learnMcp") or {}).get("explanation") or "").strip()
    reasoning_explanation = str((sources.get("reasoningModel") or {}).get("explanation") or "").strip()

    waf_status = _step_status(azure_mcp_state)
    learn_status = _step_status(learn_mcp_state)
    reasoning_status = _step_status(reasoning_state)

    step_results: list[dict[str, Any]] = [
        {"step": "initialize-agent-state", "status": "completed"},
        {
            "step": "call-waf-mcp",
            "status": waf_status,
            **({"error": azure_mcp_explanation or f"WAF MCP state: {azure_mcp_state}"} if waf_status == "failed" else {}),
        },
        {
            "step": "call-learn-mcp",
            "status": learn_status,
            **({"error": learn_mcp_explanation or f"Learn MCP state: {learn_mcp_state}"} if learn_status == "failed" else {}),
        },
        {
            "step": "run-llm-reasoning",
            "status": reasoning_status,
            **({"error": reasoning_explanation or f"Reasoning model state: {reasoning_state}"} if reasoning_status == "failed" else {}),
        },
    ]

    evaluation_payload = result.get("evaluation") if isinstance(result.get("evaluation"), Mapping) else {}
    evaluation_steps = evaluation_payload.get("steps") if isinstance(evaluation_payload.get("steps"), list) else []

    def _map_iteration_state_to_status(state: str) -> str:
        safe = str(state or "").strip().lower()
        if safe in {"failed", "error"}:
            return "failed"
        if safe in {"partial", "warning"}:
            return "warning"
        if safe in {"connected", "completed", "success", "ok"}:
            return "completed"
        if safe in {"running", "in-progress"}:
            return "running"
        if safe == "skipped":
            return "skipped"
        return "not-started"

    def _format_iteration_action(name: str) -> tuple[str, str]:
        safe_name = str(name or "action").strip().lower()
        slug = re.sub(r"[^a-z0-9_-]+", "-", safe_name).strip("-") or "action"
        friendly_map = {
            "waf-mcp": "Call WAF MCP",
            "learn-mcp": "Call Learn MCP",
            "analyze": "Run LLM Reasoning",
            "agent-complete": "Complete Iteration",
            "complete": "Complete Iteration",
        }
        friendly = friendly_map.get(safe_name) or " ".join(part.capitalize() for part in slug.split("-") if part)
        return slug, friendly or "Action"

    for raw_step in evaluation_steps:
        if not isinstance(raw_step, Mapping):
            continue

        details = raw_step.get("details") if isinstance(raw_step.get("details"), Mapping) else {}
        raw_iteration = details.get("iteration") if details else None
        if raw_iteration is None:
            raw_iteration = raw_step.get("iteration")

        try:
            iteration_number = int(raw_iteration)
        except Exception:
            continue

        if iteration_number <= 0:
            continue

        action_slug, action_label = _format_iteration_action(str(raw_step.get("name") or ""))
        step_results.append(
            {
                "step": f"iteration-{iteration_number}-{action_slug}",
                "status": _map_iteration_state_to_status(str(raw_step.get("state") or "")),
                "message": str(raw_step.get("explanation") or "").strip(),
                "iteration": iteration_number,
                "action": action_label,
                "label": f"Iteration {iteration_number} · {action_label}",
            }
        )

    step_results.append({"step": "finalize-output", "status": "completed"})

    report_payload = result.get("final_intelligent_report") if isinstance(result.get("final_intelligent_report"), Mapping) else {}
    if isinstance(report_payload, Mapping):
        final_report_json_path = entry["projectDir"] / "Documentation" / "final-report.json"
        try:
            final_report_json_path.write_text(json.dumps(report_payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    artifact_path = "/Documentation/final_intelligent_report.json"
    return {
        "ok": bool(result.get("ok")),
        "artifactPath": artifact_path,
        "stepResults": step_results,
        "status": "completed",
        "runId": str(result.get("runId") or ""),
        "evaluation": evaluation_payload,
        "report": report_payload,
    }


def _final_report_required_sections() -> list[str]:
    return [
        "architecture_summary",
        "configuration_issues",
        "architecture_antipatterns",
        "recommended_patterns",
        "missing_capabilities",
        "pillar_assessment",
        "priority_improvements",
        "quick_configuration_fixes",
    ]


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if value in (None, ""):
        return []
    return [value]


def _as_text(value: Any) -> str:
    return str(value or "").strip()


def _render_bullets(values: list[Any], *, fallback: str = "Not provided") -> list[str]:
    items = [_as_text(value) for value in values if _as_text(value)]
    if not items:
        return [f"- {fallback}"]
    return [f"- {item}" for item in items]


def _normalize_report_text(value: Any, *, as_sentence: bool = False) -> str:
    text = _as_text(value)
    if not text:
        return ""
    normalized = re.sub(r"\s+", " ", text).strip()
    if "..." in normalized:
        normalized = normalized.replace("...", "\u2026")
    if as_sentence and normalized and normalized[-1] not in ".!?":
        normalized = f"{normalized}."
    return normalized


def _normalize_key(value: Any) -> str:
    return re.sub(r"\s+", " ", _normalize_report_text(value).lower()).strip()


def _dedupe_by_key(rows: list[Any], key_builder) -> list[Any]:
    seen: set[str] = set()
    output: list[Any] = []
    for row in rows:
        key = str(key_builder(row) or "").strip()
        if not key or key in seen:
            continue
        seen.add(key)
        output.append(row)
    return output


def _pillar_label(value: Any) -> str:
    raw = _normalize_key(value)
    mapping = {
        "reliability": "Reliability",
        "security": "Security",
        "cost_optimization": "Cost Optimization",
        "cost optimization": "Cost Optimization",
        "operational_excellence": "Operational Excellence",
        "operational excellence": "Operational Excellence",
        "performance_efficiency": "Performance Efficiency",
        "performance efficiency": "Performance Efficiency",
    }
    return mapping.get(raw, _normalize_report_text(value) or "Uncategorized")


def _build_resource_display_lookup(project_dir: Path) -> dict[str, dict[str, str]]:
    state_path = project_dir / "Architecture" / "canvas.state.json"
    state_payload = read_json_file(state_path, {})
    if not isinstance(state_payload, Mapping):
        return {}

    items = state_payload.get("canvasItems") if isinstance(state_payload.get("canvasItems"), list) else []
    lookup: dict[str, dict[str, str]] = {}
    for item in items:
        if not isinstance(item, Mapping):
            continue
        resource_id = _as_text(item.get("id"))
        if not resource_id:
            continue
        resource_name = _as_text(item.get("name"))
        resource_type = _as_text(item.get("resourceType") or item.get("resourceName") or item.get("type"))
        lookup[resource_id] = {
            "name": resource_name,
            "type": resource_type,
        }
    return lookup


def _format_resource_reference(value: Any, resource_lookup: Mapping[str, Any]) -> str:
    resource_id = _normalize_report_text(value)
    if not resource_id:
        return "Not specified"

    if _normalize_key(resource_id) == "global":
        return "Global (global)"

    resource_entry = resource_lookup.get(resource_id)
    resource_name = ""
    resource_type = ""
    if isinstance(resource_entry, Mapping):
        resource_name = _normalize_report_text(resource_entry.get("name"))
        resource_type = _normalize_report_text(resource_entry.get("type"))
    elif resource_entry is not None:
        resource_name = _normalize_report_text(resource_entry)

    if not resource_name:
        resource_name = resource_id

    if resource_type:
        return f"{resource_type}: {resource_name} ({resource_id})"

    if _normalize_key(resource_name) != _normalize_key(resource_id):
        return f"{resource_name} ({resource_id})"
    return resource_id


def _format_final_report_markdown(
    payload: Mapping[str, Any],
    *,
    resource_lookup: Mapping[str, Any] | None = None,
    project_description: str = "",
) -> str:
    safe_resource_lookup = resource_lookup if isinstance(resource_lookup, Mapping) else {}
    lines: list[str] = [
        "# Azure Architecture Validation Report",
        "",
        "## Executive Summary",
    ]

    summary = _as_text(payload.get("architecture_summary"))
    if summary:
        lines.append(summary)
    else:
        lines.append("No executive summary provided.")

    safe_project_description = _normalize_report_text(project_description, as_sentence=True)
    if safe_project_description:
        lines.extend(["", f"Project Description: {safe_project_description}"])

    lines.extend(["", "## Detected Services"])
    services = _as_list(payload.get("detected_services"))
    if not services:
        lines.append("- None")
    else:
        for service in services:
            if isinstance(service, Mapping):
                name = _as_text(service.get("name") or service.get("service") or service.get("type"))
                detail = _as_text(service.get("description") or service.get("role"))
                lines.append(f"- {name or 'Service'}{f': {detail}' if detail else ''}")
            else:
                lines.append(f"- {_as_text(service)}")

    lines.extend(["", "## Well-Architected Assessment"])
    pillar_assessment = payload.get("pillar_assessment")
    if isinstance(pillar_assessment, Mapping) and pillar_assessment:
        for pillar_name, pillar_data in pillar_assessment.items():
            pillar_label = _pillar_label(pillar_name)
            details = pillar_data if isinstance(pillar_data, Mapping) else {}
            status = _as_text(details.get("status") or details.get("score") or details.get("rating") or "Not provided")
            findings_value = details.get("findings")
            findings_count = "Not provided" if findings_value is None else str(findings_value).strip()
            if not findings_count:
                findings_count = "Not provided"
            top_recommendations = _as_list(details.get("top_recommendations"))

            strengths = _as_list(details.get("strengths"))
            weaknesses = _as_list(details.get("weaknesses"))
            recommendations = _as_list(details.get("recommendations"))

            if not strengths and status.lower() in {"acceptable", "good", "healthy", "passed"}:
                strengths = ["Current architecture controls are broadly aligned for this pillar."]

            if not weaknesses and status.lower() in {"needs_improvement", "needs-improvement", "warning", "poor", "critical"}:
                weaknesses = top_recommendations

            if not recommendations:
                recommendations = top_recommendations

            strength_lines = [
                f"  - {item}"
                for item in (_normalize_report_text(v, as_sentence=True) for v in strengths)
                if item
            ]
            weakness_lines = [
                f"  - {item}"
                for item in (_normalize_report_text(v, as_sentence=True) for v in weaknesses)
                if item
            ]
            recommendation_lines = [
                f"  - {item}"
                for item in (_normalize_report_text(v, as_sentence=True) for v in recommendations)
                if item
            ]

            weakness_keys = {_normalize_key(item) for item in weakness_lines}
            recommendation_keys = {_normalize_key(item) for item in recommendation_lines}
            if weakness_keys and recommendation_keys and weakness_keys == recommendation_keys:
                recommendation_lines = ["  - No additional distinct recommendations were supplied beyond the weaknesses listed above."]

            if not strength_lines:
                strength_lines = ["  - No explicit strengths were supplied for this pillar in the current payload."]
            if not weakness_lines:
                if str(findings_count).strip() == "0":
                    weakness_lines = ["  - No weaknesses were identified for this pillar in this run."]
                else:
                    weakness_lines = ["  - Weakness details were not explicitly provided; review associated findings for this pillar."]
            if not recommendation_lines:
                if str(findings_count).strip() == "0":
                    recommendation_lines = ["  - No corrective recommendations are required at this time. Continue monitoring this pillar."]
                else:
                    recommendation_lines = ["  - Recommendations were not explicitly supplied; use next-step sections for remediation planning."]

            lines.extend(
                [
                    f"### {pillar_label}",
                    f"- Status: {status}",
                    f"- Findings: {findings_count}",
                    "- Strengths:",
                    *strength_lines,
                    "- Weaknesses:",
                    *weakness_lines,
                    "- Recommendations:",
                    *recommendation_lines,
                ]
            )
    else:
        lines.append("- Not provided")

    lines.extend(["", "## Scenario findings"])
    scenario_findings = _as_list(payload.get("scenario_findings"))
    if scenario_findings:
        for index, item in enumerate(scenario_findings, start=1):
            if isinstance(item, Mapping):
                scenario_title = _normalize_report_text(item.get("scenario") or item.get("title"), as_sentence=True) or f"Scenario {index}."
                architecture_path = _normalize_report_text(item.get("architecture_path") or item.get("path") or "End-to-end architecture flow", as_sentence=True)
                priority = _normalize_report_text(item.get("priority")) or "Not specified"
                impact = _normalize_report_text(item.get("impact"), as_sentence=True) or "Not specified."
                recommendation = _normalize_report_text(item.get("recommendation"), as_sentence=True) or "Not specified."
                focus_pillars = _dedupe_by_key(_as_list(item.get("focus_pillars")), lambda value: _normalize_key(value))
                controls = _dedupe_by_key(_as_list(item.get("existing_controls")), lambda value: _normalize_key(value))
                gaps = _dedupe_by_key(_as_list(item.get("gaps")), lambda value: _normalize_key(value))
                evidence_resources = _dedupe_by_key(_as_list(item.get("evidence_resources")), lambda value: _normalize_key(value))
            else:
                scenario_title = _normalize_report_text(item, as_sentence=True) or f"Scenario {index}."
                architecture_path = "End-to-end architecture flow."
                priority = "Not specified"
                impact = "Not specified."
                recommendation = "Not specified."
                focus_pillars = []
                controls = []
                gaps = []
                evidence_resources = []

            pillar_lines = [f"  - {_pillar_label(value)}" for value in focus_pillars if _normalize_report_text(value)]
            control_lines = [f"  - {_normalize_report_text(value, as_sentence=True)}" for value in controls if _normalize_report_text(value)]
            gap_lines = [f"  - {_normalize_report_text(value, as_sentence=True)}" for value in gaps if _normalize_report_text(value)]
            evidence_lines = [f"  - {_normalize_report_text(value)}" for value in evidence_resources if _normalize_report_text(value)]

            if not pillar_lines:
                pillar_lines = ["  - Not specified"]
            if not control_lines:
                control_lines = ["  - No explicit controls were supplied."]
            if not gap_lines:
                gap_lines = ["  - No explicit scenario-level gaps were supplied."]
            if not evidence_lines:
                evidence_lines = ["  - Not specified"]

            lines.extend(
                [
                    f"### {index}. {scenario_title}",
                    f"- Architecture Path: {architecture_path}",
                    f"- Priority: {priority}",
                    f"- Impact: {impact}",
                    "- Focus Pillars:",
                    *pillar_lines,
                    "- Existing Controls:",
                    *control_lines,
                    "- Gaps:",
                    *gap_lines,
                    f"- Recommendation: {recommendation}",
                    "- Evidence Resources:",
                    *evidence_lines,
                ]
            )
    else:
        lines.extend(
            [
                "- No scenario-level findings were supplied in this run.",
                "- Add scenario findings to capture end-to-end architecture risks (ingress flow, failover behavior, and operational response).",
            ]
        )

    lines.extend(["", "## Issues and Anti-Pattern"])
    unified_issues: dict[str, dict[str, Any]] = {}

    for issue in _as_list(payload.get("configuration_issues")):
        if isinstance(issue, Mapping):
            topic = _normalize_report_text(issue.get("issue") or issue.get("title") or issue.get("description"), as_sentence=True) or "Not specified."
            impact = _normalize_report_text(issue.get("impact") or issue.get("risk"), as_sentence=True) or "Not specified."
            recommendation = _normalize_report_text(issue.get("resolution") or issue.get("recommendation") or issue.get("fix"), as_sentence=True) or "Not specified."
            affected = _format_resource_reference(
                issue.get("resource") or issue.get("resource_name") or issue.get("target"),
                safe_resource_lookup,
            )
        else:
            topic = _normalize_report_text(issue, as_sentence=True) or "Not specified."
            impact = "Not specified."
            recommendation = "Not specified."
            affected = "Not specified"

        key = _normalize_key(topic)
        if key not in unified_issues:
            unified_issues[key] = {
                "topic": topic,
                "impact": impact,
                "recommendation": recommendation,
                "affected": [],
            }
        if len(impact) > len(unified_issues[key]["impact"]):
            unified_issues[key]["impact"] = impact
        if len(recommendation) > len(unified_issues[key]["recommendation"]):
            unified_issues[key]["recommendation"] = recommendation
        unified_issues[key]["affected"].append(affected)

    for item in _as_list(payload.get("architecture_antipatterns")):
        if isinstance(item, Mapping):
            topic = _normalize_report_text(item.get("name") or item.get("title"), as_sentence=True) or "Anti-pattern."
            impact = _normalize_report_text(item.get("risk") or item.get("impact"), as_sentence=True) or "Not specified."
            recommendation = _normalize_report_text(item.get("recommendation") or item.get("resolution"), as_sentence=True) or "Not specified."
            affected_components = _as_list(item.get("affected_components"))
        else:
            topic = _normalize_report_text(item, as_sentence=True) or "Anti-pattern."
            impact = "Not specified."
            recommendation = "Refactor this pattern to align with Azure Well-Architected recommendations."
            affected_components = []

        key = _normalize_key(topic)
        if key not in unified_issues:
            unified_issues[key] = {
                "topic": topic,
                "impact": impact,
                "recommendation": recommendation,
                "affected": [],
            }
        if len(impact) > len(unified_issues[key]["impact"]):
            unified_issues[key]["impact"] = impact
        if len(recommendation) > len(unified_issues[key]["recommendation"]):
            unified_issues[key]["recommendation"] = recommendation
        unified_issues[key]["affected"].extend(
            [
                _format_resource_reference(value, safe_resource_lookup)
                for value in affected_components
                if _normalize_report_text(value)
            ]
        )

    if not unified_issues:
        lines.append("- No issues or anti-patterns were detected in this validation run.")
    else:
        ranked_groups: list[dict[str, Any]] = []
        for group in unified_issues.values():
            affected = _dedupe_by_key(group.get("affected") or [], lambda value: _normalize_key(value))
            has_specific_resource = any(
                _normalize_key(value) not in {"", "not specified", "global (global)", "global"}
                for value in affected
            )
            ranked_groups.append({
                "group": group,
                "affected": affected,
                "has_specific_resource": has_specific_resource,
            })

        ranked_groups.sort(
            key=lambda item: (
                0 if item["has_specific_resource"] else 1,
                -len(item["affected"]),
                _normalize_key(item["group"].get("topic")),
            )
        )

        max_issue_items = 12
        visible_groups = ranked_groups[:max_issue_items]
        hidden_count = max(0, len(ranked_groups) - len(visible_groups))

        for index, item in enumerate(visible_groups, start=1):
            group = item["group"]
            affected = item["affected"]
            affected = _dedupe_by_key(group.get("affected") or [], lambda value: _normalize_key(value))
            affected_lines = [f"  - {value}" for value in affected] if affected else ["  - Not specified"]
            lines.extend(
                [
                    f"### {index}. {group['topic']}",
                    f"- Impact: {group['impact']}",
                    f"- Recommendation: {group['recommendation']}",
                    "- Affected Resources:",
                    *affected_lines,
                ]
            )

        if hidden_count:
            lines.append(f"- Additional findings consolidated: {hidden_count} similar issue(s) were omitted here to keep this section focused. See final-report.json for full detail.")

    lines.extend(["", "## Recommended Patterns"])
    recommended_patterns = _as_list(payload.get("recommended_patterns"))
    deduped_patterns = _dedupe_by_key(
        recommended_patterns,
        lambda item: "|".join(
            [
                _normalize_key(item.get("name") if isinstance(item, Mapping) else item),
                _normalize_key(item.get("reason") if isinstance(item, Mapping) else ""),
            ]
        ),
    )
    if not deduped_patterns:
        lines.append("- No additional architecture pattern substitutions are required at this stage.")
    else:
        for index, pattern in enumerate(deduped_patterns, start=1):
            if isinstance(pattern, Mapping):
                name = _normalize_report_text(pattern.get("name"), as_sentence=True) or f"Pattern {index}."
                reason = _normalize_report_text(pattern.get("reason"), as_sentence=True) or "No rationale provided."
                components = _dedupe_by_key(_as_list(pattern.get("components")), lambda value: _normalize_key(value))
                azure_services = _dedupe_by_key(_as_list(pattern.get("azure_services")), lambda value: _normalize_key(value))
            else:
                name = _normalize_report_text(pattern, as_sentence=True) or f"Pattern {index}."
                reason = "No rationale provided."
                components = []
                azure_services = []

            component_lines = [
                f"  - {_normalize_report_text(value)}"
                for value in components
                if _normalize_report_text(value)
            ]
            if not component_lines:
                component_lines = ["  - Not specified"]

            azure_service_lines = [
                f"  - {_normalize_report_text(value)}"
                for value in azure_services
                if _normalize_report_text(value)
            ]
            if not azure_service_lines:
                azure_service_lines = ["  - Not specified"]

            lines.extend(
                [
                    f"### {index}. {name}",
                    f"- Why: {reason}",
                    "- Components in Scope:",
                    *component_lines,
                    "- Azure Services:",
                    *azure_service_lines,
                ]
            )

    lines.extend(["", "## Questions to think about next"])
    questions = _dedupe_by_key(
        _as_list(
            payload.get("questions_to_think_about_next")
            or payload.get("questions_to_think_about")
            or payload.get("open_questions")
        ),
        lambda value: _normalize_key(value),
    )
    if not questions:
        lines.extend(
            [
                "- What scale profile (peak traffic and growth) should this architecture be sized for in the next 12 months?",
                "- Which compliance controls require explicit evidence collection and audit trails in this design?",
                "- Which failure scenarios require automated failover, and what are the RTO/RPO targets?",
            ]
        )
    else:
        lines.extend(
            [
                f"- {_normalize_report_text(question, as_sentence=True)}"
                for question in questions
                if _normalize_report_text(question)
            ]
        )

    lines.extend(["", "## What to do next", "", "### Priority Improvements"])
    priority_improvements_raw = _as_list(payload.get("priority_improvements"))
    priority_improvements = _dedupe_by_key(
        priority_improvements_raw,
        lambda item: "|".join(
            [
                _normalize_key(item.get("title") if isinstance(item, Mapping) else item),
                _normalize_key(item.get("pillar") if isinstance(item, Mapping) else ""),
                _normalize_key(item.get("description") if isinstance(item, Mapping) else ""),
            ]
        ),
    )
    if not priority_improvements:
        lines.append("- No priority improvements were identified after consolidation and de-duplication.")
    else:
        for index, item in enumerate(priority_improvements, start=1):
            if isinstance(item, Mapping):
                rank = str(index)
                title = _normalize_report_text(item.get("title"), as_sentence=True) or "Improvement item."
                description = _normalize_report_text(item.get("description"), as_sentence=True) or "No description provided."
                pillar = _pillar_label(item.get("pillar"))
                impact = _normalize_report_text(item.get("impact")) or "Not specified"
                effort = _normalize_report_text(item.get("effort")) or "Not specified"
                services = _dedupe_by_key(_as_list(item.get("azure_services")), lambda value: _normalize_key(value))
            else:
                rank = str(index)
                title = _normalize_report_text(item, as_sentence=True) or "Improvement item."
                description = "No description provided."
                pillar = "Uncategorized"
                impact = "Not specified"
                effort = "Not specified"
                services = []

            service_lines = [
                f"    - {_normalize_report_text(value)}"
                for value in services
                if _normalize_report_text(value)
            ]
            if not service_lines:
                service_lines = ["    - Not specified"]

            lines.extend(
                [
                    f"- {rank}. {title}",
                    f"  - Category: {pillar}",
                    f"  - Why It Matters: {description}",
                    f"  - Impact: {impact}",
                    f"  - Effort: {effort}",
                    "  - Related Azure Services:",
                    *service_lines,
                ]
            )

    lines.extend(["", "### Quick Fixes"])
    quick_fixes_raw = _as_list(payload.get("quick_configuration_fixes"))
    quick_fixes = _dedupe_by_key(
        quick_fixes_raw,
        lambda item: "|".join(
            [
                _normalize_key(item.get("title") if isinstance(item, Mapping) else item),
                _normalize_key(item.get("resource") if isinstance(item, Mapping) else ""),
                _normalize_key(item.get("current_state") if isinstance(item, Mapping) else ""),
            ]
        ),
    )
    if not quick_fixes:
        lines.append("- No quick fixes were required from the current configuration posture.")
    else:
        for index, item in enumerate(quick_fixes, start=1):
            if isinstance(item, Mapping):
                title = _normalize_report_text(item.get("title"), as_sentence=True) or "Quick fix item."
                resource = _format_resource_reference(item.get("resource"), safe_resource_lookup)
                current_state = _normalize_report_text(item.get("current_state"), as_sentence=True) or "Not specified."
                target_state = _normalize_report_text(item.get("target_state"), as_sentence=True) or "Not specified."
                impact = _normalize_report_text(item.get("impact")) or "Not specified"
            else:
                title = _normalize_report_text(item, as_sentence=True) or "Quick fix item."
                resource = "Not specified"
                current_state = "Not specified."
                target_state = "Not specified."
                impact = "Not specified"

            lines.extend(
                [
                    f"- {index}. {title}",
                    f"  - Resource: {resource}",
                    f"  - Current State: {current_state}",
                    f"  - Target State: {target_state}",
                    f"  - Impact: {impact}",
                ]
            )

    return "\n".join(lines).rstrip() + "\n"


def _run_final_report_stage(*, project_dir: Path) -> dict[str, Any]:
    log_path = _ensure_validation_log_path(project_dir)
    input_path = project_dir / "Documentation" / "final-report.json"
    output_path = project_dir / "Documentation" / "final-report.md"

    _append_input_verification_log(log_path, "INFO", "Final Report started")

    step_results: list[dict[str, Any]] = []
    step_results.append({"step": "load-ai-output", "status": "started"})

    if not input_path.exists():
        reason = "Input file not found: /Documentation/final-report.json"
        step_results.append({"step": "load-ai-output", "status": "failed", "error": reason})
        return {"ok": False, "errors": [reason], "stepResults": step_results}

    try:
        payload = json.loads(input_path.read_text(encoding="utf-8"))
    except Exception as exc:
        reason = f"Invalid JSON in /Documentation/final-report.json: {exc}"
        step_results.append({"step": "load-ai-output", "status": "failed", "error": reason})
        return {"ok": False, "errors": [reason], "stepResults": step_results}

    if not isinstance(payload, Mapping):
        reason = "Invalid JSON root: expected object"
        step_results.append({"step": "load-ai-output", "status": "failed", "error": reason})
        return {"ok": False, "errors": [reason], "stepResults": step_results}

    _append_input_verification_log(log_path, "INFO", "AI output loaded")
    step_results.append({"step": "load-ai-output", "status": "completed"})

    required_sections = _final_report_required_sections()
    missing_sections = [section for section in required_sections if section not in payload]
    if missing_sections:
        reason = f"Missing required fields: {', '.join(missing_sections)}"
        step_results.append({"step": "format-report", "status": "failed", "error": reason})
        return {"ok": False, "errors": [reason], "stepResults": step_results}

    step_results.append({"step": "format-report", "status": "started"})
    resource_lookup = _build_resource_display_lookup(project_dir)
    metadata_path = project_dir / "Architecture" / "project.metadata.json"
    metadata = read_json_file(metadata_path, {})
    project_description = ""
    if isinstance(metadata, Mapping):
        project_description = _as_text(metadata.get("applicationDescription"))

    markdown_text = _format_final_report_markdown(
        payload,
        resource_lookup=resource_lookup,
        project_description=project_description,
    )
    step_results.append({"step": "format-report", "status": "completed"})

    step_results.append({"step": "generate-artifacts", "status": "started"})
    output_path.write_text(markdown_text, encoding="utf-8")
    step_results.append({"step": "generate-artifacts", "status": "completed"})

    _append_input_verification_log(log_path, "INFO", "Markdown report generated")
    _append_input_verification_log(log_path, "INFO", "Final Report completed")

    return {
        "ok": True,
        "artifactPath": "/Documentation/final-report.md",
        "inputPath": "/Documentation/final-report.json",
        "stepResults": step_results,
    }


@app.post("/api/project/{project_id}/validation/final-report")
def run_final_report_stage(project_id: str):
    entry = find_project_entry(project_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Project not found")

    result = _run_final_report_stage(project_dir=entry["projectDir"])
    if not result.get("ok"):
        errors = result.get("errors") if isinstance(result.get("errors"), list) else []
        detail = str(errors[0] if errors else "Final Report stage failed")
        raise HTTPException(status_code=400, detail=detail)
    return result


@app.get("/api/project/{project_id}/validation/final-report/download")
def download_final_report_markdown(project_id: str):
    entry = find_project_entry(project_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Project not found")

    report_path = entry["projectDir"] / "Documentation" / "final-report.md"
    if not report_path.exists() or not report_path.is_file():
        raise HTTPException(status_code=404, detail="Final report not found")

    file_stem = sanitize_segment(str(entry.get("name") or entry["id"]), "project")
    file_name = f"{file_stem}-final-report.md"

    return FileResponse(
        report_path,
        media_type="text/markdown; charset=utf-8",
        filename=file_name,
    )


@app.get("/api/project/{project_id}/validation/final-report/content")
def get_final_report_content(project_id: str):
    entry = find_project_entry(project_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Project not found")

    report_path = entry["projectDir"] / "Documentation" / "final-report.md"
    artifact_path = "/Documentation/final-report.md"
    if not report_path.exists() or not report_path.is_file():
        return {
            "ok": True,
            "exists": False,
            "projectId": entry["id"],
            "artifactPath": artifact_path,
            "title": "",
            "content": "",
        }

    try:
        content = report_path.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to read final report: {exc}") from exc

    title = ""
    for raw_line in content.splitlines():
        line = str(raw_line or "").strip()
        if line.startswith("# "):
            title = line[2:].strip()
            break

    return {
        "ok": True,
        "exists": True,
        "projectId": entry["id"],
        "artifactPath": artifact_path,
        "title": title,
        "content": content,
    }


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
        "allowWarnings": bool(task.get("allowWarnings", True)),
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


def _create_iac_task(project_id: str, parameter_format: str, allow_warnings: bool) -> dict:
    now = _timestamp_ms()
    task_id = uuid4().hex
    payload = {
        "taskId": task_id,
        "projectId": str(project_id or "").strip(),
        "status": "queued",
        "message": "Generation queued",
        "progress": 1,
        "parameterFormat": normalize_parameter_format(parameter_format),
        "allowWarnings": bool(allow_warnings),
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
            "allowWarnings": payload["allowWarnings"],
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
    current_key = None
    for line in lines:
        stripped = line.rstrip("\r\n")
        if not stripped or stripped.strip().startswith("#"):
            continue
        if "=" in stripped:
            key, value = stripped.split("=", 1)
            current_key = snake_to_camel(key.strip())
            payload[current_key] = value.rstrip()
        elif current_key:
            # Multiline value: append with newline
            payload[current_key] += "\n" + stripped
    return payload


def load_project_settings_file(project_dir: Path) -> dict:
    target = project_dir / "project.settings.env"
    settings = parse_env_file(target)
    return settings if isinstance(settings, dict) else {}


def merge_project_settings(existing: dict, incoming: dict) -> dict:
    existing = existing if isinstance(existing, dict) else {}
    incoming = incoming if isinstance(incoming, dict) else {}
    merged = {**existing, **incoming}

    existing_chat_thread = str(existing.get("projectChatThreadId") or existing.get("projectThreadId") or "").strip()
    incoming_chat_thread = str(incoming.get("projectChatThreadId") or incoming.get("projectThreadId") or "").strip()
    if incoming_chat_thread:
        merged["projectChatThreadId"] = incoming_chat_thread
        merged["projectThreadId"] = incoming_chat_thread
    elif existing_chat_thread:
        merged["projectChatThreadId"] = existing_chat_thread
        merged["projectThreadId"] = existing_chat_thread
    else:
        merged.pop("projectChatThreadId", None)
        merged.pop("projectThreadId", None)

    existing_validation_thread = str(existing.get("projectValidationThreadId") or "").strip()
    incoming_validation_thread = str(incoming.get("projectValidationThreadId") or "").strip()
    if incoming_validation_thread:
        merged["projectValidationThreadId"] = incoming_validation_thread
    elif existing_validation_thread:
        merged["projectValidationThreadId"] = existing_validation_thread
    else:
        merged.pop("projectValidationThreadId", None)

    # Preserve last validation thread — always keep existing value, never let saves wipe it
    existing_last_val_thread = str(existing.get("projectValidationLastThreadId") or "").strip()
    incoming_last_val_thread = str(incoming.get("projectValidationLastThreadId") or "").strip()
    if incoming_last_val_thread:
        merged["projectValidationLastThreadId"] = incoming_last_val_thread
    elif existing_last_val_thread:
        merged["projectValidationLastThreadId"] = existing_last_val_thread
    else:
        merged.pop("projectValidationLastThreadId", None)

    return merged


def _normalize_project_foundry_thread_purpose(value: str | None) -> str:
    safe_value = str(value or "chat").strip().lower()
    if safe_value in PROJECT_FOUNDRY_THREAD_FIELDS:
        return safe_value
    return "chat"


def _project_foundry_thread_fields(purpose: str | None = None) -> dict[str, str | None]:
    return PROJECT_FOUNDRY_THREAD_FIELDS[_normalize_project_foundry_thread_purpose(purpose)]


def _build_project_foundry_thread_name(project_id: str, purpose: str | None = None) -> str:
    safe_project_id = str(project_id or "").strip()
    safe_purpose = _normalize_project_foundry_thread_purpose(purpose)
    if safe_purpose == "chat":
        return f"{safe_project_id}-chat"
    return f"{safe_project_id}-{safe_purpose}"


def _thread_contains_validation_history(
    app_settings: Mapping[str, Any],
    thread_id: str | None,
    limit: int = 120,
) -> bool:
    safe_thread_id = str(thread_id or "").strip()
    if not safe_thread_id:
        return False

    result = list_thread_messages(app_settings, thread_id=safe_thread_id, limit=limit)
    if not result.get("ok") or result.get("skipped"):
        return False

    messages = result.get("messages") if isinstance(result.get("messages"), list) else []
    for message in messages:
        if not isinstance(message, Mapping):
            continue
        content = str(message.get("content") or "").strip().lower()
        if not content:
            continue
        if any(marker in content for marker in CHAT_THREAD_VALIDATION_MARKERS):
            return True

    return False


def _get_known_project_foundry_thread_id(
    project_settings: Mapping[str, Any] | None,
    metadata: Mapping[str, Any] | None,
    purpose: str | None = None,
) -> str | None:
    safe_settings = project_settings if isinstance(project_settings, Mapping) else {}
    safe_metadata = metadata if isinstance(metadata, Mapping) else {}
    legacy_thread_id = str(
        safe_settings.get("projectThreadId")
        or safe_metadata.get("foundryThreadId")
        or ""
    ).strip() or None
    fields = _project_foundry_thread_fields(purpose)
    purpose_thread_id = str(
        safe_settings.get(str(fields["settingsKey"]))
        or safe_metadata.get(str(fields["metadataKey"]))
        or ""
    ).strip() or None
    if purpose_thread_id:
        return purpose_thread_id
    if _normalize_project_foundry_thread_purpose(purpose) == "chat":
        return legacy_thread_id
    return None


def _apply_project_foundry_thread_id(
    project_settings: dict | None,
    metadata: dict | None,
    resolved_thread_id: str | None,
    purpose: str | None = None,
) -> tuple[dict, dict, bool, bool]:
    safe_settings = project_settings if isinstance(project_settings, dict) else {}
    safe_metadata = dict(metadata) if isinstance(metadata, dict) else {}
    safe_thread_id = str(resolved_thread_id or "").strip()
    if not safe_thread_id:
        return safe_settings, safe_metadata, False, False

    fields = _project_foundry_thread_fields(purpose)
    settings_updates = {str(fields["settingsKey"]): safe_thread_id}
    legacy_settings_key = fields.get("legacySettingsKey")
    if legacy_settings_key:
        settings_updates[str(legacy_settings_key)] = safe_thread_id

    updated_settings = merge_project_settings(safe_settings, settings_updates)
    settings_changed = any(
        str(safe_settings.get(key) or "").strip() != safe_thread_id
        for key in settings_updates
    )

    metadata_changed = False
    metadata_key = str(fields["metadataKey"])
    if str(safe_metadata.get(metadata_key) or "").strip() != safe_thread_id:
        safe_metadata[metadata_key] = safe_thread_id
        metadata_changed = True

    legacy_metadata_key = fields.get("legacyMetadataKey")
    if legacy_metadata_key and str(safe_metadata.get(str(legacy_metadata_key)) or "").strip() != safe_thread_id:
        safe_metadata[str(legacy_metadata_key)] = safe_thread_id
        metadata_changed = True

    return updated_settings, safe_metadata, settings_changed, metadata_changed


def ensure_project_foundry_thread_state(
    entry: dict,
    app_settings: dict,
    purpose: str | None = None,
    project_settings: dict | None = None,
    metadata: dict | None = None,
    persist: bool = True,
) -> dict:
    project_dir = entry["projectDir"]
    resolved_settings = project_settings if isinstance(project_settings, dict) else load_project_settings_file(project_dir)

    resolved_metadata = metadata if isinstance(metadata, dict) else read_json_file(entry["metadataPath"], {})
    if not isinstance(resolved_metadata, dict):
        resolved_metadata = {}

    safe_purpose = _normalize_project_foundry_thread_purpose(purpose)
    known_thread_id = _get_known_project_foundry_thread_id(
        resolved_settings,
        resolved_metadata,
        purpose=safe_purpose,
    )

    thread_result = ensure_project_foundry_thread(
        app_settings,
        project_id=entry["id"],
        known_thread_id=known_thread_id,
        purpose=safe_purpose,
    )

    resolved_thread_id = known_thread_id
    if thread_result.get("threadId"):
        resolved_thread_id = str(thread_result["threadId"]).strip() or resolved_thread_id

    settings_changed = False
    metadata_changed = False

    if (
        safe_purpose == "chat"
        and resolved_thread_id
        and _thread_contains_validation_history(app_settings, resolved_thread_id)
    ):
        legacy_chat_thread_id = str(resolved_thread_id).strip()
        known_validation_thread_id = _get_known_project_foundry_thread_id(
            resolved_settings,
            resolved_metadata,
            purpose="validation",
        )

        if not known_validation_thread_id:
            resolved_settings, resolved_metadata, validation_settings_changed, validation_metadata_changed = _apply_project_foundry_thread_id(
                resolved_settings,
                resolved_metadata,
                legacy_chat_thread_id,
                purpose="validation",
            )
            settings_changed = settings_changed or validation_settings_changed
            metadata_changed = metadata_changed or validation_metadata_changed

        replacement_thread_result = ensure_project_foundry_thread(
            app_settings,
            project_id=entry["id"],
            known_thread_id=None,
            purpose="chat",
        )
        replacement_thread_id = str(replacement_thread_result.get("threadId") or "").strip()
        if replacement_thread_id and replacement_thread_id != legacy_chat_thread_id:
            _append_app_activity(
                "foundry.thread",
                status="info",
                project_id=entry["id"],
                category="foundry",
                step="chat-thread-rotated",
                source="backend.foundry",
                details={
                    "oldThreadId": legacy_chat_thread_id,
                    "newThreadId": replacement_thread_id,
                    "validationThreadId": str(
                        _get_known_project_foundry_thread_id(
                            resolved_settings,
                            resolved_metadata,
                            purpose="validation",
                        )
                        or ""
                    ).strip(),
                    "reason": "legacy-thread-contained-validation-history",
                },
            )
            resolved_thread_id = replacement_thread_id
            thread_result = replacement_thread_result

    if resolved_thread_id:
        resolved_settings, resolved_metadata, current_settings_changed, current_metadata_changed = _apply_project_foundry_thread_id(
            resolved_settings,
            resolved_metadata,
            resolved_thread_id,
            purpose=safe_purpose,
        )
        settings_changed = settings_changed or current_settings_changed
        metadata_changed = metadata_changed or current_metadata_changed

        if persist and settings_changed:
            persist_project_settings(
                project_dir,
                entry["id"],
                entry["name"],
                entry["cloud"],
                resolved_settings,
            )

        if persist and metadata_changed:
            entry["metadataPath"].write_text(json.dumps(resolved_metadata, indent=2), encoding="utf-8")

    return {
        "threadId": resolved_thread_id,
        "threadResult": thread_result,
        "settings": resolved_settings,
        "metadata": resolved_metadata,
    }


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
    provider = str(settings.get("modelProvider") or "azure-foundry").strip().lower()
    purpose_to_var = {
        "coding": "foundryModelCoding",
        "code": "foundryModelCoding",
        "reasoning": "foundryModelReasoning",
        "fast": "foundryModelFast",
        "chat": "foundryModelReasoning",
    }
    variable = purpose_to_var.get(purpose_value, "foundryModelFast")
    model_name = str(settings.get(variable) or "").strip()
    return variable, model_name


def sanitize_app_settings_for_provider(settings: dict) -> dict:
    incoming = settings if isinstance(settings, dict) else {}
    provider = str(incoming.get("modelProvider") or "azure-foundry").strip().lower()
    normalized_provider = "azure-foundry"

    merged = {
        **DEFAULT_APP_SETTINGS,
        **incoming,
        "modelProvider": normalized_provider,
    }

    return merged


def build_persistable_app_settings(settings: dict) -> dict:
    sanitized = sanitize_app_settings_for_provider(settings)
    provider = str(sanitized.get("modelProvider") or "azure-foundry").strip().lower()

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

    return {key: sanitized.get(key, "") for key in keys}


def is_azure_foundry_provider(settings: dict) -> bool:
    return str(settings.get("modelProvider") or "azure-foundry").strip().lower() == "azure-foundry"


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


def ensure_project_foundry_thread(
    settings: dict,
    project_id: str,
    known_thread_id: str | None = None,
    purpose: str | None = None,
) -> dict:
    if not is_azure_foundry_provider(settings):
        _append_app_activity(
            "foundry.thread",
            status="info",
            project_id=str(project_id or "").strip() or None,
            category="foundry",
            step="skipped",
            source="backend.foundry",
            details={
                "reason": "provider-not-azure-foundry",
                "purpose": _normalize_project_foundry_thread_purpose(purpose),
            },
        )
        return {
            "ok": True,
            "skipped": True,
            "reason": "provider-not-azure-foundry",
        }

    safe_project_id = str(project_id or "").strip()
    safe_purpose = _normalize_project_foundry_thread_purpose(purpose)
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
                thread_name=_build_project_foundry_thread_name(safe_project_id, safe_purpose),
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
                "purpose": safe_purpose,
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
                "purpose": safe_purpose,
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
            "purpose": safe_purpose,
        },
    )

    return {
        "ok": True,
        "skipped": False,
        "threadId": thread_result.thread_id,
        "created": thread_result.created,
        "purpose": safe_purpose,
    }


def resolve_project_foundry_thread_id(entry: dict, app_settings: dict, purpose: str | None = None) -> str | None:
    thread_state = ensure_project_foundry_thread_state(
        entry,
        app_settings,
        purpose=purpose,
    )
    return str(thread_state.get("threadId") or "").strip() or None


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
        provider = str(effective_settings.get("modelProvider") or "azure-foundry").strip().lower()

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
            "provider": str(settings.get("modelProvider") or "azure-foundry").strip().lower(),
            "hasFoundryEndpoint": bool(str(settings.get("aiFoundryEndpoint") or "").strip()),
        },
    )
    return {
        "settings": settings,
        "path": str(target.relative_to(WORKSPACE_ROOT)) if target.exists() else None,
    }


@app.delete("/api/settings/app/reset")
def reset_app_settings():
    try:
        target = APP_STATE_DIR / "app.settings.env"
        
        _append_app_activity(
            "settings.app.reset",
            status="info",
            category="settings",
            step="requested",
            source="backend.api",
            details={
                "fileExists": target.exists(),
            },
        )

        # Delete the settings file if it exists
        if target.exists():
            target.unlink()

        _append_app_activity(
            "settings.app.reset",
            status="info",
            category="settings",
            step="completed",
            source="backend.api",
            details={
                "fileDeleted": True,
            },
        )

        return {"ok": True, "message": "Application settings reset successfully."}
    except Exception as exc:
        _append_app_activity(
            "settings.app.reset",
            status="error",
            category="settings",
            step="failed",
            source="backend.api",
            details={
                "error": str(exc),
            },
        )
        raise HTTPException(status_code=500, detail="Failed to reset application settings.")


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

    thread_state = ensure_project_foundry_thread_state(
        entry,
        settings,
        purpose="validation",
        project_settings=project_settings,
        metadata=metadata,
    )
    project_settings = thread_state["settings"]
    metadata = thread_state["metadata"]
    resolved_thread_id = str(thread_state.get("threadId") or "").strip() or None

    if not resolved_thread_id:
        return {
            "ok": False,
            "skipped": True,
            "reason": "project-thread-missing",
        }

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

    thread_state = ensure_project_foundry_thread_state(
        entry,
        settings,
        purpose="validation",
        project_settings=project_settings,
        metadata=metadata,
    )
    resolved_thread_id = str(thread_state.get("threadId") or "").strip() or None

    if not resolved_thread_id:
        return {
            "ok": False,
            "skipped": True,
            "reason": "project-thread-missing",
        }

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

            resolved_thread_id = resolve_project_foundry_thread_id(entry, settings, purpose="chat")
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
            resolved_thread_id = resolve_project_foundry_thread_id(entry, settings, purpose="chat")
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

    thread_id = resolve_project_foundry_thread_id(entry, settings, purpose="chat")
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
            resolved_thread_id = resolve_project_foundry_thread_id(entry, settings, purpose="validation")
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
    foundry_thread_id = resolve_project_foundry_thread_id(entry, settings, purpose="validation")
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
                project_dir=entry["projectDir"],
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


@app.get("/api/project/{project_id}/architecture/validation/log")
def get_project_architecture_validation_log(project_id: str, validationRunId: str | None = None):
    entry = find_project_entry(project_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Project not found")

    log_path = entry["projectDir"] / "Documentation" / "validation.log"
    if not log_path.exists():
        return {
            "ok": True,
            "projectId": entry["id"],
            "validationRunId": str(validationRunId or "").strip(),
            "events": [],
        }

    safe_run_id = str(validationRunId or "").strip()

    try:
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to read validation log: {exc}") from exc

    parsed_events: list[dict[str, Any]] = []
    for raw_line in lines[-2000:]:
        parsed = _parse_validation_log_line(raw_line)
        if not isinstance(parsed, dict):
            continue

        if safe_run_id:
            details_payload = parsed.get("details")
            run_id_in_event = ""
            if isinstance(details_payload, Mapping):
                run_id_in_event = str(details_payload.get("validationRunId") or "").strip()
            if run_id_in_event != safe_run_id:
                continue

        parsed_events.append(parsed)

    return {
        "ok": True,
        "projectId": entry["id"],
        "validationRunId": safe_run_id,
        "events": parsed_events,
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

    foundry_thread_id = resolve_project_foundry_thread_id(entry, settings, purpose="validation")
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

    provider = str(settings.get("modelProvider") or "azure-foundry").strip().lower()
    try:
        _append_app_activity(
            "settings.app.verify",
            status="info",
            category="settings",
            step="requested",
            source="backend.api",
            details={"provider": provider},
        )

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
        chat_thread_id = str(merged_settings.get("projectChatThreadId") or merged_settings.get("projectThreadId") or "").strip()
        validation_thread_id = str(merged_settings.get("projectValidationThreadId") or "").strip()
        if description_text:
            metadata["applicationDescription"] = description_text
        if application_type:
            metadata["applicationType"] = application_type
        if chat_thread_id:
            metadata["foundryChatThreadId"] = chat_thread_id
            metadata["foundryThreadId"] = chat_thread_id
        if validation_thread_id:
            metadata["foundryValidationThreadId"] = validation_thread_id
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

    app_settings = load_app_settings()
    chat_thread_state = ensure_project_foundry_thread_state(
        entry,
        app_settings,
        purpose="chat",
        project_settings=settings,
        metadata=metadata,
    )
    settings = chat_thread_state["settings"]
    metadata = chat_thread_state["metadata"]

    validation_thread_state = ensure_project_foundry_thread_state(
        entry,
        app_settings,
        purpose="validation",
        project_settings=settings,
        metadata=metadata,
    )
    settings = validation_thread_state["settings"]
    if not str(settings.get("projectValidationLastThreadId") or "").strip():
        settings["projectValidationLastThreadId"] = str(
            settings.get("projectValidationThreadId")
            or validation_thread_state.get("threadId")
            or ""
        ).strip()

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

        app_settings = load_app_settings()
        bootstrap_result = {'skipped': True, 'reason': 'deferred'}

        seeded_metadata = dict(existing_metadata)
        
        foundry_thread_id = str(
            body.project.foundryChatThreadId 
            or body.project.foundryThreadId 
            or ''
        ).strip() or str(
            _get_known_project_foundry_thread_id(project_settings, seeded_metadata, purpose='chat') 
            or ''
        ).strip() or None
        
        foundry_validation_thread_id = str(
            body.project.foundryValidationThreadId 
            or ''
        ).strip() or str(
            _get_known_project_foundry_thread_id(project_settings, seeded_metadata, purpose='validation') 
            or ''
        ).strip() or None
        
        foundry_thread_result = {'skipped': True, 'threadId': foundry_thread_id}
        foundry_validation_thread_result = {'skipped': True, 'threadId': foundry_validation_thread_id}

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

        project_settings, metadata_payload, _, _ = _apply_project_foundry_thread_id(
            project_settings,
            metadata_payload,
            foundry_thread_id,
            purpose="chat",
        )
        project_settings, metadata_payload, _, _ = _apply_project_foundry_thread_id(
            project_settings,
            metadata_payload,
            foundry_validation_thread_id,
            purpose="validation",
        )

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
                    "validationThreadCreated": bool(foundry_validation_thread_result.get("created")),
                    "validationThreadId": foundry_validation_thread_id or "",
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
                "foundryValidationThread": foundry_validation_thread_result,
            },
        )

        return {
            "ok": True,
            "projectPath": str(project_dir.relative_to(WORKSPACE_ROOT)),
            "foundryBootstrap": bootstrap_result,
            "foundryThread": foundry_thread_result,
            "foundryValidationThread": foundry_validation_thread_result,
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

    app_settings = load_app_settings()
    chat_thread_state = ensure_project_foundry_thread_state(
        entry,
        app_settings,
        purpose="chat",
        project_settings=project_settings,
        metadata=metadata,
    )
    project_settings = chat_thread_state["settings"]
    metadata = chat_thread_state["metadata"]
    resolved_thread_id = str(chat_thread_state.get("threadId") or "").strip() or None

    validation_thread_state = ensure_project_foundry_thread_state(
        entry,
        app_settings,
        purpose="validation",
        project_settings=project_settings,
        metadata=metadata,
    )
    project_settings = validation_thread_state["settings"]
    metadata = validation_thread_state["metadata"]
    resolved_validation_thread_id = str(validation_thread_state.get("threadId") or "").strip() or None

    return {
        "project": {
            "id": entry["id"],
            "name": str(metadata.get("name") or entry["name"]),
            "cloud": str(metadata.get("cloud") or entry["cloud"]),
            "applicationType": str(metadata.get("applicationType") or ""),
            "applicationDescription": str(metadata.get("applicationDescription") or ""),
            "foundryThreadId": str(resolved_thread_id or metadata.get("foundryThreadId") or ""),
            "foundryChatThreadId": str(resolved_thread_id or metadata.get("foundryChatThreadId") or metadata.get("foundryThreadId") or ""),
            "foundryValidationThreadId": str(resolved_validation_thread_id or metadata.get("foundryValidationThreadId") or ""),
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
    allow_warnings: bool = True,
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
    foundry_thread_id = resolve_project_foundry_thread_id(entry, app_settings, purpose="validation")

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
            "allowWarnings": bool(allow_warnings),
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
            allow_warnings=bool(allow_warnings),
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
                "allowWarnings": bool(allow_warnings),
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
                "allowWarnings": bool(allow_warnings),
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
                "allowWarnings": bool(allow_warnings),
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
        "allowWarnings": bool(allow_warnings),
        "generation": result,
    }


def _run_iac_generation_task(task_id: str, project_id: str, parameter_format: str, allow_warnings: bool) -> None:
    with IAC_TASK_LOCK:
        _mark_iac_task_running(task_id)

    def on_progress(event: dict) -> None:
        with IAC_TASK_LOCK:
            _record_iac_progress_event(task_id, event)

    try:
        payload = _generate_project_iac_payload(
            project_id,
            requested_parameter_format=parameter_format,
            allow_warnings=bool(allow_warnings),
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
    allow_warnings = True
    if body and body.parameterFormat is not None:
        requested_format_raw = str(body.parameterFormat or "").strip()
    if body and body.allowWarnings is not None:
        allow_warnings = bool(body.allowWarnings)

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

        created_task = _create_iac_task(entry["id"], parameter_format, allow_warnings)

    worker = Thread(
        target=_run_iac_generation_task,
        args=(created_task["taskId"], entry["id"], parameter_format, allow_warnings),
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
    allow_warnings = True
    if body and body.parameterFormat is not None:
        requested_format_raw = str(body.parameterFormat or "").strip()
    if body and body.allowWarnings is not None:
        allow_warnings = bool(body.allowWarnings)

    return _generate_project_iac_payload(
        project_id,
        requested_parameter_format=requested_format_raw,
        allow_warnings=allow_warnings,
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

        thread_ids = [
            _get_known_project_foundry_thread_id(project_settings, metadata, purpose="chat"),
            _get_known_project_foundry_thread_id(project_settings, metadata, purpose="validation"),
        ]

        unique_thread_ids: list[str] = []
        for raw_thread_id in thread_ids:
            safe_thread_id = str(raw_thread_id or "").strip()
            if safe_thread_id and safe_thread_id not in unique_thread_ids:
                unique_thread_ids.append(safe_thread_id)

        if unique_thread_ids:
            app_settings = load_app_settings()
            for thread_id in unique_thread_ids:
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
    return RedirectResponse(url="/index.html")


@app.get("/index.html", include_in_schema=False)
def index_page():
    return FileResponse(FRONTEND_DIR / "index.html")


@app.get("/landing.html", include_in_schema=False)
def landing_page():
    return FileResponse(FRONTEND_DIR / "LandingScreen" / "index.html")


@app.get("/canvas.html", include_in_schema=False)
def canvas_page():
    return FileResponse(FRONTEND_DIR / "CanvasScreen" / "canvas.html")


@app.get("/old-canvas.html", include_in_schema=False)
def old_canvas_page():
    return FileResponse(FRONTEND_DIR / "canvas.html")


@app.get("/settings.html", include_in_schema=False)
def settings_page():
    return FileResponse(FRONTEND_DIR / "ApplicationSettingsScreen" / "index.html")


app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=False), name="frontend-static")
