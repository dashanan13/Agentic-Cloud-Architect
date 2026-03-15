# Agentic Cloud Architect - ASCII Architecture Diagrams

This file captures repo-aligned ASCII diagrams for the current Agentic Cloud Architect solution.

## 1) High-Level Platform Architecture

```text
+--------------------------------------------------------------------------------------------------+
|                           Agentic Cloud Architect - High Level                                   |
+--------------------------------------------------------------------------------------------------+

  User
   |
   v
+-------------------------+
| Browser / Web UI        |
| - Canvas designer       |
| - AI chat               |
| - Validation panel      |
| - IaC generation view   |
+------------+------------+
             |
             | HTTP / JSON
             v
+--------------------------------------------------------------------------------------------------+
| FastAPI Application Server                                                                       |
| - serves frontend assets                                                                         |
| - exposes project/settings APIs                                                                  |
| - orchestrates chat, validation, and IaC generation                                              |
+-------------+----------------------------+-----------------------------+-------------------------+
              |                            |                             |
              v                            v                             v
   +----------------------+    +----------------------+     +-------------------------------+
   | Chat Agent           |    | Validation Agent     |     | IaC Generation Agent          |
   | Azure architect Q&A  |    | findings + tips      |     | modular Bicep / IaC output    |
   +----------+-----------+    +----------+-----------+     +---------------+---------------+
              \                         |                                   /
               \                        |                                  /
                +-----------------------+---------------------------------+
                                        |
                                        v
                           +------------------------------+
                           | Project Canvas State         |
                           | single source of truth       |
                           | nodes + edges + properties   |
                           +---------------+--------------+
                                           |
                    +----------------------+----------------------+
                    |                                             |
                    v                                             v
         +----------------------------+               +-------------------------------+
         | Project Artifacts          |               | App State / Logs              |
         | - architecture JSON        |               | - app settings                |
         | - diagrams                 |               | - activity logs               |
         | - documentation            |               | - validation provenance       |
         | - generated IaC            |               +-------------------------------+
         +-------------+--------------+
                       |
                       v
         +----------------------------+               +-------------------------------+
         | Cloud Catalogs / Schemas   |<--------------| External Context Providers    |
         | - Azure resource catalog   |               | - Azure MCP                   |
         | - icons                    |               | - Azure AI Foundry            |
         | - schema/template data     |               +-------------------------------+
         +----------------------------+

Main flow:
Design on canvas -> save shared project state -> chat / validate -> apply changes -> generate IaC
```

## 2) Technology Integration Architecture

```text
+--------------------------------------------------------------------------------------------------+
|                 Agentic Cloud Architect - Technology Integration Architecture                    |
+--------------------------------------------------------------------------------------------------+

BUILD / EVOLUTION LANE
+---------------------+         designs / prompts / code help         +---------------------------+
| Product Engineer    | <-------------------------------------------> | GitHub Copilot            |
+----------+----------+                                               +-------------+-------------+
           |                                                                          |
           +--------------------------------------------------------------------------+
                                              improves
                                                 |
                                                 v
                                    +------------+-------------+
                                    | A3 Codebase              |
                                    | UI + API + agent logic   |
                                    +--------------------------+


RUNTIME LANE
User
 |
 v
+--------------------------------------------------------------------------------------------------+
| Web App                                                                                          |
| - Canvas designer                                                                                |
| - AI chat                                                                                        |
| - Validate architecture                                                                          |
| - Generate IaC                                                                                   |
+---------------------------------------------+----------------------------------------------------+
                                              |
                                              v
+--------------------------------------------------------------------------------------------------+
| Backend API / Orchestration                                                                      |
| - project APIs                                                                                   |
| - settings + status                                                                              |
| - logging + provenance                                                                           |
| - request routing                                                                                |
+-----------------------------+-------------------------------+------------------------------------+
                              |                               |
                              | reads / writes                | invokes
                              v                               v
                    +-------------------------+   +------------------------------------------------+
                    | Project Canvas State    |   | Agent Layer / Workflow Orchestration          |
                    | single source of truth  |   | - Chat agent                                   |
                    | nodes / edges / props   |   | - Validation agent                             |
                    +------------+------------+   | - IaC generation agent                         |
                                 ^                +----------------------+-------------------------+
                                 |                                       |
                                 | architecture context                  |
                                 |                                       |
                                 |                        +--------------+---------------+
                                 |                        | Azure MCP Server              |
                                 |                        | - cloudarchitect_design       |
                                 |                        | - Azure best practices        |
                                 |                        | - schema/template guidance    |
                                 |                        +--------------+---------------+
                                 |                                       |
                                 |                        +--------------v---------------+
                                 |                        | Azure AI Foundry             |
                                 |                        | - model deployments          |
                                 |                        | - agent definitions          |
                                 |                        | - threads / runs             |
                                 |                        +--------------+---------------+
                                 |                                       |
                                 +---------------------------------------+
                                                         |
                                                         v
+--------------------------------------------------------------------------------------------------+
| Outputs                                                                                          |
| - architecture recommendations                                                                   |
| - validation findings + provenance                                                               |
| - generated Bicep / IaC                                                                          |
| - updated project artifacts                                                                      |
+------------------------------------------------+-------------------------------------------------+
                                                 |
                                                 v
+--------------------------------------------------------------------------------------------------+
| Azure Services Designed / Generated                                                              |
| Resource Groups | VNets | Subnets | App Gateway | Firewall | App Service | Functions | SQL      |
| Storage | Key Vault | Container Apps | AKS | Log Analytics | Application Insights | etc.        |
+--------------------------------------------------------------------------------------------------+
```

## Notes

- `GitHub Copilot` is shown as a build-time accelerator for developing the product.
- `Azure MCP` is shown as the Azure-grounded reasoning and template/schema guidance layer.
- `Azure AI Foundry` is shown as the model and agent runtime used by chat, validation, and IaC flows.
- The project canvas state remains the central shared state for the system.
