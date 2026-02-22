# 🌐 Azure Agentic Architect (A3)

**Azure Agentic Architect (A3)** is a containerized, visual Infrastructure-as-Code (IaC) designer. It empowers application teams to "vibe code" their cloud infrastructure by dragging and dropping cloud resources onto a canvas, which is then translated into production-ready IaC and CI/CD assets via a multi-agent system.

---

## 🚀 Core Features

* **Visual Architecture Canvas:** A drag-and-drop interface powered by `React Flow` with real-time zooming, panning, and resource connecting.
* **Agentic Sidekick:** A bottom-pane chat powered by the **Microsoft Agent Framework** that can build or modify the architecture based on natural language.
* **Live Schema Inspection:** Integrated with **Azure MCP** to fetch real-time properties, constraints, and valid ranges for every Azure resource.
* **Module-First Approach:** Uses verified cloud modules/patterns so generated IaC aligns with production readiness best practices.
* **Instant IaC & CI/CD:** One-click conversion from visual diagram to `main.bicep` and `azure-pipelines.yml`.

---

## 🧠 Multi-Agent Orchestration

A3 uses a sophisticated **Agent-to-Agent (A2A)** workflow built on the **Microsoft Agent Framework**:

| Agent | Responsibility | Tooling |
| --- | --- | --- |
| **Architect Agent** | Interprets user intent and updates the Canvas JSON. | `Azure AI Search`, `Cloud Catalog Metadata` |
| **Bicep Specialist** | Converts graph nodes into Bicep modules. | `Azure MCP Server`, `Bicep CLI` |
| **DevOps Agent** | Generates YAML pipelines and environment configs. | `Azure DevOps MCP` |
| **Model Router** | Directs tasks to GPT-4o (Reasoning) or GPT-4o-mini (Speed). | `Azure AI Foundry` |

---

## 🛠️ Tech Stack

* **Frontend:** React 19, Vite, Tailwind CSS, **React Flow**.
* **Backend:** Python 3.11, FastAPI, **Microsoft Agent Framework**.
* **Intelligence:** **Azure AI Foundry** (Model Routing & Safety), **Azure MCP Servers**.
* **Deployment:** Docker, Azure Container Apps.

---

## 🧩 Final Layout Design

The workspace uses a fully resizable split-pane layout. All major sections are resizable, with the **Visual Canvas** as the largest default area.

```text
+--------------------------------------------------------------------------------------------------+
| [Cloud Provider ▼]                                                  [Project: <Project_Name>]    |
+------------------------------+-----------------------------------------------+-------------------+
| [Search resources...]        |                                               | Resource Details  |
|------------------------------|                                               |-------------------|
| Dynamic Resource List        |                VISUAL CANVAS                  | Selected Resource |
| (provider-scoped catalog)    |             (largest default pane)            | Properties / Form |
|                              |                                               | Validation        |
|                              |                                               | Dependencies      |
+------------------------------+-----------------------------------------------+-------------------+
| [Chat] [Terminal] (tabbed)                                   | Status: info / warn / error      |
+--------------------------------------------------------------+-----------------------------------+

Resizable splitters:
- Vertical: Resource List ↔ Canvas ↔ Properties
- Horizontal: Main Workspace ↕ Bottom Pane
- Bottom split: Chat/Terminal ↔ Status
```

---

## 📂 Project Structure

```bash
.
├── Agents/
├── App_Backend/
├── App_Frontend/
├── App_State/
├── Clouds/
│   ├── Azure/
│   │   └── Icons/
│   ├── AWS/
│   └── GCP/
├── Connections/
├── IaC/
│   ├── Bicep/
│   ├── Terraform/
│   └── OpenTofu/
├── MCP/
├── Projects/
│   └── <Project_Name>/
│       ├── Architecture/
│       ├── IaC/
│       │   └── <cloud>/<engine>/
│       └── Docs/
├── Dockerfile
├── docker-compose.yml
└── README.md
```

### Folder Purposes

| Folder | Purpose |
| --- | --- |
| `Agents/` | Multi-agent definitions, prompts, routing, and orchestration contracts. |
| `App_Backend/` | FastAPI services, APIs, and orchestration endpoints used by UI/agents. |
| `App_Frontend/` | React UI, canvas, chat sidekick, and client-side workflows. |
| `App_State/` | Shared state models, persistence helpers, and runtime context. |
| `Clouds/` | Cloud-provider-specific assets (schemas, adapters, icons, constraints). |
| `Clouds/Azure/Icons/` | Azure service icon library used on the architecture canvas. |
| `Connections/` | External integrations (Foundry, MCP clients, model routing connectors). |
| `IaC/` | IaC-engine-specific logic and templates (`Bicep`, `Terraform`, `OpenTofu`). |
| `MCP/` | MCP server configuration and bindings used by agents/tools. |
| `Projects/` | Generated project outputs grouped by project name. |
| `Projects/<Project_Name>/Architecture/` | Source-of-truth architecture/canvas JSON for a single project. |
| `Projects/<Project_Name>/IaC/<cloud>/<engine>/` | Generated deployment artifacts by cloud and IaC engine. |
| `Projects/<Project_Name>/Docs/` | Design notes, reports, decision logs, and validation output. |

---

## 🚦 Getting Started

### Prerequisites

* Docker & Docker Compose
* Azure OpenAI API Key (configured in Azure AI Foundry)
* MCP-enabled environment

### Installation

1. **Clone the repository:**

```bash
git clone https://github.com/your-repo/azure-agentic-architect.git
cd azure-agentic-architect
```

2. **Configure Environment:**
Create a `.env` file in the root with your `AZURE_OPENAI_ENDPOINT` and `AZURE_OPENAI_KEY`.

3. **Spin up the stack:**

```bash
docker-compose up --build
```

4. **Access the App:**
Open `http://localhost:3000` to start designing.

### Current First Step (Implemented)

The current containerized app serves the **layout shell UI** with:
- Resizable left resource panel, center canvas, and right properties panel
- Resizable bottom workspace with **Chat / Terminal** tabs and **Status Messages** pane
- Canvas as the largest default design area

Run with:

```bash
docker compose up --build
```

If port `3000` is already in use, run on another host port:

```bash
APP_PORT=3001 docker compose up --build
```

---

## 🔮 Future Roadmap

* **WAF Auditor Agent:** Real-time cost and security scoring during the design phase.
* **Reverse Engineering:** Import existing Azure Resource Groups into the visual canvas via Azure MCP.
* **Multi-Cloud Support:** Extend provider adapters in `Clouds/` and generate IaC into `Projects/<Project_Name>/IaC/<cloud>/<engine>/`.

---

## ⚖️ Judging Criteria Alignment

* **Technological Implementation:** High-quality React/Python codebase with containerization.
* **Agentic Design:** Multi-agent collaboration using the latest Microsoft Agent Framework.
* **Real-World Impact:** Bridges the gap between "drawing" an architecture and "deploying" it.
