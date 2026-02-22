# 🌐 Azure Agentic Architect (A3)

**Azure Agentic Architect (A3)** is a containerized, visual Infrastructure-as-Code (IaC) designer. It empowers application teams to "vibe code" their cloud infrastructure by dragging and dropping **Azure Verified Modules (AVM)** onto a canvas, which is then translated into production-ready Bicep and Azure DevOps pipelines via a multi-agent system.

---

## 🚀 Core Features

* **Visual Architecture Canvas:** A drag-and-drop interface powered by `React Flow` with real-time zooming, panning, and resource connecting.
* **Agentic Sidekick:** A bottom-pane chat powered by the **Microsoft Agent Framework** that can build or modify the architecture based on natural language.
* **Live Schema Inspection:** Integrated with **Azure MCP** to fetch real-time properties, constraints, and valid ranges for every Azure resource.
* **AVM-First Approach:** Strictly uses **Azure Verified Modules** to ensure the generated Bicep is compliant with the **Well-Architected Framework (WAF)**.
* **Instant IaC & CI/CD:** One-click conversion from visual diagram to `main.bicep` and `azure-pipelines.yml`.

---

## 🧠 Multi-Agent Orchestration

A3 uses a sophisticated **Agent-to-Agent (A2A)** workflow built on the **Microsoft Agent Framework**:

| Agent | Responsibility | Tooling |
| --- | --- | --- |
| **Architect Agent** | Interprets user intent and updates the Canvas JSON. | `Azure AI Search`, `AVM Metadata` |
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

## 📂 Project Structure

```bash
.
├── Agents/                         # Microsoft Agent Framework definitions (Architect, Bicep, DevOps)
├── AVM/                            # Azure Verified Modules (Bicep library & metadata)
├── Azure_Icons/                    # Visual assets for the Canvas nodes
├── Connections/                    # API Clients (Azure Foundry, MCP Client, Model Router)
├── MCP/                            # MCP Server configurations (Azure, MS Learn, Bicep)
├── Projects/                       # User-generated output (organized by project name)
│   └── <Project_Name>/
│       ├── Bicep/                  # Generated main.bicep and parameters
│       ├── Diagram/                # JSON state of the React Flow canvas
│       └── Documentation/          # Activity logs, WAF reports, and AI explanations
├── App_Frontend/                   # Vite/React code (UI, Canvas, Sidebar)
├── App_Backend/                    # FastAPI entry points and WebSocket handlers
├── App_State/                      # Global session persistence and orchestration state
├── Dockerfile                      # Multi-stage build for the containerized webapp
├── docker-compose.yml              # Orchestrates Frontend, Backend, and MCP servers
└── README.md                       # Project documentation
```

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

---

## 🔮 Future Roadmap

* **WAF Auditor Agent:** Real-time cost and security scoring during the design phase.
* **Reverse Engineering:** Import existing Azure Resource Groups into the visual canvas via Azure MCP.
* **Multi-Cloud Support:** Expanding AVM patterns to include Terraform/OpenTofu for AWS/GCP.

---

## ⚖️ Judging Criteria Alignment

* **Technological Implementation:** High-quality React/Python codebase with containerization.
* **Agentic Design:** Multi-agent collaboration using the latest Microsoft Agent Framework.
* **Real-World Impact:** Bridges the gap between "drawing" an architecture and "deploying" it.
