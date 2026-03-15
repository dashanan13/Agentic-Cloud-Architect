# Architecture Validation - Structured Logging Implementation

## Overview

The validation agent now implements comprehensive structured logging across all 14 validation steps, providing complete observability of the architecture validation pipeline while maintaining security through automatic redaction of sensitive data.

---

## Logging Architecture

### Core Components

#### 1. **ValidationStep** Dataclass
Represents a single step in the validation pipeline with:
- `step_number`: Sequential step identifier
- `step_name`: Human-readable step name
- `timestamp`: ISO 8601 UTC timestamp
- `status`: `started` | `completed` | `failed`
- `duration_ms`: Execution time in milliseconds
- `details`: Structured metadata (auto-sanitized)
- `error`: Error message if failed

#### 2. **ValidationLog** Dataclass
Complete structured validation log containing:
- `run_id`: Unique validation run identifier
- `start_time`, `end_time`: ISO timestamps
- `validator_version`: Agent version
- `agent_name`: "architecture-validation-agent"
- `steps`: List of ValidationStep objects
- `tool_discovery`: Available MCP tools discovered
- `tool_selection`: MCP tools selected for this run
- `tool_telemetry`: Per-tool execution metrics
- `recommendations_by_pillar`: Findings grouped by Well-Architected pillar
- `foundry_thread_id`: Azure AI Foundry thread reference
- `foundry_messages`: Count of messages written to thread
- `final_status`: `SUCCESS` | `FAILURE`
- `error_message`: Overall validation error (if any)

#### 3. **Security & Redaction**

Two helper functions handle secure logging:

**`_sanitize_dict_for_logging(obj, depth=0)`**
- Recursively redacts sensitive fields
- Redacts keys containing: `secret`, `key`, `token`, `password`, `credential`, `auth`, `apikey`
- Limits object depth to 3 levels
- Limits arrays to first 5 items
- Truncates strings > 500 characters

**`_redact_sensitive_value(value)`**
- Redacts values with sensitive context
- Returns `***REDACTED***` for keys matching patterns
- Preserves overall structure

---

## 14-Step Validation Pipeline

### Step 1: Validation Initialization
**Captures:**
- run_id (unique for each validation)
- timestamp
- validator version
- agent identifier
- validation trigger source
- foundry thread id (if available)

### Step 2: Canvas and Project Context Collection
**Captures:**
- project name
- project description
- architecture canvas content
- detected Azure services count
- environment type

**Does NOT log:**
- credentials, connection strings, secrets

### Step 3: Metadata Enrichment
**Records:**
- project metadata
- application settings (sanitized)
- environment configuration
- deployment region(s)
- architecture tags
- detected service inventory
- computed state hash
- generated run id

### Step 4: Validation Context Assembly
**Constructs validation context containing:**
- sanitized canvas data
- enriched metadata
- run id
- foundry thread id
- Azure service inventory
- validation scope

### Step 5: MCP Tool Discovery
**Queries MCP server and records:**
- MCP server identifier
- number of tools returned
- list of available tool identifiers and descriptions

Example output fields in `tool_discovery`:
```json
{
  "tool_id": "advisor",
  "description": "Azure Advisor recommendations"
}
```

### Step 6: MCP Tool Selection
**Records:**
- selection criteria
- list of selected tools:
  - `advisor`
  - `cloudarchitect`
  - `wellarchitectedframework`
- excluded tools and reasons (if any)

### Step 7: AI Agent Initialization
**Records:**
- agent name: "architecture-validation-agent"
- framework: "Microsoft Azure Well-Architected Framework"
- validation pillars enabled:
  - Reliability
  - Security
  - Cost Optimization
  - Operational Excellence
  - Performance Efficiency

### Step 8: MCP Tool Execution
**For each tool, logs telemetry:**
- `tool_name`: Name of MCP tool
- `tool_identifier`: Tool ID
- `status`: `success` | `failure` | `timeout` | `guidance`
- `attempt_count`: Number of attempts made
- `start_timestamp`: When tool call started
- `end_timestamp`: When tool call ended
- `duration_ms`: Total execution time
- `error_message`: Error (if failed)
- `input_summary`: Sanitized input arguments
- `output_summary`: Condensed response

Each tool invocation sends:
```json
{
  "tool_name": "cloudarchitect",
  "status": "success",
  "attemptCount": 1,
  "durationMs": 2450,
  "inputSummary": {
    "argKeys": ["question", "answer", "state"]
  },
  "outputSummary": {
    "findings": 8,
    "payloadType": "json"
  }
}
```

### Step 9: Context Consolidation
**Records:**
- number of tool responses merged
- merged context summary
- data quality indicators

### Step 10: LLM Architectural Reasoning Pass
**Records:**
- model identifier
- reasoning start timestamp
- reasoning duration_ms
- reasoning engine: "Azure Foundry"

**Logs only summarized reasoning statement:**
- Example: "Architecture evaluated against reliability, security, and cost optimization guidance using Azure Advisor recommendations and Well-Architected patterns."
- Does NOT expose chain-of-thought internal reasoning

### Step 11: Recommendation Aggregation
**Groups recommendations by pillar:**
- `reliability`: List of findings
- `security`: List of findings
- `cost_optimization`: List of findings
- `operational_excellence`: List of findings
- `performance_efficiency`: List of findings

**For each pillar records:**
- `number_of_findings`
- `severity_distribution` (failure/warning/info counts)
- `impacted_services` (list of resource types affected)

### Step 12: Azure AI Foundry Thread Logging
**Records:**
- `thread_id`: Foundry thread identifier
- `number_of_messages_written`: Count of messages
- `message_types`:
  - `context_update`
  - `tool_execution`
  - `validation_result`

### Step 13: Final Output Generation
**Records:**
- validation tips generated (count)
- impacted components (list)
- architecture improvement suggestions (count)
- delivery confirmation to Tips tab

### Step 14: Validation Completion
**Records:**
- `final_status`: `SUCCESS` | `FAILURE`
- `total_duration_ms`: Total pipeline execution time
- `number_of_tools_discovered`: Count during discovery
- `number_of_tools_selected`: Count for execution
- `number_of_mcp_tools_called`: Count actually invoked
- `number_of_recommendations_generated`: Total findings

---

## Log Output Format

Each validation run produces a structured JSON object:

```json
{
  "run_id": "val-1710426102000-a1b2c3",
  "start_time": "2026-03-15T14:01:42Z",
  "end_time": "2026-03-15T14:05:18Z",
  "validator_version": "1.0.0",
  "agent_name": "architecture-validation-agent",
  "total_duration_ms": 216000,
  "final_status": "SUCCESS",
  "error_message": null,
  "foundry_thread_id": "thread_xicHyY4ZDvZREmpnHuSR6DYX",
  "foundry_messages": 5,
  "steps": [
    {
      "step_number": 1,
      "step_name": "validation_initialization",
      "timestamp": "2026-03-15T14:01:42Z",
      "status": "completed",
      "duration_ms": 145,
      "details": {
        "run_id": "val-1710426102000-a1b2c3",
        "validator_version": "1.0.0",
        "agent_identifier": "architecture-validation-agent",
        "trigger_source": "api_request"
      }
    },
    {
      "step_number": 2,
      "step_name": "canvas_context_collection",
      "timestamp": "2026-03-15T14:01:43Z",
      "status": "completed",
      "duration_ms": 234,
      "details": {
        "project_name": "Azure-Project1",
        "detected_resources_count": 6,
        "connections_count": 3
      }
    }
  ],
  "tool_discovery": [
    {
      "tool_id": "advisor",
      "tool_name": "Azure Advisor",
      "description": "Azure Advisor recommendations"
    },
    {
      "tool_id": "cloudarchitect",
      "tool_name": "Azure Cloud Architect",
      "description": "Architecture design patterns"
    },
    {
      "tool_id": "wellarchitectedframework",
      "tool_name": "Azure Well-Architected Framework",
      "description": "Architecture best practices"
    }
  ],
  "tool_selection": [
    {
      "tool_id": "advisor",
      "selected": true,
      "reason": "Core recommendation engine"
    },
    {
      "tool_id": "cloudarchitect",
      "selected": true,
      "reason": "Architecture validation"
    },
    {
      "tool_id": "wellarchitectedframework",
      "selected": true,
      "reason": "Pillar-based guidance"
    }
  ],
  "tool_telemetry": [
    {
      "label": "wellarchitectedframework",
      "selectedTool": "get_azure_bestpractices_get",
      "status": "success",
      "findingCount": 4,
      "attemptCount": 1,
      "durationMs": 2850,
      "error": "",
      "attempts": [
        {
          "tool": "get_azure_bestpractices_get",
          "variantIndex": 0,
          "status": "success",
          "durationMs": 2850,
          "argKeys": ["action", "context", "resource"],
          "payloadType": "json",
          "error": ""
        }
      ]
    },
    {
      "label": "advisor",
      "selectedTool": "advisor_recommendation_list",
      "status": "success",
      "findingCount": 3,
      "attemptCount": 1,
      "durationMs": 1920,
      "error": "",
      "attempts": [...]
    },
    {
      "label": "cloudarchitect",
      "selectedTool": "cloudarchitect_design",
      "status": "success",
      "findingCount": 5,
      "attemptCount": 2,
      "durationMs": 4200,
      "error": "",
      "attempts": [...]
    }
  ],
  "recommendations_by_pillar": {
    "reliability": [
      {
        "id": "finding-1",
        "severity": "warning",
        "title": "Missing multi-region failover",
        "message": "Architecture lacks multi-region redundancy for disaster recovery."
      }
    ],
    "security": [
      {
        "id": "finding-2",
        "severity": "failure",
        "title": "Public endpoint exposed",
        "message": "API endpoint is publicly accessible without authentication."
      }
    ],
    "cost_optimization": [
      {
        "id": "finding-3",
        "severity": "info",
        "title": "Consider reserved instances",
        "message": "Sustained usage shows 60% predictable compute load."
      }
    ],
    "operational_excellence": [],
    "performance_efficiency": []
  }
}
```

---

## Security & Redaction Guarantees

**Never logged:**
- API keys
- Tokens
- Passwords
- Secrets
- Connection strings
- Credentials

**Replaced with:** `***REDACTED***`

**Truncation limits:**
- String values: 500 characters max
- Object depth: 3 levels max
- Array items: First 5 items only

---

## Integration Points

### With Existing Activity Log
Structured validation logs are **persisted** via existing `_log_validation_event()` calls, maintaining backward compatibility with:
- Activity log service
- Historical audit trails
- Dashboard metrics

### With Validation Endpoints
The validation response includes:
- `validationLogPath`: File path to text log
- Validation results (findings, groups, summary)
- Diagnostic info from each channel (MCP, reasoning model)

### With Foundry Thread
Messages written to Azure AI Foundry thread include:
- `validation.input`: Canvas + metadata sent to agent
- `validation.output`: Results returned from agent
- `validation.context`: Intermediate context updates

---

## Usage Example

```python
# Validation runs automatically capture structured logs
validation_result = run_architecture_validation_agent(
    app_settings=settings,
    canvas_state=canvas,
    project_name="My-Project",
    project_id="proj-123",
    project_description="Web API with event streaming",
)

# Access logs from validation response
log_path = validation_result.get("validationLogPath")
# File contains structured JSON log entries

# JSON structured logs available via API
# GET /api/project/{id}/architecture/validation/log/{run_id}
```

---

## Debugging & Observability

Use the structured logs to troubleshoot:

1. **Tool failures**: Check `tool_telemetry[].attempts[]` for attempt details
2. **MCP server issues**: Review `tool_discovery` and `tool_selection`
3. **Reasoning failures**: Check `steps[]` for reasoning model step status
4. **Missing recommendations**: Verify tools were selected and executed
5. **Pillar coverage**: Confirm `recommendations_by_pillar` has findings for all pillars

---

## Example: Complete Validation Log Flow

```
Step 1: Initialize (145ms)
  ↓
Step 2: Collect Canvas (234ms)
  ↓
Step 3: Enrich Metadata (89ms)
  ↓
Step 4: Assemble Context (156ms)
  ↓
Step 5: Discover MCP Tools (823ms)
  - Found: advisor, cloudarchitect, wellarchitectedframework
  ↓
Step 6: Select Tools (45ms)
  - Selected: all 3 tools
  ↓
Step 7: Initialize Agent (67ms)
  - Framework: Well-Architected
  - Pillars: 5 enabled
  ↓
Step 8: Execute Tools (8,970ms)
  - wellarchitectedframework: 2,850ms → 4 findings
  - advisor: 1,920ms → 3 findings
  - cloudarchitect: 4,200ms → 5 findings
  ↓
Step 9: Consolidate Context (234ms)
  - Merged: 12 findings
  ↓
Step 10: Reasoning Pass (45,600ms)
  - Model: Kimi-K2-Thinking
  - Status: Completed
  ↓
Step 11: Aggregate by Pillar (156ms)
  - Reliability: 3
  - Security: 4
  - Cost: 2
  - Operations: 2
  - Performance: 1
  ↓
Step 12: Foundry Thread (289ms)
  - Messages: 5
  ↓
Step 13: Generate Output (178ms)
  - Tips: 12
  ↓
Step 14: Completion (0ms)
  - Status: SUCCESS
  - Total: ~216 seconds
```

