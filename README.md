# Secure Production Gate

AI-powered pre-deployment risk and readiness enforcement for Azure.

## Quick Verdict

A strong MVP is achievable in **7 days** for:

- One Azure subscription
- Exactly 10 deterministic rules
- One gate score + deployment decision

Multi-subscription expansion, auto-remediation, and deep workflow automation are intentionally out of scope for MVP.

---

## 1) Product Definition (Locked MVP Scope)

Run a one-click **Production Gate scan** before production deployment.

### Inputs

- `subscriptionId`
- Optional allowlist config (for owner exceptions)

### Evaluates

#### A. Production Readiness

- Diagnostics enabled
- Alerts configured
- Backup configured

#### B. Security & Access

- NSG open to `0.0.0.0/0` on management ports
- Public blob access enabled
- Subscription-level Owner outside allowlist
- Service principal credentials stale or non-expiring

#### C. Cost Red Flags

- Idle running VM (>72h low utilization)
- Unattached managed disk
- Oversized VM SKU vs baseline

### Output

- Production Gate Score (0–100)
- Decision (`Deployment Blocked`, `Deploy with Caution`, `Production Ready`)
- Findings with severity + evidence + confidence
- AI explanation and remediation snippet (Bicep or Azure CLI)
- Optional GitHub issue draft payload

> This is a deployment decision engine, not a dashboard replacement.

---

## 2) Required Stack Mapping (Explicit)

| Component | Purpose in this project |
|---|---|
| Microsoft Agent Framework | Multi-agent orchestration (Orchestrator, Collector, Policy, Risk, Explanation, Action) |
| Azure MCP | Secure Azure access and agent-to-tool integration for inventory collection |
| Model Router | Cost-aware model choice (small for short classification, large for explanation/remediation text) |
| Microsoft Foundry | Prompt governance, template registry, model policy controls |
| GitHub | Source control + remediation issue draft generation |

No “checkbox integration”: each tool has one clear responsibility.

---

## 3) Functional Requirements (MVP)

### 3.1 Scan Trigger

- `POST /scan?subscriptionId=<id>`
- Triggerable from minimal web UI
- Auth via Managed Identity or Service Principal

### 3.2 Inventory Collection (Collector Agent)

Must collect:

- VM metadata + utilization metrics
- Managed disks
- Storage accounts
- NSGs + rules
- Role assignments
- Diagnostic settings
- Alert rules
- Backup configuration

Primary sources:

- Azure Resource Graph
- Azure Monitor
- Azure RBAC / Authorization APIs

### 3.3 Deterministic Policy Evaluation (Policy Agent)

Exactly **10 rules** (locked):

1. Idle VM
2. Unattached disk
3. Oversized SKU
4. Missing diagnostics
5. No alert rule
6. No backup
7. NSG `0.0.0.0/0` on `22`/`3389`
8. Public blob access enabled
9. Subscription Owner outside allowlist
10. Stale or non-expiring SP credential

Each finding returns:

```json
{
	"ruleId": "SEC-NSG-OPEN-MGMT",
	"resourceId": "/subscriptions/.../resourceGroups/.../providers/...",
	"category": "security",
	"severity": "Critical",
	"evidence": {
		"port": 22,
		"source": "0.0.0.0/0",
		"ruleName": "AllowSSHFromInternet"
	},
	"confidence": 0.98
}
```

LLMs are **not** used in policy detection logic.

### 3.4 Risk Scoring (Risk Agent)

Computes:

- `readinessScore` (0–100)
- `securityScore` (0–100)
- `costScore` (0–100)
- `overallScore` (0–100)

Gate decision:

- `< 70` → `Deployment Blocked`
- `70–85` → `Deploy with Caution`
- `> 85` → `Production Ready`

### 3.5 Explanation & Remediation (Explanation Agent)

Uses Model Router:

- Small model for short classification refinement
- Large model for explanation + risk rationale + remediation snippet

Prompt templates are stored and governed in Foundry.

Fallback behavior: static templates if model call fails/quota is exceeded.

### 3.6 GitHub Issue Draft (Action Agent)

Generates Markdown issue body with:

- Risk summary
- Affected resources
- Remediation steps

No automatic fixes in MVP.

### 3.7 UI Requirements

Minimal UI includes:

- Prominent Gate Score
- Color-coded severity indicators
- Findings table
- Detail panel (evidence, explanation, remediation, confidence)
- JSON export
- `Create GitHub Issue` action

No chat interface.

---

## 4) Non-Functional Requirements

- End-to-end scan in `< 120s`
- Least-privilege RBAC (`Reader`, `Monitoring Reader`, `Security Reader`)
- Secrets in Key Vault
- Telemetry in Application Insights
- Deployable via Bicep
- Fully Azure-hosted (Container Apps or Functions)

---

## 5) Architecture

### Core Components

- Backend API (containerized)
- Minimal React UI
- Multi-agent orchestration layer

### Agent Roles

- **Orchestrator Agent**: coordinates full scan lifecycle
- **Collector Agent**: gathers Azure inventory through MCP-integrated connectors
- **Policy Agent**: executes deterministic rule checks
- **Risk Agent**: computes category + overall scores and gate decision
- **Explanation Agent**: uses Model Router + Foundry prompts
- **Action Agent**: builds optional GitHub issue draft

### Data Flow

`UI -> Orchestrator -> Collector -> Policy -> Risk -> Explanation -> UI/GitHub`

---

## 6) API Contracts

### Start Scan

`POST /scan?subscriptionId=<id>`

Response:

```json
{
	"scanId": "scan_20260222_001",
	"status": "completed",
	"subscriptionId": "<id>",
	"durationMs": 81342,
	"scores": {
		"readiness": 62,
		"security": 48,
		"cost": 71,
		"overall": 61
	},
	"decision": "Deployment Blocked",
	"findings": [],
	"summary": {
		"critical": 2,
		"high": 3,
		"medium": 2,
		"low": 1
	}
}
```

### Create GitHub Draft

`POST /issues/draft`

Request:

```json
{
	"scanId": "scan_20260222_001",
	"repository": "org/repo",
	"title": "Production Gate: Deployment Blocked (Score 61)",
	"labels": ["security", "production-gate"]
}
```

Response:

```json
{
	"draft": "## Risk Summary\n...",
	"previewUrl": "https://github.com/org/repo/issues/new?..."
}
```

---

## 7) Scoring Model (Reference)

Use a deterministic weighted penalty model:

- Base score per category: `100`
- Penalties by severity:
	- Critical: `-25`
	- High: `-15`
	- Medium: `-8`
	- Low: `-3`
- Clamp each category at `[0,100]`
- Overall weighted score:
	- Security: `45%`
	- Readiness: `35%`
	- Cost: `20%`

Decision mapping:

- `overall < 70` blocked
- `70 <= overall <= 85` caution
- `overall > 85` ready

---

## 8) Suggested Repo Structure

```text
.
├── README.md
├── infra/
│   ├── main.bicep
│   └── params.dev.json
├── backend/
│   ├── src/
│   │   ├── api/
│   │   ├── agents/
│   │   ├── rules/
│   │   ├── scoring/
│   │   └── integrations/
│   └── Dockerfile
└── ui/
		├── src/
		└── package.json
```

---

## 9) 7-Day Delivery Plan

- **Day 0**: repo scaffold, identity setup, Key Vault, base Bicep
- **Day 1**: Collector Agent (Resource Graph + Monitor + RBAC), scan API live
- **Day 2**: implement all 10 deterministic rules
- **Day 3**: Risk scoring and gate decision
- **Day 4**: Model Router + Foundry prompt integration + fallback templates
- **Day 5**: minimal UI (score, table, detail panel, JSON export, issue draft action)
- **Day 6**: hardening (error handling, telemetry, architecture diagram, demo rehearsal)
- **Day 7**: deployment, final test pass, demo recording

---

## 10) Acceptance Criteria

- Scan completes in demo window (target: under 120s)
- Uses Agent Framework for orchestration
- Uses Azure MCP integration for inventory access
- Uses Model Router for model selection
- Uses Foundry for prompt governance
- Produces clear architecture diagram
- Public GitHub repo + 2-minute demo video

---

## 11) Two-Minute Demo Script

1. Title + value statement
2. Click **Run Production Gate**
3. Show result: `Gate Score 61/100 — Deployment Blocked`
4. Open critical NSG finding and show explanation + Bicep/CLI remediation
5. Show overprivileged Owner finding
6. Show idle VM cost impact
7. Simulate remediation and re-run (`87/100 — Production Ready`)
8. Show generated GitHub issue draft
9. Close with deployment-risk prevention message

---

## 12) Stretch Goals (Only If Time Remains)

- Simulated auto-remediation
- Multi-subscription mode
- Scheduled scans
- Least-privilege role recommendation engine
- Executive PDF export

---

## Final Scope Lock

**One subscription. Ten rules. One Gate Score. AI explanations. GitHub draft.**

That scope is cohesive, defensible, and buildable in one week.
