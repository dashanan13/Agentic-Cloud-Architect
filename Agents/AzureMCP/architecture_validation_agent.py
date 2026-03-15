from __future__ import annotations

import asyncio
import importlib
import json
import re
import time
import urllib.request as _urllib_request
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping
from uuid import uuid4

from Agents.AzureAIFoundry.foundry_bootstrap import (
    FoundryConfigurationError,
    FoundryConnectionSettings,
)
from Agents.AzureAIFoundry.foundry_description import FoundryAssistantRunner
from Agents.AzureMCP.cloudarchitect_chat_agent import AzureMcpCredentials
from Agents.common.activity_log import log_activity as write_activity_log

DEFAULT_VALIDATION_AGENT_NAME = "architecture-validation-agent"
DEFAULT_VALIDATION_MAX_FINDINGS = 30
MCP_VALIDATION_TIMEOUT_SECONDS = 45
PILLAR_HUMANIZATION_TIMEOUT_SECONDS = 45
MAX_PILLAR_RECOMMENDATIONS_FOR_HUMANIZATION = 10
WELL_ARCHITECTED_PILLARS = [
    "reliability",
    "security",
    "cost_optimization",
    "operational_excellence",
    "performance_efficiency",
]

WAF_SUPPORTED_SERVICE_SLUGS = {
    "application-insights",
    "app-service-web-apps",
    "azure-api-management",
    "azure-application-gateway",
    "azure-blob-storage",
    "azure-container-apps",
    "azure-databricks",
    "azure-database-for-mysql",
    "azure-disk-storage",
    "azure-event-grid",
    "azure-event-hubs",
    "azure-expressroute",
    "azure-files",
    "azure-firewall",
    "azure-front-door",
    "azure-functions",
    "azure-kubernetes-service",
    "azure-load-balancer",
    "azure-local",
    "azure-log-analytics",
    "azure-machine-learning",
    "azure-netapp-files",
    "azure-service-bus",
    "azure-service-fabric",
    "azure-sql-database",
    "azure-traffic-manager",
    "azure-virtual-wan",
    "cosmos-db",
    "postgresql",
    "virtual-machines",
    "virtual-network",
}

WAF_SERVICE_PATTERN_MAP: list[tuple[str, tuple[str, ...]]] = [
    ("virtual-network", ("virtual network", "virtualnetworks", "vnet", "subnet", "network security group", "networksecuritygroups", "nsg", "route table", "routetables", "public ip", "publicipaddresses", "private endpoint", "privateendpoints", "network interface", "networkinterfaces", "nic")),
    ("virtual-machines", ("virtual machine", "virtualmachines", "microsoft.compute/virtualmachines", "vmss", "virtual machine scale set")),
    ("azure-kubernetes-service", ("azure kubernetes service", "managedclusters", "kubernetes", "aks")),
    ("azure-functions", ("azure functions", "function app", "functionapp", "microsoft.web/sites/functions", "function")),
    ("app-service-web-apps", ("app service", "web app", "webapp", "microsoft.web/sites")),
    ("azure-application-gateway", ("application gateway", "applicationgateways")),
    ("azure-api-management", ("api management", "apimanagement", "microsoft.apimanagement/service")),
    ("azure-load-balancer", ("load balancer", "loadbalancer", "loadbalancers")),
    ("azure-front-door", ("front door", "frontdoor")),
    ("azure-traffic-manager", ("traffic manager", "trafficmanagerprofiles")),
    ("azure-firewall", ("azure firewall", "azurefirewalls", "firewall")),
    ("azure-expressroute", ("express route", "expressroute", "expressroutecircuits")),
    ("azure-virtual-wan", ("virtual wan", "virtualwans", "vwan")),
    ("azure-service-bus", ("service bus", "servicebus", "microsoft.servicebus")),
    ("azure-event-hubs", ("event hubs", "eventhubs", "microsoft.eventhub")),
    ("azure-event-grid", ("event grid", "eventgrid", "microsoft.eventgrid")),
    ("azure-blob-storage", ("blob storage", "blobservice", "storage account", "storageaccounts", "microsoft.storage/storageaccounts")),
    ("azure-files", ("azure files", "fileshare", "file share", "microsoft.storage/storageaccounts/fileservices")),
    ("azure-disk-storage", ("managed disk", "manageddisks", "disk storage", "microsoft.compute/disks")),
    ("azure-netapp-files", ("netapp", "net app files", "microsoft.netapp/netappaccounts")),
    ("azure-sql-database", ("sql database", "azuresql", "microsoft.sql/servers/databases")),
    ("azure-database-for-mysql", ("mysql", "database for mysql", "microsoft.dbformysql")),
    ("postgresql", ("postgres", "postgresql", "database for postgresql", "microsoft.dbforpostgresql")),
    ("cosmos-db", ("cosmos", "cosmos db", "documentdb", "microsoft.documentdb/databaseaccounts")),
    ("azure-databricks", ("databricks", "microsoft.databricks/workspaces")),
    ("azure-machine-learning", ("machine learning", "ml workspace", "microsoft.machinelearningservices/workspaces")),
    ("azure-log-analytics", ("log analytics", "operationalinsights", "workspace", "microsoft.operationalinsights/workspaces")),
    ("application-insights", ("application insights", "app insights", "microsoft.insights/components")),
    ("azure-container-apps", ("container app", "containerapp", "container apps", "microsoft.app/containerapps")),
    ("azure-local", ("azure local",)),
]

# Map lowercase section header text in WAF service-guide markdown to canonical pillar names.
_WAF_PILLAR_HEADERS: dict[str, str] = {
    "reliability": "reliability",
    "security": "security",
    "cost optimization": "cost_optimization",
    "cost optimisation": "cost_optimization",
    "operational excellence": "operational_excellence",
    "performance efficiency": "performance_efficiency",
}

# Session-level cache: guide_url → parsed findings list.
# Avoids redundant HTTP fetches when the same service is queried multiple times.
_WAF_GUIDE_CACHE: dict[str, list[dict[str, Any]]] = {}


def _timestamp_utc() -> str:
    """Return current UTC timestamp in ISO format."""
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _redact_sensitive_value(value: str | None) -> str:
    """Redact sensitive values like keys, tokens, passwords."""
    if not value:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    if len(text) > 500:
        return text[:500] + "..."
    return text


def _sanitize_dict_for_logging(obj: Any, depth: int = 0) -> Any:
    """Recursively remove/redact sensitive fields from context objects."""
    if depth > 3:
        return "..."
    if isinstance(obj, dict):
        sanitized = {}
        for key, value in list(obj.items())[:20]:  # Limit keys
            key_lower = str(key).lower()
            if any(x in key_lower for x in ["secret", "key", "token", "password", "credential", "auth", "apikey"]):
                sanitized[key] = "***REDACTED***"
            else:
                sanitized[key] = _sanitize_dict_for_logging(value, depth + 1)
        return sanitized
    elif isinstance(obj, list):
        return [_sanitize_dict_for_logging(item, depth + 1) for item in obj[:5]]
    elif isinstance(obj, str) and len(obj) > 500:
        return obj[:500] + "..."
    else:
        return obj


@dataclass
class ValidationStep:
    """Represents a single step in the validation pipeline."""

    step_number: int
    step_name: str
    timestamp: str
    status: str  # started, completed, failed
    duration_ms: int = 0
    details: dict[str, Any] | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        result = {
            "step_number": self.step_number,
            "step_name": self.step_name,
            "timestamp": self.timestamp,
            "status": self.status,
            "duration_ms": self.duration_ms,
        }
        if self.details:
            result["details"] = _sanitize_dict_for_logging(self.details)
        if self.error:
            result["error"] = str(self.error)[:500]
        return result


@dataclass
class ValidationLog:
    """Complete structured validation log."""

    run_id: str
    start_time: str
    validator_version: str = "1.0.0"
    agent_name: str = DEFAULT_VALIDATION_AGENT_NAME
    steps: list[ValidationStep] | None = None
    tool_discovery: list[dict[str, Any]] | None = None
    tool_selection: list[dict[str, Any]] | None = None
    tool_telemetry: list[dict[str, Any]] | None = None
    foundry_thread_id: str | None = None
    foundry_messages: int = 0
    recommendations_by_pillar: dict[str, list[dict[str, Any]]] | None = None
    end_time: str | None = None
    total_duration_ms: int = 0
    final_status: str = "pending"
    error_message: str | None = None

    def to_json(self) -> dict[str, Any]:
        """Convert to JSON-serializable format."""
        return {
            "run_id": self.run_id,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "validator_version": self.validator_version,
            "agent_name": self.agent_name,
            "total_duration_ms": self.total_duration_ms,
            "final_status": self.final_status,
            "error_message": self.error_message,
            "foundry_thread_id": self.foundry_thread_id,
            "foundry_messages": self.foundry_messages,
            "steps": [s.to_dict() for s in (self.steps or [])] if self.steps else [],
            "tool_discovery": self.tool_discovery or [],
            "tool_selection": self.tool_selection or [],
            "tool_telemetry": self.tool_telemetry or [],
            "recommendations_by_pillar": self.recommendations_by_pillar
            or {p: [] for p in WELL_ARCHITECTED_PILLARS},
        }



def _log_validation_event(
    event_type: str,
    *,
    level: str = "info",
    step: str = "",
    details: Mapping[str, Any] | None = None,
    project_id: str | None = None,
) -> None:
    category = "mcp" if str(event_type or "").strip().lower().startswith("mcp.") else "validation"
    write_activity_log(
        event_type=str(event_type or "validation.event").strip() or "validation.event",
        category=category,
        level=_normalize_level(level),
        step=str(step or event_type or "validation.event").strip() or "validation.event",
        source="agent.validation",
        project_id=str(project_id or "").strip() or None,
        details=details if isinstance(details, Mapping) else None,
    )


def _normalize_level(value: str | None) -> str:
    text = str(value or "info").strip().lower()
    if text in {"warning", "warn"}:
        return "warning"
    if text in {"error", "failed", "failure", "fatal"}:
        return "error"
    return "info"


def _first_non_empty(settings: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = settings.get(key)
        text = str(value).strip() if value is not None else ""
        if text:
            return text
    return ""


def _normalize_severity(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text in {"failure", "failed", "fail", "error", "critical", "high"}:
        return "failure"
    if text in {"warning", "warn", "medium", "moderate"}:
        return "warning"
    return "info"


def _normalize_operation_name(value: Any) -> str:
    text = re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")
    if text in {"set_resource_property", "set_property", "update_property", "update_resource_property"}:
        return "set_resource_property"
    if text in {"set_resource_name", "rename_resource", "set_name"}:
        return "set_resource_name"
    if text in {"remove_connection", "delete_connection"}:
        return "remove_connection"
    if text in {"set_connection_direction", "update_connection_direction", "set_direction"}:
        return "set_connection_direction"
    if text in {"add_connection", "create_connection"}:
        return "add_connection"
    if text in {"remove_resource", "delete_resource"}:
        return "remove_resource"
    return ""


def _normalize_finding_id(value: Any, index: int) -> str:
    text = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(value or "").strip())
    text = text.strip("-")
    if text:
        return text[:80]
    return f"finding-{index + 1}"


def _normalize_string(value: Any, fallback: str = "") -> str:
    text = str(value or "").strip()
    return text or fallback


def _truncate_text(value: Any, *, max_chars: int = 900) -> str:
    text = str(value or "").strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"


def _is_mcp_guidance_text(value: Any) -> bool:
    text = re.sub(r"\s+", " ", str(value or "").strip().lower())
    if not text:
        return False

    if re.search(r"^the tool .+ was not found", text):
        return True

    guidance_markers = (
        "when not learning",
        "run again with the \"learn\" argument",
        "run again with the 'learn' argument",
        "to learn about a specific tool",
        "use the \"tool\" argument",
        "use the 'tool' argument",
        "list of available tools and their parameters",
        "learn=true",
        "hierarchical mcp command router",
        "to invoke a command, set \"command\"",
        "supported child tools and parameters",
        "learn about this tool and its supported child tools",
    )
    if any(marker in text for marker in guidance_markers):
        return True

    if "available tools" in text and "parameters" in text and "learn" in text:
        return True

    return False


def _extract_json_from_text(payload_text: str) -> Any | None:
    text = str(payload_text or "").strip()
    if not text:
        return None

    direct = _try_json(text)
    if direct is not None:
        return direct

    fenced = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    fenced = re.sub(r"\s*```$", "", fenced)
    direct = _try_json(fenced)
    if direct is not None:
        return direct

    decoder = json.JSONDecoder()
    for idx, ch in enumerate(text):
        if ch not in "[{":
            continue
        try:
            obj, _ = decoder.raw_decode(text[idx:])
            return obj
        except Exception:
            continue
    return None


def _try_json(text: str) -> Any | None:
    try:
        return json.loads(text)
    except Exception:
        return None


def _coerce_canvas_items(canvas_state: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw = canvas_state.get("canvasItems") if isinstance(canvas_state, Mapping) else None
    if not isinstance(raw, list):
        return []

    items: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, Mapping):
            continue
        item_id = str(item.get("id") or "").strip()
        if not item_id:
            continue
        items.append(dict(item))
    return items


def _coerce_canvas_connections(canvas_state: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw = canvas_state.get("canvasConnections") if isinstance(canvas_state, Mapping) else None
    if not isinstance(raw, list):
        return []

    connections: list[dict[str, Any]] = []
    for connection in raw:
        if not isinstance(connection, Mapping):
            continue
        conn_id = str(connection.get("id") or "").strip()
        if not conn_id:
            continue
        connections.append(dict(connection))
    return connections


def _resource_type_text(item: Mapping[str, Any]) -> str:
    return str(
        item.get("resourceType")
        or item.get("resourceName")
        or item.get("name")
        or ""
    ).strip().lower()


def _is_public_ip_item(item: Mapping[str, Any]) -> bool:
    text = _resource_type_text(item)
    return bool(re.search(r"\b(public\s*ip|publicipaddress|public\s*ip\s*address)\b", text))


def _is_subnet_item(item: Mapping[str, Any]) -> bool:
    text = _resource_type_text(item)
    return bool(re.search(r"\bsubnet\b", text))


def _default_name_from_item(item: Mapping[str, Any], index: int) -> str:
    base = str(item.get("resourceType") or item.get("resourceName") or "resource").strip().lower()
    base = re.sub(r"[^a-z0-9]+", "-", base).strip("-")
    if not base:
        base = "resource"
    return f"{base}-{index + 1}"


def _deterministic_findings(
    *,
    items: list[dict[str, Any]],
    connections: list[dict[str, Any]],
) -> list[dict[str, Any]]:

    findings: list[dict[str, Any]] = []
    item_by_id = {str(item.get("id") or "").strip(): item for item in items}

    fallback_location = ""
    for item in items:
        properties = item.get("properties") if isinstance(item.get("properties"), Mapping) else {}
        location = str(properties.get("location") or item.get("location") or "").strip()
        if location:
            fallback_location = location
            break

    # Helper: Identify Azure container resources (resource group, vnet, subnet, etc.)
    def is_azure_container(item):
        rtype = str(item.get("resourceType") or "").lower()
        return any(
            key in rtype
            for key in ["resource group", "resource_group", "vnet", "virtual network", "subnet", "container group", "container app environment", "aks", "kubernetes", "app service plan"]
        )

    # Build containment map: parent_id -> set(child_id)
    containment = {}
    for item in items:
        parent_id = str(item.get("parentId") or "").strip()
        if parent_id:
            containment.setdefault(parent_id, set()).add(str(item.get("id") or "").strip())

    for index, item in enumerate(items):
        item_id = str(item.get("id") or "").strip()
        if not item_id:
            continue

        item_name = str(item.get("name") or "").strip()
        if not item_name:
            findings.append(
                {
                    "severity": "failure",
                    "title": "Resource name is missing",
                    "message": "This resource has no name. Named resources are required for clear architecture intent and IaC generation.",
                    "target": {
                        "resourceId": item_id,
                        "field": "name",
                    },
                    "fix": {
                        "label": "Set resource name",
                        "operations": [
                            {
                                "op": "set_resource_name",
                                "resourceId": item_id,
                                "value": _default_name_from_item(item, index),
                            }
                        ],
                    },
                }
            )

        properties = item.get("properties") if isinstance(item.get("properties"), Mapping) else {}
        location = str(properties.get("location") or item.get("location") or "").strip()
        if not location:
            findings.append(
                {
                    "severity": "warning",
                    "title": "Resource location is missing",
                    "message": "This resource does not define a location. Set a location to avoid deployment ambiguity.",
                    "target": {
                        "resourceId": item_id,
                        "field": "location",
                    },
                    "fix": {
                        "label": "Set location",
                        "operations": [
                            {
                                "op": "set_resource_property",
                                "resourceId": item_id,
                                "field": "location",
                                "value": fallback_location or "eastus",
                            }
                        ],
                    },
                }
            )

        # Azure-aware: Only flag as isolated if not a container, or container with no children and no links
        linked = False
        for connection in connections:
            from_id = str(connection.get("fromId") or "").strip()
            to_id = str(connection.get("toId") or "").strip()
            if from_id == item_id or to_id == item_id:
                linked = True
                break

        has_children = item_id in containment and len(containment[item_id]) > 0
        if not linked and not (is_azure_container(item) and has_children):
            findings.append(
                {
                    "severity": "info",
                    "title": "Resource has no connections",
                    "message": "This resource is currently isolated from the rest of the architecture.",
                    "target": {
                        "resourceId": item_id,
                    },
                }
            )

    for connection in connections:
        conn_id = str(connection.get("id") or "").strip()
        from_id = str(connection.get("fromId") or "").strip()
        to_id = str(connection.get("toId") or "").strip()
        from_item = item_by_id.get(from_id)
        to_item = item_by_id.get(to_id)

        if not from_item or not to_item:
            findings.append(
                {
                    "severity": "failure",
                    "title": "Connection references missing resource",
                    "message": "This connection points to a resource that does not exist in the current canvas.",
                    "target": {
                        "connectionId": conn_id,
                    },
                    "fix": {
                        "label": "Remove invalid connection",
                        "operations": [
                            {
                                "op": "remove_connection",
                                "connectionId": conn_id,
                            }
                        ],
                    },
                }
            )
            continue

        if (_is_public_ip_item(from_item) and _is_subnet_item(to_item)) or (
            _is_public_ip_item(to_item) and _is_subnet_item(from_item)
        ):
            findings.append(
                {
                    "severity": "failure",
                    "title": "Public IP connected directly to subnet",
                    "message": "Direct Public IP ↔ Subnet connections are invalid. Attach Public IP to supported resources (for example gateway, load balancer, or firewall).",
                    "target": {
                        "connectionId": conn_id,
                    },
                    "fix": {
                        "label": "Remove invalid connection",
                        "operations": [
                            {
                                "op": "remove_connection",
                                "connectionId": conn_id,
                            }
                        ],
                    },
                }
            )

    return findings


def _trim_value(value: Any, *, depth: int = 0) -> Any:
    if depth >= 2:
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        return str(value)

    if isinstance(value, Mapping):
        payload: dict[str, Any] = {}
        for idx, (key, item) in enumerate(value.items()):
            if idx >= 12:
                payload["_truncated"] = True
                break
            payload[str(key)] = _trim_value(item, depth=depth + 1)
        return payload

    if isinstance(value, list):
        trimmed = [_trim_value(item, depth=depth + 1) for item in value[:8]]
        if len(value) > 8:
            trimmed.append("...truncated...")
        return trimmed

    if isinstance(value, str) and len(value) > 280:
        return value[:280] + "..."

    return value


def _build_architecture_context(
    *,
    items: list[dict[str, Any]],
    connections: list[dict[str, Any]],
    project_name: str,
    project_id: str,
    project_description: str,
) -> dict[str, Any]:
    resources: list[dict[str, Any]] = []
    for item in items[:150]:
        resource_id = str(item.get("id") or "").strip()
        if not resource_id:
            continue
        properties = item.get("properties") if isinstance(item.get("properties"), Mapping) else {}
        resources.append(
            {
                "id": resource_id,
                "name": str(item.get("name") or ""),
                "resourceType": str(item.get("resourceType") or item.get("resourceName") or ""),
                "category": str(item.get("category") or ""),
                "properties": _trim_value(properties),
            }
        )

    edges: list[dict[str, Any]] = []
    for connection in connections[:250]:
        edges.append(
            {
                "id": str(connection.get("id") or ""),
                "fromId": str(connection.get("fromId") or ""),
                "toId": str(connection.get("toId") or ""),
                "direction": str(connection.get("direction") or "one-way"),
            }
        )

    return {
        "project": {
            "id": project_id,
            "name": project_name,
            "description": project_description,
        },
        "resources": resources,
        "connections": edges,
    }


def _slugify_waf_service_name(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""

    text = text.replace("_", "-").replace("/", "-")
    text = re.sub(r"[^a-z0-9-]+", "-", text)
    text = re.sub(r"-{2,}", "-", text).strip("-")
    return text


def _map_resource_to_waf_service(resource_type: Any, resource_name: Any = "") -> str:
    probe_parts = [str(resource_type or "").strip().lower(), str(resource_name or "").strip().lower()]
    probe = " ".join(part for part in probe_parts if part).strip()
    if not probe:
        return ""

    probe_flat = re.sub(r"[^a-z0-9]+", "", probe)
    for service_slug, patterns in WAF_SERVICE_PATTERN_MAP:
        for pattern in patterns:
            pattern_flat = re.sub(r"[^a-z0-9]+", "", str(pattern or "").lower())
            if not pattern_flat:
                continue
            if pattern_flat in probe_flat:
                return service_slug

    # Fallback: if input already looks like a supported service slug or alias.
    if probe_parts[0]:
        type_tail = probe_parts[0].split("/")[-1]
        candidate_slug = _slugify_waf_service_name(type_tail)
        if candidate_slug in WAF_SUPPORTED_SERVICE_SLUGS:
            return candidate_slug

    candidate_slug = _slugify_waf_service_name(probe)
    if candidate_slug in WAF_SUPPORTED_SERVICE_SLUGS:
        return candidate_slug

    return ""


def _extract_waf_services_from_architecture_context(architecture_context: Mapping[str, Any]) -> list[str]:
    resources = architecture_context.get("resources") if isinstance(architecture_context.get("resources"), list) else []
    detected: list[str] = []
    seen: set[str] = set()

    for resource in resources:
        if not isinstance(resource, Mapping):
            continue

        raw_type = str(resource.get("resourceType") or "").strip()
        raw_name = str(resource.get("name") or "").strip()
        if not raw_type and not raw_name:
            continue

        mapped_service = _map_resource_to_waf_service(raw_type, raw_name)
        if not mapped_service:
            continue

        if mapped_service in seen:
            continue
        seen.add(mapped_service)
        detected.append(mapped_service)

        if len(detected) >= 12:
            break

    return detected


def _parse_waf_markdown_to_findings(markdown_text: str, service_slug: str = "") -> list[dict[str, Any]]:
    """Parse a WAF service-guide markdown into structured per-pillar findings.

    The markdown is structured as H2 pillar sections (## Reliability, ## Security, etc.)
    each containing checklist items that start with ``> -`` or ``- ``.

    Returns a list of finding dicts, one per checklist item, tagged with the
    matching WAF pillar and ``source='azure_mcp'``.
    """
    findings: list[dict[str, Any]] = []
    current_pillar = ""

    for raw_line in markdown_text.splitlines():
        line = raw_line.strip()

        # Detect H2 pillar section headers (## Reliability, ## Security, …)
        h2_match = re.match(r"^##\s+(.+)$", line)
        if h2_match:
            section_title = h2_match.group(1).strip().lower()
            # Strip optional anchor ids like {#reliability}
            section_title = re.sub(r"\{#[^}]+\}", "", section_title).strip()
            current_pillar = _WAF_PILLAR_HEADERS.get(section_title, "")
            continue

        if not current_pillar:
            continue

        # Detect checklist bullets: "> - text", "- text", "* text"
        item_match = re.match(r"^>?\s*[-*]\s+(.+)$", line)
        if not item_match:
            continue

        raw = item_match.group(1).strip()
        # Strip markdown bold/italic markers
        raw = re.sub(r"\*{1,3}(.+?)\*{1,3}", r"\1", raw)
        # Convert markdown links [label](url) → label
        raw = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", raw)
        raw = raw.strip()
        if len(raw) < 20:
            continue

        title = raw[:120] + ("..." if len(raw) > 120 else "")
        finding: dict[str, Any] = {
            "title": title,
            "message": raw,
            "severity": "warning",
            "pillar": current_pillar,
            "source": "azure_mcp",
            "tool": "wellarchitectedframework",
        }
        if service_slug:
            finding["service"] = service_slug
        findings.append(finding)

    return findings


def _fetch_waf_service_guide(guide_url: str, service_slug: str = "") -> list[dict[str, Any]]:
    """Fetch a WAF service-guide markdown and parse it into per-pillar findings.

    Results are cached in ``_WAF_GUIDE_CACHE`` so repeated calls for the same URL
    (e.g. multiple validation passes) do not re-fetch from GitHub.

    Falls back to a single informational URL finding when the HTTP fetch fails.
    """
    if guide_url in _WAF_GUIDE_CACHE:
        return _WAF_GUIDE_CACHE[guide_url]

    fallback: list[dict[str, Any]] = [
        {
            "title": "Review Azure Well-Architected service guide",
            "message": (
                f"See the Microsoft Well-Architected service guide for"
                f" {service_slug or 'this service'}: {guide_url}"
            ),
            "severity": "info",
            "pillar": "operational_excellence",
            "source": "azure_mcp",
            "tool": "wellarchitectedframework",
        }
    ]
    if service_slug:
        fallback[0]["service"] = service_slug

    try:
        req = _urllib_request.Request(
            guide_url,
            headers={"User-Agent": "agentic-cloud-architect/1.0"},
        )
        with _urllib_request.urlopen(req, timeout=20) as resp:
            markdown_text = resp.read().decode("utf-8", errors="replace")

        parsed = _parse_waf_markdown_to_findings(markdown_text, service_slug=service_slug)
        result: list[dict[str, Any]] = parsed if parsed else fallback
    except Exception:
        result = fallback

    _WAF_GUIDE_CACHE[guide_url] = result
    return result


def _extract_candidate_findings(payload: Any) -> list[Any]:
    if isinstance(payload, str):
        text = str(payload or "").strip()
        if not text:
            return []
        # Accept guidance text as findings candidates - split by lines
        candidate_lines: list[str] = []
        for raw_line in text.splitlines():
            line = re.sub(r"^\s*(?:[-*•]+|\d+[.)])\s*", "", str(raw_line or "")).strip()
            if len(line) < 24:
                continue
            if _is_mcp_guidance_text(line):
                continue
            if line.lower().startswith(("schema", "architecture context", "azure mcp findings context")):
                continue
            candidate_lines.append(_truncate_text(line, max_chars=500))
            if len(candidate_lines) >= 20:
                break
        if candidate_lines:
            return candidate_lines
        return [_truncate_text(text, max_chars=800)]

    if isinstance(payload, list):
        return payload

    if not isinstance(payload, Mapping):
        return []

    structured = _extract_structured_findings(payload)
    if structured:
        return structured

    # Handle Well-Architected tool envelope payloads that return answer/question fields
    # instead of a direct findings array.
    waf_envelope_keys = {
        "answer",
        "question",
        "question-number",
        "total-questions",
        "next-question-needed",
        "state",
    }
    has_waf_envelope = any(key in payload for key in waf_envelope_keys)
    if has_waf_envelope:
        answer_text = _normalize_string(payload.get("answer"))
        question_text = _normalize_string(payload.get("question"))
        source_text = answer_text or question_text

        if not source_text and isinstance(payload.get("state"), str):
            parsed_state = _try_json(str(payload.get("state") or "").strip())
            if isinstance(parsed_state, Mapping):
                source_text = _normalize_string(parsed_state.get("answer") or parsed_state.get("question"))

        if source_text:
            candidate_items: list[dict[str, Any]] = []
            for raw_line in source_text.splitlines():
                line = re.sub(r"^\s*(?:[-*•]+|\d+[.)])\s*", "", str(raw_line or "")).strip()
                if len(line) < 24:
                    continue
                if _is_mcp_guidance_text(line):
                    continue

                pillar = _infer_pillar_from_text(line)
                item: dict[str, Any] = {
                    "title": "Well-Architected recommendation",
                    "message": _truncate_text(line, max_chars=500),
                    "severity": "warning" if pillar else "info",
                    "source": "azure_mcp",
                    "tool": "wellarchitectedframework",
                }
                if pillar:
                    item["pillar"] = pillar
                candidate_items.append(item)
                if len(candidate_items) >= 20:
                    break

            if candidate_items:
                return candidate_items

            fallback_pillar = _infer_pillar_from_text(source_text)
            fallback_item: dict[str, Any] = {
                "title": "Well-Architected recommendation",
                "message": _truncate_text(source_text, max_chars=800),
                "severity": "info",
                "source": "azure_mcp",
                "tool": "wellarchitectedframework",
            }
            if fallback_pillar:
                fallback_item["pillar"] = fallback_pillar
            return [fallback_item]

    # Handle guidance dict from MCP tools
    if "guidance" in payload and isinstance(payload.get("guidance"), str):
        guidance_text = str(payload.get("guidance")).strip()
        if guidance_text:
            candidate_lines = []
            for raw_line in guidance_text.splitlines():
                line = re.sub(r"^\s*(?:[-*•]+|\d+[.)])\s*", "", str(raw_line or "")).strip()
                if len(line) < 24:
                    continue
                candidate_lines.append(_truncate_text(line, max_chars=500))
                if len(candidate_lines) >= 20:
                    break
            if candidate_lines:
                return candidate_lines
            return [_truncate_text(guidance_text, max_chars=800)]

    candidates: list[Any] = []
    for key in (
        "findings",
        "recommendations",
        "checks",
        "issues",
        "value",
        "items",
        "data",
    ):
        value = payload.get(key)
        if isinstance(value, list):
            candidates.extend(value)

    response_object = payload.get("responseObject") if isinstance(payload.get("responseObject"), Mapping) else None
    if response_object:
        for key in ("findings", "recommendations", "checks", "issues"):
            value = response_object.get(key)
            if isinstance(value, list):
                candidates.extend(value)

    results = payload.get("results") if isinstance(payload.get("results"), Mapping) else None
    if results:
        response_object = results.get("responseObject") if isinstance(results.get("responseObject"), Mapping) else None
        if response_object:
            for key in ("findings", "recommendations", "checks", "issues"):
                value = response_object.get(key)
                if isinstance(value, list):
                    candidates.extend(value)

    result_list = payload.get("results") if isinstance(payload.get("results"), list) else None
    if result_list:
        textual_results: list[Any] = []
        for item in result_list:
            if isinstance(item, Mapping):
                for key in ("findings", "recommendations", "checks", "issues", "value", "items"):
                    value = item.get(key)
                    if isinstance(value, list):
                        candidates.extend(value)
                continue

            if isinstance(item, str):
                text = _normalize_string(item)
                if not text:
                    continue
                if _is_mcp_guidance_text(text):
                    continue

                lower_text = text.lower()
                if "service is not available" in lower_text and "supported services include" in lower_text:
                    continue

                url_match = re.search(r"https?://\S+", text)
                if url_match:
                    guide_url = url_match.group(0).rstrip(").,;")
                    # Derive service slug from URL path (e.g. "virtual-network" from
                    # "…/service-guides/virtual-network.md")
                    _slug = re.sub(r"\.md$", "", guide_url.rstrip("/").rsplit("/", 1)[-1])
                    fetched = _fetch_waf_service_guide(guide_url, service_slug=_slug)
                    textual_results.extend(fetched)
                else:
                    textual_results.append(_truncate_text(text, max_chars=800))

        if textual_results:
            candidates.extend(textual_results)

    if candidates:
        return candidates

    display_hint = str(payload.get("displayHint") or "").strip()
    if display_hint:
        if _is_mcp_guidance_text(display_hint):
            return []
        return [
            line.strip()
            for line in display_hint.splitlines()
            if line.strip() and not _is_mcp_guidance_text(line)
        ]

    return []


def _normalize_pillar_name(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        return ""

    normalized = raw.replace("-", "_").replace(" ", "_")
    alias_map = {
        "cost": "cost_optimization",
        "costoptimization": "cost_optimization",
        "cost_optimisation": "cost_optimization",
        "operations": "operational_excellence",
        "operation": "operational_excellence",
        "operational": "operational_excellence",
        "performance": "performance_efficiency",
        "perf": "performance_efficiency",
    }
    candidate = alias_map.get(normalized, normalized)
    if candidate in WELL_ARCHITECTED_PILLARS:
        return candidate
    return ""


def _infer_pillar_from_text(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""

    keyword_map: list[tuple[str, tuple[str, ...]]] = [
        (
            "reliability",
            (
                "reliability",
                "availability",
                "resilien",
                "redundan",
                "failover",
                "disaster recovery",
                "rto",
                "rpo",
            ),
        ),
        (
            "security",
            (
                "security",
                "identity",
                "rbac",
                "encryption",
                "key vault",
                "compliance",
                "threat",
                "vulnerability",
            ),
        ),
        (
            "cost_optimization",
            (
                "cost",
                "finops",
                "rightsizing",
                "reserved",
                "savings",
                "budget",
                "waste",
            ),
        ),
        (
            "operational_excellence",
            (
                "operational",
                "ops",
                "monitoring",
                "logging",
                "observability",
                "runbook",
                "automation",
                "incident",
            ),
        ),
        (
            "performance_efficiency",
            (
                "performance",
                "latency",
                "throughput",
                "scale",
                "scalability",
                "caching",
                "capacity",
            ),
        ),
    ]

    for pillar, keywords in keyword_map:
        if any(keyword in text for keyword in keywords):
            return pillar
    return ""


def _coerce_structured_recommendation_item(
    item: Any,
    *,
    classification: str,
    default_pillar: str,
) -> dict[str, Any] | None:
    if not isinstance(item, Mapping):
        return None

    normalized = dict(item)

    title = _normalize_string(
        normalized.get("title")
        or normalized.get("name")
        or normalized.get("issue")
        or normalized.get("recommendation")
        or normalized.get("action")
    )
    message = _normalize_string(
        normalized.get("message")
        or normalized.get("description")
        or normalized.get("details")
        or normalized.get("rationale")
        or normalized.get("guidance")
    )

    if not title and not message:
        return None

    if not title:
        title = "Recommendation"
    if not message:
        message = title

    normalized["title"] = title
    normalized["message"] = _truncate_text(message, max_chars=900)
    normalized["classification"] = classification
    normalized["source"] = _normalize_string(normalized.get("source"), "reasoning_model")

    pillar = _normalize_pillar_name(
        normalized.get("pillar")
        or normalized.get("category")
        or default_pillar
    )
    if pillar:
        normalized["pillar"] = pillar

    if not _normalize_string(normalized.get("severity")):
        normalized["severity"] = "warning" if classification == "priority_improvement" else "info"

    if not isinstance(normalized.get("target"), Mapping):
        normalized["target"] = {}

    return normalized


def _extract_structured_findings(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    extracted: list[dict[str, Any]] = []

    def append_items(raw_items: Any, *, classification: str, default_pillar: str) -> None:
        if not isinstance(raw_items, list):
            return
        for raw_item in raw_items:
            normalized = _coerce_structured_recommendation_item(
                raw_item,
                classification=classification,
                default_pillar=default_pillar,
            )
            if normalized:
                extracted.append(normalized)

    append_items(
        payload.get("priority_improvements"),
        classification="priority_improvement",
        default_pillar="operational_excellence",
    )
    append_items(
        payload.get("quick_configuration_fixes"),
        classification="quick_configuration_fix",
        default_pillar="operational_excellence",
    )
    append_items(
        payload.get("configuration_issues"),
        classification="quick_configuration_fix",
        default_pillar="operational_excellence",
    )

    recommendations = payload.get("recommendations")
    if isinstance(recommendations, Mapping):
        for pillar_key, items in recommendations.items():
            normalized_pillar = _normalize_pillar_name(pillar_key)
            append_items(
                items,
                classification="recommendation",
                default_pillar=normalized_pillar or "operational_excellence",
            )
    elif isinstance(recommendations, list):
        append_items(
            recommendations,
            classification="recommendation",
            default_pillar="operational_excellence",
        )

    return extracted


def _normalize_operation(
    operation: Any,
    *,
    valid_resource_ids: set[str],
    valid_connection_ids: set[str],
) -> dict[str, Any] | None:
    if not isinstance(operation, Mapping):
        return None

    op = _normalize_operation_name(operation.get("op") or operation.get("type") or operation.get("action"))
    if not op:
        return None

    if op == "set_resource_property":
        resource_id = _normalize_string(operation.get("resourceId") or operation.get("itemId") or operation.get("nodeId"))
        field = _normalize_string(operation.get("field") or operation.get("path") or operation.get("property"))
        if not resource_id or not field or resource_id not in valid_resource_ids:
            return None
        return {
            "op": op,
            "resourceId": resource_id,
            "field": field,
            "value": operation.get("value"),
        }

    if op == "set_resource_name":
        resource_id = _normalize_string(operation.get("resourceId") or operation.get("itemId") or operation.get("nodeId"))
        value = _normalize_string(operation.get("value"))
        if not resource_id or not value or resource_id not in valid_resource_ids:
            return None
        return {
            "op": op,
            "resourceId": resource_id,
            "value": value,
        }

    if op == "remove_connection":
        connection_id = _normalize_string(operation.get("connectionId") or operation.get("id"))
        if not connection_id or connection_id not in valid_connection_ids:
            return None
        return {
            "op": op,
            "connectionId": connection_id,
        }

    if op == "set_connection_direction":
        connection_id = _normalize_string(operation.get("connectionId") or operation.get("id"))
        direction = _normalize_string(operation.get("direction"), "one-way").lower()
        direction = "bi" if direction in {"bi", "both", "two-way", "two_way"} else "one-way"
        if not connection_id or connection_id not in valid_connection_ids:
            return None
        return {
            "op": op,
            "connectionId": connection_id,
            "direction": direction,
        }

    if op == "add_connection":
        from_id = _normalize_string(operation.get("fromId") or operation.get("sourceId"))
        to_id = _normalize_string(operation.get("toId") or operation.get("targetId"))
        direction = _normalize_string(operation.get("direction"), "one-way").lower()
        direction = "bi" if direction in {"bi", "both", "two-way", "two_way"} else "one-way"
        if not from_id or not to_id:
            return None
        if from_id not in valid_resource_ids or to_id not in valid_resource_ids:
            return None
        return {
            "op": op,
            "fromId": from_id,
            "toId": to_id,
            "direction": direction,
        }

    if op == "remove_resource":
        resource_id = _normalize_string(operation.get("resourceId") or operation.get("itemId") or operation.get("nodeId"))
        if not resource_id or resource_id not in valid_resource_ids:
            return None
        return {
            "op": op,
            "resourceId": resource_id,
        }

    return None


def _normalize_finding(
    finding: Any,
    *,
    index: int,
    valid_resource_ids: set[str],
    valid_connection_ids: set[str],
) -> dict[str, Any] | None:
    if isinstance(finding, str):
        text = _normalize_string(finding)
        if not text:
            return None
        if _is_mcp_guidance_text(text):
            return None
        normalized: dict[str, Any] = {
            "id": _normalize_finding_id("", index),
            "severity": "info",
            "title": "Recommendation",
            "message": text,
            "target": {},
        }
        pillar = _infer_pillar_from_text(text)
        if pillar:
            normalized["pillar"] = pillar
        return normalized

    if not isinstance(finding, Mapping):
        return None

    short_description = finding.get("shortDescription") if isinstance(finding.get("shortDescription"), Mapping) else {}
    short_problem = _normalize_string(short_description.get("problem"))
    short_solution = _normalize_string(short_description.get("solution"))
    remediation = finding.get("remediation") if isinstance(finding.get("remediation"), Mapping) else {}
    remediation_desc = _normalize_string(remediation.get("description") if isinstance(remediation, Mapping) else "")

    severity = _normalize_severity(
        finding.get("severity")
        or finding.get("level")
        or finding.get("status")
        or finding.get("impact")
        or finding.get("riskLevel")
        or finding.get("priority")
    )
    title = _normalize_string(
        finding.get("title")
        or finding.get("name")
        or finding.get("recommendationType")
        or finding.get("category")
        or short_problem,
        "Recommendation",
    )
    message = _normalize_string(
        finding.get("message")
        or finding.get("reason")
        or finding.get("description")
        or remediation_desc
        or short_solution
        or short_problem,
    )

    if not message and title:
        message = title
    if short_problem and short_solution and short_solution not in message:
        message = f"{short_problem} Suggested action: {short_solution}"
    message = _truncate_text(message, max_chars=900)

    guidance_text_probe = " ".join(
        [
            _normalize_string(title),
            _normalize_string(message),
            _normalize_string(finding.get("recommendationType")),
            _normalize_string(finding.get("category")),
        ]
    ).strip()
    if _is_mcp_guidance_text(guidance_text_probe):
        return None

    target_input = finding.get("target") if isinstance(finding.get("target"), Mapping) else {}
    resource_id = _normalize_string(
        target_input.get("resourceId")
        or finding.get("resourceId")
        or finding.get("itemId")
        or finding.get("nodeId")
    )
    connection_id = _normalize_string(
        target_input.get("connectionId")
        or finding.get("connectionId")
        or finding.get("edgeId")
    )
    field = _normalize_string(target_input.get("field") or finding.get("field") or finding.get("path"))

    target: dict[str, Any] = {}
    if resource_id and resource_id in valid_resource_ids:
        target["resourceId"] = resource_id
    if connection_id and connection_id in valid_connection_ids:
        target["connectionId"] = connection_id
    if field:
        target["field"] = field

    fix_input = finding.get("fix") if isinstance(finding.get("fix"), Mapping) else {}
    operations_input = fix_input.get("operations") if isinstance(fix_input.get("operations"), list) else finding.get("operations")
    operations: list[dict[str, Any]] = []
    if isinstance(operations_input, list):
        for raw_operation in operations_input[:8]:
            normalized = _normalize_operation(
                raw_operation,
                valid_resource_ids=valid_resource_ids,
                valid_connection_ids=valid_connection_ids,
            )
            if normalized:
                operations.append(normalized)

    fix: dict[str, Any] | None = None
    if operations:
        fix_label = _normalize_string(fix_input.get("label") or finding.get("fixLabel"), "Apply suggested fix")
        fix = {
            "label": fix_label,
            "operations": operations,
        }

    normalized_finding: dict[str, Any] = {
        "id": _normalize_finding_id(finding.get("id") or finding.get("key"), index),
        "severity": severity,
        "title": title,
        "message": message,
        "target": target,
    }
    
    # Preserve source and pillar fields if they exist
    if "source" in finding:
        normalized_finding["source"] = finding["source"]
    if "pillar" in finding:
        normalized_finding["pillar"] = finding["pillar"]
    if "classification" in finding:
        classification = _normalize_string(finding.get("classification")).lower()
        if classification:
            normalized_finding["classification"] = classification
    if "tool" in finding:
        normalized_finding["tool"] = finding["tool"]
    
    if fix:
        normalized_finding["fix"] = fix

    return normalized_finding


def _dedupe_findings(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for finding in findings:
        signature = "|".join(
            [
                str(finding.get("severity") or ""),
                str(finding.get("title") or ""),
                str(finding.get("message") or ""),
                str((finding.get("target") or {}).get("resourceId") or ""),
                str((finding.get("target") or {}).get("connectionId") or ""),
                str((finding.get("target") or {}).get("field") or ""),
            ]
        ).strip().lower()
        if not signature:
            continue
        if signature in seen:
            continue
        seen.add(signature)
        deduped.append(finding)
        if len(deduped) >= DEFAULT_VALIDATION_MAX_FINDINGS:
            break
    return deduped


def _generate_fallback_multi_pillar_findings(
    items: list[dict[str, Any]],
    connections: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Generate synthetic multi-pillar recommendations when MCP tools fail."""
    findings: list[dict[str, Any]] = []
    resource_count = len(items)
    
    pillars = [
        ("reliability", "Ensure high availability with redundancy, failover, and health monitoring"),
        ("security", "Protect data with identity management, encryption, and compliance controls"),
        ("cost_optimization", "Optimize spending through rightsizing, automation, and scale policies"),
        ("operational_excellence", "Enable operations with monitoring, logging, and runbooks"),
        ("performance_efficiency", "Scale performance through caching, regions, and distribution"),
    ]
    
    if resource_count == 0:
        # Empty architecture - suggest foundational guardrails
        for idx, (pillar, description) in enumerate(pillars):
            findings.append({
                "id": f"{pillar}-1",
                "severity": "info",
                "title": f"Add {pillar.replace('_', ' ')} guardrails",
                "message": description + ". Start by adding core infrastructure components with proper configuration.",
                "source": "synthesis",
                "target": {},
                "pillar": pillar,
            })
    else:
        # Existing architecture - suggest improvements per pillar
        pillar_findings = {
            "reliability": [
                ("warning", "Add redundancy", "Consider adding backup/secondary instances for critical resources to prevent single points of failure"),
                ("info", "Configure health monitoring", "Set up health probes and auto-healing to detect and recover from failures"),
                ("info", "Plan disaster recovery", "Document and test recovery procedures for critical components"),
            ],
            "security": [
                ("warning", "Implement identity controls", "Use managed identities and role-based access control (RBAC) for all resources"),
                ("warning", "Enable encryption", "Encrypt data at rest and in transit for sensitive resources"),
                ("info", "Add network isolation", "Consider network security groups, firewalls, or private endpoints"),
            ],
            "cost_optimization": [
                ("info", "Monitor resource utilization", "Set up Azure Cost Management to track and optimize spending"),
                ("info", "Review scale settings", "Ensure autoscaling policies match actual demand patterns"),
                ("info", "Plan for reserved capacity", "Consider reserved instances or savings plans for predictable workloads"),
            ],
            "operational_excellence": [
                ("warning", "Add comprehensive logging", "Enable diagnostic logs for all resources to troubleshoot issues"),
                ("warning", "Set up alerting", "Configure alerts for critical metrics to enable rapid incident response"),
                ("info", "Document runbooks", "Create operational procedures for common tasks and incidents"),
            ],
            "performance_efficiency": [
                ("info", "Optimize caching strategy", "Use caching for frequently accessed data to reduce latency"),
                ("info", "Evaluate regional distribution", "Consider geo-distribution for global scale and reduced latency"),
                ("info", "Review throughput configuration", "Ensure resources are sized and configured for expected workloads"),
            ],
        }
        
        # Add 1-2 findings per pillar
        for idx, (pillar, recommendations) in enumerate(pillar_findings.items()):
            for rec_idx, (severity, title, message) in enumerate(recommendations[:2]):
                findings.append({
                    "id": f"{pillar}-{rec_idx+1}",
                    "severity": severity,
                    "title": title,
                    "message": message,
                    "source": "synthesis",
                    "target": {},
                    "pillar": pillar,
                })
    
    return findings[:15]  # Limit to 15 synthetic findings


def _group_findings(findings: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped = {
        "failure": [],
        "warning": [],
        "info": [],
    }
    for finding in findings:
        severity = _normalize_severity(finding.get("severity"))
        grouped[severity].append(finding)
    return grouped


def _organize_into_recommendations_and_quick_fixes(
    findings: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Reorganize findings into:
    - Recommendations: Well-Architected Framework assessments grouped by pillar
    - Quick Fixes: Canvas configuration issues grouped by type (priority_improvements, quick_configuration_fixes)
    """
    recommendations = {
        "reliability": [],
        "security": [],
        "cost_optimization": [],
        "operational_excellence": [],
        "performance_efficiency": [],
    }
    quick_fixes = {
        "priority_improvements": [],
        "quick_configuration_fixes": [],
    }

    for finding in findings:
        source = str(finding.get("source") or "").strip().lower()
        pillar = str(finding.get("pillar") or "").strip().lower()
        title = str(finding.get("title") or "").strip()
        severity = _normalize_severity(finding.get("severity"))
        classification = str(finding.get("classification") or "").strip().lower()

        # Determine if finding is a recommendation or quick fix
        # Quick fixes are canvas configuration issues (deterministic source)
        # Recommendations are well-architected assessments (MCP and reasoning models)
        is_quick_fix = source == "deterministic" or classification in {
            "priority_improvement",
            "quick_configuration_fix",
        }

        if is_quick_fix:
            # Categorize quick fixes
            quick_fix_item = {
                "id": finding.get("id", f"qf-{len(quick_fixes['priority_improvements']) + len(quick_fixes['quick_configuration_fixes']) + 1}"),
                "severity": severity,
                "title": title,
                "message": finding.get("message", ""),
                "target": finding.get("target", {}),
                "fix": finding.get("fix"),
            }

            # Determine if it's a priority improvement or quick config fix
            if classification == "quick_configuration_fix":
                quick_fixes["quick_configuration_fixes"].append(quick_fix_item)
            elif classification == "priority_improvement":
                quick_fixes["priority_improvements"].append(quick_fix_item)
            elif severity in {"failure", "warning"}:
                quick_fixes["priority_improvements"].append(quick_fix_item)
            else:
                quick_fixes["quick_configuration_fixes"].append(quick_fix_item)
        else:
            # This is a recommendation - organize by pillar
            if pillar and pillar in recommendations:
                rec_item = {
                    "id": finding.get("id", f"{pillar}-{len(recommendations[pillar]) + 1}"),
                    "severity": severity,
                    "title": title,
                    "message": finding.get("message", ""),
                    "source": source,
                    "tool": finding.get("tool", ""),
                    "target": finding.get("target", {}),
                }
                recommendations[pillar].append(rec_item)
            else:
                # If no pillar specified, try to infer from title or message
                full_text = (title + " " + finding.get("message", "")).lower()
                found_pillar = None
                for p in WELL_ARCHITECTED_PILLARS:
                    if p.replace("_", " ") in full_text:
                        found_pillar = p
                        break

                if found_pillar:
                    pillar = found_pillar
                else:
                    # Default to operational excellence if can't infer
                    pillar = "operational_excellence"

                rec_item = {
                    "id": finding.get("id", f"{pillar}-{len(recommendations[pillar]) + 1}"),
                    "severity": severity,
                    "title": title,
                    "message": finding.get("message", ""),
                    "source": source,
                    "tool": finding.get("tool", ""),
                    "target": finding.get("target", {}),
                }
                recommendations[pillar].append(rec_item)

    return {
        "recommendations": recommendations,
        "quick_fixes": quick_fixes,
    }



def _extract_and_normalize_findings(
    payload: Any,
    *,
    valid_resource_ids: set[str],
    valid_connection_ids: set[str],
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    candidates = _extract_candidate_findings(payload)
    for idx, candidate in enumerate(candidates):
        normalized = _normalize_finding(
            candidate,
            index=idx,
            valid_resource_ids=valid_resource_ids,
            valid_connection_ids=valid_connection_ids,
        )
        if normalized:
            findings.append(normalized)
    return findings


def _build_architecture_summary_text(
    *,
    summary: Mapping[str, Any],
    dual_pass_complete: bool,
) -> str:
    failures = int(summary.get("failure") or 0)
    warnings = int(summary.get("warning") or 0)
    info = int(summary.get("info") or 0)
    total = int(summary.get("total") or 0)
    run_mode = "dual-pass" if dual_pass_complete else "partial"
    return (
        f"Validation ({run_mode}) identified {total} findings: "
        f"{failures} failure, {warnings} warning, {info} info."
    )


def _build_pillar_assessment(recommendations: Mapping[str, Any]) -> dict[str, str]:
    assessment: dict[str, str] = {}
    for pillar in WELL_ARCHITECTED_PILLARS:
        items = recommendations.get(pillar) if isinstance(recommendations, Mapping) else []
        findings = items if isinstance(items, list) else []
        if not findings:
            assessment[pillar] = "No major recommendations identified."
            continue

        severities = {
            _normalize_severity(item.get("severity"))
            for item in findings
            if isinstance(item, Mapping)
        }
        if "failure" in severities:
            status_text = "High-priority improvements required."
        elif "warning" in severities:
            status_text = "Moderate improvements recommended."
        else:
            status_text = "Minor optimizations available."

        assessment[pillar] = f"{status_text} {len(findings)} recommendation(s)."

    return assessment


def _format_pillar_title(pillar: str) -> str:
    safe = str(pillar or "").strip().replace("_", " ")
    if not safe:
        return "Operational Excellence"
    return " ".join(part.capitalize() for part in safe.split())


def _coerce_recommendations_for_pillar(
    recommendations: Mapping[str, Any],
    pillar: str,
    *,
    limit: int = MAX_PILLAR_RECOMMENDATIONS_FOR_HUMANIZATION,
) -> list[dict[str, Any]]:
    items = recommendations.get(pillar) if isinstance(recommendations, Mapping) else []
    if not isinstance(items, list):
        return []

    normalized: list[dict[str, Any]] = []
    for item in items[: max(1, int(limit))]:
        if not isinstance(item, Mapping):
            continue
        normalized.append(
            {
                "id": _normalize_string(item.get("id")),
                "severity": _normalize_severity(item.get("severity")),
                "title": _truncate_text(item.get("title"), max_chars=180),
                "message": _truncate_text(item.get("message"), max_chars=400),
                "source": _normalize_string(item.get("source"), "unknown"),
                "tool": _normalize_string(item.get("tool")),
            }
        )
    return normalized


def _fallback_pillar_impact_lines(pillar: str, recommendations_received: list[dict[str, Any]]) -> list[str]:
    if not recommendations_received:
        return []

    impact_lines: list[str] = []
    for item in recommendations_received[:6]:
        title = _normalize_string(item.get("title"), "Recommendation")
        message = _normalize_string(item.get("message"))
        severity = _normalize_severity(item.get("severity"))

        if message:
            prefix = "High risk" if severity == "failure" else "Risk"
            line = f"{prefix} for {_format_pillar_title(pillar)}: {title}. {message}"
        else:
            line = f"Risk for {_format_pillar_title(pillar)}: {title}."

        impact_lines.append(_truncate_text(line, max_chars=280))

    return impact_lines


def _fallback_pillar_action_plan(
    pillar: str,
    recommendations_received: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not recommendations_received:
        return [
            {
                "category": "Should Do",
                "items": [],
            },
            {
                "category": "Could Do",
                "items": [],
            },
        ]

    should_do: list[str] = []
    could_do: list[str] = []

    for item in recommendations_received:
        title = _normalize_string(item.get("title"), "Recommendation")
        message = _normalize_string(item.get("message"))
        action_text = f"{title}. {message}" if message else title
        action_text = _truncate_text(action_text, max_chars=220)

        severity = _normalize_severity(item.get("severity"))
        if severity in {"failure", "warning"}:
            should_do.append(action_text)
        else:
            could_do.append(action_text)

    if not should_do and recommendations_received:
        should_do.extend(could_do[:2])
        could_do = could_do[2:]

    if not should_do:
        should_do.append(
            f"Define a prioritized {_format_pillar_title(pillar)} implementation plan for the current architecture."
        )

    if not could_do:
        could_do.append(
            f"Add incremental {_format_pillar_title(pillar)} optimizations after critical fixes are completed."
        )

    return [
        {
            "category": "Should Do",
            "items": should_do[:6],
        },
        {
            "category": "Could Do",
            "items": could_do[:6],
        },
    ]


def _normalize_humanized_impact_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    lines: list[str] = []
    for raw in value:
        line = _normalize_string(raw)
        if not line:
            continue
        lines.append(_truncate_text(line, max_chars=280))
    return lines[:8]


def _normalize_humanized_action_plan(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []

    plan: list[dict[str, Any]] = []
    for raw in value:
        if not isinstance(raw, Mapping):
            continue
        category = _normalize_string(raw.get("category") or raw.get("title"))
        if not category:
            continue
        items = raw.get("items") if isinstance(raw.get("items"), list) else []
        normalized_items: list[str] = []
        for raw_item in items:
            item_text = _normalize_string(raw_item)
            if not item_text:
                continue
            normalized_items.append(_truncate_text(item_text, max_chars=220))
        plan.append({
            "category": category,
            "items": normalized_items[:8],
        })

    return plan[:6]


def _build_pillar_humanization_prompt(
    *,
    pillar: str,
    project_name: str,
    project_description: str,
    architecture_context: Mapping[str, Any],
    recommendations_received: list[dict[str, Any]],
    mcp_findings: list[dict[str, Any]],
    model_findings: list[dict[str, Any]],
) -> str:
    pillar_title = _format_pillar_title(pillar)
    context_payload = {
        "project": {
            "name": project_name,
            "description": _truncate_text(project_description, max_chars=1200),
        },
        "pillar": pillar,
        "recommendations_received": recommendations_received,
        "architecture_context": _trim_value(architecture_context, depth=2),
        "mcp_findings": _serialize_findings_for_reasoning_context(mcp_findings, limit=8),
        "reasoning_findings": _serialize_findings_for_reasoning_context(model_findings, limit=8),
    }

    return "\n".join(
        [
            "You are an Azure Principal Architect writing implementation guidance for one Well-Architected pillar.",
            "Return strict JSON only.",
            "JSON schema:",
            '{"architecture_impact":["..."],"action_plan":[{"category":"Should Do","items":["..."]},{"category":"Could Do","items":["..."]}]}',
            f"Pillar: {pillar_title}",
            "Rules:",
            "- architecture_impact: 3-6 bullets that explain how recommendations affect THIS architecture.",
            "- action_plan: categorized actionable steps with concrete Azure actions.",
            "- Include both Should Do and Could Do categories whenever possible.",
            "- Avoid repeating recommendation text verbatim; explain consequences and next actions.",
            "- Keep each bullet concise and implementation-oriented.",
            "Context:",
            json.dumps(context_payload, ensure_ascii=False),
        ]
    )


def _build_pillar_details(
    *,
    app_settings: Mapping[str, Any],
    architecture_context: Mapping[str, Any],
    recommendations: Mapping[str, Any],
    project_name: str,
    project_description: str,
    foundry_agent_id: str | None,
    foundry_thread_id: str | None,
    mcp_findings: list[dict[str, Any]],
    model_findings: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    details: dict[str, dict[str, Any]] = {}

    for pillar in WELL_ARCHITECTED_PILLARS:
        recommendations_received = _coerce_recommendations_for_pillar(recommendations, pillar)
        details[pillar] = {
            "recommendations_received": recommendations_received,
            "architecture_impact": _fallback_pillar_impact_lines(pillar, recommendations_received),
            "action_plan": _fallback_pillar_action_plan(pillar, recommendations_received),
            "generation": {
                "source": "fallback",
                "status": "ready",
                "explanation": "Generated from collected findings.",
            },
        }

    provider = str(app_settings.get("modelProvider") or "").strip().lower()
    if provider != "azure-foundry":
        for pillar in WELL_ARCHITECTED_PILLARS:
            details[pillar]["generation"] = {
                "source": "fallback",
                "status": "skipped",
                "explanation": "Azure Foundry provider is not active. Used deterministic humanization.",
            }
        return details

    model_name = _first_non_empty(
        app_settings,
        "foundryModelReasoning",
        "modelReasoning",
        "foundryModelFast",
        "modelFast",
    )
    safe_agent_id = str(foundry_agent_id or app_settings.get("foundryValidationAgentId") or "").strip()
    safe_thread_id = str(foundry_thread_id or app_settings.get("foundryDefaultThreadId") or "").strip()
    if not model_name or not safe_agent_id or not safe_thread_id:
        for pillar in WELL_ARCHITECTED_PILLARS:
            details[pillar]["generation"] = {
                "source": "fallback",
                "status": "skipped",
                "explanation": "Foundry model/agent/thread is not configured. Used deterministic humanization.",
            }
        return details

    try:
        base_connection = FoundryConnectionSettings.from_app_settings(app_settings)
    except FoundryConfigurationError as exc:
        for pillar in WELL_ARCHITECTED_PILLARS:
            details[pillar]["generation"] = {
                "source": "fallback",
                "status": "skipped",
                "explanation": f"Foundry configuration is incomplete: {exc}",
            }
        return details

    connection = FoundryConnectionSettings(
        endpoint=base_connection.endpoint,
        tenant_id=base_connection.tenant_id,
        client_id=base_connection.client_id,
        client_secret=base_connection.client_secret,
        model_deployment=str(model_name).strip(),
        api_version=base_connection.api_version,
    )
    runner = FoundryAssistantRunner(
        connection,
        timeout_seconds=int(PILLAR_HUMANIZATION_TIMEOUT_SECONDS),
        agent_name="architecture-validation-agent",
    )

    for pillar in WELL_ARCHITECTED_PILLARS:
        recommendations_received = details[pillar].get("recommendations_received")
        if not isinstance(recommendations_received, list) or not recommendations_received:
            details[pillar]["architecture_impact"] = []
            details[pillar]["action_plan"] = [
                {"category": "Should Do", "items": []},
                {"category": "Could Do", "items": []},
            ]
            details[pillar]["generation"] = {
                "source": "fallback",
                "status": "skipped",
                "explanation": "No recommendations were available for this pillar.",
            }
            continue

        prompt = _build_pillar_humanization_prompt(
            pillar=pillar,
            project_name=project_name,
            project_description=project_description,
            architecture_context=architecture_context,
            recommendations_received=recommendations_received,
            mcp_findings=mcp_findings,
            model_findings=model_findings,
        )

        try:
            result = runner.run_assistant(
                assistant_id=safe_agent_id,
                thread_id=safe_thread_id,
                content=prompt,
                allow_stateless_retry=True,
            )
            parsed = _extract_json_from_text(str(result.response_text or ""))
            parsed_mapping = parsed if isinstance(parsed, Mapping) else {}

            impact_lines = _normalize_humanized_impact_list(
                parsed_mapping.get("architecture_impact")
                or parsed_mapping.get("impact")
                or parsed_mapping.get("translation")
            )
            action_plan = _normalize_humanized_action_plan(
                parsed_mapping.get("action_plan")
                or parsed_mapping.get("actions")
                or parsed_mapping.get("suggestions")
            )

            if impact_lines:
                details[pillar]["architecture_impact"] = impact_lines
            if action_plan:
                details[pillar]["action_plan"] = action_plan

            details[pillar]["generation"] = {
                "source": "reasoning_model",
                "status": "connected",
                "explanation": "Humanized through Foundry reasoning model.",
            }
        except Exception as exc:
            details[pillar]["generation"] = {
                "source": "fallback",
                "status": "failed",
                "explanation": f"Reasoning-model humanization failed: {exc}",
            }

    return details


def _serialize_findings_for_reasoning_context(
    findings: list[dict[str, Any]],
    *,
    limit: int = 12,
) -> list[dict[str, Any]]:
    serialized: list[dict[str, Any]] = []
    for finding in findings[:limit]:
        if not isinstance(finding, Mapping):
            continue
        serialized.append(
            {
                "id": str(finding.get("id") or "").strip(),
                "severity": _normalize_severity(finding.get("severity")),
                "title": str(finding.get("title") or "").strip(),
                "message": str(finding.get("message") or "").strip(),
                "source": str(finding.get("source") or "").strip(),
                "target": _trim_value(finding.get("target") if isinstance(finding.get("target"), Mapping) else {}),
                "fix": _trim_value(finding.get("fix") if isinstance(finding.get("fix"), Mapping) else {}),
            }
        )
    return serialized


@dataclass
class McpToolRequest:
    label: str
    tool_candidates: list[str]
    argument_variants: list[dict[str, Any]]


@dataclass
class McpToolResponse:
    label: str
    tool_name: str
    payload: Any | None
    error: str = ""
    attempts: list[dict[str, Any]] | None = None
    duration_ms: int = 0


async def _invoke_mcp_validation(
    credentials: AzureMcpCredentials,
    requests: list[McpToolRequest],
) -> list[McpToolResponse]:
    _log_validation_event(
        "mcp.validation",
        level="info",
        step="session-start",
        details={"command": "@azure/mcp server start"},
    )

    try:
        mcp_module = importlib.import_module("mcp")
        mcp_stdio_module = importlib.import_module("mcp.client.stdio")
    except ModuleNotFoundError as exc:
        _log_validation_event(
            "mcp.validation",
            level="error",
            step="failed",
            details={"error": str(exc)},
        )
        raise RuntimeError("Python package 'mcp' is required for Azure MCP validation") from exc

    client_session_cls = getattr(mcp_module, "ClientSession")
    server_parameters_cls = getattr(mcp_module, "StdioServerParameters")
    stdio_client = getattr(mcp_stdio_module, "stdio_client")

    mcp_env = {
        "AZURE_CLIENT_ID": credentials.client_id,
        "AZURE_CLIENT_SECRET": credentials.client_secret,
        "AZURE_TENANT_ID": credentials.tenant_id,
    }
    if credentials.subscription_id:
        mcp_env["AZURE_SUBSCRIPTION_ID"] = credentials.subscription_id

    server_params = server_parameters_cls(
        command="npx",
        args=["-y", "@azure/mcp@latest", "server", "start"],
        env=mcp_env,
    )

    responses: list[McpToolResponse] = []

    async with stdio_client(server_params) as (read_stream, write_stream):
        async with client_session_cls(read_stream, write_stream) as session:
            await session.initialize()
            for request in requests:
                responses.append(await _call_mcp_tool_with_fallbacks(session, request))

    _log_validation_event(
        "mcp.validation",
        level="info",
        step="session-completed",
    )
    return responses


async def _call_mcp_tool_with_fallbacks(session: Any, request: McpToolRequest) -> McpToolResponse:
    last_error = ""
    candidates = [str(name or "").strip() for name in request.tool_candidates if str(name or "").strip()]
    argument_variants = [dict(item) for item in request.argument_variants if isinstance(item, Mapping)] or [{}]

    attempts: list[dict[str, Any]] = []
    overall_start = time.perf_counter()

    for tool_name in candidates:
        for variant_index, args in enumerate(argument_variants):
            attempt_status = "failed"
            attempt_error = ""
            payload_text: str | None = None
            start = time.perf_counter()
            recorded = False
            call_args = dict(args or {})
            if tool_name == "cloudarchitect_design":
                command_name = str(call_args.get("command") or "").strip().lower()
                if command_name in {"cloudarchitect", "cloudarchitect_design"}:
                    call_args.pop("command", None)
            try:
                result = await asyncio.wait_for(
                    session.call_tool(tool_name, call_args),
                    timeout=float(MCP_VALIDATION_TIMEOUT_SECONDS),
                )
                payload_text = _extract_tool_text(getattr(result, "content", result))
                # Guidance from MCP tools can be valid recommendation text, but
                # "tool not found" guidance should trigger fallback to next alias.
                if _is_mcp_guidance_text(payload_text):
                    guidance_text = str(payload_text or "").strip()
                    if re.search(r"^the tool\s+.+\s+was not found", guidance_text, flags=re.IGNORECASE):
                        attempt_status = "failed"
                        attempt_error = guidance_text or f"Tool {tool_name} was not found"
                        last_error = attempt_error
                        continue

                    attempt_status = "guidance_recommendation"
                    payload = {"guidance": guidance_text, "source": tool_name}
                else:
                    # Try to extract JSON payload
                    payload = _extract_json_from_text(payload_text)
                attempt_status = "success"
                attempts.append(
                    {
                        "tool": tool_name,
                        "variantIndex": variant_index,
                        "status": attempt_status,
                        "durationMs": int((time.perf_counter() - start) * 1000),
                        "argKeys": sorted(call_args.keys()),
                        "payloadType": _describe_payload_type(payload_text),
                        "error": attempt_error,
                    }
                )
                recorded = True
                return McpToolResponse(
                    label=request.label,
                    tool_name=tool_name,
                    payload=payload if payload is not None else payload_text,
                    attempts=attempts,
                    duration_ms=int((time.perf_counter() - overall_start) * 1000),
                )
            except Exception as exc:
                attempt_error = str(exc)
                last_error = attempt_error
                _log_validation_event(
                    "mcp.validation",
                    level="warning",
                    step="tool-retry",
                    details={
                        "label": request.label,
                        "tool": tool_name,
                        "error": attempt_error,
                    },
                )
            finally:
                if not recorded:
                    attempts.append(
                        {
                            "tool": tool_name,
                            "variantIndex": variant_index,
                            "status": attempt_status,
                            "durationMs": int((time.perf_counter() - start) * 1000),
                            "argKeys": sorted(call_args.keys()),
                            "payloadType": _describe_payload_type(payload_text),
                            "error": attempt_error,
                        }
                    )

    return McpToolResponse(
        label=request.label,
        tool_name=candidates[0] if candidates else "",
        payload=None,
        error=last_error or "No MCP tool candidates available.",
        attempts=attempts,
        duration_ms=int((time.perf_counter() - overall_start) * 1000),
    )


def _extract_tool_text(content: Any) -> str:
    def _extract_item_text(item: Any) -> str:
        if item is None:
            return ""

        if isinstance(item, str):
            return item

        if isinstance(item, Mapping):
            text_block = item.get("text")
            if isinstance(text_block, Mapping):
                value = text_block.get("value")
                if value is not None:
                    return str(value)
            if text_block is not None:
                return str(text_block)

            for key in ("value", "content", "message", "output"):
                value = item.get(key)
                if value is not None:
                    return str(value)

            return str(item)

        text_attr = getattr(item, "text", None)
        if text_attr is not None:
            if isinstance(text_attr, Mapping):
                value = text_attr.get("value")
                if value is not None:
                    return str(value)

            value_attr = getattr(text_attr, "value", None)
            if value_attr is not None:
                return str(value_attr)

            return str(text_attr)

        value_attr = getattr(item, "value", None)
        if value_attr is not None:
            return str(value_attr)

        content_attr = getattr(item, "content", None)
        if content_attr is not None and content_attr is not item:
            return _extract_tool_text(content_attr)

        return str(item)

    if isinstance(content, list):
        lines: list[str] = []
        for item in content:
            extracted = _extract_item_text(item).strip()
            if extracted:
                lines.append(extracted)
        return "\n".join(lines).strip()

    return _extract_item_text(content).strip()


def _describe_payload_type(payload_text: Any) -> str:
    text = str(payload_text or "").strip()
    if not text:
        return ""
    if text.startswith("{") or text.startswith("["):
        return "json"
    if len(text) > 1500:
        return "text/long"
    return "text"


def _run_async(coro):
    try:
        return asyncio.run(coro)
    except RuntimeError as exc:
        raise RuntimeError("Synchronous Azure MCP validation called while event loop is running") from exc


@dataclass
class ValidationDiagnostics:
    connection_state: str
    explanation: str
    finding_count: int
    tools: list[dict[str, Any]] | None = None


def _collect_findings_from_mcp(
    *,
    app_settings: Mapping[str, Any],
    architecture_context: Mapping[str, Any],
    valid_resource_ids: set[str],
    valid_connection_ids: set[str],
    project_id: str,
) -> tuple[list[dict[str, Any]], ValidationDiagnostics]:
    try:
        credentials = AzureMcpCredentials.from_app_settings(app_settings)
    except Exception as exc:
        return [], ValidationDiagnostics(
            connection_state="unavailable",
            explanation=f"Azure MCP credentials are not configured: {exc}",
            finding_count=0,
        )

    detected_services = _extract_waf_services_from_architecture_context(architecture_context)
    if not detected_services:
        return [], ValidationDiagnostics(
            connection_state="partial",
            explanation="Azure MCP Well-Architected validation skipped: no service types were detected from architecture resources.",
            finding_count=0,
            tools=[],
        )

    tool_requests: list[McpToolRequest] = []
    for service_name in detected_services:
        tool_requests.append(
            McpToolRequest(
                label=f"wellarchitectedframework:{service_name}",
                tool_candidates=[
                    "wellarchitectedframework",
                    "wellarchitected_framework",
                ],
                argument_variants=[
                    {
                        "intent": f"Get Azure Well-Architected guidance for service: {service_name}",
                        "command": "wellarchitectedframework_serviceguide_get",
                        "parameters": {
                            "service": service_name,
                        },
                    },
                    {
                        "command": "wellarchitectedframework_serviceguide_get",
                        "parameters": {
                            "service": service_name,
                        },
                    },
                    {
                        "command": "wellarchitectedframework_serviceguide_get",
                        "service": service_name,
                    },
                ],
            )
        )

    try:
        tool_responses = _run_async(_invoke_mcp_validation(credentials, tool_requests))

        findings: list[dict[str, Any]] = []
        explanation_parts: list[str] = []
        successful_tools = 0
        tool_details: list[dict[str, Any]] = []

        for response in tool_responses:
            response_label = str(response.label or "").strip()
            service_name = response_label.split(":", 1)[1].strip() if ":" in response_label else ""

            if response.error:
                failed_label = response_label or "wellarchitectedframework"
                explanation_parts.append(f"{failed_label}: failed ({response.error})")
                tool_details.append(
                    {
                        "label": response_label or "wellarchitectedframework",
                        "service": service_name,
                        "selectedTool": response.tool_name,
                        "status": "failed",
                        "findingCount": 0,
                        "attemptCount": len(response.attempts or []),
                        "durationMs": int(response.duration_ms or 0),
                        "error": response.error,
                        "attempts": response.attempts or [],
                    }
                )
                continue

            successful_tools += 1
            normalized = _extract_and_normalize_findings(
                response.payload,
                valid_resource_ids=valid_resource_ids,
                valid_connection_ids=valid_connection_ids,
            )
            for finding in normalized:
                if isinstance(finding, dict):
                    finding.setdefault("source", "azure_mcp")
                    finding.setdefault("tool", "wellarchitectedframework")
                    if service_name and not finding.get("service"):
                        finding["service"] = service_name
            findings.extend(normalized)
            success_label = response_label or "wellarchitectedframework"
            explanation_parts.append(f"{success_label}: {len(normalized)} findings")
            tool_details.append(
                {
                    "label": response_label or "wellarchitectedframework",
                    "service": service_name,
                    "selectedTool": response.tool_name,
                    "status": "success",
                    "findingCount": len(normalized),
                    "attemptCount": len(response.attempts or []),
                    "durationMs": int(response.duration_ms or 0),
                    "error": response.error,
                    "attempts": response.attempts or [],
                }
            )

        explanation_text = "Azure MCP validation completed."
        if explanation_parts:
            explanation_text = f"{explanation_text} {'; '.join(explanation_parts)}"

        return findings, ValidationDiagnostics(
            connection_state="connected" if successful_tools > 0 or len(findings) > 0 else "partial",
            explanation=explanation_text,
            finding_count=len(findings),
            tools=tool_details,
        )
    except Exception as exc:
        return [], ValidationDiagnostics(
            connection_state="failed",
            explanation=f"Azure MCP validation failed: {exc}",
            finding_count=0,
            tools=[],
        )


def _collect_findings_from_reasoning_model(
    *,
    app_settings: Mapping[str, Any],
    architecture_context: Mapping[str, Any],
    valid_resource_ids: set[str],
    valid_connection_ids: set[str],
    mcp_findings: list[dict[str, Any]] | None,
    foundry_agent_id: str | None,
    foundry_thread_id: str | None,
) -> tuple[list[dict[str, Any]], ValidationDiagnostics]:
    provider = str(app_settings.get("modelProvider") or "").strip().lower()
    if provider != "azure-foundry":
        return [], ValidationDiagnostics(
            connection_state="skipped",
            explanation="Model provider is not Azure Foundry.",
            finding_count=0,
            tools=[],
        )

    model_name = _first_non_empty(
        app_settings,
        "foundryModelReasoning",
        "modelReasoning",
        "foundryModelFast",
        "modelFast",
    )
    if not model_name:
        return [], ValidationDiagnostics(
            connection_state="skipped",
            explanation="No reasoning model is configured.",
            finding_count=0,
            tools=[],
        )

    safe_agent_id = str(
        foundry_agent_id
        or app_settings.get("foundryValidationAgentId")
        or ""
    ).strip()
    safe_thread_id = str(foundry_thread_id or app_settings.get("foundryDefaultThreadId") or "").strip()
    if not safe_agent_id or not safe_thread_id:
        return [], ValidationDiagnostics(
            connection_state="skipped",
            explanation="Foundry validation agent or project thread is missing.",
            finding_count=0,
            tools=[],
        )

    try:
        base_connection = FoundryConnectionSettings.from_app_settings(app_settings)
    except FoundryConfigurationError as exc:
        return [], ValidationDiagnostics(
            connection_state="skipped",
            explanation=f"Foundry configuration is incomplete: {exc}",
            finding_count=0,
            tools=[],
        )

    connection = FoundryConnectionSettings(
        endpoint=base_connection.endpoint,
        tenant_id=base_connection.tenant_id,
        client_id=base_connection.client_id,
        client_secret=base_connection.client_secret,
        model_deployment=str(model_name).strip(),
        api_version=base_connection.api_version,
    )

    serialized_mcp_findings = _serialize_findings_for_reasoning_context(
        mcp_findings if isinstance(mcp_findings, list) else [],
        limit=12,
    )

    prompt = "\n".join(
        [
            "You are a Principal Azure Cloud Architect reviewing an Azure architecture for enterprise quality.",
            "Return strict JSON only.",
            "Schema:",
            '{"architecture_summary":"...","pillar_assessment":{"reliability":"...","security":"...","cost_optimization":"...","operational_excellence":"...","performance_efficiency":"..."},"priority_improvements":[{"id":"...","severity":"warning|failure","title":"...","message":"...","pillar":"reliability|security|cost_optimization|operational_excellence|performance_efficiency","target":{"resourceId":"...","connectionId":"...","field":"..."}}],"quick_configuration_fixes":[{"id":"...","severity":"info|warning","title":"...","message":"...","pillar":"operational_excellence","target":{"resourceId":"...","connectionId":"...","field":"..."}}],"findings":[{"id":"...","severity":"info|warning|failure","title":"...","message":"...","pillar":"reliability|security|cost_optimization|operational_excellence|performance_efficiency","target":{"resourceId":"...","connectionId":"...","field":"..."},"fix":{"label":"...","operations":[{"op":"set_resource_property|set_resource_name|remove_connection|set_connection_direction|add_connection|remove_resource","resourceId":"...","connectionId":"...","field":"...","value":"...","fromId":"...","toId":"...","direction":"one-way|bi"}]}}]}',
            "Use only IDs present in the architecture context below.",
            "Use Azure Well-Architected guidance and Azure service-specific best-practice correctness.",
            "Think like an enterprise Azure architect: challenge unnecessary complexity, recommend managed service alternatives, and identify missing resiliency/security/observability controls.",
            "Prioritize architecture-quality recommendations. Do not focus only on missing field values unless they materially affect deployability or architecture correctness.",
            "For suboptimal service choices, recommend Azure-native alternatives and explain why they are better.",
            "Treat Azure MCP findings as first-class evidence: verify, refine, and augment them.",
            "Keep findings actionable and concise, mapped to the five pillars.",
            "Populate priority_improvements and quick_configuration_fixes when possible.",
            "Include findings as a complete normalized list even when additional structured fields are returned.",
            "Azure MCP findings context:",
            json.dumps(serialized_mcp_findings, ensure_ascii=False),
            "Architecture context:",
            json.dumps(architecture_context, ensure_ascii=False),
        ]
    )

    try:
        runner = FoundryAssistantRunner(connection, timeout_seconds=120, agent_name="architecture-validation-agent")
        result = runner.run_assistant(
            assistant_id=safe_agent_id,
            thread_id=safe_thread_id,
            content=prompt,
            allow_stateless_retry=True,
        )
        parsed = _extract_json_from_text(str(result.response_text or ""))
        payload = parsed if parsed is not None else str(result.response_text or "")
        findings = _extract_and_normalize_findings(
            payload,
            valid_resource_ids=valid_resource_ids,
            valid_connection_ids=valid_connection_ids,
        )
        return findings, ValidationDiagnostics(
            connection_state="connected",
            explanation=f"Reasoning-model validation completed with MCP context ({len(serialized_mcp_findings)} findings provided).",
            finding_count=len(findings),
            tools=[],
        )
    except Exception as exc:
        return [], ValidationDiagnostics(
            connection_state="failed",
            explanation=f"Reasoning-model validation failed: {exc}",
            finding_count=0,
            tools=[],
        )


def run_architecture_validation_agent(
    *,
    app_settings: Mapping[str, Any],
    canvas_state: Mapping[str, Any],
    project_name: str,
    project_id: str,
    project_description: str | None = None,
    foundry_agent_id: str | None = None,
    foundry_thread_id: str | None = None,
    validation_run_id: str | None = None,
) -> dict[str, Any]:
    safe_project_name = _normalize_string(project_name, "Project")
    safe_project_id = _normalize_string(project_id)
    safe_project_description = _normalize_string(project_description)
    run_id = _normalize_string(validation_run_id)
    if not run_id:
        run_id = f"val-{int(time.time() * 1000)}-{uuid4().hex[:6]}"

    items = _coerce_canvas_items(canvas_state)
    connections = _coerce_canvas_connections(canvas_state)

    valid_resource_ids = {str(item.get("id") or "").strip() for item in items if str(item.get("id") or "").strip()}
    valid_connection_ids = {
        str(connection.get("id") or "").strip()
        for connection in connections
        if str(connection.get("id") or "").strip()
    }

    _log_validation_event(
        "validation.run",
        level="info",
        step="requested",
        project_id=safe_project_id,
        details={
            "projectName": safe_project_name,
            "projectDescription": safe_project_description,
            "validationRunId": run_id,
            "resourceCount": len(items),
            "connectionCount": len(connections),
        },
    )

    architecture_context = _build_architecture_context(
        items=items,
        connections=connections,
        project_name=safe_project_name,
        project_id=safe_project_id,
        project_description=safe_project_description,
    )

    deterministic = _deterministic_findings(items=items, connections=connections)
    # Tag deterministic findings as quick fixes (canvas configuration issues)
    for finding in deterministic:
        finding.setdefault("source", "deterministic")
        # Configuration issues typically relate to operational excellence or reliability
        if not finding.get("pillar"):
            if finding.get("severity") == "failure":
                finding["pillar"] = "reliability"  # Missing required config affects reliability
            else:
                finding["pillar"] = "operational_excellence"  # Best practice config

    mcp_findings, mcp_diagnostics = _collect_findings_from_mcp(
        app_settings=app_settings,
        architecture_context=architecture_context,
        valid_resource_ids=valid_resource_ids,
        valid_connection_ids=valid_connection_ids,
        project_id=safe_project_id,
    )

    _log_validation_event(
        "validation.channel",
        level="info" if mcp_diagnostics.connection_state == "connected" else "warning",
        step="azure-mcp",
        project_id=safe_project_id,
        details={
            "projectName": safe_project_name,
            "validationRunId": run_id,
            "state": mcp_diagnostics.connection_state,
            "findingCount": mcp_diagnostics.finding_count,
            "explanation": mcp_diagnostics.explanation,
        },
    )

    model_findings, model_diagnostics = _collect_findings_from_reasoning_model(
        app_settings=app_settings,
        architecture_context=architecture_context,
        valid_resource_ids=valid_resource_ids,
        valid_connection_ids=valid_connection_ids,
        mcp_findings=mcp_findings,
        foundry_agent_id=foundry_agent_id,
        foundry_thread_id=foundry_thread_id,
    )
    # Tag model findings as recommendations from reasoning engine
    for finding in model_findings:
        finding.setdefault("source", "reasoning_model")

    _log_validation_event(
        "validation.channel",
        level="info" if model_diagnostics.connection_state == "connected" else "warning",
        step="thinking-model",
        project_id=safe_project_id,
        details={
            "projectName": safe_project_name,
            "validationRunId": run_id,
            "state": model_diagnostics.connection_state,
            "findingCount": model_diagnostics.finding_count,
            "explanation": model_diagnostics.explanation,
            "usedMcpContext": bool(mcp_findings),
        },
    )

    dual_pass_complete = (
        mcp_diagnostics.connection_state == "connected"
        and model_diagnostics.connection_state == "connected"
    )

    normalized: list[dict[str, Any]] = []
    for idx, finding in enumerate(deterministic + mcp_findings + model_findings):
        normalized_finding = _normalize_finding(
            finding,
            index=idx,
            valid_resource_ids=valid_resource_ids,
            valid_connection_ids=valid_connection_ids,
        )
        if normalized_finding:
            normalized.append(normalized_finding)

    deduped = _dedupe_findings(normalized)
    grouped = _group_findings(deduped)
    organized = _organize_into_recommendations_and_quick_fixes(deduped)

    summary = {
        "failure": len(grouped["failure"]),
        "warning": len(grouped["warning"]),
        "info": len(grouped["info"]),
        "total": len(deduped),
    }

    architecture_summary_text = _build_architecture_summary_text(
        summary=summary,
        dual_pass_complete=dual_pass_complete,
    )
    pillar_assessment = _build_pillar_assessment(organized["recommendations"])
    pillar_details = _build_pillar_details(
        app_settings=app_settings,
        architecture_context=architecture_context,
        recommendations=organized["recommendations"],
        project_name=safe_project_name,
        project_description=safe_project_description,
        foundry_agent_id=foundry_agent_id,
        foundry_thread_id=foundry_thread_id,
        mcp_findings=mcp_findings,
        model_findings=model_findings,
    )

    _log_validation_event(
        "validation.run",
        level="info",
        step="completed",
        project_id=safe_project_id,
        details={
            "projectName": safe_project_name,
            "validationRunId": run_id,
            "failureCount": summary["failure"],
            "warningCount": summary["warning"],
            "infoCount": summary["info"],
            "mcpState": mcp_diagnostics.connection_state,
            "reasoningState": model_diagnostics.connection_state,
            "dualPassComplete": dual_pass_complete,
        },
    )

    return {
        "ok": True,
        "runId": run_id,
        "evaluation": {
            "mode": "azure-dual-pass",
            "dualPassComplete": dual_pass_complete,
            "steps": [
                {
                    "name": "azure-mcp",
                    "state": mcp_diagnostics.connection_state,
                    "findingCount": mcp_diagnostics.finding_count,
                    "explanation": mcp_diagnostics.explanation,
                    "tools": mcp_diagnostics.tools or [],
                },
                {
                    "name": "thinking-model",
                    "state": model_diagnostics.connection_state,
                    "findingCount": model_diagnostics.finding_count,
                    "explanation": model_diagnostics.explanation,
                    "usedMcpContext": bool(mcp_findings),
                    "tools": model_diagnostics.tools or [],
                },
            ],
        },
        "summary": summary,
        "findings": deduped,
        "groups": grouped,
        "architecture_summary": architecture_summary_text,
        "pillar_assessment": pillar_assessment,
        "pillar_details": pillar_details,
        "priority_improvements": organized["quick_fixes"]["priority_improvements"],
        "quick_configuration_fixes": organized["quick_fixes"]["quick_configuration_fixes"],
        "recommendations": organized["recommendations"],
        "quick_fixes": organized["quick_fixes"],
        "sources": {
            "deterministic": {
                "connectionState": "connected",
                "findingCount": len(deterministic),
                "explanation": "Deterministic architecture checks completed.",
            },
            "azureMcp": {
                "connectionState": mcp_diagnostics.connection_state,
                "findingCount": mcp_diagnostics.finding_count,
                "explanation": mcp_diagnostics.explanation,
            },
            "reasoningModel": {
                "connectionState": model_diagnostics.connection_state,
                "findingCount": model_diagnostics.finding_count,
                "explanation": model_diagnostics.explanation,
                "usedMcpContext": bool(mcp_findings),
            },
        },
    }


def get_architecture_validation_status(
    app_settings: Mapping[str, Any],
    *,
    foundry_thread_id: str | None = None,
    foundry_agent_id: str | None = None,
) -> dict[str, Any]:
    provider = str(app_settings.get("modelProvider") or "").strip().lower()
    configured_model = _first_non_empty(
        app_settings,
        "foundryModelReasoning",
        "modelReasoning",
        "foundryModelFast",
        "modelFast",
    )

    foundry_connection_configured = False
    try:
        FoundryConnectionSettings.from_app_settings(app_settings)
        foundry_connection_configured = True
    except Exception:
        foundry_connection_configured = False

    mcp_configured = False
    try:
        AzureMcpCredentials.from_app_settings(app_settings)
        mcp_configured = True
    except Exception:
        mcp_configured = False

    safe_agent_id = str(
        foundry_agent_id
        or app_settings.get("foundryValidationAgentId")
        or ""
    ).strip()
    safe_thread_id = str(foundry_thread_id or app_settings.get("foundryDefaultThreadId") or "").strip()

    foundry_agent_configured = (
        provider == "azure-foundry"
        and foundry_connection_configured
        and bool(configured_model)
        and bool(safe_agent_id)
        and bool(safe_thread_id)
    )

    return {
        "agentName": DEFAULT_VALIDATION_AGENT_NAME,
        "model": {
            "provider": provider,
            "configuredModel": configured_model,
            "activeModel": configured_model,
            "usedFoundryModel": False,
        },
        "connections": {
            "azureMcp": {
                "configured": bool(mcp_configured),
                "connected": False,
            },
            "azureFoundry": {
                "configured": bool(foundry_agent_configured),
                "connected": False,
            },
        },
        "validation": {
            "agentId": safe_agent_id,
            "threadId": safe_thread_id,
        },
    }
