from __future__ import annotations

import asyncio
import importlib
import json
import re
import time
from dataclasses import dataclass
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
        },
        "resources": resources,
        "connections": edges,
    }


def _extract_candidate_findings(payload: Any) -> list[Any]:
    if isinstance(payload, list):
        return payload

    if not isinstance(payload, Mapping):
        return []

    candidates: list[Any] = []
    for key in (
        "findings",
        "recommendations",
        "checks",
        "issues",
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

    if candidates:
        return candidates

    display_hint = str(payload.get("displayHint") or "").strip()
    if display_hint:
        return [line.strip() for line in display_hint.splitlines() if line.strip()]

    return []


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
        return {
            "id": _normalize_finding_id("", index),
            "severity": "info",
            "title": "Recommendation",
            "message": text,
            "target": {},
        }

    if not isinstance(finding, Mapping):
        return None

    severity = _normalize_severity(finding.get("severity") or finding.get("level") or finding.get("status"))
    title = _normalize_string(finding.get("title") or finding.get("name"), "Recommendation")
    message = _normalize_string(finding.get("message") or finding.get("reason") or finding.get("description"))

    if not message and title:
        message = title

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
                "target": _trim_value(finding.get("target") if isinstance(finding.get("target"), Mapping) else {}),
                "fix": _trim_value(finding.get("fix") if isinstance(finding.get("fix"), Mapping) else {}),
            }
        )
    return serialized


async def _invoke_mcp_validation(credentials: AzureMcpCredentials, args: dict[str, Any]) -> Any:
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

    async with stdio_client(server_params) as (read_stream, write_stream):
        async with client_session_cls(read_stream, write_stream) as session:
            await session.initialize()
            payload_text = await _call_mcp_cloudarchitect_tool(session, args)

    _log_validation_event(
        "mcp.validation",
        level="info",
        step="session-completed",
    )

    parsed = _extract_json_from_text(payload_text)
    if parsed is not None:
        return parsed
    return payload_text


async def _call_mcp_cloudarchitect_tool(session: Any, args: dict[str, Any]) -> str:
    tool_candidates = ["cloudarchitect", "cloudarchitect_design"]
    last_error = ""

    for tool_name in tool_candidates:
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
            return _extract_tool_text(getattr(result, "content", result))
        except Exception as exc:
            last_error = str(exc)
            _log_validation_event(
                "mcp.validation",
                level="warning",
                step="tool-retry",
                details={
                    "tool": tool_name,
                    "error": last_error,
                },
            )

    _log_validation_event(
        "mcp.validation",
        level="error",
        step="failed",
        details={"error": last_error},
    )
    raise RuntimeError(f"Unable to call Azure MCP validation tool: {last_error}")


def _extract_tool_text(content: Any) -> str:
    if isinstance(content, list):
        lines: list[str] = []
        for item in content:
            text = getattr(item, "text", None)
            if text is None:
                text = str(item)
            lines.append(str(text))
        return "\n".join(lines)
    return str(content)


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

    prompt = "\n".join(
        [
            "Validate this Azure architecture.",
            "Return strict JSON only with this shape:",
            '{"findings":[{"id":"...","severity":"info|warning|failure","title":"...","message":"...","target":{"resourceId":"...","connectionId":"...","field":"..."},"fix":{"label":"...","operations":[{"op":"set_resource_property|set_resource_name|remove_connection|set_connection_direction|add_connection|remove_resource","resourceId":"...","connectionId":"...","field":"...","value":"...","fromId":"...","toId":"...","direction":"one-way|bi"}]}}],"summary":"..."}',
            "Use only resourceId/connectionId values present in the provided architecture context.",
            "Limit to 20 findings focused on Azure Well-Architected and Azure best-practice violations.",
            "If no safe fix is available for a finding, omit the fix object.",
        ]
    )

    args = {
        "command": "cloudarchitect_design",
        "question": prompt,
        "answer": json.dumps(architecture_context, ensure_ascii=False),
        "question-number": 1,
        "total-questions": 1,
        "next-question-needed": False,
        "state": "{}",
    }

    try:
        payload = _run_async(_invoke_mcp_validation(credentials, args))
        findings = _extract_and_normalize_findings(
            payload,
            valid_resource_ids=valid_resource_ids,
            valid_connection_ids=valid_connection_ids,
        )
        return findings, ValidationDiagnostics(
            connection_state="connected",
            explanation="Azure MCP validation completed.",
            finding_count=len(findings),
        )
    except Exception as exc:
        return [], ValidationDiagnostics(
            connection_state="failed",
            explanation=f"Azure MCP validation failed: {exc}",
            finding_count=0,
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
        )

    try:
        base_connection = FoundryConnectionSettings.from_app_settings(app_settings)
    except FoundryConfigurationError as exc:
        return [], ValidationDiagnostics(
            connection_state="skipped",
            explanation=f"Foundry configuration is incomplete: {exc}",
            finding_count=0,
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
            "You are an Azure principal cloud architect validating an Azure architecture diagram.",
            "Return strict JSON only.",
            "Schema:",
            '{"findings":[{"id":"...","severity":"info|warning|failure","title":"...","message":"...","target":{"resourceId":"...","connectionId":"...","field":"..."},"fix":{"label":"...","operations":[{"op":"set_resource_property|set_resource_name|remove_connection|set_connection_direction|add_connection|remove_resource","resourceId":"...","connectionId":"...","field":"...","value":"...","fromId":"...","toId":"...","direction":"one-way|bi"}]}},...],"summary":"...","provenance":[{"step":"mcp","explanation":"..."},{"step":"best-practices","explanation":"..."},{"step":"reasoning","explanation":"..."}]}',
            "Use only IDs present in the architecture context below.",
            "Use Azure Well-Architected guidance and Azure service-specific best-practice correctness.",
            "Treat Azure MCP findings as first-class evidence: verify, refine, and augment them. For each finding, include a provenance trace: which step (MCP, best-practices, reasoning) contributed to the finding, and a short explanation for each step.",
            "Avoid generic graph-only advice; reason using Azure resource properties, containment, and relationships.",
            "Keep findings actionable and concise.",
            "Azure MCP findings context:",
            json.dumps(serialized_mcp_findings, ensure_ascii=False),
            "Architecture context:",
            json.dumps(architecture_context, ensure_ascii=False),
        ]
    )

    try:
        runner = FoundryAssistantRunner(connection, timeout_seconds=60)
        result = runner.run_assistant(
            assistant_id=safe_agent_id,
            thread_id=safe_thread_id,
            content=prompt,
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
        )
    except Exception as exc:
        return [], ValidationDiagnostics(
            connection_state="failed",
            explanation=f"Reasoning-model validation failed: {exc}",
            finding_count=0,
        )


def run_architecture_validation_agent(
    *,
    app_settings: Mapping[str, Any],
    canvas_state: Mapping[str, Any],
    project_name: str,
    project_id: str,
    foundry_agent_id: str | None = None,
    foundry_thread_id: str | None = None,
    validation_run_id: str | None = None,
) -> dict[str, Any]:
    safe_project_name = _normalize_string(project_name, "Project")
    safe_project_id = _normalize_string(project_id)
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
    )

    deterministic = _deterministic_findings(items=items, connections=connections)

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

    summary = {
        "failure": len(grouped["failure"]),
        "warning": len(grouped["warning"]),
        "info": len(grouped["info"]),
        "total": len(deduped),
    }

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
                },
                {
                    "name": "thinking-model",
                    "state": model_diagnostics.connection_state,
                    "findingCount": model_diagnostics.finding_count,
                    "explanation": model_diagnostics.explanation,
                    "usedMcpContext": True,
                },
            ],
        },
        "summary": summary,
        "findings": deduped,
        "groups": grouped,
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
                "usedMcpContext": True,
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
