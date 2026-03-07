from __future__ import annotations

import asyncio
import importlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

DEFAULT_TOTAL_QUESTIONS = 4
DEFAULT_AGENT_DEFINITION_FILE = "cloudarchitect_chat_agent.md"
DEFAULT_SYSTEM_PROMPT = """You are a senior cloud architect.

You help users design cloud architectures.

You will receive:
- a user question

You should:
- answer the architecture question
"""


class AzureMcpChatConfigurationError(ValueError):
    pass


class AzureMcpChatRequestError(RuntimeError):
    pass


@dataclass(frozen=True)
class AzureMcpCredentials:
    tenant_id: str
    client_id: str
    client_secret: str
    subscription_id: str = ""

    @classmethod
    def from_app_settings(cls, settings: Mapping[str, Any]) -> "AzureMcpCredentials":
        if not isinstance(settings, Mapping):
            raise AzureMcpChatConfigurationError("settings must be a mapping")

        tenant_id = _first_non_empty(settings, "azureTenantId", "foundryTenantId")
        client_id = _first_non_empty(settings, "azureClientId", "foundryClientId")
        client_secret = _first_non_empty(settings, "azureClientSecret", "foundryClientSecret")
        subscription_id = _first_non_empty(settings, "azureSubscriptionId", "subscriptionId") or ""

        if not tenant_id:
            raise AzureMcpChatConfigurationError("AZURE_TENANT_ID is required for Azure MCP chat.")
        if not client_id:
            raise AzureMcpChatConfigurationError("AZURE_CLIENT_ID is required for Azure MCP chat.")
        if not client_secret:
            raise AzureMcpChatConfigurationError("AZURE_CLIENT_SECRET is required for Azure MCP chat.")

        return cls(
            tenant_id=str(tenant_id).strip(),
            client_id=str(client_id).strip(),
            client_secret=str(client_secret).strip(),
            subscription_id=str(subscription_id).strip(),
        )


def run_cloudarchitect_chat_agent(
    app_settings: Mapping[str, Any],
    user_message: str,
    agent_state: Mapping[str, Any] | None = None,
    project_context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    text = str(user_message or "").strip()
    if not text:
        raise AzureMcpChatConfigurationError("message is required")

    credentials = AzureMcpCredentials.from_app_settings(app_settings)
    normalized_state = _normalize_agent_state(agent_state)

    turn_count = normalized_state["turnCount"] + 1
    total_questions = int(normalized_state.get("totalQuestions") or DEFAULT_TOTAL_QUESTIONS)
    follow_up_question = _next_clarifying_question(turn_count)

    question_for_tool = follow_up_question
    next_question_needed = _needs_clarification(text, turn_count)

    tool_args: dict[str, Any] = {
        "command": "cloudarchitect_design",
        "question": question_for_tool,
        "answer": text,
        "question-number": turn_count,
        "total-questions": total_questions,
        "next-question-needed": next_question_needed,
        "state": json.dumps(normalized_state["cloudArchitectState"], ensure_ascii=False),
    }

    try:
        tool_result = _run_async(_invoke_cloudarchitect(credentials, tool_args))
    except AzureMcpChatConfigurationError:
        raise
    except AzureMcpChatRequestError:
        raise
    except Exception as exc:
        raise AzureMcpChatRequestError(f"Azure MCP Cloud Architect call failed: {exc}") from exc

    response_object = _extract_response_object(tool_result.get("payload"))

    cloud_state = _coerce_state(response_object.get("state"))
    if not cloud_state:
        cloud_state = normalized_state["cloudArchitectState"]

    recent_user_messages = list(normalized_state.get("recentUserMessages") or [])
    recent_user_messages.append(text)
    recent_user_messages = recent_user_messages[-6:]

    scenario = _classify_scenario("\n".join(recent_user_messages))
    if next_question_needed:
        assistant_message = follow_up_question
    else:
        assistant_message = _render_architecture_response(
            scenario=scenario,
            user_message=text,
            system_prompt=_load_agent_definition(),
            mcp_hint=str(response_object.get("displayHint") or "").strip(),
            project_context=project_context,
            follow_up_question=follow_up_question,
        )

    updated_state = {
        "turnCount": turn_count,
        "questionNumber": int(response_object.get("questionNumber") or turn_count),
        "totalQuestions": int(response_object.get("totalQuestions") or total_questions),
        "nextQuestionNeeded": bool(response_object.get("nextQuestionNeeded", next_question_needed)),
        "cloudArchitectState": cloud_state,
        "recentUserMessages": recent_user_messages,
        "lastToolQuestion": question_for_tool,
    }

    return {
        "ok": True,
        "message": assistant_message,
        "agentState": updated_state,
        "meta": {
            "tool": "cloudarchitect_design",
            "questionUsed": question_for_tool,
            "nextQuestionNeeded": updated_state["nextQuestionNeeded"],
        },
    }


def _run_async(coro):
    return asyncio.run(coro)


async def _invoke_cloudarchitect(credentials: AzureMcpCredentials, args: dict[str, Any]) -> dict[str, Any]:
    try:
        mcp_module = importlib.import_module("mcp")
        mcp_stdio_module = importlib.import_module("mcp.client.stdio")
    except ModuleNotFoundError as exc:
        raise AzureMcpChatConfigurationError(
            "Python package 'mcp' is required for architecture chat. Install backend dependencies and rebuild the app container."
        ) from exc

    ClientSession = getattr(mcp_module, "ClientSession")
    StdioServerParameters = getattr(mcp_module, "StdioServerParameters")
    stdio_client = getattr(mcp_stdio_module, "stdio_client")

    mcp_env = {
        **os.environ,
        "AZURE_CLIENT_ID": credentials.client_id,
        "AZURE_CLIENT_SECRET": credentials.client_secret,
        "AZURE_TENANT_ID": credentials.tenant_id,
    }
    if credentials.subscription_id:
        mcp_env["AZURE_SUBSCRIPTION_ID"] = credentials.subscription_id

    server_params = StdioServerParameters(
        command="npx",
        args=["-y", "@azure/mcp@latest", "server", "start"],
        env=mcp_env,
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tool_name, raw_response = await _call_cloudarchitect_tool(session, args)

    payload = _try_json(raw_response)
    if not isinstance(payload, dict):
        payload = {"raw": raw_response}

    return {
        "toolName": tool_name,
        "raw": raw_response,
        "payload": payload,
    }


async def _call_cloudarchitect_tool(session: Any, args: dict[str, Any]) -> tuple[str, str]:
    candidate_tools = ["cloudarchitect", "cloudarchitect_design"]
    failures: list[str] = []

    for tool_name in candidate_tools:
        try:
            result = await session.call_tool(tool_name, args)
            return tool_name, _extract_text(result.content)
        except Exception as exc:
            failures.append(f"{tool_name}: {exc}")

    raise AzureMcpChatRequestError(
        "Unable to call Azure MCP Cloud Architect tool. " + " | ".join(failures)
    )


def _extract_text(content: Any) -> str:
    if isinstance(content, list):
        return "\n".join(getattr(item, "text", str(item)) for item in content)
    return str(content)


def _try_json(raw: str) -> Any:
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None


def _extract_response_object(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        return {}

    results = payload.get("results")
    if isinstance(results, Mapping):
        response_object = results.get("responseObject")
        if isinstance(response_object, Mapping):
            return dict(response_object)

    response_object = payload.get("responseObject")
    if isinstance(response_object, Mapping):
        return dict(response_object)

    return {}


def _coerce_state(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str):
        parsed = _try_json(value)
        if isinstance(parsed, Mapping):
            return dict(parsed)
    return {}


def _normalize_agent_state(agent_state: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(agent_state, Mapping):
        return {
            "turnCount": 0,
            "questionNumber": 0,
            "totalQuestions": DEFAULT_TOTAL_QUESTIONS,
            "nextQuestionNeeded": False,
            "cloudArchitectState": _default_cloudarchitect_state(),
            "recentUserMessages": [],
            "lastToolQuestion": "",
        }

    cloud_state = _coerce_state(agent_state.get("cloudArchitectState"))
    if not cloud_state:
        cloud_state = _default_cloudarchitect_state()

    return {
        "turnCount": _coerce_int(agent_state.get("turnCount"), 0),
        "questionNumber": _coerce_int(agent_state.get("questionNumber"), 0),
        "totalQuestions": _coerce_int(agent_state.get("totalQuestions"), DEFAULT_TOTAL_QUESTIONS),
        "nextQuestionNeeded": bool(agent_state.get("nextQuestionNeeded", False)),
        "cloudArchitectState": cloud_state,
        "recentUserMessages": _coerce_string_list(agent_state.get("recentUserMessages")),
        "lastToolQuestion": str(agent_state.get("lastToolQuestion") or "").strip(),
    }


def _coerce_int(value: Any, fallback: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _coerce_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        text = str(item or "").strip()
        if text:
            result.append(text)
    return result


def _default_cloudarchitect_state() -> dict[str, Any]:
    return {
        "architectureComponents": [],
        "architectureTiers": {
            "infrastructure": [],
            "platform": [],
            "application": [],
            "data": [],
            "security": [],
            "operations": [],
        },
        "thought": "",
        "suggestedHint": "",
        "requirements": {
            "explicit": [],
            "implicit": [],
            "assumed": [],
        },
        "confidenceFactors": {
            "explicitRequirementsCoverage": 0,
            "implicitRequirementsCertainty": 0,
            "assumptionRisk": 0,
        },
    }


def _needs_clarification(user_message: str, turn_count: int) -> bool:
    words = re.findall(r"\w+", user_message)
    return turn_count == 1 and len(words) < 7


def _next_clarifying_question(turn_count: int) -> str:
    questions = [
        "What are your scale targets (users/requests per second) and expected regions?",
        "What are your security and compliance requirements (for example, PII, PCI, HIPAA)?",
        "Do you prefer managed PaaS services or a container-first approach (AKS/Container Apps)?",
        "What are your availability and recovery targets (SLA, RTO, RPO)?",
    ]

    if turn_count <= 0:
        return questions[0]

    index = min(turn_count - 1, len(questions) - 1)
    return questions[index]


def _classify_scenario(text: str) -> str:
    value = str(text or "").lower()
    if any(token in value for token in ["secure api", "secure an api", "api security", "oauth", "jwt", "api gateway"]):
        return "secure_api"
    if any(token in value for token in ["ai app", "ai application", "llm", "rag", "vector", "copilot", "ai"]):
        return "ai_application"
    return "scalable_web"


def _render_architecture_response(
    scenario: str,
    user_message: str,
    system_prompt: str,
    mcp_hint: str,
    project_context: Mapping[str, Any] | None,
    follow_up_question: str,
) -> str:
    rows = _architecture_rows(scenario)
    diagram = _architecture_diagram(scenario)

    context_line = ""
    if isinstance(project_context, Mapping):
        project_name = str(project_context.get("name") or "").strip()
        app_type = str(project_context.get("applicationType") or "").strip()
        if project_name or app_type:
            context_bits = [item for item in [project_name, app_type] if item]
            context_line = f"Project context: {' · '.join(context_bits)}"

    lines: list[str] = [
        "Here is a practical Azure architecture for your question.",
        "",
    ]

    if context_line:
        lines.extend([context_line, ""])

    lines.extend([
        "| Component | Purpose | Tier/SKU |",
        "|---|---|---|",
    ])
    for component, purpose, sku in rows:
        lines.append(f"| {component} | {purpose} | {sku} |")

    lines.extend([
        "",
        "Architecture flow:",
        diagram,
        "",
        "Implementation notes:",
        "- Prefer private networking between API/data services (VNet integration + private endpoints).",
        "- Use managed identity and Key Vault for secrets; avoid app secrets in code or config files.",
        "- Add autoscale rules and SLO-driven monitoring (latency, error rate, saturation).",
        "",
        f"Next refinement question: {follow_up_question}",
    ])

    if mcp_hint:
        lines.extend(["", f"MCP hint: {mcp_hint}"])

    if system_prompt:
        lines.extend(["", "(Advisor mode: senior cloud architect)"])

    return "\n".join(lines)


def _architecture_rows(scenario: str) -> list[tuple[str, str, str]]:
    if scenario == "secure_api":
        return [
            ("Azure API Management", "Policy enforcement, auth, throttling", "Standard/Premium"),
            ("Azure Application Gateway + WAF", "Layer-7 protection and routing", "WAF_v2"),
            ("Azure App Service or Container Apps", "Run API workloads", "P1v3 / Consumption"),
            ("Microsoft Entra ID", "OAuth2/OIDC identity provider", "Managed"),
            ("Azure Key Vault", "Secret and certificate management", "Standard/Premium"),
            ("Azure SQL Database or Cosmos DB", "Transactional/persistent data", "Serverless/Autoscale"),
            ("Azure Monitor + Application Insights", "Observability and alerting", "Managed"),
        ]

    if scenario == "ai_application":
        return [
            ("Azure Front Door + WAF", "Global ingress and edge protection", "Standard/Premium"),
            ("Azure App Service or Container Apps", "Host chat UI and backend API", "P1v3 / Consumption"),
            ("Azure AI Foundry / Azure OpenAI", "LLM inference", "Model deployment"),
            ("Azure AI Search", "Vector + hybrid retrieval", "Standard/S1+"),
            ("Azure Blob Storage", "Document and artifact storage", "Hot/Cool"),
            ("Azure Cosmos DB", "Session, user profile, app state", "Serverless/Autoscale"),
            ("Azure Key Vault", "Secrets and keys", "Standard/Premium"),
            ("Azure Monitor + Application Insights", "Tracing, metrics, and diagnostics", "Managed"),
        ]

    return [
        ("Azure Front Door + WAF", "Global entry point, TLS termination, and protection", "Standard/Premium"),
        ("Azure App Service", "Host web frontend/backend", "P1v3+ (autoscale)"),
        ("Azure API Management", "API governance and rate limits", "Standard"),
        ("Azure Cache for Redis", "Low-latency caching", "Standard/Premium"),
        ("Azure SQL Database", "Relational app data", "General Purpose / Hyperscale"),
        ("Azure Storage", "Static assets and backups", "GPv2"),
        ("Azure Key Vault", "Secrets/certs management", "Standard/Premium"),
        ("Azure Monitor + Application Insights", "Monitoring and alerting", "Managed"),
    ]


def _architecture_diagram(scenario: str) -> str:
    if scenario == "secure_api":
        return (
            "Client -> Front Door/App Gateway(WAF) -> API Management -> API Service -> Data Store\n"
            "                                     -> Entra ID (Auth)\n"
            "                                     -> Key Vault\n"
            "                                     -> Monitor/App Insights"
        )

    if scenario == "ai_application":
        return (
            "Client -> Front Door(WAF) -> App/API -> AI Search -> Blob Storage\n"
            "                           -> AI Foundry/OpenAI\n"
            "                           -> Cosmos DB\n"
            "                           -> Key Vault + Monitor"
        )

    return (
        "Client -> Front Door(WAF) -> App Service/API -> Redis + SQL\n"
        "                              -> Storage\n"
        "                              -> Key Vault + Monitor"
    )


def _load_agent_definition() -> str:
    path = Path(__file__).resolve().parents[1] / DEFAULT_AGENT_DEFINITION_FILE
    try:
        text = path.read_text(encoding="utf-8").strip()
        return text or DEFAULT_SYSTEM_PROMPT
    except Exception:
        return DEFAULT_SYSTEM_PROMPT


def _first_non_empty(settings: Mapping[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = settings.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None
