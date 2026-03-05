# 🌐 Azure Agentic Architect (A3)

**Azure Agentic Architect (A3)** is a visual Infrastructure-as-Code (IaC) designer built on a multi-agent architecture. It empowers teams to design cloud infrastructure through an intuitive drag-and-drop canvas, augmented by intelligent agents that understand Azure architecture patterns, validate configurations, and generate production-ready, modular Bicep code.

---

## 🏃 Quick Start

**Run the application (clean build from scratch):**

```
docker-compose down --rmi all --volumes && docker-compose up --build -d
```
```
docker-compose up -d --build
```
Access at `http://localhost:3000`

---

## 🚀 Core Features

* **Visual Architecture Canvas:** Drag-and-drop interface with support for container resources (Resource Groups, VNets, Subnets, Management Groups) that visually represent Azure's resource hierarchy.
* **AI Chat:** Integrated AI assistant powered by **Azure MCP Architecture Tool** that provides real-time architectural guidance, suggests best practices, and can even help design complete architectures that are then rendered on the canvas.
* **Bicep MCP Integration:** Live integration with **Bicep MCP Server** to fetch resource schemas, validate properties, and ensure type-safe configuration before code generation.
* **Live Schema Inspection:** Real-time property suggestions, constraints, and valid ranges for every Azure resource through **Azure MCP Servers**.
* **Modular IaC Generation:** Generates one Bicep file per resource with a `main.bicep` orchestrator for cohesive, maintainable deployments.
* **Deployment Testing:** Built-in dry-run validation and test deployment capabilities against a sample subscription before committing code.
* **Git Integration:** Automated export to configured Git repositories per project with CI/CD pipeline generation.

---

## 🧠 Multi-Agent Orchestration

A3 uses a sophisticated **Agent-to-Agent (A2A)** workflow built on the **Microsoft Agent Framework**:

### 🧩 1. The Architect Agent (The Guide)
**Role:** Architectural Expertise & Discovery.

**Responsibility:** An Azure architecture expert that uses the **Azure MCP Architecture Tool** to provide context-aware guidance. When you drop a Storage Account onto the canvas, it queries the resource hierarchy and suggests: "Would you like to add Blob Service or File Share? Should we configure Private Endpoints?" Available through the **AI Chat** tab in the right panel, users can describe their needs in natural language, and the agent will design the architecture, which—upon user approval—is rendered directly onto the canvas with proper relationships.

**Tools Used:** Azure MCP Architecture Tool, Azure MCP Documentation

### 🛡️ 2. The Integrity Agent (The Validator)
**Role:** Logic & Compliance.

**Responsibility:** The "brain" of the canvas. It monitors connections in real-time. If you link a Virtual Network to a SQL Database, the Integrity Agent validates the Bicep type-system constraints to ensure the properties match. It prevents "invalid wiring" before a single line of code is written.

### ⌨️ 3. The Coder Agent (The Bicep Expert)
**Role:** Code Synthesis & Validation.

**Responsibility:** A Bicep specialist that leverages the **Bicep MCP Server** to generate modular, production-ready code. For each resource on the canvas, it creates a dedicated Bicep file with proper parameterization, then orchestrates them through a `main.bicep`. It validates syntax using Bicep Linter and performs dry-run deployments to catch errors before you commit. The generated code follows Azure best practices and is ready for immediate deployment.

**Tools Used:** Bicep MCP Server, Azure CLI, Bicep Linter

**Output Structure:**
```
Project/IaC/Bicep/
├── main.bicep                    # Orchestrator
├── modules/
│   ├── storage-account.bicep     # One file per resource
│   ├── virtual-network.bicep
│   └── app-service.bicep
└── parameters/
    └── main.parameters.json
```

### 🚀 4. The DevOps Agent (The Automator)
**Role:** Deployment & Lifecycle.

**Responsibility:** Once the architecture is finalized, this agent generates the GitHub Actions or Azure DevOps YAML pipelines. It handles the "last mile"—committing code to your repository, configuring environment secrets, and triggering the initial deployment to Azure.

### 🚦 5. The Model Router (The Optimizer)
**Role:** Performance & Cost Efficiency.

**Responsibility:** Our "Traffic Controller." It analyzes the complexity of your request.

- **GPT-4o (Reasoning):** Used for complex architectural decisions and deep integrity checks.
- **GPT-4o-mini (Speed):** Used for rapid UI property fetching and simple Bicep boilerplate.

This ensures a snappy, low-latency experience for the user.

---

## 🛠️ Tech Stack

**Design Philosophy:** Simple, lightweight, and easily auditable code with minimal dependencies.

* **Frontend:** Vanilla JavaScript, HTML5, CSS3 with minimal frameworks for maximum transparency and auditability.
* **Backend:** Python 3.11+, FastAPI (lightweight ASGI framework).
* **Agent Framework:** Microsoft Agent Framework with MCP protocol for agent-to-tool communication.
* **Intelligence:** 
  - **Azure AI Foundry** (Model hosting and routing)
  - **Azure MCP Servers** (Resource management, schema inspection, best practices)
  - **Bicep MCP Server** (IaC generation, validation, schema queries)
* **IaC Engine:** Bicep (primary), with planned support for Terraform and OpenTofu.
* **Deployment:** Self-sufficient containerization with all dependencies in `requirements.txt`. Can containerize itself on-demand.

---

## 🧩 Workspace Layout

A clean, three-panel design with resizable splitters. The **Visual Canvas** is the primary workspace.

```text
+--------------------------------------------------------------------------------------------------+
| [Cloud Provider ▼]                                                     [Project: Default-Name]    |
+------------------------------+-----------------------------------------------+-------------------+
| [Search resources...]        |                                               | [Property] [Tips] [AI Chat] |
|------------------------------|                                               |-------------------|
| Resource Groups              |                                               |                   |
| Virtual Networks             |                                               | Resource          |
| Storage Accounts             |           VISUAL CANVAS                       | Property          |
| Databases                    |                                               | (when selected)   |
| App Services                 |       (drag-and-drop design area)             |                   |
| Key Vaults                   |                                               |        OR         |
| Container Resources          |                                               |                   |
|   • Management Groups        |                                               | AI Chat           |
|   • Subscriptions            |                                               | (AI assistant)    |
|   • Resource Groups (visual) |                                               |                   |
|   • VNets (containers)       |                                               |                   |
|   • Subnets (nested)         |                                               |                   |
+------------------------------+-----------------------------------------------+-------------------+

Resizable splitters:
- Vertical: Resource List ↔ Canvas ↔ Right Panel

Right Panel Tabs:
- Property: Schema-driven form for the selected resource with live validation
- Tips: Quick guidance and recommendations for the current design
- AI Chat: Natural language interface to Azure architecture expert
```

---

## 📂 Project Structure

```bash
.
├── Agents/
├── App_Backend/
├── App_Frontend/
│   ├── landing.html            # Landing page for selecting/creating projects (served at /)
│   ├── canvas.html             # Main design workspace page (resource list, canvas, property/tips/AI chat panes)
│   ├── landing.js              # Landing page logic (project CRUD, localStorage, cloud accordion behavior)
│   ├── canvas.js               # Canvas page logic (catalog rendering, splitters, tabs, project name editing)
│   ├── styles.css              # Shared UI styles for both landing and canvas pages
│   ├── nginx.conf              # Nginx routing config (root -> landing.html)
│   ├── generate_catalogs.py    # Builds provider resource catalogs from icon folders during image build
│   └── app.js                  # Legacy monolithic script (not used by split-page flow)
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
│   └── Default/
│       ├── Architecture/
│       ├── IaC/
│       │   ├── Bicep/
│       │   ├── Terraform/
│       │   └── OpenTofu/
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
| `Projects/Default/Architecture/` | Source-of-truth architecture/canvas JSON for the default template project. |
| `Projects/Default/IaC/<engine>/` | Generated deployment artifacts grouped by IaC engine (`Bicep`, `Terraform`, `OpenTofu`). |
| `Projects/Default/Docs/` | Design notes, reports, decision logs, and validation output. |

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

2. **Install Dependencies:**

```bash
# Python dependencies (all required packages)
pip install -r requirements.txt

# Node.js dependencies for MCP servers
npm install @azure/mcp@latest
npm install @bicep/mcp@latest  # If available
```

3. **Configure Application Settings:**

Open **Settings → Application Settings** in the UI to configure:

**Azure Identity (Required):**
- Azure AD App Registration credentials
  - `AZURE_TENANT_ID`
  - `AZURE_CLIENT_ID`
  - `AZURE_CLIENT_SECRET`
  - `AZURE_SUBSCRIPTION_ID`
- Grant the App Registration `Contributor` role on your subscription

**Azure AI Foundry (Required for Agents):**
- Foundry Project details
  - `Project Region` (e.g., eastus, westeurope)
  - `Foundry Endpoint`
  - `Foundry API Key`
  - `API Version`
- Model Configuration
  - `Reasoning Model` (e.g., gpt-4o) - for complex architectural decisions
  - `Coding Model` (e.g., gpt-4o) - for Bicep generation
  - `Fast Model` (e.g., gpt-4o-mini) - for quick UI interactions

Once verified, the app pulls available models from Foundry and displays them in settings.

**Project Settings (Per-Project):**
- Git repository URL for code export
- Branch name and commit preferences
- Deployment subscription (for testing)

Settings persisted in:
- `App_State/app.settings.env` (application-level)
- `Projects/{project-name}/project.settings.env` (project-level)

Configuration verification endpoint:
- `POST /api/settings/app/verify` - Validates Azure credentials and Foundry connection

4. **Run the Application:**

**Development Mode:**
```bash
# Start backend
cd App_Backend
uvicorn main:app --reload --port 8000

# Serve frontend (in another terminal)
cd App_Frontend
python -m http.server 3000
```

**Containerized (Self-Sufficient):**
```bash
# The app can containerize itself on-demand
docker-compose up --build
```

If port `3000` is already in use:
```bash
APP_PORT=3001 docker compose up --build
```

5. **Access the Application:**

Open `http://localhost:3000`

---

## 🎯 How It Works

### 1. Visual Design
- **Drag resources** from the left panel onto the canvas
- **Container resources** (Resource Groups, VNets, Subnets) render as visual containers
- **Organize resources** by dragging them into containers (e.g., VM into a Subnet)
- **Connect resources** to establish relationships (e.g., App Service → SQL Database)

### 2. Get Architectural Guidance
- Click the **AI Chat** tab in the right panel
- Describe your architecture: *"I need a secure web app with a database"*
- The Architect Agent suggests a complete architecture:
  - App Service with VNet integration
  - Azure SQL with Private Endpoint
  - Key Vault for secrets
  - Application Insights for monitoring
- **Approve** the design, and it's automatically rendered on the canvas

### 3. Configure Resources
- **Select a resource** on the canvas
- The **Property tab** shows a schema-driven form with:
  - Required and optional fields
  - Valid values and constraints (from Bicep MCP)
  - Real-time validation
  - Dependency suggestions

### 4. Generate & Deploy
- Click **Generate IaC** to produce modular Bicep files
- The Coder Agent creates:
  - One `.bicep` file per resource in `modules/`
  - A `main.bicep` orchestrator
  - Parameterized for reusability
- **Dry-run validation** tests the code before deployment
- **Test deployment** to your sample subscription
- **Export to Git** when ready for production

### 5. Deploy via CI/CD
- The DevOps Agent generates:
  - GitHub Actions or Azure DevOps YAML
  - Environment configurations
  - Secret references
- Push to your repository and trigger automated deployment

---

## 🔮 Roadmap

### In Progress
- [x] Visual canvas with drag-and-drop
- [x] Resource catalog with Azure icons
- [x] AI Chat with Azure MCP integration
- [x] Modular Bicep code generation
- [x] Application settings for Azure AD and Foundry
- [ ] Container resource visual representation
- [ ] Live resource property forms with Bicep MCP
- [ ] Dry-run validation and test deployments
- [ ] Git export per project

### Planned Features
- **WAF Auditor Agent:** Real-time cost, security, and Well-Architected Framework scoring
- **Reverse Engineering:** Import existing Azure environments into the canvas via Azure Resource Graph
- **Terraform Support:** Generate Terraform modules alongside Bicep
- **Multi-Cloud:** AWS and GCP provider support
- **Collaboration:** Real-time multi-user canvas editing
- **Version Control:** Architecture versioning and diff visualization

---

## 🏗️ MCP Integration Architecture

A3 is built as a **multi-agent system** communicating with multiple **Model Context Protocol (MCP) servers**:

```
┌─────────────────────────────────────────────────────────────┐
│                    A3 Application                           │
│                                                             │
│  ┌─────────────┐  ┌──────────────┐  ┌──────────────┐      │
│  │  Architect  │  │   Integrity  │  │    Coder     │      │
│  │    Agent    │  │    Agent     │  │    Agent     │      │
│  └──────┬──────┘  └──────┬───────┘  └──────┬───────┘      │
│         │                 │                  │               │
│         └─────────────────┴──────────────────┘               │
│                           │                                  │
│                    MCP Protocol Layer                        │
└───────────────────────────┼──────────────────────────────────┘
                            │
        ┌───────────────────┼───────────────────┐
        │                   │                   │
   ┌────▼────┐        ┌─────▼─────┐      ┌─────▼─────┐
   │ Azure   │        │   Bicep   │      │   Azure   │
   │  MCP    │        │    MCP    │      │    CLI    │
   │ Server  │        │  Server   │      │           │
   └─────────┘        └───────────┘      └───────────┘
   • Resources        • Schemas           • Deployment
   • Architecture     • Validation        • Testing
   • Best Practices   • Code Gen          • Dry-run
```

**Why MCP?**
- **Standardized interface** for agent-to-tool communication
- **Extensible** - Add new capabilities by plugging in MCP servers
- **Auditable** - All agent actions go through observable MCP calls
- **Testable** - Mock MCP servers for development and testing

---

## 📋 Requirements

**Runtime:**
- Python 3.10+
- Node.js 20+ (for MCP servers)
- Azure subscription with Contributor access
- Azure AI Foundry project

**Azure Prerequisites:**
- App Registration (Service Principal) with RBAC permissions
- Azure AI Foundry project with deployed models
- (Optional) Test subscription for deployment validation

**Self-Sufficiency:**
- All Python dependencies listed in `requirements.txt`
- Containerization scripts included for on-demand deployment
- No external build tools required beyond Python and Node.js

---

## ⚖️ Design Principles

* **Simplicity:** Minimal dependencies, vanilla JavaScript where possible, easily auditable code.
* **Multi-Agent Architecture:** Specialized agents for architecture, validation, and code generation.
* **MCP-First:** All external integrations through Model Context Protocol for extensibility.
* **Modular Output:** One Bicep file per resource for maintainability and reusability.
* **Production-Ready:** Generated code includes validation, testing, and CI/CD pipelines.
* **Self-Sufficient:** Can containerize itself on-demand with all dependencies included.

---

## 🚀 Antigravity

**Antigravity** is the development environment for this project, providing an isolated Ubuntu container with all necessary tools.

### Docker Run Command

```bash
docker run -it \
  --name antigravity \
  -p 8100-8200:8100-8200 \
  -v "/Users/mohit.sharma/Documents/GitHub/Antigravity":/workspace \
  -w /workspace \
  ubuntu:latest \
  bash
```

### Update & Install Dependencies

Once inside the container, run:

```bash
apt update
apt install -y \
  python3 \
  python3-venv \
  python3-pip \
  curl \
  wget \
  git \
  net-tools \
  iputils-ping \
  nano \
  vim

mkdir "Agentic-Cloud-Architect"
```

### Prompt for Agentic Cloud Architect

```text
Agentic Cloud Architect – Web Application Requirements (Non-Containerized Phase)

⚠️ IMPORTANT SCOPE CONSTRAINT

This phase is strictly for building the working web application only.

❌ DO NOT create Dockerfile
❌ DO NOT create docker-compose.yml
❌ DO NOT attempt self-containerization
❌ DO NOT assume execution inside a container
❌ DO NOT optimize for container builds

The application must run directly from source on a local machine.

Containerization will be handled separately in a later phase.

---

📁 Project Context

- The root folder already exists
- The folder name is: Agentic Cloud Architect
- All generated code must live inside this folder
- Use clean project structure and standard conventions

---

📄 Mandatory Setup Documentation

The system MUST create a file at project root called: preparation.md

This file must include:

1. Environment Requirements
- Required Python version
- Required Node.js version (if used)
- Required Azure CLI version (if used)
- Any global tools that must be installed
- Any OS assumptions (Windows/macOS/Linux compatible)

2. Installation Steps (Step-by-step)
Exact commands to:
- Create virtual environment
- Activate virtual environment
- Install Python dependencies
- Install frontend dependencies (if applicable)
- Start backend
- Start frontend

3. Configuration Instructions
- How to configure Azure AD credentials
- How to configure Azure AI Foundry
- Where environment variables should be placed
- Required .env format (include template)

4. External Services Required
List any:
- Azure subscriptions required
- Azure permissions required
- MCP servers required
- Git access requirements

5. Verification Steps
How to confirm the application is working:
- URLs to open
- Health check endpoints
- How to verify Azure connectivity
- How to verify Foundry connectivity

This file must be complete enough that someone else can reproduce the environment manually.

---

🎯 Core Mission

A visual Infrastructure-as-Code (IaC) designer that enables application teams to design Azure architectures through drag-and-drop, assisted by a multi-agent system leveraging MCP servers, generating modular and deployable Bicep code.

This phase focuses only on building the working web application.

---

🎯 Functional Requirements

1. Visual Architecture Canvas

1.1 Web UI
- The system MUST provide a browser-based UI

1.2 Layout
The UI must have:
- Left Panel: Azure resource catalog
- Center Panel: Drag-and-drop canvas
- Right Panel: Tabbed interface with exactly three tabs:
  - Property
  - Tips
  - AI Chat

1.3 Canvas Capabilities
The canvas must support:
- Drag resources onto canvas
- Move resources visually
- Connect resources visually
- Container hierarchy visualization:
  - Resource Groups
  - Management Groups
  - Subscriptions
  - Virtual Networks
  - Subnets
- Container resources must visually contain child resources

2. Multi-Agent System

2.1 Architecture
- Implement a lightweight multi-agent orchestration layer in Python
- Preferred: Microsoft Agent Framework OR a clean custom lightweight orchestration abstraction

2.2 Required Agents

Agent | Responsibility
Architect Agent | Azure architecture advisor via chat
Integrity Agent | Validates relationships and hierarchy
Coder Agent | Generates modular Bicep code
DevOps Agent | Handles Git export

2.3 Agent Collaboration Flow
1. Architect proposes architecture in chat
2. User approves
3. Canvas auto-populates
4. Integrity agent validates in real time
5. Coder agent generates code on demand

3. Azure & MCP Integration

The system must support:
- Azure MCP Server integration
- Bicep MCP integration
- Configurable authentication
- Real-time schema validation
- Subscription-based dry runs (what-if deployments)
- All MCP communication must be logged

4. Modular Infrastructure-as-Code Generation

The Coder Agent must:
- Generate one .bicep file per Azure resource
- Generate a main.bicep file orchestrating everything
- Follow Azure best practices
- Perform az deployment group what-if validation
- Report validation results

5. Project Structure Requirements

Each project must persist:
- Canvas state (JSON)
- Generated Bicep files
- Deployment logs
- Validation history

Projects must be:
- Saveable
- Loadable
- Independently exportable

6. Application-Level Configuration

Global application settings (not per project):

6.1 Azure AD Configuration
- Fields: Client ID, Tenant ID, Client Secret or Certificate
- Must be used for: Azure operations, MCP access, Deployment validation

6.2 Azure AI Foundry Integration
- Fields: Region, Endpoint, API key, API version
- Flow: Verify connection → Retrieve available models → Allow assigning models to agents

6.3 Model Assignment
- Allow model selection for: Architect Agent, Coder Agent, Integrity Agent
- Fast vs reasoning models

7. Technical Implementation (Web App Only)

7.1 Backend
- Python
- FastAPI preferred
- Clear separation of: API layer, Agent orchestration, MCP communication, File management

7.2 Frontend
- Minimal React OR Vanilla JS
- Canvas: Fabric.js, React Flow, or simple SVG-based system
- Keep it lightweight and readable

8. Code Quality Constraints
- Prioritize readability
- Avoid unnecessary abstraction
- Explicit logic preferred
- Log all MCP interactions
- Minimal dependencies where reasonable
- Clear folder structure

---

🚫 Explicitly Out of Scope (This Phase)
- Dockerfile
- docker-compose
- Self-containerization
- CI/CD pipelines
- Kubernetes manifests
- Cloud deployment of the web app itself

The app must simply run locally from source.

---

🧩 Future Phase

Containerization will be done manually later using:
- The preparation.md file
- The dependency definitions
- The working application source

Therefore, setup documentation must be complete and precise.

---

✅ Final Deliverables

Antigravity must produce:
- Working web application source code
- Clean project structure
- Fully working local run instructions
- preparation.md at project root
- All dependencies explicitly defined
- No container-related artifacts
```