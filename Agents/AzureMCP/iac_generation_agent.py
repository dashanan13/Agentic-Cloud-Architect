from __future__ import annotations

import asyncio
import importlib
import json
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from Agents.AzureAIFoundry.foundry_bootstrap import (
    FoundryConfigurationError,
    FoundryConnectionSettings,
)
from Agents.AzureAIFoundry.foundry_description import FoundryAssistantRunner
from Agents.AzureMCP.cloudarchitect_chat_agent import AzureMcpCredentials

DEFAULT_PARAMETER_FORMAT = "bicepparam"
SUPPORTED_PARAMETER_FORMATS = {"bicepparam", "json"}
CODING_MODEL_GUARDRAIL_TIMEOUT_SECONDS = 45
CATALOG_PATH = Path(__file__).resolve().parents[2] / "Clouds" / "Azure" / "resource_catalog.json"
AGENT_DEFINITION_PATH = Path(__file__).resolve().parents[1] / "iac_generation_agent.md"
IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
NON_DEPENDENCY_REF_KEYS = {
    "associatedsubnetref",
}


@dataclass
class ResourceNode:
    id: str
    key: str
    symbol: str
    module_file: str
    name: str
    resource_label: str
    arm_type: str
    api_version: str
    bicep_type: str
    category: str
    parent_id: str | None
    properties: dict[str, Any]
    resource_group_name: str


def _emit_progress(
    progress_callback: Callable[[dict[str, Any]], None] | None,
    *,
    stage: str,
    status: str,
    message: str,
    progress: int,
) -> None:
    if not callable(progress_callback):
        return

    payload = {
        "stage": stage,
        "status": status,
        "message": str(message or "").strip(),
        "progress": max(0, min(100, int(progress))),
    }

    try:
        progress_callback(payload)
    except Exception:
        return


def generate_bicep_iac_from_canvas(
    *,
    app_settings: Mapping[str, Any],
    canvas_state: Mapping[str, Any],
    output_dir: Path,
    project_name: str,
    project_id: str,
    parameter_format: str = DEFAULT_PARAMETER_FORMAT,
    foundry_agent_id: str | None = None,
    foundry_thread_id: str | None = None,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    _emit_progress(
        progress_callback,
        stage="gather_properties",
        status="running",
        message="Gathering canvas resources and properties",
        progress=5,
    )

    safe_parameter_format = _normalize_parameter_format(parameter_format)
    canvas_items = _coerce_canvas_items(canvas_state)
    canvas_connections = _coerce_canvas_connections(canvas_state)
    if not canvas_items:
        raise ValueError("No canvas resources found. Add resources to the canvas before generating IaC.")

    _emit_progress(
        progress_callback,
        stage="gather_properties",
        status="completed",
        message=f"Captured {len(canvas_items)} resources and {len(canvas_connections)} connections",
        progress=15,
    )

    _emit_progress(
        progress_callback,
        stage="dependency_tree",
        status="running",
        message="Building dependency graph and deployment order",
        progress=20,
    )

    catalog = _load_resource_catalog()
    specs = _build_resource_specs(canvas_items, catalog)
    spec_by_id = {spec["id"]: spec for spec in specs}

    deps_by_id, dep_warnings = _build_dependencies(specs, canvas_connections)
    ordered_ids, cycle_warnings = _topological_order(specs, deps_by_id)

    _emit_progress(
        progress_callback,
        stage="dependency_tree",
        status="completed",
        message=f"Dependency tree built for {len(ordered_ids)} resources",
        progress=30,
    )

    _emit_progress(
        progress_callback,
        stage="render_templates",
        status="running",
        message="Rendering Bicep modules and main template",
        progress=35,
    )

    nodes = _build_nodes_in_order(ordered_ids, spec_by_id)
    nodes_by_id = {node.id: node for node in nodes}

    fallback_rg_name = _first_resource_group_name(nodes) or "CHANGE_ME_RESOURCE_GROUP"
    for node in nodes:
        node.resource_group_name = _resolve_resource_group_name(node, nodes_by_id, fallback_rg_name)

    generation_warnings: list[str] = []
    generation_warnings.extend(dep_warnings)
    generation_warnings.extend(cycle_warnings)

    unsupported_nodes = [
        node for node in nodes if _resource_kind(node.arm_type) == "generic"
    ]
    if unsupported_nodes:
        preview = ", ".join(f"{node.name} ({node.resource_label})" for node in unsupported_nodes[:8])
        generation_warnings.append(
            "Some resources are generated as scaffolding modules and need manual completion: "
            + preview
        )

    module_contents: dict[str, str] = {}
    for node in nodes:
        module_contents[node.module_file] = _render_module_bicep(node)

    main_bicep = _render_main_bicep(nodes, nodes_by_id, deps_by_id)

    _emit_progress(
        progress_callback,
        stage="render_templates",
        status="completed",
        message=f"Rendered {len(module_contents)} modules and main.bicep",
        progress=55,
    )

    _emit_progress(
        progress_callback,
        stage="generate_parameters",
        status="running",
        message="Generating parameter file and pipeline",
        progress=60,
    )

    resources_parameter = _build_resources_parameter(nodes)

    if safe_parameter_format == "bicepparam":
        parameter_file_name = "main.bicepparam"
        parameter_content = _render_bicepparam(resources_parameter)
        alternate_parameter_file_name = "main.parameters.json"
    else:
        parameter_file_name = "main.parameters.json"
        parameter_content = _render_json_parameters(resources_parameter)
        alternate_parameter_file_name = "main.bicepparam"

    pipeline_yml = _render_pipeline_yaml(parameter_file_name, safe_parameter_format)

    _emit_progress(
        progress_callback,
        stage="generate_parameters",
        status="completed",
        message=f"Generated {parameter_file_name} and pipeline.yml",
        progress=70,
    )

    _emit_progress(
        progress_callback,
        stage="guardrails_mcp",
        status="running",
        message="Running Azure MCP guardrail checks",
        progress=74,
    )

    mcp_guardrails = _collect_guardrails_from_mcp(app_settings, nodes, deps_by_id)

    _emit_progress(
        progress_callback,
        stage="guardrails_mcp",
        status="completed",
        message=f"Azure MCP guardrails completed ({len(mcp_guardrails)} checks)",
        progress=82,
    )

    _emit_progress(
        progress_callback,
        stage="guardrails_model",
        status="running",
        message="Running coding-model guardrail checks",
        progress=85,
    )

    coding_model_guardrails = _collect_guardrails_from_coding_model(
        app_settings=app_settings,
        nodes=nodes,
        deps_by_id=deps_by_id,
        generation_warnings=generation_warnings,
        parameter_format=safe_parameter_format,
        foundry_agent_id=foundry_agent_id,
        foundry_thread_id=foundry_thread_id,
        project_name=project_name,
        project_id=project_id,
    )

    _emit_progress(
        progress_callback,
        stage="guardrails_model",
        status="completed",
        message=f"Coding-model guardrails completed ({len(coding_model_guardrails)} checks)",
        progress=90,
    )

    _emit_progress(
        progress_callback,
        stage="write_files",
        status="running",
        message="Writing IaC files to project",
        progress=93,
    )

    written_files = _write_iac_files(
        output_dir=output_dir,
        modules=module_contents,
        main_bicep=main_bicep,
        parameter_file_name=parameter_file_name,
        parameter_content=parameter_content,
        alternate_parameter_file_name=alternate_parameter_file_name,
        pipeline_content=pipeline_yml,
    )

    _emit_progress(
        progress_callback,
        stage="write_files",
        status="completed",
        message=f"Wrote {len(written_files)} IaC files",
        progress=100,
    )

    deployment_order = [node.name for node in nodes]
    return {
        "ok": True,
        "parameterFormat": safe_parameter_format,
        "files": written_files,
        "deploymentOrder": deployment_order,
        "warnings": generation_warnings,
        "guardrails": {
            "azureMcp": mcp_guardrails,
            "codingModel": coding_model_guardrails,
        },
    }


def _normalize_parameter_format(value: str | None) -> str:
    candidate = str(value or DEFAULT_PARAMETER_FORMAT).strip().lower()
    if candidate not in SUPPORTED_PARAMETER_FORMATS:
        return DEFAULT_PARAMETER_FORMAT
    return candidate


def _coerce_canvas_items(canvas_state: Mapping[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(canvas_state, Mapping):
        return []
    raw = canvas_state.get("canvasItems")
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
    if not isinstance(canvas_state, Mapping):
        return []
    raw = canvas_state.get("canvasConnections")
    if not isinstance(raw, list):
        return []
    connections: list[dict[str, Any]] = []
    for conn in raw:
        if not isinstance(conn, Mapping):
            continue
        from_id = str(conn.get("fromId") or "").strip()
        to_id = str(conn.get("toId") or "").strip()
        if not from_id or not to_id:
            continue
        connections.append(dict(conn))
    return connections


def _load_resource_catalog() -> dict[str, dict[str, Any]]:
    if not CATALOG_PATH.exists():
        return {}
    try:
        payload = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}
    normalized: dict[str, dict[str, Any]] = {}
    for key, value in payload.items():
        if not isinstance(value, Mapping):
            continue
        normalized[str(key)] = dict(value)
    return normalized


def _split_bicep_type(value: str) -> tuple[str, str]:
    text = str(value or "").strip()
    if "@" not in text:
        return "", ""
    arm_type, api_version = text.split("@", 1)
    return arm_type.strip(), api_version.strip()


def _build_resource_specs(items: list[dict[str, Any]], catalog: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = []
    for item in items:
        item_id = str(item.get("id") or "").strip()
        if not item_id:
            continue
        resource_label = str(item.get("resourceType") or item.get("type") or "Resource").strip() or "Resource"
        catalog_entry = catalog.get(resource_label) or {}
        bicep_type = str(catalog_entry.get("bicepType") or "").strip()
        arm_type, api_version = _split_bicep_type(bicep_type)
        if not arm_type:
            arm_type = str(catalog_entry.get("resourceType") or "").strip()
        properties = item.get("properties") if isinstance(item.get("properties"), Mapping) else {}
        specs.append(
            {
                "id": item_id,
                "name": str(item.get("name") or resource_label).strip() or resource_label,
                "resourceLabel": resource_label,
                "armType": arm_type,
                "apiVersion": api_version,
                "bicepType": bicep_type,
                "category": str(item.get("category") or catalog_entry.get("category") or "").strip(),
                "parentId": str(item.get("parentId") or "").strip() or None,
                "properties": _sanitize_json_value(properties),
            }
        )
    return specs


def _build_dependencies(
    specs: list[dict[str, Any]],
    connections: list[dict[str, Any]],
) -> tuple[dict[str, set[str]], list[str]]:
    known_ids = {spec["id"] for spec in specs}
    deps: dict[str, set[str]] = {spec["id"]: set() for spec in specs}
    warnings: list[str] = []

    for spec in specs:
        parent_id = spec.get("parentId")
        if isinstance(parent_id, str) and parent_id in known_ids and parent_id != spec["id"]:
            deps[spec["id"]].add(parent_id)

    for conn in connections:
        from_id = str(conn.get("fromId") or "").strip()
        to_id = str(conn.get("toId") or "").strip()
        if from_id in known_ids and to_id in known_ids and from_id != to_id:
            deps[to_id].add(from_id)

    for spec in specs:
        spec_id = spec["id"]
        refs = _extract_refs(spec.get("properties"), known_ids)
        for ref_id in refs:
            if ref_id != spec_id:
                deps[spec_id].add(ref_id)

    for spec_id, spec_deps in deps.items():
        missing = [ref for ref in spec_deps if ref not in known_ids]
        if missing:
            warnings.append(f"Resource {spec_id} references unknown dependencies: {', '.join(sorted(missing))}")

    return deps, warnings


def _extract_refs(value: Any, known_ids: set[str]) -> set[str]:
    refs: set[str] = set()
    if isinstance(value, Mapping):
        for key, nested in value.items():
            key_text = str(key).strip().lower()
            if key_text.endswith("ref") and key_text not in NON_DEPENDENCY_REF_KEYS and isinstance(nested, str):
                ref = nested.strip()
                if ref in known_ids:
                    refs.add(ref)
            refs.update(_extract_refs(nested, known_ids))
    elif isinstance(value, list):
        for nested in value:
            refs.update(_extract_refs(nested, known_ids))
    return refs


def _topological_order(
    specs: list[dict[str, Any]],
    deps_by_id: dict[str, set[str]],
) -> tuple[list[str], list[str]]:
    known_ids = [spec["id"] for spec in specs]
    known_set = set(known_ids)
    indegree: dict[str, int] = {}
    forward: dict[str, set[str]] = {item_id: set() for item_id in known_ids}

    sort_key = {
        spec["id"]: (str(spec.get("name") or "").strip().lower(), spec["id"])
        for spec in specs
    }

    for item_id in known_ids:
        predecessors = {dep for dep in deps_by_id.get(item_id, set()) if dep in known_set and dep != item_id}
        indegree[item_id] = len(predecessors)
        for predecessor in predecessors:
            forward.setdefault(predecessor, set()).add(item_id)

    queue = [item_id for item_id in known_ids if indegree[item_id] == 0]
    queue.sort(key=lambda item_id: sort_key[item_id])

    ordered: list[str] = []
    while queue:
        current = queue.pop(0)
        ordered.append(current)
        next_items = sorted(forward.get(current, set()), key=lambda item_id: sort_key[item_id])
        for neighbor in next_items:
            indegree[neighbor] -= 1
            if indegree[neighbor] == 0:
                queue.append(neighbor)
        queue.sort(key=lambda item_id: sort_key[item_id])

    warnings: list[str] = []
    if len(ordered) < len(known_ids):
        remaining = [item_id for item_id in known_ids if item_id not in set(ordered)]
        remaining.sort(key=lambda item_id: sort_key[item_id])
        ordered.extend(remaining)
        names = ", ".join(remaining[:8])
        warnings.append(
            "Dependency cycle detected. The remaining resources were appended in deterministic order: "
            + names
        )

    return ordered, warnings


def _build_nodes_in_order(
    ordered_ids: list[str],
    spec_by_id: dict[str, dict[str, Any]],
) -> list[ResourceNode]:
    nodes: list[ResourceNode] = []
    used_keys: set[str] = set()
    used_symbols: set[str] = set()
    used_modules: set[str] = set()

    for index, item_id in enumerate(ordered_ids, start=1):
        spec = spec_by_id[item_id]
        key_seed = _to_identifier(spec.get("name"), fallback=f"resource_{index:03d}")
        key = _ensure_unique_identifier(key_seed, used_keys)
        used_keys.add(key)

        symbol_seed = _to_identifier(f"mod_{key}", fallback=f"mod_resource_{index:03d}")
        symbol = _ensure_unique_identifier(symbol_seed, used_symbols)
        used_symbols.add(symbol)

        module_base = _slugify(spec.get("name"), fallback=f"resource-{index:03d}")
        module_file = _ensure_unique_module_name(module_base, used_modules)
        used_modules.add(module_file)

        nodes.append(
            ResourceNode(
                id=item_id,
                key=key,
                symbol=symbol,
                module_file=f"modules/{module_file}",
                name=str(spec.get("name") or f"Resource {index}").strip() or f"Resource {index}",
                resource_label=str(spec.get("resourceLabel") or "Resource").strip() or "Resource",
                arm_type=str(spec.get("armType") or "").strip(),
                api_version=str(spec.get("apiVersion") or "").strip(),
                bicep_type=str(spec.get("bicepType") or "").strip(),
                category=str(spec.get("category") or "").strip(),
                parent_id=spec.get("parentId"),
                properties=spec.get("properties") if isinstance(spec.get("properties"), dict) else {},
                resource_group_name="",
            )
        )

    return nodes


def _to_identifier(value: Any, fallback: str) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9_]+", "_", text)
    text = text.strip("_")
    if not text:
        text = fallback
    if text[0].isdigit():
        text = f"r_{text}"
    return text


def _ensure_unique_identifier(value: str, used: set[str]) -> str:
    if value not in used:
        return value
    index = 2
    while f"{value}_{index}" in used:
        index += 1
    return f"{value}_{index}"


def _slugify(value: Any, fallback: str) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text or fallback


def _ensure_unique_module_name(base_slug: str, used_files: set[str]) -> str:
    candidate = f"{base_slug}.bicep"
    if candidate not in used_files:
        return candidate
    index = 2
    while f"{base_slug}-{index}.bicep" in used_files:
        index += 1
    return f"{base_slug}-{index}.bicep"


def _first_resource_group_name(nodes: list[ResourceNode]) -> str:
    for node in nodes:
        if node.arm_type == "Microsoft.Resources/resourceGroups":
            return node.name
    return ""


def _resolve_resource_group_name(
    node: ResourceNode,
    nodes_by_id: dict[str, ResourceNode],
    fallback_rg_name: str,
) -> str:
    if node.arm_type == "Microsoft.Resources/resourceGroups":
        return node.name

    properties = node.properties if isinstance(node.properties, Mapping) else {}

    resource_group_ref = str(properties.get("resourceGroupRef") or "").strip()
    if resource_group_ref in nodes_by_id:
        dep = nodes_by_id[resource_group_ref]
        if dep.arm_type == "Microsoft.Resources/resourceGroups":
            return dep.name

    resource_group_name = str(properties.get("resourceGroupName") or "").strip()
    if resource_group_name:
        return resource_group_name

    current_parent_id = node.parent_id
    visited: set[str] = set()
    while current_parent_id and current_parent_id not in visited:
        visited.add(current_parent_id)
        parent = nodes_by_id.get(current_parent_id)
        if not parent:
            break
        if parent.arm_type == "Microsoft.Resources/resourceGroups":
            return parent.name
        parent_props = parent.properties if isinstance(parent.properties, Mapping) else {}
        parent_rg_name = str(parent_props.get("resourceGroupName") or "").strip()
        if parent_rg_name:
            return parent_rg_name
        current_parent_id = parent.parent_id

    return fallback_rg_name


def _resource_kind(arm_type: str) -> str:
    normalized = str(arm_type or "").strip().lower()
    if normalized == "microsoft.resources/resourcegroups":
        return "resource_group"
    if normalized == "microsoft.network/virtualnetworks":
        return "virtual_network"
    if normalized == "microsoft.network/virtualnetworks/subnets":
        return "subnet"
    if normalized == "microsoft.network/networksecuritygroups":
        return "network_security_group"
    if normalized == "microsoft.network/routetables":
        return "route_table"
    if normalized == "microsoft.network/publicipaddresses":
        return "public_ip"
    return "generic"


def _render_module_bicep(node: ResourceNode) -> str:
    kind = _resource_kind(node.arm_type)
    if kind == "resource_group":
        return _render_resource_group_module()
    if kind == "virtual_network":
        return _render_virtual_network_module()
    if kind == "subnet":
        return _render_subnet_module()
    if kind == "network_security_group":
        return _render_network_security_group_module()
    if kind == "route_table":
        return _render_route_table_module()
    if kind == "public_ip":
        return _render_public_ip_module()
    return _render_generic_module(node)


def _render_main_bicep(
    nodes: list[ResourceNode],
    nodes_by_id: dict[str, ResourceNode],
    deps_by_id: dict[str, set[str]],
) -> str:
    lines: list[str] = [
        "targetScope = 'subscription'",
        "",
        "@description('Generated resource map keyed by canvas resources.')",
        "param resources object",
        "",
    ]

    for node in nodes:
        kind = _resource_kind(node.arm_type)
        resource_expr = f"resources.{node.key}"
        properties_expr = f"{resource_expr}.properties"
        deps = [dep for dep in deps_by_id.get(node.id, set()) if dep in nodes_by_id]
        dep_nodes = sorted((nodes_by_id[dep] for dep in deps), key=lambda item: item.symbol)

        lines.append(f"module {node.symbol} '{node.module_file}' = {{")

        if kind != "resource_group":
            scope_expr = _resolve_scope_expression(node, nodes_by_id)
            lines.append(f"  scope: {scope_expr}")

        lines.append("  params: {")

        if kind == "resource_group":
            lines.extend(
                [
                    f"    resourceName: string({resource_expr}.name)",
                    f"    location: empty(string({resource_expr}.location ?? '')) ? deployment().location : string({resource_expr}.location)",
                    f"    tags: {resource_expr}.tags ?? {{}}",
                ]
            )
        elif kind == "virtual_network":
            lines.extend(
                [
                    f"    resourceName: string({resource_expr}.name)",
                    f"    location: empty(string({resource_expr}.location ?? '')) ? deployment().location : string({resource_expr}.location)",
                    f"    addressPrefixes: {properties_expr}.?addressPrefixes ?? ['10.0.0.0/16']",
                    f"    dnsServers: {properties_expr}.?dnsServers ?? []",
                    f"    tags: {resource_expr}.tags ?? {{}}",
                ]
            )
        elif kind == "network_security_group":
            lines.extend(
                [
                    f"    resourceName: string({resource_expr}.name)",
                    f"    location: empty(string({resource_expr}.location ?? '')) ? deployment().location : string({resource_expr}.location)",
                    f"    securityRules: {properties_expr}.?securityRules ?? []",
                    f"    tags: {resource_expr}.tags ?? {{}}",
                ]
            )
        elif kind == "route_table":
            lines.extend(
                [
                    f"    resourceName: string({resource_expr}.name)",
                    f"    location: empty(string({resource_expr}.location ?? '')) ? deployment().location : string({resource_expr}.location)",
                    f"    disableBgpRoutePropagation: bool({properties_expr}.?disableBgpRoutePropagation ?? false)",
                    f"    routes: {properties_expr}.?routes ?? []",
                    f"    tags: {resource_expr}.tags ?? {{}}",
                ]
            )
        elif kind == "public_ip":
            lines.extend(
                [
                    f"    resourceName: string({resource_expr}.name)",
                    f"    location: empty(string({resource_expr}.location ?? '')) ? deployment().location : string({resource_expr}.location)",
                    f"    allocationMethod: string({properties_expr}.?publicIPAllocationMethod ?? 'static')",
                    f"    ipVersion: string({properties_expr}.?ipVersion ?? 'ipv4')",
                    f"    skuName: string({properties_expr}.?sku ?? 'standard')",
                    f"    idleTimeoutMinutes: int({properties_expr}.?idleTimeoutMinutes ?? 4)",
                    f"    tags: {resource_expr}.tags ?? {{}}",
                ]
            )
        elif kind == "subnet":
            virtual_network_name = _subnet_virtual_network_name_expression(node, nodes_by_id, resource_expr, properties_expr)
            nsg_id_expr = _dependency_output_or_property(
                node=node,
                dependency_property_name="networkSecurityGroupRef",
                dependency_arm_type="Microsoft.Network/networkSecurityGroups",
                output_field="id",
                nodes_by_id=nodes_by_id,
                fallback_expression=f"string({properties_expr}.?networkSecurityGroupId ?? '')",
            )
            route_table_id_expr = _dependency_output_or_property(
                node=node,
                dependency_property_name="routeTableRef",
                dependency_arm_type="Microsoft.Network/routeTables",
                output_field="id",
                nodes_by_id=nodes_by_id,
                fallback_expression=f"string({properties_expr}.?routeTableId ?? '')",
            )
            lines.extend(
                [
                    f"    resourceName: string({properties_expr}.?subnetName ?? {resource_expr}.name)",
                    f"    virtualNetworkName: {virtual_network_name}",
                    f"    addressPrefix: string({properties_expr}.?addressPrefix ?? '10.0.1.0/24')",
                    f"    privateEndpointNetworkPolicies: string({properties_expr}.?privateEndpointNetworkPolicies ?? 'disabled')",
                    f"    serviceEndpoints: {properties_expr}.?serviceEndpoints ?? []",
                    f"    networkSecurityGroupId: {nsg_id_expr}",
                    f"    routeTableId: {route_table_id_expr}",
                ]
            )
        else:
            lines.extend(
                [
                    f"    resourceName: string({resource_expr}.name)",
                    f"    location: empty(string({resource_expr}.location ?? '')) ? deployment().location : string({resource_expr}.location)",
                    f"    tags: {resource_expr}.tags ?? {{}}",
                ]
            )

        lines.append("  }")
        if dep_nodes:
            lines.append("  dependsOn: [")
            for dep_node in dep_nodes:
                lines.append(f"    {dep_node.symbol}")
            lines.append("  ]")
        lines.append("}")
        lines.append("")

    lines.append("output deploymentOrder array = [")
    for node in nodes:
        lines.append(f"  '{node.name}'")
    lines.append("]")
    lines.append("")

    lines.append("output resourceIds object = {")
    for node in nodes:
        lines.append(f"  {node.key}: {node.symbol}.outputs.id")
    lines.append("}")
    lines.append("")

    return "\n".join(lines)


def _resolve_scope_expression(node: ResourceNode, nodes_by_id: dict[str, ResourceNode]) -> str:
    properties = node.properties if isinstance(node.properties, Mapping) else {}

    resource_group_ref = str(properties.get("resourceGroupRef") or "").strip()
    if resource_group_ref in nodes_by_id:
        rg_node = nodes_by_id[resource_group_ref]
        if rg_node.arm_type == "Microsoft.Resources/resourceGroups":
            return f"resourceGroup({rg_node.symbol}.outputs.name)"

    current_parent_id = node.parent_id
    visited: set[str] = set()
    while current_parent_id and current_parent_id not in visited:
        visited.add(current_parent_id)
        parent = nodes_by_id.get(current_parent_id)
        if not parent:
            break
        if parent.arm_type == "Microsoft.Resources/resourceGroups":
            return f"resourceGroup({parent.symbol}.outputs.name)"
        current_parent_id = parent.parent_id

    return f"resourceGroup(string(resources.{node.key}.resourceGroupName))"


def _subnet_virtual_network_name_expression(
    node: ResourceNode,
    nodes_by_id: dict[str, ResourceNode],
    resource_expr: str,
    properties_expr: str,
) -> str:
    properties = node.properties if isinstance(node.properties, Mapping) else {}
    virtual_network_ref = str(properties.get("virtualNetworkRef") or "").strip()
    if virtual_network_ref in nodes_by_id:
        dep_node = nodes_by_id[virtual_network_ref]
        if dep_node.arm_type == "Microsoft.Network/virtualNetworks":
            return f"{dep_node.symbol}.outputs.name"

    if node.parent_id and node.parent_id in nodes_by_id:
        parent = nodes_by_id[node.parent_id]
        if parent.arm_type == "Microsoft.Network/virtualNetworks":
            return f"{parent.symbol}.outputs.name"

    return f"string({properties_expr}.?virtualNetworkName ?? {resource_expr}.name)"


def _dependency_output_or_property(
    *,
    node: ResourceNode,
    dependency_property_name: str,
    dependency_arm_type: str,
    output_field: str,
    nodes_by_id: dict[str, ResourceNode],
    fallback_expression: str,
) -> str:
    properties = node.properties if isinstance(node.properties, Mapping) else {}
    ref_id = str(properties.get(dependency_property_name) or "").strip()
    if ref_id in nodes_by_id:
        dep_node = nodes_by_id[ref_id]
        if dep_node.arm_type == dependency_arm_type:
            return f"{dep_node.symbol}.outputs.{output_field}"
    return fallback_expression


def _build_resources_parameter(nodes: list[ResourceNode]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for node in nodes:
        properties = node.properties if isinstance(node.properties, Mapping) else {}
        tags = _normalize_tags(properties.get("tags"))
        filtered_properties = dict(properties)
        filtered_properties.pop("tags", None)

        payload[node.key] = {
            "name": node.name,
            "resourceType": node.resource_label,
            "resourceGroupName": node.resource_group_name,
            "location": str(properties.get("location") or "").strip(),
            "tags": tags,
            "properties": _sanitize_json_value(filtered_properties),
        }
    return payload


def _normalize_tags(value: Any) -> dict[str, str]:
    if isinstance(value, Mapping):
        normalized: dict[str, str] = {}
        for key, item in value.items():
            key_text = str(key).strip()
            if not key_text:
                continue
            normalized[key_text] = str(item or "").strip()
        return normalized

    if isinstance(value, list):
        normalized: dict[str, str] = {}
        for item in value:
            if not isinstance(item, Mapping):
                continue
            key_text = str(item.get("key") or "").strip()
            if not key_text:
                continue
            normalized[key_text] = str(item.get("value") or "").strip()
        return normalized

    return {}


def _sanitize_json_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _sanitize_json_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_sanitize_json_value(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _render_bicepparam(resources_payload: dict[str, Any]) -> str:
    literal = _to_bicep_literal(resources_payload, indent=0)
    return "\n".join([
        "using './main.bicep'",
        "",
        "param resources = " + literal,
        "",
    ])


def _render_json_parameters(resources_payload: dict[str, Any]) -> str:
    payload = {
        "$schema": "https://schema.management.azure.com/schemas/2019-04-01/deploymentParameters.json#",
        "contentVersion": "1.0.0.0",
        "parameters": {
            "resources": {
                "value": resources_payload,
            }
        },
    }
    return json.dumps(payload, indent=2) + "\n"


def _to_bicep_literal(value: Any, indent: int = 0) -> str:
    if isinstance(value, Mapping):
        if not value:
            return "{}"
        lines = ["{"]
        for key, nested in value.items():
            key_text = str(key)
            key_rendered = key_text if IDENTIFIER_PATTERN.match(key_text) else "'" + key_text.replace("'", "''") + "'"
            nested_literal = _to_bicep_literal(nested, indent + 1)
            nested_lines = nested_literal.splitlines()
            lines.append("  " * (indent + 1) + f"{key_rendered}: " + nested_lines[0])
            for line in nested_lines[1:]:
                lines.append(line)
        lines.append("  " * indent + "}")
        return "\n".join(lines)

    if isinstance(value, list):
        if not value:
            return "[]"
        lines = ["["]
        for nested in value:
            nested_literal = _to_bicep_literal(nested, indent + 1)
            nested_lines = nested_literal.splitlines()
            lines.append("  " * (indent + 1) + nested_lines[0])
            for line in nested_lines[1:]:
                lines.append(line)
        lines.append("  " * indent + "]")
        return "\n".join(lines)

    if isinstance(value, bool):
        return "true" if value else "false"

    if isinstance(value, int):
        return str(value)

    if isinstance(value, float):
        if value.is_integer():
            return str(int(value))
        return str(value)

    if value is None:
        return "null"

    return "'" + str(value).replace("'", "''") + "'"


def _render_pipeline_yaml(parameter_file_name: str, parameter_format: str) -> str:
    parameter_arg = (
        f"IaC/Bicep/{parameter_file_name}"
        if parameter_format == "bicepparam"
        else f"@IaC/Bicep/{parameter_file_name}"
    )
    return "\n".join(
        [
            "name: deploy-generated-bicep",
            "",
            "on:",
            "  workflow_dispatch:",
            "",
            "jobs:",
            "  deploy:",
            "    runs-on: ubuntu-latest",
            "    steps:",
            "      - uses: actions/checkout@v4",
            "      - uses: azure/login@v2",
            "        with:",
            "          creds: ${{ secrets.AZURE_CREDENTIALS }}",
            "      - name: Deploy generated Bicep",
            "        env:",
            "          AZURE_LOCATION: ${{ vars.AZURE_LOCATION }}",
            "        run: |",
            "          LOCATION=\"${AZURE_LOCATION:-eastus}\"",
            "          az deployment sub create \\",
            "            --name \"aca-${{ github.run_number }}\" \\",
            "            --location \"$LOCATION\" \\",
            "            --template-file IaC/Bicep/main.bicep \\",
            f"            --parameters {parameter_arg}",
            "",
        ]
    )


def _render_resource_group_module() -> str:
    return """targetScope = 'subscription'

param resourceName string
param location string
param tags object = {}

resource rg 'Microsoft.Resources/resourceGroups@2024-03-01' = {
  name: resourceName
  location: location
  tags: tags
}

output id string = rg.id
output name string = rg.name
output type string = 'Microsoft.Resources/resourceGroups'
"""


def _render_virtual_network_module() -> str:
    return """targetScope = 'resourceGroup'

param resourceName string
param location string
param addressPrefixes array = [
  '10.0.0.0/16'
]
param dnsServers array = []
param tags object = {}

var vnetProperties = union(
  {
    addressSpace: {
      addressPrefixes: addressPrefixes
    }
  },
  empty(dnsServers)
    ? {}
    : {
        dhcpOptions: {
          dnsServers: dnsServers
        }
      }
)

resource vnet 'Microsoft.Network/virtualNetworks@2024-03-01' = {
  name: resourceName
  location: location
  tags: tags
  properties: vnetProperties
}

output id string = vnet.id
output name string = vnet.name
output type string = 'Microsoft.Network/virtualNetworks'
"""


def _render_subnet_module() -> str:
    return """targetScope = 'resourceGroup'

param resourceName string
param virtualNetworkName string
param addressPrefix string = '10.0.1.0/24'
param privateEndpointNetworkPolicies string = 'disabled'
param serviceEndpoints array = []
param networkSecurityGroupId string = ''
param routeTableId string = ''

resource vnet 'Microsoft.Network/virtualNetworks@2024-03-01' existing = {
  name: virtualNetworkName
}

var subnetProperties = union(
  {
    addressPrefix: addressPrefix
    privateEndpointNetworkPolicies: toLower(privateEndpointNetworkPolicies) == 'enabled' ? 'Enabled' : 'Disabled'
    serviceEndpoints: [for endpoint in serviceEndpoints: {
      service: string(endpoint)
    }]
  },
  empty(networkSecurityGroupId)
    ? {}
    : {
        networkSecurityGroup: {
          id: networkSecurityGroupId
        }
      },
  empty(routeTableId)
    ? {}
    : {
        routeTable: {
          id: routeTableId
        }
      }
)

resource subnet 'Microsoft.Network/virtualNetworks/subnets@2024-03-01' = {
  name: resourceName
  parent: vnet
  properties: subnetProperties
}

output id string = subnet.id
output name string = subnet.name
output type string = 'Microsoft.Network/virtualNetworks/subnets'
"""


def _render_network_security_group_module() -> str:
    return """targetScope = 'resourceGroup'

param resourceName string
param location string
param securityRules array = []
param tags object = {}

resource nsg 'Microsoft.Network/networkSecurityGroups@2024-03-01' = {
  name: resourceName
  location: location
  tags: tags
  properties: {
    securityRules: [for rule in securityRules: {
      name: string(rule.?name ?? 'rule')
      properties: {
        priority: int(rule.?priority ?? 100)
        direction: toLower(string(rule.?direction ?? 'inbound')) == 'outbound' ? 'Outbound' : 'Inbound'
        access: toLower(string(rule.?access ?? 'allow')) == 'deny' ? 'Deny' : 'Allow'
        protocol: toLower(string(rule.?protocol ?? '*')) == 'tcp' ? 'Tcp' : (toLower(string(rule.?protocol ?? '*')) == 'udp' ? 'Udp' : '*')
        sourceAddressPrefix: string(rule.?sourceAddressPrefix ?? '*')
        sourcePortRange: string(rule.?sourcePortRange ?? '*')
        destinationAddressPrefix: string(rule.?destinationAddressPrefix ?? '*')
        destinationPortRange: string(rule.?destinationPortRange ?? '*')
      }
    }]
  }
}

output id string = nsg.id
output name string = nsg.name
output type string = 'Microsoft.Network/networkSecurityGroups'
"""


def _render_route_table_module() -> str:
    return """targetScope = 'resourceGroup'

param resourceName string
param location string
param disableBgpRoutePropagation bool = false
param routes array = []
param tags object = {}

resource routeTable 'Microsoft.Network/routeTables@2024-03-01' = {
  name: resourceName
  location: location
  tags: tags
  properties: {
    disableBgpRoutePropagation: disableBgpRoutePropagation
    routes: [for route in routes: {
      name: string(route.?name ?? 'route')
      properties: {
        addressPrefix: string(route.?addressPrefix ?? '0.0.0.0/0')
        nextHopType: toLower(string(route.?nextHopType ?? 'internet')) == 'virtualappliance'
          ? 'VirtualAppliance'
          : (toLower(string(route.?nextHopType ?? 'internet')) == 'vnetlocal'
            ? 'VnetLocal'
            : (toLower(string(route.?nextHopType ?? 'internet')) == 'none' ? 'None' : 'Internet'))
        nextHopIpAddress: string(route.?nextHopIpAddress ?? '')
      }
    }]
  }
}

output id string = routeTable.id
output name string = routeTable.name
output type string = 'Microsoft.Network/routeTables'
"""


def _render_public_ip_module() -> str:
    return """targetScope = 'resourceGroup'

param resourceName string
param location string
param allocationMethod string = 'static'
param ipVersion string = 'ipv4'
param skuName string = 'standard'
param idleTimeoutMinutes int = 4
param tags object = {}

resource publicIp 'Microsoft.Network/publicIPAddresses@2024-03-01' = {
  name: resourceName
  location: location
  tags: tags
  sku: {
    name: toLower(skuName) == 'basic' ? 'Basic' : 'Standard'
  }
  properties: {
    publicIPAllocationMethod: toLower(allocationMethod) == 'dynamic' ? 'Dynamic' : 'Static'
    publicIPAddressVersion: toLower(ipVersion) == 'ipv6' ? 'IPv6' : 'IPv4'
    idleTimeoutInMinutes: idleTimeoutMinutes
  }
}

output id string = publicIp.id
output name string = publicIp.name
output type string = 'Microsoft.Network/publicIPAddresses'
"""


def _render_generic_module(node: ResourceNode) -> str:
    safe_type = node.arm_type or "unknown"
    safe_api = node.api_version or "unknown"
    return "\n".join(
        [
            "targetScope = 'resourceGroup'",
            "",
            "@description('Manual implementation required for this resource type.')",
            "param resourceName string",
            "param location string",
            "param tags object = {}",
            "",
            f"output id string = ''",
            "output name string = resourceName",
            f"output type string = '{safe_type}@{safe_api}'",
            "",
        ]
    )


def _write_iac_files(
    *,
    output_dir: Path,
    modules: Mapping[str, str],
    main_bicep: str,
    parameter_file_name: str,
    parameter_content: str,
    alternate_parameter_file_name: str,
    pipeline_content: str,
) -> list[str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    modules_dir = output_dir / "modules"
    modules_dir.mkdir(parents=True, exist_ok=True)

    generated_module_names = {Path(path).name for path in modules.keys()}
    for existing_module in modules_dir.glob("*.bicep"):
        if existing_module.name not in generated_module_names:
            existing_module.unlink(missing_ok=True)

    written_files: list[str] = []

    for module_path, content in modules.items():
        target = output_dir / module_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        written_files.append(target.relative_to(output_dir).as_posix())

    main_path = output_dir / "main.bicep"
    main_path.write_text(main_bicep, encoding="utf-8")
    written_files.append("main.bicep")

    parameter_path = output_dir / parameter_file_name
    parameter_path.write_text(parameter_content, encoding="utf-8")
    written_files.append(parameter_file_name)

    alternate_parameter_path = output_dir / alternate_parameter_file_name
    if alternate_parameter_path.exists():
        alternate_parameter_path.unlink(missing_ok=True)

    pipeline_path = output_dir / "pipeline.yml"
    pipeline_path.write_text(pipeline_content, encoding="utf-8")
    written_files.append("pipeline.yml")

    return sorted(set(written_files))


def _collect_guardrails_from_mcp(
    app_settings: Mapping[str, Any],
    nodes: list[ResourceNode],
    deps_by_id: dict[str, set[str]],
) -> list[str]:
    try:
        credentials = AzureMcpCredentials.from_app_settings(app_settings)
    except Exception:
        return []

    summary_lines = ["Generated architecture resources:"]
    for node in nodes[:30]:
        summary_lines.append(f"- {node.name} ({node.resource_label})")
    summary_lines.append("Dependencies:")
    for node in nodes[:30]:
        deps = [dep for dep in deps_by_id.get(node.id, set())]
        if not deps:
            continue
        summary_lines.append(f"- {node.name} depends on {len(deps)} resources")

    args = {
        "command": "cloudarchitect_design",
        "question": "Provide concise Azure IaC guardrails for this architecture. Return JSON: {\"guardrails\":[\"...\"]}",
        "answer": "\n".join(summary_lines),
        "question-number": 1,
        "total-questions": 1,
        "next-question-needed": False,
        "state": "{}",
    }

    try:
        payload = _run_async(_invoke_mcp_guardrails(credentials, args))
        extracted = _extract_guardrails_from_payload(payload)
        return extracted[:10]
    except Exception:
        return []


def _collect_guardrails_from_coding_model(
    *,
    app_settings: Mapping[str, Any],
    nodes: list[ResourceNode],
    deps_by_id: dict[str, set[str]],
    generation_warnings: list[str],
    parameter_format: str,
    foundry_agent_id: str | None,
    foundry_thread_id: str | None,
    project_name: str,
    project_id: str,
) -> list[str]:
    result_holder: dict[str, list[str]] = {"value": []}
    done = threading.Event()

    def worker() -> None:
        try:
            result_holder["value"] = _collect_guardrails_from_coding_model_inner(
                app_settings=app_settings,
                nodes=nodes,
                deps_by_id=deps_by_id,
                generation_warnings=generation_warnings,
                parameter_format=parameter_format,
                foundry_agent_id=foundry_agent_id,
                foundry_thread_id=foundry_thread_id,
                project_name=project_name,
                project_id=project_id,
            )
        except Exception:
            result_holder["value"] = []
        finally:
            done.set()

    thread = threading.Thread(target=worker, daemon=True, name="iac-coding-guardrails")
    thread.start()
    done.wait(timeout=CODING_MODEL_GUARDRAIL_TIMEOUT_SECONDS)

    if not done.is_set():
        generation_warnings.append("Coding-model guardrails timed out and were skipped.")
        return []

    return result_holder.get("value", [])


def _collect_guardrails_from_coding_model_inner(
    *,
    app_settings: Mapping[str, Any],
    nodes: list[ResourceNode],
    deps_by_id: dict[str, set[str]],
    generation_warnings: list[str],
    parameter_format: str,
    foundry_agent_id: str | None,
    foundry_thread_id: str | None,
    project_name: str,
    project_id: str,
) -> list[str]:
    provider = str(app_settings.get("modelProvider") or "").strip().lower()
    if provider != "azure-foundry":
        return []

    coding_model = _first_non_empty(
        app_settings,
        "foundryModelCoding",
        "modelCoding",
    )
    if not coding_model:
        return []

    safe_agent_id = str(foundry_agent_id or app_settings.get("foundryDefaultAgentId") or "").strip()
    safe_thread_id = str(foundry_thread_id or app_settings.get("foundryDefaultThreadId") or "").strip()
    if not safe_agent_id or not safe_thread_id:
        return []

    try:
        base_connection = FoundryConnectionSettings.from_app_settings(app_settings)
    except FoundryConfigurationError:
        return []

    connection = FoundryConnectionSettings(
        endpoint=base_connection.endpoint,
        tenant_id=base_connection.tenant_id,
        client_id=base_connection.client_id,
        client_secret=base_connection.client_secret,
        model_deployment=str(coding_model).strip(),
        api_version=base_connection.api_version,
    )

    instruction_text = _load_agent_definition_text()

    dependency_summary: list[str] = []
    for node in nodes[:40]:
        dep_count = len(deps_by_id.get(node.id, set()))
        dependency_summary.append(f"- {node.name} ({node.resource_label}) deps={dep_count}")

    warning_summary = "\n".join(f"- {item}" for item in generation_warnings[:10]) if generation_warnings else "- none"

    prompt = "\n".join(
        [
            instruction_text,
            "",
            "Return compact JSON with this shape:",
            '{"guardrails":["..."]}',
            "",
            f"Project: {project_name} ({project_id})",
            f"Parameter format: {parameter_format}",
            "",
            "Resources and dependency counts:",
            "\n".join(dependency_summary),
            "",
            "Existing generator warnings:",
            warning_summary,
            "",
            "Provide up to 8 practical guardrails focused on: schema validity, dependency safety, secure defaults, and deployability.",
            "Do not include explanations. Return JSON only.",
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
        if not isinstance(parsed, Mapping):
            return []
        raw_guardrails = parsed.get("guardrails")
        if not isinstance(raw_guardrails, list):
            return []
        cleaned = [str(item).strip() for item in raw_guardrails if str(item).strip()]
        return cleaned[:8]
    except Exception:
        return []


def _load_agent_definition_text() -> str:
    try:
        content = AGENT_DEFINITION_PATH.read_text(encoding="utf-8").strip()
        return content or "You are an IaC quality reviewer."
    except Exception:
        return "You are an IaC quality reviewer."


def _extract_guardrails_from_payload(payload: Any) -> list[str]:
    if isinstance(payload, Mapping):
        candidates = [
            payload.get("guardrails"),
            payload.get("recommendations"),
        ]

        response_object = payload.get("responseObject") if isinstance(payload.get("responseObject"), Mapping) else None
        if response_object:
            candidates.append(response_object.get("guardrails"))
            display_hint = str(response_object.get("displayHint") or "").strip()
            if display_hint:
                candidates.append(display_hint.splitlines())

        results = payload.get("results") if isinstance(payload.get("results"), Mapping) else None
        if results:
            response_obj = results.get("responseObject") if isinstance(results.get("responseObject"), Mapping) else None
            if response_obj:
                candidates.append(response_obj.get("guardrails"))
                display_hint = str(response_obj.get("displayHint") or "").strip()
                if display_hint:
                    candidates.append(display_hint.splitlines())

        extracted: list[str] = []
        for candidate in candidates:
            if isinstance(candidate, list):
                extracted.extend(str(item).strip(" -\t") for item in candidate if str(item).strip())
            elif isinstance(candidate, str):
                extracted.extend(
                    str(item).strip(" -\t")
                    for item in candidate.splitlines()
                    if str(item).strip()
                )

        filtered = [
            item for item in extracted
            if item and "tool" not in item.lower() and "not found" not in item.lower() and "error" not in item.lower()
        ]
        return _dedupe_preserve_order(filtered)

    if isinstance(payload, str):
        filtered = [
            str(item).strip(" -\t")
            for item in payload.splitlines()
            if str(item).strip()
        ]
        filtered = [
            item for item in filtered
            if item and "tool" not in item.lower() and "not found" not in item.lower() and "error" not in item.lower()
        ]
        return _dedupe_preserve_order(filtered)

    return []


def _extract_json_from_text(text: str) -> Any:
    raw = str(text or "").strip()
    if not raw:
        return None

    try:
        return json.loads(raw)
    except Exception:
        pass

    fenced_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, flags=re.DOTALL | re.IGNORECASE)
    if fenced_match:
        fenced = fenced_match.group(1)
        try:
            return json.loads(fenced)
        except Exception:
            pass

    brace_match = re.search(r"(\{.*\})", raw, flags=re.DOTALL)
    if brace_match:
        candidate = brace_match.group(1)
        try:
            return json.loads(candidate)
        except Exception:
            return None

    return None


def _dedupe_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        safe = str(value or "").strip()
        if not safe:
            continue
        key = safe.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(safe)
    return result


def _first_non_empty(settings: Mapping[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = settings.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def _run_async(coro):
    return asyncio.run(coro)


async def _invoke_mcp_guardrails(credentials: AzureMcpCredentials, args: dict[str, Any]) -> Any:
    try:
        mcp_module = importlib.import_module("mcp")
        mcp_stdio_module = importlib.import_module("mcp.client.stdio")
    except ModuleNotFoundError as exc:
        raise RuntimeError("Python package 'mcp' is required for Azure MCP guardrails") from exc

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
            payload_text = await _call_mcp_guardrail_tool(session, args)

    parsed = _extract_json_from_text(payload_text)
    if parsed is not None:
        return parsed
    return payload_text


async def _call_mcp_guardrail_tool(session: Any, args: dict[str, Any]) -> str:
    tool_candidates = ["cloudarchitect_design", "cloudarchitect"]
    last_error = ""

    for tool_name in tool_candidates:
        try:
            result = await asyncio.wait_for(session.call_tool(tool_name, args), timeout=35)
            return _extract_tool_text(getattr(result, "content", result))
        except Exception as exc:
            last_error = str(exc)
            continue

    raise RuntimeError(f"Unable to call Azure MCP guardrail tool: {last_error}")


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
