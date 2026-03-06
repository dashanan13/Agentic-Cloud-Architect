# Azure Agentic Architect (A3)

**Azure Agentic Architect (A3)** is a visual Infrastructure-as-Code (IaC) designer built on a multi-agent architecture. It enables teams to design Azure infrastructure through a drag-and-drop canvas, backed by intelligent agents that validate configurations and generate production-ready, modular Bicep.

## Highlights

- Visual architecture design with a drag-and-drop canvas
- Multi-agent guidance for architecture review and best practices
- One-click validation with actionable recommendations
- Modular IaC generation using Azure Bicep and Verified Modules
- **Single source of truth** via the project JSON state

## Quick Start

**Prereqs:** Docker and Docker Compose

**Clean rebuild (from scratch):**

```bash
docker-compose down --rmi all --volumes && docker-compose up --build -d
```

**Incremental rebuild:**

```bash
docker-compose up -d --build
```

Access the app at `http://localhost:3000`

> **Note:** The clean rebuild removes images and volumes.

---

## System Overview

The Agentic Cloud Architect is a visual architecture design platform that lets users build Azure infrastructure with a drag-and-drop canvas and generate Infrastructure-as-Code.

The system combines deterministic architecture modeling with AI-assisted reasoning to provide architecture guidance, validation, and automated IaC generation.

> **Important:** The canvas architecture is stored as a project JSON state, which is the single source of truth for the system.

## Core Components

### 1) Canvas Engine

Manages the visual architecture diagram and updates the project state.

**Responsibilities:**

- Render architecture components
- Add or remove resources
- Create connections
- Manage layout and selection
- Update resource properties
- Persist the project JSON state

Canvas operations are deterministic and do not require AI.

### 2) Project Canvas State

Each project maintains a JSON state file containing:

- Canvas layout
- Resource components
- Connections between resources
- UI state

This state is used by:

- AI Chat
- Architecture Validation
- IaC Generation

### 3) Architecture Advisor Agent (AI Chat)

Allows users to discuss and improve their architecture using Azure best practices.

**Responsibilities:**

- Interpret user architecture questions
- Analyze the current canvas architecture
- Recommend improvements and patterns
- Propose structured modifications to the canvas

Users can approve suggestions before applying them to the canvas.

### 4) Architecture Planning Agent (Validation & Tips)

Validates the architecture when the user clicks **Validate**.

**Responsibilities:**

- Evaluate the current architecture
- Detect missing components
- Identify design conflicts
- Provide best-practice suggestions
- Generate implementable improvements

Each suggestion can be applied directly to the canvas.

### 5) IaC Generation Engine

Converts the finalized architecture into Infrastructure-as-Code.

**Supported formats:**

- Azure Bicep

**Code generation is based on:**

- Azure resource schemas
- Predefined templates
- Azure Verified Modules

The system generates modular infrastructure code with a central deployment file.

**Example structure:**

```text
infra/
  main.bicep
  modules/
    openai.bicep
    anomalyDetector.bicep
```

The generated infrastructure can optionally be deployed to a test subscription for validation.

## System Interaction Flow

1. **Architecture Design**: Drag Azure resources onto the canvas and connect them.
2. **Architecture Review**: Click **Validate** to analyze the architecture and receive recommendations.
3. **AI Architecture Discussion**: Use AI Chat to discuss design decisions and explore improvements.
4. **Infrastructure Generation**: Generate IaC from the finalized architecture.

## Design Principles

- Deterministic core system for reliability
- AI used only for reasoning and recommendations
- Single source of truth via project JSON state
- Modular Infrastructure-as-Code generation
- On-demand AI analysis to control token usage

## Architecture Overview

```mermaid
flowchart LR
  UI[User Interface] --> Canvas[Canvas Engine]
  Canvas --> State[Architecture Graph (project state)]

  UI --> Chat[AI Chat]
  Chat --> Advisor[Architecture Advisor Agent]
  Advisor --> State

  UI --> Validate[Validate]
  Validate --> Planner[Architecture Planning Agent]
  Planner --> State

  UI --> Generate[Generate Code]
  Generate --> IaC[IaC Generation Engine]
  IaC --> State
```





---

# AI-Assisted Azure Infrastructure Architecture Designer

## Overview

The **AI-Assisted Azure Infrastructure Architecture Designer** is an interactive platform that enables users to visually design, analyze, and generate Infrastructure-as-Code (IaC) for cloud architectures on **Microsoft Azure**.

The platform provides a **drag-and-drop canvas** where users construct infrastructure architectures using Azure service icons. Resources can be configured through a properties panel, while AI-driven guidance helps improve architecture quality, security, scalability, and cost efficiency.

The system integrates conversational AI, architecture analysis, automated IaC generation, deployment validation, and Git-based delivery pipelines, allowing teams to move seamlessly from **visual design to production-ready infrastructure code**.

---

# Core Capabilities

## 1. Visual Infrastructure Design Canvas

The platform provides a **graphical architecture canvas** where users can design Azure infrastructure by dragging and connecting resource icons.

### Key Features

* Drag-and-drop Azure services onto the canvas
* Visual connections to represent dependencies and network flows
* Real-time architecture editing (move, delete, group resources)
* Logical grouping of architecture components

The canvas internally represents the architecture as a **graph model**:

* **Nodes** → Azure resources
* **Edges** → dependencies or communication relationships

This graph representation enables AI analysis, architecture validation, and IaC generation.

---

# 2. Resource Properties Configuration

A **context-aware properties panel** allows users to configure the settings of the selected resource.

### Capabilities

* Automatically updates when a resource is selected
* Displays configurable parameters specific to the Azure service
* Supports validation and recommended defaults
* Updates architecture state in real time

Example configurable properties include:

* Resource name
* Region
* SKU / service tier
* Scaling configuration
* Networking configuration
* Security settings

This panel ensures the architecture contains all required deployment parameters.

---

# 3. Architecture Tips and Best-Practice Advisor

The **Tips panel** continuously analyzes the architecture currently present on the canvas and provides best-practice recommendations.

### Context Awareness

The Tips engine understands:

* All resources currently on the canvas
* Their configuration
* Their relationships and dependencies
* Overall architecture patterns

### Types of Recommendations

**Resource-Level Tips**

* Missing configuration
* Security hardening recommendations
* Cost optimization suggestions

**Architecture-Level Tips**

* High-availability improvements
* Networking architecture recommendations
* Resilience and fault-tolerance patterns
* Scalability improvements

### Implementable Recommendations

Each tip includes an **Implement** button.

When selected, the system automatically applies the recommendation to the canvas by:

* Adding required resources
* Updating configurations
* Creating necessary connections

This allows users to adopt best practices with a single action.

---

# 4. AI Architecture Chat Assistant

The platform includes a conversational AI assistant that helps users design and improve their infrastructure architecture.

The AI assistant has **full awareness of the current architecture on the canvas**, including:

* resources
* relationships
* configuration settings
* architectural patterns

### Supported Interactions

**Architecture Discussion**

Users can ask questions about their design:

> “Is this architecture production ready?”

**Architecture Improvement Suggestions**

AI analyzes the architecture and recommends improvements.

Example:

* add load balancing
* add private networking
* introduce high availability

**Generate Architecture from Natural Language**

Users can start with a prompt such as:

> “Create a scalable web application architecture using Kubernetes and a managed database.”

The AI generates a full architecture that can be applied to the canvas.

**Modify Existing Architecture**

AI can suggest modifications such as:

* introducing redundancy
* restructuring networking
* adding security layers

Users can **apply these suggestions directly to the canvas**, either by:

* augmenting the existing design
* replacing the current architecture

---

# 5. Infrastructure-as-Code Generation

Once the architecture design is finalized, the platform generates production-ready Infrastructure-as-Code.

Supported formats include:

* **Azure Bicep**
* **Terraform**

The system translates the architecture graph into structured infrastructure definitions.

---

# 6. Modular IaC Generation

The platform generates **modular Infrastructure-as-Code** to improve maintainability and reuse.

### Bicep Project Structure

Each resource is generated as a **separate Bicep module**, while a central orchestration file coordinates the deployment.

Example structure:

```
/infra
  main.bicep
  modules/
    vnet.bicep
    subnet.bicep
    appservice.bicep
    storage.bicep
```

* **main.bicep** orchestrates deployment
* Resource modules encapsulate resource logic
* Parameters and outputs allow modular composition

This approach aligns with recommended best practices for **Azure Bicep** deployments.

---

# 7. Deployment Testing and Validation

Before exporting code, the platform provides **deployment validation capabilities**.

### Features

* Infrastructure syntax validation
* Dry-run deployment simulation
* Test deployments against a sample Azure subscription

This allows users to verify that the generated infrastructure definitions are valid and deployable.

---

# 8. Git Integration and CI/CD Automation

The platform integrates with Git repositories to streamline infrastructure delivery workflows.

### Capabilities

* Automatic export of generated IaC to configured repositories
* Project-specific Git repository configuration
* Commit and version management
* Automatic CI/CD pipeline generation

Example pipeline capabilities include:

* Infrastructure validation
* automated deployment workflows
* environment promotion

This enables seamless integration with modern DevOps workflows.

---

# 9. Iterative Architecture Workflow

The platform supports a **round-trip design workflow** between architecture and code.

### Workflow

1. Design infrastructure visually on the canvas
2. Configure resource properties
3. Review AI tips and apply improvements
4. Discuss architecture with AI assistant
5. Generate Infrastructure-as-Code
6. Validate and test deployment
7. Export code to Git repository

Users can **return to the canvas from the code generation screen** to modify the architecture and regenerate the infrastructure code, enabling rapid iterative development.

---

# Key Value Proposition

The platform bridges the gap between **visual cloud architecture design and Infrastructure-as-Code implementation** by combining:

* Visual modeling
* AI-assisted architecture guidance
* Automated code generation
* Deployment validation
* Git-based DevOps integration

This significantly reduces the time required to move from **conceptual architecture to deployable infrastructure** while ensuring adherence to cloud best practices.

