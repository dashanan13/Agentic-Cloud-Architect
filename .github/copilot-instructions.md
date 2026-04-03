# Copilot Instructions — Agentic Cloud Architect

## Project Overview

A browser-based, AI-assisted Azure cloud architecture diagramming tool that validates
and generates infrastructure-as-code for Azure designs. Users drag Azure resources onto
a canvas, draw connections, then run an AI-powered validation pipeline that scores the
design against the Azure Well-Architected Framework (WAF) and generates Bicep/Terraform.

**Tech Stack**

| Layer | Technology |
|---|---|
| Frontend | Vanilla JS/HTML/CSS, single-page app |
| Backend | FastAPI (Python), single-file monolith |
| AI / Agents | Azure AI Foundry, Azure MCP (`@azure/mcp@latest`), Python `mcp` SDK |
| IaC templates | Bicep (primary), Terraform |
| Runtime | Docker (single container), port 3000 |

---

## Repository Layout

```
App_Backend/settings_server.py       ← ALL API routes + pipeline logic (8 600+ lines)
App_Frontend/CanvasScreen/
  canvas.js                          ← Main UI controller (10 000+ lines)
  canvas.html
  styles.css
App_Frontend/                        ← Screen-per-folder SPA (LandingScreen, IaCScreen, …)
Agents/
  AzureMCP/
    architecture_validation_agent.py ← Core validation engine (3 700+ lines)
    cloudarchitect_chat_agent.py     ← Chat / design-assist agent
    iac_generation_agent.py          ← Bicep/Terraform generator
  AzureAIFoundry/
    foundry_bootstrap.py             ← Foundry project/agent/thread initialisation
    foundry_description.py           ← Agent description loader + FoundryAssistantRunner
    foundry_messages.py              ← Thread messaging helpers
  common/
    activity_log.py                  ← Structured activity logger
  architecture_validation_agent.md  ← System prompt loaded at runtime
  cloudarchitect_chat_agent.md      ← System prompt loaded at runtime
  iac_generation_agent.md           ← System prompt loaded at runtime
App_State/
  app.settings.env                   ← Global settings (MODEL_PROVIDER, Foundry creds, etc.)
  logs/
Projects/<name>/
  project.settings.env               ← Per-project overrides
  Architecture/canvas.state.json     ← Source of truth: canvas items + connections
  Documentation/                     ← Intermediate pipeline artifacts (JSON per stage)
  IaC/                               ← Generated Bicep/Terraform output
  Diagram/
Clouds/Azure/
  resource_catalog.json              ← Azure resource type catalogue for the UI
  ResourceSchema/Bicep|Terraform/    ← Per-resource schema templates
docker-compose.yml                   ← Single service `a3-app`, port 3000
```

---

## Architecture Flow

```
Browser (canvas.js)
  └─ POST /api/project/{id}/validation/* (8-stage pipeline)
        └─ App_Backend/settings_server.py
              ├─ Agents/AzureMCP/architecture_validation_agent.py
              │     ├─ deterministic checks (_deterministic_findings)
              │     ├─ Azure MCP WAF tool calls (_collect_findings_from_mcp)
              │     ├─ WAF GitHub markdown fetch (_fetch_waf_service_guide)
              │     ├─ Azure Learn MCP search (_learn_mcp_search_async)
              │     └─ Foundry reasoning model (_collect_findings_from_reasoning_model)
              ├─ Agents/AzureMCP/cloudarchitect_chat_agent.py
              └─ Agents/AzureMCP/iac_generation_agent.py
```

---

## Validation Pipeline — Stage Sequence

All stages are invoked via `POST /api/project/{project_id}/validation/<stage>` in order:

| # | Endpoint | Artifact written |
|---|---|---|
| 1 | `input-verification` | — |
| 2 | `graph-builder` | `architecture-graph.json` |
| 3 | `enricher` | `enricher_findings.json` |
| 4 | `rule-engine` | `rule_engine_findings.json` |
| 5 | `structured-findings` | `structured_findings.json` |
| 6 | `knowledge-retrieval` | `azure_learn_docs.json` |
| 7 | `ai-validation-agent` | `ai_validation_findings.json` |
| 8 | `final-report` | `final-report.md` + `final-report-full.md` |

The final-report stage writes **two files**:
- `final-report.md` — capped to 12 issue groups for Tips tab display
- `final-report-full.md` — uncapped for the download endpoint

Download endpoint (`GET .../final-report/download`) serves `final-report-full.md`;
content endpoint (`GET .../final-report/content`) serves the capped `final-report.md`.

---

## Key Source Files & Responsibilities

### `App_Backend/settings_server.py`

- Sole FastAPI application (~8 600 lines). All routes live here.
- `_format_final_report_markdown(payload, *, full_report: bool = False)`:
  - `full_report=True` → no section caps (download build).
  - `full_report=False` → `max_issue_items = 12` (Tips display).
- `_run_final_report_stage()` writes both report variants and returns `fullArtifactPath`.
- `_normalize_report_text()` replaces `"..."` with `"…"` (Unicode ellipsis U+2026).
- Settings loaded from `App_State/app.settings.env` (global) and
  `Projects/<name>/project.settings.env` (per-project); accessed via `load_app_settings()`
  and `load_project_settings()`.

### `Agents/AzureMCP/architecture_validation_agent.py`

- Entry point: `run_architecture_validation_agent()` — called from settings_server
  for stage 7 (`ai-validation-agent`).
- **Reasoning loop** capped at `AI_VALIDATION_MAX_ITERATIONS = 5`.
- **WAF pillars**: `WELL_ARCHITECTED_PILLARS = ["reliability", "security",
  "cost_optimization", "operational_excellence", "performance_efficiency"]`.
- `_deterministic_findings()` — rule-based canvas checks (missing names, isolated
  resources, invalid connections).
- `_fetch_waf_service_guide(url, service_slug)` — fetches GitHub raw WAF markdown
  and parses it with `_parse_waf_markdown_to_findings()`; results cached in
  `_WAF_GUIDE_CACHE`.
- `_parse_waf_markdown_to_findings()` extracts the **first sentence** of each
  checklist bullet as `title`; full text stored in `message`.
- `_build_final_intelligent_report()` assembles the complete JSON payload with
  **no count caps** — all findings are stored.
- `_is_mcp_guidance_text()` — filters out MCP tool meta-responses before they
  become findings.
- `_extract_json_from_text()` — best-effort JSON extraction from LLM responses.

### `Agents/AzureAIFoundry/foundry_description.py`

- `_load_agent_description("name")` reads `Agents/<name>.md` as the LLM system
  prompt (e.g. `Agents/architecture_validation_agent.md`).
- `FoundryAssistantRunner` wraps Azure AI Foundry assistant calls and handles
  stateless retry.
- All agent invocations use `runner.run_assistant(assistant_id, thread_id, content)`.
- The runner uses **Microsoft AI Agent Framework** (`agent_framework` core +
  `agent_framework_azure_ai`). Import path is:
  ```python
  from agent_framework import Agent, AgentSession
  from agent_framework_azure_ai import AzureAIAgentClient   # NOT agent_framework.azure
  ```
  `agent_framework.azure` only re-exports Azure Durable/Functions connectors —
  `AzureAIAgentClient` lives in the direct `agent_framework_azure_ai` package.

### `patch_agent_framework.py` (repo root)

Fixes three API mismatches in `agent-framework-azure-ai 1.0.0rc6` vs
`agent-framework-core 1.0.0` (stable):

| Symbol | Before patch | After patch |
|---|---|---|
| `BaseContextProvider` | renamed to `ContextProvider` in core | patched in all `.py` files |
| `OpenAIResponsesOptions` | removed (was alias for `OpenAIChatOptions`) | patched in all `.py` files |
| `agent_framework_openai._assistants_client` | module removed | stub created |

The Dockerfile runs `python3 /app/patch_agent_framework.py` immediately after
`pip install`. **Do not remove this step or the Foundry agent calls will fail
with `ImportError` at runtime.**

### `App_Frontend/CanvasScreen/canvas.js`

- `parseFinalReportMarkdown()` — `## ` = top-level section, `### N.` =
  collapsible subsection, `- ` bullet lines = body content.
- `renderFinalReportPanel()` — renders the Tips tab; shows a blue banner when a
  download link is present, indicating the full report has more content.
- Never use `### ` headings for list content; they render as collapsible UI panels.

---

## Settings Reference

`App_State/app.settings.env` key fields:

```
MODEL_PROVIDER=azure-foundry            # or: none
AI_FOUNDRY_ENDPOINT=https://...
AI_FOUNDRY_TENANT_ID=...
AI_FOUNDRY_CLIENT_ID=...
AI_FOUNDRY_CLIENT_SECRET=...
FOUNDRY_CHAT_AGENT_ID=...
FOUNDRY_VALIDATION_AGENT_ID=...
FOUNDRY_IAC_AGENT_ID=...
FOUNDRY_MODEL_REASONING=...             # deployment name for reasoning calls
FOUNDRY_MODEL_FAST=...                  # deployment name for fast calls
AZURE_SUBSCRIPTION_ID=...               # used by Azure MCP
AZURE_TENANT_ID=...
AZURE_CLIENT_ID=...
AZURE_CLIENT_SECRET=...
```

When `MODEL_PROVIDER` is not `azure-foundry`, all Foundry and reasoning-model
stages are skipped silently (deterministic + WAF-fetched findings still run).

---

## Common Coding Patterns

### JSON extraction from LLM responses

```python
parsed = _extract_json_from_text(str(result.response_text or ""))
if isinstance(parsed, Mapping):
    ...
```

Always use `_extract_json_from_text()` — never `json.loads()` directly on model
output. LLM responses may include fenced code blocks or surrounding prose.

### Normalisation helpers

| Helper | Purpose |
|---|---|
| `_normalize_string(value, fallback)` | `str(value).strip()` with fallback |
| `_normalize_severity(value)` | maps aliases → `"failure"/"warning"/"info"` |
| `_normalize_pillar_name(value)` | maps aliases → canonical pillar key |
| `_normalize_operation_name(value)` | maps op name aliases →canonical op |
| `_truncate_text(value, max_chars)` | truncates with `…` (U+2026), not `...` |
| `_is_mcp_guidance_text(value)` | returns True for MCP router meta-responses |

**Never** append `"..."` (three dots) when truncating. Use `"…"` or the
`_truncate_text()` helper which appends `"…"` automatically.

### Foundry agent calls

```python
connection = FoundryConnectionSettings(
    endpoint=..., tenant_id=..., client_id=..., client_secret=...,
    model_deployment=model_name, api_version=...,
)
runner = FoundryAssistantRunner(connection, timeout_seconds=120,
                                agent_name="architecture-validation-agent")
result = runner.run_assistant(
    assistant_id=agent_id, thread_id=thread_id,
    content=prompt, allow_stateless_retry=True,
)
```

Always pass `allow_stateless_retry=True` for idempotent inference calls.

### Adding a new validation rule

1. Add the finding dict to `_deterministic_findings()`. Severity values:
   `"failure"` (blocks deployment), `"warning"` (WAF risk), `"info"` (advisory).
2. Finding `fix.operations` items must use ops from:
   `set_resource_property`, `set_resource_name`, `remove_connection`,
   `set_connection_direction`, `add_connection`, `remove_resource`.
3. All `resourceId` values **must** exist in `valid_resource_ids`; same for
   `connectionId` in `valid_connection_ids`.

### Adding a new API route

All routes live in `settings_server.py`. Use the existing project-context pattern:

```python
@app.post("/api/project/{project_id}/my_stage")
async def my_stage(project_id: str, request: Request):
    app_settings = load_app_settings()
    project_dir = get_project_dir(project_id)
    ...
```

Write artifacts to `project_dir / "Documentation" / "my-stage-output.json"`.

---

## Docker Workflow

```bash
# Rebuild and start
docker-compose up --build -d

# Exec into running container
docker exec -it a3-app bash

# View backend logs
docker-compose logs -f app

# App available at
open http://localhost:3000
```

The container mounts `App_State/` and `Projects/` as volumes so settings and
project data persist across rebuilds.

---

## WAF Data Sources

1. **GitHub raw markdown** — fetched by `_fetch_waf_service_guide()` from
   `https://raw.githubusercontent.com/MicrosoftDocs/well-architected/main/well-architected/service-guides/<slug>.md`.
   Parsed per-pillar by `_parse_waf_markdown_to_findings()`. Cached in
   `_WAF_GUIDE_CACHE` for the process lifetime.

2. **Azure Learn MCP** — `_learn_mcp_search_async()` calls
   `microsoft_docs_search` tool at `https://learn.microsoft.com/api/mcp`.

3. **Azure MCP WAF tool** — `_collect_findings_from_mcp()` connects to
   `npx @azure/mcp@latest server start` and calls `wellarchitectedframework`
   tool with detected service slugs.

Service slugs are mapped from canvas resource types via `WAF_SERVICE_PATTERN_MAP`
(list of `(slug, patterns)` tuples) and `WAF_SUPPORTED_SERVICE_SLUGS` (set of
valid slugs).

---

## Project State Model

`Projects/<name>/Architecture/canvas.state.json` is the canonical source:

```jsonc
{
  "canvasItems": [
    {
      "id": "...",           // stable unique ID – used in findings target.resourceId
      "name": "...",
      "resourceType": "...",
      "category": "...",
      "properties": { ... },
      "parentId": "..."      // optional – containment (VNet → Subnet, etc.)
    }
  ],
  "canvasConnections": [
    {
      "id": "...",
      "fromId": "...",
      "toId": "...",
      "direction": "one-way" // or "bi"
    }
  ]
}
```

---

## Testing & Validation

There is no automated test suite. Manually validate changes by:

1. Starting the container: `docker-compose up --build -d`
2. Opening `http://localhost:3000`
3. Loading or creating a project with Azure resources
4. Running the full validation pipeline via the UI Tips tab
5. Verifying `Projects/<name>/Documentation/final-report.md` and
   `final-report-full.md` contain expected content

For backend-only iteration, the FastAPI server can be run directly (outside
Docker) if a Python venv with `requirements.txt` packages is active:

```bash
cd App_Backend
uvicorn settings_server:app --port 3000 --reload
```

---

## Style & Conventions

- **Python**: no type-ignore needed; use `Any` and `Mapping` from `typing` for
  dict-typed inputs (already imported in all agent files). Use `|` union syntax
  for Python 3.10+.
- **No bare `...` in user-facing strings**: use `"…"` (U+2026) or
  `_truncate_text()`.
- **No hard-coded count caps in JSON assembly**: display caps belong only in
  `_format_final_report_markdown()` via `max_issue_items`.
- **Agent `.md` files are system prompts**: edit them to change agent behaviour
  without touching Python code.
- **Frontend**: no build step, no bundler. Raw ES6 in `canvas.js`. Keep DOM
  manipulation inside the `render*` function family.
- **CSS classes** for the validation report use the `validation-report__*` BEM
  namespace (defined in `App_Frontend/CanvasScreen/styles.css`).
