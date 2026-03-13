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
from Agents.common.activity_log import log_activity as write_activity_log

DEFAULT_PARAMETER_FORMAT = "bicepparam"
SUPPORTED_PARAMETER_FORMATS = {"bicepparam", "json"}
CODING_MODEL_GUARDRAIL_TIMEOUT_SECONDS = 45
MCP_TEMPLATE_GENERATION_TIMEOUT_SECONDS = 45
DEFAULT_LIVE_TEMPLATE_STRICT = True
CATALOG_PATH = Path(__file__).resolve().parents[2] / "Clouds" / "Azure" / "resource_catalog.json"
AGENT_DEFINITION_PATH = Path(__file__).resolve().parents[1] / "iac_generation_agent.md"
IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
NON_DEPENDENCY_REF_KEYS = {
    "associatedsubnetref",
}


def _log_iac_event(
    event_type: str,
    *,
    level: str = "info",
    step: str = "",
    details: Mapping[str, Any] | None = None,
    project_id: str | None = None,
) -> None:
    category = "mcp" if str(event_type or "").strip().lower().startswith("mcp.") else "codegen"
    write_activity_log(
        event_type=str(event_type or "codegen.event").strip() or "codegen.event",
        category=category,
        level=level,
        step=str(step or event_type or "codegen.event").strip() or "codegen.event",
        source="agent.iac",
        project_id=str(project_id or "").strip() or None,
        details=details if isinstance(details, Mapping) else None,
    )


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
    detail_items: list[Mapping[str, Any]] | None = None,
    detail_summary: str = "",
) -> None:
    if not callable(progress_callback):
        return

    payload = {
        "stage": stage,
        "status": status,
        "message": str(message or "").strip(),
        "progress": max(0, min(100, int(progress))),
    }

    safe_detail_items: list[dict[str, str]] = []
    if isinstance(detail_items, list):
        for item in detail_items:
            if not isinstance(item, Mapping):
                continue
            label = str(item.get("label") or "Step").strip() or "Step"
            value = str(item.get("value") or "").strip()
            if not value:
                continue
            status_value = str(item.get("status") or "info").strip().lower()
            if status_value not in {"info", "pass", "fail", "warning", "skipped"}:
                status_value = "info"
            safe_detail_items.append(
                {
                    "label": label,
                    "value": value,
                    "status": status_value,
                }
            )
            if len(safe_detail_items) >= 20:
                break

    if safe_detail_items:
        payload["detailItems"] = safe_detail_items

    safe_detail_summary = str(detail_summary or "").strip()
    if safe_detail_summary:
        payload["detailSummary"] = safe_detail_summary

    try:
        progress_callback(payload)
    except Exception:
        return


def _normalize_guardrail_status(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text in {"pass", "passed", "ok", "success", "compliant", "true"}:
        return "pass"
    if text in {"fail", "failed", "error", "violation", "noncompliant", "false"}:
        return "fail"
    if text in {"warn", "warning", "caution"}:
        return "warning"
    if text in {"skip", "skipped", "n/a", "na"}:
        return "skipped"
    return "info"


def _looks_like_guardrail_tooling_error(value: Any) -> bool:
    text = str(value or "").strip().lower()
    if not text:
        return False

    if re.search(r"\btool\b.+\bnot\s+found\b", text):
        return True

    known_fragments = (
        "unable to call azure mcp",
        "azure mcp guardrail evaluation failed",
        "python package 'mcp' is required",
        "traceback (most recent call last)",
    )
    return any(fragment in text for fragment in known_fragments)


def _coerce_guardrail_check(item: Any) -> dict[str, str] | None:
    if isinstance(item, Mapping):
        name = str(
            item.get("name")
            or item.get("rule")
            or item.get("title")
            or item.get("guardrail")
            or item.get("check")
            or ""
        ).strip()
        if not name:
            return None

        reason = str(
            item.get("reason")
            or item.get("detail")
            or item.get("details")
            or item.get("message")
            or item.get("result")
            or ""
        ).strip()

        status = _normalize_guardrail_status(item.get("status") or item.get("outcome") or item.get("state"))
        if status == "info" and isinstance(item.get("passed"), bool):
            status = "pass" if bool(item.get("passed")) else "fail"

        return {
            "name": name,
            "status": status,
            "reason": reason,
        }

    text = str(item or "").strip()
    if not text:
        return None

    if _looks_like_guardrail_tooling_error(text):
        return None

    prefix_match = re.match(r"^(pass|passed|fail|failed|warning|warn|skipped|skip|info)\s*[:\-]\s*(.+)$", text, flags=re.IGNORECASE)
    if prefix_match:
        status = _normalize_guardrail_status(prefix_match.group(1))
        name = str(prefix_match.group(2) or "").strip()
        if name:
            return {
                "name": name,
                "status": status,
                "reason": "",
            }

    suffix_match = re.match(r"^(.+?)\s*[:\-]\s*(pass|passed|fail|failed|warning|warn|skipped|skip|info)\s*$", text, flags=re.IGNORECASE)
    if suffix_match:
        name = str(suffix_match.group(1) or "").strip()
        status = _normalize_guardrail_status(suffix_match.group(2))
        if name:
            return {
                "name": name,
                "status": status,
                "reason": "",
            }

    return {
        "name": text,
        "status": "info",
        "reason": "",
    }


def _extract_guardrail_checks_from_payload(payload: Any) -> tuple[list[dict[str, str]], str]:
    checks: list[dict[str, str]] = []
    explanation = ""

    def append_candidate(candidate: Any) -> None:
        nonlocal explanation
        if isinstance(candidate, list):
            for item in candidate:
                coerced = _coerce_guardrail_check(item)
                if coerced:
                    checks.append(coerced)
            return

        if isinstance(candidate, str):
            if not candidate.strip():
                return
            if "\n" in candidate:
                parsed_any = False
                for line in candidate.splitlines():
                    coerced = _coerce_guardrail_check(line)
                    if coerced:
                        checks.append(coerced)
                        parsed_any = True
                if not parsed_any and not explanation:
                    explanation = str(candidate).strip()
                return
            coerced = _coerce_guardrail_check(candidate)
            if coerced:
                checks.append(coerced)
            elif not explanation:
                explanation = str(candidate).strip()
            return

        if isinstance(candidate, Mapping):
            append_candidate(candidate.get("checks"))
            append_candidate(candidate.get("guardrails"))
            append_candidate(candidate.get("recommendations"))
            if not explanation:
                explanation = str(
                    candidate.get("explanation")
                    or candidate.get("summary")
                    or candidate.get("message")
                    or ""
                ).strip()

            response_object = candidate.get("responseObject") if isinstance(candidate.get("responseObject"), Mapping) else None
            if response_object:
                append_candidate(response_object)

            results = candidate.get("results") if isinstance(candidate.get("results"), Mapping) else None
            if results:
                append_candidate(results)

    append_candidate(payload)

    seen: set[str] = set()
    deduped: list[dict[str, str]] = []
    for item in checks:
        key = f"{item.get('name','').strip().lower()}|{item.get('status','').strip().lower()}|{item.get('reason','').strip().lower()}"
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(item)
        if len(deduped) >= 20:
            break

    return deduped, explanation


def _summarize_guardrail_counts(checks: list[dict[str, str]]) -> dict[str, int]:
    counts = {
        "tested": len(checks),
        "passed": 0,
        "failed": 0,
        "warning": 0,
        "skipped": 0,
        "info": 0,
    }
    for item in checks:
        status = _normalize_guardrail_status(item.get("status"))
        if status == "pass":
            counts["passed"] += 1
        elif status == "fail":
            counts["failed"] += 1
        elif status == "warning":
            counts["warning"] += 1
        elif status == "skipped":
            counts["skipped"] += 1
        else:
            counts["info"] += 1
    return counts


def _guardrail_checks_to_lines(checks: list[dict[str, str]]) -> list[str]:
    lines: list[str] = []
    for item in checks:
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        status = _normalize_guardrail_status(item.get("status"))
        reason = str(item.get("reason") or "").strip()
        if reason:
            lines.append(f"{status.upper()}: {name} ({reason})")
        else:
            lines.append(f"{status.upper()}: {name}")
    return lines


def _safe_non_negative_int(value: Any) -> int:
    try:
        return max(0, int(value))
    except Exception:
        return 0


def _evaluate_guardrail_source_pass(diagnostics: Mapping[str, Any]) -> tuple[bool, str]:
    source = str(diagnostics.get("source") or "Guardrail source").strip() or "Guardrail source"
    explanation = str(diagnostics.get("explanation") or "").strip()
    connection_state = str(diagnostics.get("connectionState") or "unknown").strip().lower()
    counts = diagnostics.get("counts") if isinstance(diagnostics.get("counts"), Mapping) else {}

    tested = _safe_non_negative_int(counts.get("tested"))
    passed = _safe_non_negative_int(counts.get("passed"))
    failed = _safe_non_negative_int(counts.get("failed"))
    warning = _safe_non_negative_int(counts.get("warning"))
    skipped = _safe_non_negative_int(counts.get("skipped"))
    info = _safe_non_negative_int(counts.get("info"))

    if connection_state != "connected":
        reason = explanation or f"{source} did not complete successfully ({connection_state})."
        return False, reason

    if tested <= 0:
        reason = explanation or f"{source} returned zero checks."
        return False, reason

    if failed > 0 or warning > 0 or skipped > 0 or info > 0 or passed < tested:
        outcomes: list[str] = []
        if failed > 0:
            outcomes.append(f"failed={failed}")
        if warning > 0:
            outcomes.append(f"warning={warning}")
        if skipped > 0:
            outcomes.append(f"skipped={skipped}")
        if info > 0:
            outcomes.append(f"info={info}")
        if passed < tested:
            outcomes.append(f"passed={passed}/{tested}")

        reason = f"{source} requires all checks to pass ({'; '.join(outcomes)})."
        if explanation:
            reason = f"{reason} {explanation}"
        return False, reason

    return True, explanation or f"{source} passed all checks ({passed}/{tested})."


def _build_guardrail_gate_failure_message(
    *,
    mcp_diagnostics: Mapping[str, Any],
    coding_diagnostics: Mapping[str, Any],
) -> tuple[bool, str]:
    mcp_ok, mcp_reason = _evaluate_guardrail_source_pass(mcp_diagnostics)
    coding_ok, coding_reason = _evaluate_guardrail_source_pass(coding_diagnostics)

    if mcp_ok and coding_ok:
        return True, ""

    reasons: list[str] = []
    if not mcp_ok:
        reasons.append(f"Azure MCP guardrails failed: {mcp_reason}")
    if not coding_ok:
        reasons.append(f"Coding-model guardrails failed: {coding_reason}")

    return False, "Guardrail gate blocked file generation. " + " ".join(reasons)


def _build_template_stage_detail_items(diagnostics: Mapping[str, Any]) -> list[dict[str, str]]:
    details: list[dict[str, str]] = []

    connection_state = str(diagnostics.get("connectionState") or "unknown").strip().lower()
    connection_label = {
        "connected": "Connected",
        "unavailable": "Unavailable",
        "failed": "Failed",
    }.get(connection_state, "Unknown")
    details.append(
        {
            "label": "MCP connection",
            "value": connection_label,
            "status": "pass" if connection_state == "connected" else ("warning" if connection_state == "unavailable" else "fail"),
        }
    )

    details.append(
        {
            "label": "Schema queries",
            "value": (
                f"{int(diagnostics.get('queryCount') or 0)} type queries; "
                f"{int(diagnostics.get('querySuccessCount') or 0)} succeeded; "
                f"{int(diagnostics.get('queryFailureCount') or 0)} failed"
            ),
            "status": "info",
        }
    )

    queried_types = diagnostics.get("queriedResourceTypes") if isinstance(diagnostics.get("queriedResourceTypes"), list) else []
    if queried_types:
        details.append(
            {
                "label": "Resource types queried",
                "value": ", ".join(str(item) for item in queried_types[:10]),
                "status": "info",
            }
        )

    response_quality = str(diagnostics.get("responseQuality") or "unknown").strip().lower()
    details.append(
        {
            "label": "Template response",
            "value": (
                f"{response_quality}; {int(diagnostics.get('liveTemplateModules') or 0)} live template modules; "
                f"{int(diagnostics.get('fallbackTemplateModules') or 0)} fallback modules"
            ),
            "status": "pass" if response_quality == "full" else ("warning" if response_quality in {"partial", "none"} else "info"),
        }
    )

    failure_samples = diagnostics.get("failureSamples") if isinstance(diagnostics.get("failureSamples"), list) else []
    if failure_samples:
        details.append(
            {
                "label": "Fallback reasons",
                "value": "; ".join(str(item) for item in failure_samples[:3]),
                "status": "warning",
            }
        )

    return details[:12]


def _build_guardrail_stage_detail_items(diagnostics: Mapping[str, Any]) -> list[dict[str, str]]:
    details: list[dict[str, str]] = []

    source = str(diagnostics.get("source") or "Guardrail engine").strip() or "Guardrail engine"
    connection_state = str(diagnostics.get("connectionState") or "unknown").strip().lower()
    connection_label = {
        "connected": "Connected",
        "unavailable": "Unavailable",
        "failed": "Failed",
        "skipped": "Skipped",
    }.get(connection_state, "Unknown")
    details.append(
        {
            "label": f"{source} connection",
            "value": connection_label,
            "status": "pass" if connection_state == "connected" else ("warning" if connection_state in {"unavailable", "skipped"} else "fail"),
        }
    )

    counts = diagnostics.get("counts") if isinstance(diagnostics.get("counts"), Mapping) else {}
    details.append(
        {
            "label": "Check totals",
            "value": (
                f"tested={int(counts.get('tested') or 0)}, "
                f"passed={int(counts.get('passed') or 0)}, "
                f"failed={int(counts.get('failed') or 0)}, "
                f"warning={int(counts.get('warning') or 0)}, "
                f"skipped={int(counts.get('skipped') or 0)}"
            ),
            "status": "info",
        }
    )

    checks = diagnostics.get("checks") if isinstance(diagnostics.get("checks"), list) else []
    for item in checks[:8]:
        if not isinstance(item, Mapping):
            continue
        name = str(item.get("name") or "Guardrail").strip() or "Guardrail"
        status = _normalize_guardrail_status(item.get("status"))
        reason = str(item.get("reason") or "").strip()
        if reason:
            value = f"{status.upper()} — {reason}"
        else:
            value = status.upper()
        details.append(
            {
                "label": name,
                "value": value,
                "status": status,
            }
        )

    return details[:14]


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
    _log_iac_event(
        "codegen.generate",
        level="info",
        step="requested",
        project_id=project_id,
        details={
            "projectName": project_name,
            "parameterFormat": parameter_format,
        },
    )

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
        _log_iac_event(
            "codegen.generate",
            level="warning",
            step="failed",
            project_id=project_id,
            details={
                "projectName": project_name,
                "reason": "no-canvas-resources",
            },
        )
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
        message="Rendering live Bicep modules from Azure MCP and composing main template",
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

    module_contents, template_diagnostics = _render_modules_from_live_templates(
        app_settings=app_settings,
        nodes=nodes,
        generation_warnings=generation_warnings,
    )

    main_bicep = _render_main_bicep(nodes, nodes_by_id, deps_by_id)

    template_response_quality = str(template_diagnostics.get("responseQuality") or "unknown").strip().lower()
    if template_response_quality == "none":
        template_message = "No live templates were returned by Azure MCP; local fallback templates were used"
    elif template_response_quality == "partial":
        template_message = "Azure MCP returned partial templates; missing templates used local fallback"
    else:
        template_message = f"Rendered {len(module_contents)} modules and main.bicep"

    _emit_progress(
        progress_callback,
        stage="render_templates",
        status="completed",
        message=template_message,
        progress=55,
        detail_items=_build_template_stage_detail_items(template_diagnostics),
        detail_summary=str(template_diagnostics.get("explanation") or "").strip(),
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

    mcp_guardrails, mcp_guardrail_diagnostics = _collect_guardrails_from_mcp(app_settings, nodes, deps_by_id)
    mcp_counts = mcp_guardrail_diagnostics.get("counts") if isinstance(mcp_guardrail_diagnostics.get("counts"), Mapping) else {}
    mcp_tested = int(mcp_counts.get("tested") or 0)
    if mcp_tested <= 0:
        mcp_message = "Azure MCP guardrails returned no checks"
    else:
        mcp_message = f"Azure MCP guardrails evaluated ({mcp_tested} checks)"

    _emit_progress(
        progress_callback,
        stage="guardrails_mcp",
        status="completed",
        message=mcp_message,
        progress=82,
        detail_items=_build_guardrail_stage_detail_items(mcp_guardrail_diagnostics),
        detail_summary=str(mcp_guardrail_diagnostics.get("explanation") or "").strip(),
    )

    _emit_progress(
        progress_callback,
        stage="guardrails_model",
        status="running",
        message="Running coding-model guardrail checks",
        progress=85,
    )

    coding_model_guardrails, coding_guardrail_diagnostics = _collect_guardrails_from_coding_model(
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

    coding_counts = coding_guardrail_diagnostics.get("counts") if isinstance(coding_guardrail_diagnostics.get("counts"), Mapping) else {}
    coding_tested = int(coding_counts.get("tested") or 0)
    if coding_tested <= 0:
        coding_message = "Coding-model guardrails returned no checks"
    else:
        coding_message = f"Coding-model guardrails evaluated ({coding_tested} checks)"

    _emit_progress(
        progress_callback,
        stage="guardrails_model",
        status="completed",
        message=coding_message,
        progress=90,
        detail_items=_build_guardrail_stage_detail_items(coding_guardrail_diagnostics),
        detail_summary=str(coding_guardrail_diagnostics.get("explanation") or "").strip(),
    )

    _emit_progress(
        progress_callback,
        stage="write_files",
        status="running",
        message="Writing IaC files to project",
        progress=93,
    )

    guardrails_passed, guardrail_failure_message = _build_guardrail_gate_failure_message(
        mcp_diagnostics=mcp_guardrail_diagnostics,
        coding_diagnostics=coding_guardrail_diagnostics,
    )
    if not guardrails_passed:
        generation_warnings.append(guardrail_failure_message)
        _log_iac_event(
            "codegen.guardrails",
            level="warning",
            step="blocked",
            project_id=project_id,
            details={
                "reason": guardrail_failure_message,
                "mcpConnectionState": str(mcp_guardrail_diagnostics.get("connectionState") or "").strip(),
                "codingConnectionState": str(coding_guardrail_diagnostics.get("connectionState") or "").strip(),
            },
        )
        raise ValueError(guardrail_failure_message)

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
    _log_iac_event(
        "codegen.generate",
        level="info",
        step="completed",
        project_id=project_id,
        details={
            "projectName": project_name,
            "resourceCount": len(canvas_items),
            "connectionCount": len(canvas_connections),
            "moduleCount": len(module_contents),
            "warningCount": len(generation_warnings),
        },
    )

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


def _read_live_template_strict_setting(app_settings: Mapping[str, Any]) -> bool:
    raw_value = app_settings.get("iacLiveTemplateStrict")
    if raw_value is None:
        return DEFAULT_LIVE_TEMPLATE_STRICT
    if isinstance(raw_value, bool):
        return raw_value

    text = str(raw_value).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return DEFAULT_LIVE_TEMPLATE_STRICT


def _render_modules_from_live_templates(
    *,
    app_settings: Mapping[str, Any],
    nodes: list[ResourceNode],
    generation_warnings: list[str],
) -> tuple[dict[str, str], dict[str, Any]]:
    strict_live_templates = _read_live_template_strict_setting(app_settings)
    diagnostics: dict[str, Any] = {
        "source": "Azure MCP bicepschema",
        "strictMode": bool(strict_live_templates),
        "resourceCount": len(nodes),
        "connectionState": "unknown",
        "queryCount": 0,
        "querySuccessCount": 0,
        "queryFailureCount": 0,
        "cacheHitCount": 0,
        "liveTemplateModules": 0,
        "fallbackTemplateModules": 0,
        "responseQuality": "unknown",
        "queriedResourceTypes": [],
        "failureSamples": [],
        "explanation": "",
    }

    _log_iac_event(
        "mcp.live-template",
        level="info",
        step="requested",
        details={
            "resourceCount": len(nodes),
            "strictMode": bool(strict_live_templates),
        },
    )

    try:
        credentials = AzureMcpCredentials.from_app_settings(app_settings)
        diagnostics["connectionState"] = "connected"
    except Exception as exc:
        message = "Azure MCP credentials are required for live template generation."
        diagnostics["connectionState"] = "unavailable"
        diagnostics["responseQuality"] = "none"
        diagnostics["liveTemplateModules"] = 0
        diagnostics["fallbackTemplateModules"] = len(nodes)
        diagnostics["explanation"] = "Azure MCP credentials are not configured, so local templates were used for all resources."
        if strict_live_templates:
            _log_iac_event(
                "mcp.live-template",
                level="error",
                step="failed",
                details={"error": str(exc)},
            )
            raise ValueError(message) from exc
        generation_warnings.append(message + " Falling back to local templates.")
        _log_iac_event(
            "mcp.live-template",
            level="warning",
            step="fallback",
            details={
                "reason": "credentials-missing",
                "resourceCount": len(nodes),
            },
        )
        return ({node.module_file: _render_module_bicep(node) for node in nodes}, diagnostics)

    module_contents: dict[str, str] = {}
    template_cache: dict[str, str] = {}
    template_source_by_key: dict[str, str] = {}
    queried_types: set[str] = set()

    for node in nodes:
        cache_key = _live_template_cache_key(node)
        template_text = template_cache.get(cache_key)

        if template_text is None:
            diagnostics["queryCount"] = int(diagnostics.get("queryCount") or 0) + 1
            type_label = str(node.resource_label or node.arm_type or "").strip()
            if type_label:
                queried_types.add(type_label)
            try:
                template_text = _generate_live_module_template(credentials=credentials, node=node)
                diagnostics["querySuccessCount"] = int(diagnostics.get("querySuccessCount") or 0) + 1
                template_source_by_key[cache_key] = "live"
            except Exception as exc:
                diagnostics["queryFailureCount"] = int(diagnostics.get("queryFailureCount") or 0) + 1
                error_message = (
                    f"Live template generation failed for {node.name} ({node.resource_label}): {exc}"
                )
                failure_samples = diagnostics.get("failureSamples") if isinstance(diagnostics.get("failureSamples"), list) else []
                if len(failure_samples) < 5:
                    failure_samples.append(error_message)
                    diagnostics["failureSamples"] = failure_samples
                if strict_live_templates:
                    _log_iac_event(
                        "mcp.live-template",
                        level="error",
                        step="failed",
                        details={
                            "resourceName": node.name,
                            "resourceType": node.resource_label,
                            "error": str(exc),
                        },
                    )
                    raise ValueError(error_message) from exc
                generation_warnings.append(error_message + " Falling back to local template.")
                template_text = _render_module_bicep(node)
                template_source_by_key[cache_key] = "fallback"
            template_cache[cache_key] = template_text
        else:
            diagnostics["cacheHitCount"] = int(diagnostics.get("cacheHitCount") or 0) + 1

        module_contents[node.module_file] = template_text

    live_template_modules = 0
    fallback_template_modules = 0
    for node in nodes:
        cache_key = _live_template_cache_key(node)
        source = str(template_source_by_key.get(cache_key) or "live").strip().lower()
        if source == "fallback":
            fallback_template_modules += 1
        else:
            live_template_modules += 1

    diagnostics["queriedResourceTypes"] = sorted(queried_types)
    diagnostics["liveTemplateModules"] = live_template_modules
    diagnostics["fallbackTemplateModules"] = fallback_template_modules

    query_failures = int(diagnostics.get("queryFailureCount") or 0)
    if live_template_modules <= 0:
        diagnostics["responseQuality"] = "none"
        diagnostics["explanation"] = "Azure MCP returned no usable live templates, so local fallback templates were used for all resources."
    elif query_failures > 0:
        diagnostics["responseQuality"] = "partial"
        diagnostics["explanation"] = "Azure MCP returned partial template coverage; missing templates were filled with local fallback templates."
    else:
        diagnostics["responseQuality"] = "full"
        diagnostics["explanation"] = "Azure MCP returned live templates for all queried resource types."

    _log_iac_event(
        "mcp.live-template",
        level="warning" if fallback_template_modules else "info",
        step="completed",
        details={
            "moduleCount": len(module_contents),
            "fallbackCount": fallback_template_modules,
            "strictMode": bool(strict_live_templates),
        },
    )

    return module_contents, diagnostics


def _live_template_cache_key(node: ResourceNode) -> str:
    kind = _resource_kind(node.arm_type)
    if kind != "generic":
        return kind
    arm_type = str(node.arm_type or "").strip().lower() or "unknown"
    api_version = str(node.api_version or "").strip().lower() or "latest"
    return f"generic::{arm_type}@{api_version}"


def _module_contract_for_kind(kind: str) -> tuple[str, list[str]]:
    if kind == "resource_group":
        return "subscription", ["resourceName", "location", "tags"]
    if kind == "virtual_network":
        return "resourceGroup", ["resourceName", "location", "addressPrefixes", "dnsServers", "tags"]
    if kind == "subnet":
        return "resourceGroup", [
            "resourceName",
            "virtualNetworkName",
            "addressPrefix",
            "privateEndpointNetworkPolicies",
            "serviceEndpoints",
            "networkSecurityGroupId",
            "routeTableId",
        ]
    if kind == "network_security_group":
        return "resourceGroup", ["resourceName", "location", "securityRules", "tags"]
    if kind == "route_table":
        return "resourceGroup", ["resourceName", "location", "disableBgpRoutePropagation", "routes", "tags"]
    if kind == "public_ip":
        return "resourceGroup", [
            "resourceName",
            "location",
            "allocationMethod",
            "ipVersion",
            "skuName",
            "idleTimeoutMinutes",
            "tags",
        ]
    return "resourceGroup", ["resourceName", "location", "tags"]


def _generate_live_module_template(*, credentials: AzureMcpCredentials, node: ResourceNode) -> str:
    kind = _resource_kind(node.arm_type)
    default_target_scope, _ = _module_contract_for_kind(kind)

    schema_details = _fetch_live_schema_details(
        credentials=credentials,
        resource_type=str(node.arm_type or "").strip(),
        default_target_scope=default_target_scope,
    )

    resource_type = str(schema_details.get("resourceType") or node.arm_type or "").strip() or "unknown"
    api_version = str(schema_details.get("apiVersion") or node.api_version or "").strip() or "2024-03-01"
    target_scope = str(schema_details.get("targetScope") or default_target_scope).strip() or default_target_scope

    if kind == "resource_group":
        return _render_resource_group_module(resource_type=resource_type, api_version=api_version)
    if kind == "virtual_network":
        return _render_virtual_network_module(resource_type=resource_type, api_version=api_version)
    if kind == "subnet":
        return _render_subnet_module(resource_type=resource_type, api_version=api_version)
    if kind == "network_security_group":
        return _render_network_security_group_module(resource_type=resource_type, api_version=api_version)
    if kind == "route_table":
        return _render_route_table_module(resource_type=resource_type, api_version=api_version)
    if kind == "public_ip":
        return _render_public_ip_module(resource_type=resource_type, api_version=api_version)
    return _render_generic_module(
        node,
        resource_type=resource_type,
        api_version=api_version,
        target_scope=target_scope,
    )


def _fetch_live_schema_details(
    *,
    credentials: AzureMcpCredentials,
    resource_type: str,
    default_target_scope: str,
) -> dict[str, str]:
    safe_resource_type = str(resource_type or "").strip()
    if not safe_resource_type:
        raise RuntimeError("Missing ARM resource type for MCP schema lookup.")

    args = {
        "intent": f"Get latest Bicep schema for {safe_resource_type}",
        "command": "bicepschema_get",
        "parameters": {
            "resource-type": safe_resource_type,
        },
    }

    payload = _run_async(_invoke_mcp_live_template(credentials, args))
    parsed_payload = payload if isinstance(payload, Mapping) else _extract_json_from_text(str(payload))
    if not isinstance(parsed_payload, Mapping):
        raise RuntimeError("Invalid response from bicepschema MCP tool.")

    results = parsed_payload.get("results") if isinstance(parsed_payload.get("results"), Mapping) else None
    schema_items = results.get("BicepSchemaResult") if isinstance(results, Mapping) else None
    if not isinstance(schema_items, list) or not schema_items:
        raise RuntimeError("No schema results returned by bicepschema MCP tool.")

    selected_resource = _select_schema_resource(schema_items, safe_resource_type)
    if not isinstance(selected_resource, Mapping):
        raise RuntimeError(f"No matching schema found for {safe_resource_type}.")

    schema_name = str(selected_resource.get("name") or "").strip()
    schema_resource_type, schema_api_version = _split_bicep_type(schema_name)
    if not schema_resource_type:
        schema_resource_type = safe_resource_type

    writable_scopes = str(selected_resource.get("writableScopes") or "").strip()
    normalized_scope = _normalize_schema_scope(writable_scopes, default_target_scope)

    return {
        "resourceType": schema_resource_type,
        "apiVersion": schema_api_version,
        "targetScope": normalized_scope,
    }


def _select_schema_resource(schema_items: list[Any], resource_type: str) -> Mapping[str, Any] | None:
    normalized_type = str(resource_type or "").strip().lower()
    fallback_match: Mapping[str, Any] | None = None

    for item in schema_items:
        if not isinstance(item, Mapping):
            continue
        if str(item.get("$type") or "").strip().lower() != "resource":
            continue

        name_value = str(item.get("name") or "").strip()
        parsed_type, _ = _split_bicep_type(name_value)
        if not parsed_type:
            continue

        if not fallback_match:
            fallback_match = item

        if parsed_type.strip().lower() == normalized_type:
            return item

    return fallback_match


def _normalize_schema_scope(scope_value: str, fallback: str) -> str:
    text = str(scope_value or "").strip().lower()
    if not text:
        return fallback
    if "subscription" in text:
        return "subscription"
    if "resourcegroup" in text:
        return "resourceGroup"
    if "managementgroup" in text:
        return "managementGroup"
    if "tenant" in text:
        return "tenant"
    return fallback


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


def _render_resource_group_module(
    *,
    resource_type: str = "Microsoft.Resources/resourceGroups",
    api_version: str = "2024-03-01",
) -> str:
    template = """targetScope = 'subscription'

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
    return template.replace(
        "Microsoft.Resources/resourceGroups@2024-03-01",
        f"{resource_type}@{api_version}",
    ).replace(
        "'Microsoft.Resources/resourceGroups'",
        f"'{resource_type}'",
    )


def _render_virtual_network_module(
    *,
    resource_type: str = "Microsoft.Network/virtualNetworks",
    api_version: str = "2024-03-01",
) -> str:
    template = """targetScope = 'resourceGroup'

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
    return template.replace(
        "Microsoft.Network/virtualNetworks@2024-03-01",
        f"{resource_type}@{api_version}",
    ).replace(
        "'Microsoft.Network/virtualNetworks'",
        f"'{resource_type}'",
    )


def _render_subnet_module(
    *,
    resource_type: str = "Microsoft.Network/virtualNetworks/subnets",
    api_version: str = "2024-03-01",
) -> str:
    template = """targetScope = 'resourceGroup'

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
    return template.replace(
        "Microsoft.Network/virtualNetworks/subnets@2024-03-01",
        f"{resource_type}@{api_version}",
    ).replace(
        "Microsoft.Network/virtualNetworks@2024-03-01",
        f"Microsoft.Network/virtualNetworks@{api_version}",
    ).replace(
        "'Microsoft.Network/virtualNetworks/subnets'",
        f"'{resource_type}'",
    )


def _render_network_security_group_module(
    *,
    resource_type: str = "Microsoft.Network/networkSecurityGroups",
    api_version: str = "2024-03-01",
) -> str:
    template = """targetScope = 'resourceGroup'

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
    return template.replace(
        "Microsoft.Network/networkSecurityGroups@2024-03-01",
        f"{resource_type}@{api_version}",
    ).replace(
        "'Microsoft.Network/networkSecurityGroups'",
        f"'{resource_type}'",
    )


def _render_route_table_module(
    *,
    resource_type: str = "Microsoft.Network/routeTables",
    api_version: str = "2024-03-01",
) -> str:
    template = """targetScope = 'resourceGroup'

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
    return template.replace(
        "Microsoft.Network/routeTables@2024-03-01",
        f"{resource_type}@{api_version}",
    ).replace(
        "'Microsoft.Network/routeTables'",
        f"'{resource_type}'",
    )


def _render_public_ip_module(
    *,
    resource_type: str = "Microsoft.Network/publicIPAddresses",
    api_version: str = "2024-03-01",
) -> str:
    template = """targetScope = 'resourceGroup'

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
    return template.replace(
        "Microsoft.Network/publicIPAddresses@2024-03-01",
        f"{resource_type}@{api_version}",
    ).replace(
        "'Microsoft.Network/publicIPAddresses'",
        f"'{resource_type}'",
    )


def _render_generic_module(
    node: ResourceNode,
    *,
    resource_type: str | None = None,
    api_version: str | None = None,
    target_scope: str = "resourceGroup",
) -> str:
    safe_type = str(resource_type or node.arm_type or "unknown").strip() or "unknown"
    safe_api = str(api_version or node.api_version or "unknown").strip() or "unknown"
    safe_target_scope = str(target_scope or "resourceGroup").strip() or "resourceGroup"

    return "\n".join(
        [
            f"targetScope = '{safe_target_scope}'",
            "",
            "@description('Schema-driven generic scaffold. Manual implementation required for this resource type.')",
            "param resourceName string",
            "param location string = deployment().location",
            "param tags object = {}",
            "param properties object = {}",
            "param parentResourceName string = ''",
            "",
            "output id string = ''",
            "output name string = empty(parentResourceName) ? resourceName : '${parentResourceName}/${resourceName}'",
            f"output type string = '{safe_type}'",
            f"output apiVersion string = '{safe_api}'",
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
) -> tuple[list[str], dict[str, Any]]:
    diagnostics: dict[str, Any] = {
        "source": "Azure MCP guardrails",
        "connectionState": "unknown",
        "counts": {
            "tested": 0,
            "passed": 0,
            "failed": 0,
            "warning": 0,
            "skipped": 0,
            "info": 0,
        },
        "checks": [],
        "explanation": "",
    }

    _log_iac_event(
        "mcp.guardrails",
        level="info",
        step="requested",
        details={"resourceCount": len(nodes)},
    )

    try:
        credentials = AzureMcpCredentials.from_app_settings(app_settings)
        diagnostics["connectionState"] = "connected"
    except Exception as exc:
        diagnostics["connectionState"] = "unavailable"
        diagnostics["explanation"] = "Azure MCP credentials are not configured, so MCP guardrail checks were skipped."
        _log_iac_event(
            "mcp.guardrails",
            level="warning",
            step="skipped",
            details={"reason": "credentials-missing", "error": str(exc)},
        )
        return [], diagnostics

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
        "question": (
            "Evaluate this architecture and return guardrail check results in strict JSON with this shape: "
            "{\"checks\":[{\"name\":\"...\",\"status\":\"pass|fail|warning|skipped\",\"reason\":\"...\"}],\"explanation\":\"...\"}. "
            "If no checks can be evaluated, return checks as [] and explain why in explanation."
        ),
        "answer": "\n".join(summary_lines),
        "question-number": 1,
        "total-questions": 1,
        "next-question-needed": False,
        "state": "{}",
    }

    try:
        payload = _run_async(_invoke_mcp_guardrails(credentials, args))
        checks, explanation = _extract_guardrail_checks_from_payload(payload)
        counts = _summarize_guardrail_counts(checks)

        diagnostics["checks"] = checks[:12]
        diagnostics["counts"] = counts
        diagnostics["explanation"] = str(explanation or "").strip()

        if not checks and not diagnostics["explanation"]:
            diagnostics["explanation"] = "Azure MCP returned no guardrail checks for this architecture context."

        _log_iac_event(
            "mcp.guardrails",
            level="info",
            step="completed",
            details={
                "guardrailCount": len(checks),
                "passed": counts.get("passed"),
                "failed": counts.get("failed"),
                "warning": counts.get("warning"),
            },
        )

        return _guardrail_checks_to_lines(checks[:10]), diagnostics
    except Exception as exc:
        diagnostics["connectionState"] = "failed"
        diagnostics["explanation"] = f"Azure MCP guardrail evaluation failed: {exc}"
        _log_iac_event(
            "mcp.guardrails",
            level="error",
            step="failed",
            details={"error": str(exc)},
        )
        return [], diagnostics


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
) -> tuple[list[str], dict[str, Any]]:
    diagnostics: dict[str, Any] = {
        "source": "Coding-model guardrails",
        "connectionState": "unknown",
        "counts": {
            "tested": 0,
            "passed": 0,
            "failed": 0,
            "warning": 0,
            "skipped": 0,
            "info": 0,
        },
        "checks": [],
        "explanation": "",
    }

    _log_iac_event(
        "codegen.guardrails.model",
        level="info",
        step="requested",
        project_id=project_id,
        details={
            "projectName": project_name,
            "resourceCount": len(nodes),
            "parameterFormat": parameter_format,
        },
    )

    result_holder: dict[str, Any] = {
        "checks": [],
        "diagnostics": diagnostics,
    }
    done = threading.Event()

    def worker() -> None:
        try:
            checks, inner_diagnostics = _collect_guardrails_from_coding_model_inner(
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
            result_holder["checks"] = checks
            result_holder["diagnostics"] = inner_diagnostics
        except Exception as exc:
            result_holder["checks"] = []
            result_holder["diagnostics"] = {
                **diagnostics,
                "connectionState": "failed",
                "explanation": f"Coding-model guardrail evaluation failed: {exc}",
            }
        finally:
            done.set()

    thread = threading.Thread(target=worker, daemon=True, name="iac-coding-guardrails")
    thread.start()
    done.wait(timeout=CODING_MODEL_GUARDRAIL_TIMEOUT_SECONDS)

    if not done.is_set():
        generation_warnings.append("Coding-model guardrails timed out and were skipped.")
        diagnostics["connectionState"] = "skipped"
        diagnostics["explanation"] = (
            f"Coding-model guardrails timed out after {CODING_MODEL_GUARDRAIL_TIMEOUT_SECONDS} seconds."
        )
        _log_iac_event(
            "codegen.guardrails.model",
            level="warning",
            step="timeout",
            project_id=project_id,
            details={
                "projectName": project_name,
                "timeoutSeconds": CODING_MODEL_GUARDRAIL_TIMEOUT_SECONDS,
            },
        )
        return [], diagnostics

    final_guardrails = result_holder.get("checks") if isinstance(result_holder.get("checks"), list) else []
    final_diagnostics = result_holder.get("diagnostics") if isinstance(result_holder.get("diagnostics"), Mapping) else diagnostics

    if not isinstance(final_diagnostics.get("counts"), Mapping):
        final_diagnostics = {
            **final_diagnostics,
            "counts": _summarize_guardrail_counts(
                final_diagnostics.get("checks") if isinstance(final_diagnostics.get("checks"), list) else []
            ),
        }

    _log_iac_event(
        "codegen.guardrails.model",
        level="info",
        step="completed",
        project_id=project_id,
        details={
            "projectName": project_name,
            "guardrailCount": len(final_guardrails),
        },
    )

    return final_guardrails, dict(final_diagnostics)


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
) -> tuple[list[str], dict[str, Any]]:
    diagnostics: dict[str, Any] = {
        "source": "Coding-model guardrails",
        "connectionState": "unknown",
        "counts": {
            "tested": 0,
            "passed": 0,
            "failed": 0,
            "warning": 0,
            "skipped": 0,
            "info": 0,
        },
        "checks": [],
        "explanation": "",
    }

    provider = str(app_settings.get("modelProvider") or "").strip().lower()
    if provider != "azure-foundry":
        diagnostics["connectionState"] = "skipped"
        diagnostics["explanation"] = "Model provider is not Azure Foundry, so coding-model guardrails were skipped."
        _log_iac_event(
            "codegen.guardrails.model",
            level="warning",
            step="skipped",
            project_id=project_id,
            details={"reason": "provider-not-azure-foundry", "provider": provider},
        )
        return [], diagnostics

    coding_model = _first_non_empty(
        app_settings,
        "foundryModelCoding",
        "modelCoding",
    )
    if not coding_model:
        diagnostics["connectionState"] = "skipped"
        diagnostics["explanation"] = "No coding model is configured, so coding-model guardrails were skipped."
        _log_iac_event(
            "codegen.guardrails.model",
            level="warning",
            step="skipped",
            project_id=project_id,
            details={"reason": "coding-model-missing"},
        )
        return [], diagnostics

    safe_agent_id = str(
        foundry_agent_id
        or app_settings.get("foundryIacAgentId")
        or app_settings.get("foundryChatAgentId")
        or app_settings.get("foundryDefaultAgentId")
        or ""
    ).strip()
    safe_thread_id = str(foundry_thread_id or app_settings.get("foundryDefaultThreadId") or "").strip()
    if not safe_agent_id or not safe_thread_id:
        diagnostics["connectionState"] = "skipped"
        diagnostics["explanation"] = "Foundry agent or thread is missing, so coding-model guardrails were skipped."
        _log_iac_event(
            "codegen.guardrails.model",
            level="warning",
            step="skipped",
            project_id=project_id,
            details={
                "reason": "foundry-agent-or-thread-missing",
                "foundryAgentId": safe_agent_id,
                "foundryThreadId": safe_thread_id,
            },
        )
        return [], diagnostics

    try:
        base_connection = FoundryConnectionSettings.from_app_settings(app_settings)
    except FoundryConfigurationError:
        diagnostics["connectionState"] = "skipped"
        diagnostics["explanation"] = "Foundry configuration is incomplete, so coding-model guardrails were skipped."
        _log_iac_event(
            "codegen.guardrails.model",
            level="warning",
            step="skipped",
            project_id=project_id,
            details={"reason": "foundry-configuration-incomplete"},
        )
        return [], diagnostics

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
            '{"checks":[{"name":"...","status":"pass|fail|warning|skipped","reason":"..."}],"explanation":"..."}',
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
            "Provide up to 8 practical guardrail checks focused on schema validity, dependency safety, secure defaults, and deployability.",
            "Each check must include status and reason. Return JSON only.",
        ]
    )

    try:
        diagnostics["connectionState"] = "connected"
        runner = FoundryAssistantRunner(connection, timeout_seconds=60)
        result = runner.run_assistant(
            assistant_id=safe_agent_id,
            thread_id=safe_thread_id,
            content=prompt,
        )
        parsed = _extract_json_from_text(str(result.response_text or ""))
        checks, explanation = _extract_guardrail_checks_from_payload(parsed if parsed is not None else str(result.response_text or ""))
        checks = checks[:8]
        diagnostics["checks"] = checks
        diagnostics["counts"] = _summarize_guardrail_counts(checks)
        diagnostics["explanation"] = str(explanation or "").strip()
        if not checks and not diagnostics["explanation"]:
            diagnostics["explanation"] = "Coding model returned no guardrail checks for this architecture context."

        return _guardrail_checks_to_lines(checks), diagnostics
    except Exception as exc:
        diagnostics["connectionState"] = "failed"
        diagnostics["explanation"] = f"Coding-model guardrail evaluation failed: {exc}"
        _log_iac_event(
            "codegen.guardrails.model",
            level="error",
            step="failed",
            project_id=project_id,
            details={"error": str(exc)},
        )
        return [], diagnostics


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
    _log_iac_event(
        "mcp.guardrails",
        level="info",
        step="session-start",
        details={"command": "@azure/mcp server start"},
    )

    try:
        mcp_module = importlib.import_module("mcp")
        mcp_stdio_module = importlib.import_module("mcp.client.stdio")
    except ModuleNotFoundError as exc:
        _log_iac_event(
            "mcp.guardrails",
            level="error",
            step="failed",
            details={"error": str(exc)},
        )
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
            payload_text = await _call_mcp_cloudarchitect_tool(session, args, timeout_seconds=35)

    _log_iac_event(
        "mcp.guardrails",
        level="info",
        step="session-completed",
    )

    parsed = _extract_json_from_text(payload_text)
    if parsed is not None:
        return parsed
    return payload_text


async def _invoke_mcp_live_template(credentials: AzureMcpCredentials, args: dict[str, Any]) -> Any:
    try:
        mcp_module = importlib.import_module("mcp")
        mcp_stdio_module = importlib.import_module("mcp.client.stdio")
    except ModuleNotFoundError as exc:
        _log_iac_event(
            "mcp.live-template",
            level="error",
            step="failed",
            details={"error": str(exc)},
        )
        raise RuntimeError("Python package 'mcp' is required for live Azure MCP templates") from exc

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
            payload_text = await _call_mcp_bicepschema_tool(
                session,
                args,
                timeout_seconds=MCP_TEMPLATE_GENERATION_TIMEOUT_SECONDS,
            )

    parsed = _extract_json_from_text(payload_text)
    if parsed is not None:
        return parsed
    return payload_text


async def _call_mcp_cloudarchitect_tool(
    session: Any,
    args: dict[str, Any],
    *,
    timeout_seconds: int,
) -> str:
    tool_candidates = ["cloudarchitect", "cloudarchitect_design"]
    last_error = ""

    for tool_name in tool_candidates:
        call_args = dict(args or {})
        if tool_name == "cloudarchitect_design":
            command_name = str(call_args.get("command") or "").strip().lower()
            if command_name in {"cloudarchitect", "cloudarchitect_design"}:
                call_args.pop("command", None)
        try:
            result = await asyncio.wait_for(session.call_tool(tool_name, call_args), timeout=timeout_seconds)
            response_text = _extract_tool_text(getattr(result, "content", result))
            if _looks_like_guardrail_tooling_error(response_text):
                last_error = response_text
                _log_iac_event(
                    "mcp.guardrails",
                    level="warning",
                    step="tool-retry",
                    details={
                        "tool": tool_name,
                        "error": last_error,
                    },
                )
                continue
            return response_text
        except Exception as exc:
            last_error = str(exc)
            _log_iac_event(
                "mcp.guardrails",
                level="warning",
                step="tool-retry",
                details={
                    "tool": tool_name,
                    "error": last_error,
                },
            )
            continue

    _log_iac_event(
        "mcp.guardrails",
        level="error",
        step="failed",
        details={"error": last_error},
    )
    raise RuntimeError(f"Unable to call Azure MCP guardrail tool: {last_error}")


async def _call_mcp_bicepschema_tool(
    session: Any,
    args: dict[str, Any],
    *,
    timeout_seconds: int,
) -> str:
    try:
        result = await asyncio.wait_for(session.call_tool("bicepschema", args), timeout=timeout_seconds)
        return _extract_tool_text(getattr(result, "content", result))
    except Exception as exc:
        _log_iac_event(
            "mcp.live-template",
            level="error",
            step="failed",
            details={"error": str(exc)},
        )
        raise RuntimeError(f"Unable to call Azure MCP bicepschema tool: {exc}") from exc


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
