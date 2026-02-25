# Azure MCP Server: Python Client Setup

This repository contains a Python implementation for interacting with the official **Azure Model Context Protocol (MCP)** server. It allows AI agents or local scripts to manage Azure resources (Compute, Storage, Resource Groups, etc.) via a standardized JSON-RPC interface.

## 🏗 Architecture

The setup consists of:

1. **Host**: A Python script using the `mcp` SDK.
2. **Server**: The official `@azure/mcp` Node.js server.
3. **Transport**: Standard Input/Output (stdio) for communication.

---

## 🚀 Quick Start

### 1. Prerequisites

* **Ubuntu 22.04+** (or equivalent container)
* **Python 3.10+**
* **Node.js v20+** (Required by Azure MCP)

### 2. Environment Setup

Update your system and install the required Node.js version to avoid engine conflicts.

```bash
# Update Node.js to v20
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt-get install -y nodejs

# Setup Python environment
python3 -m venv .venv
source .venv/bin/activate
pip install mcp pydantic

```

### 3. Install Azure MCP Server

If you are on an **ARM64** architecture (e.g., Apple Silicon), you must install the platform-specific bridge explicitly.

```bash
npm install @azure/mcp@latest @azure/mcp-linux-arm64

```

### 4. Authentication

Create an **Azure App Registration** (Service Principal) and grant it the **Reader** or **Contributor** role at the Subscription level. Export the following variables:

```bash
export AZURE_TENANT_ID="your-tenant-id"
export AZURE_CLIENT_ID="your-client-id"
export AZURE_CLIENT_SECRET="your-client-secret"
export AZURE_SUBSCRIPTION_ID="your-subscription-id"

```

---

## � Detailed Authentication Setup

To get your Azure MCP server talking to your subscription, you need to bridge the gap between your local environment and Azure's Identity platform. This is done through an **App Registration**, which acts as a "service account" for your script.

Here are the step-by-step instructions for the Azure Portal and the terminal.

### Part 1: Azure Portal Configuration

#### 1. Create the App Registration

1. Log in to the [Azure Portal](https://portal.azure.com).
2. Search for **App registrations** in the top search bar.
3. Click **+ New registration**.
   * **Name:** `mcp-azure-server` (or similar).
   * **Supported account types:** Accounts in this organizational directory only (Single tenant).
   * **Redirect URI:** Leave blank.

4. Click **Register**.

#### 2. Generate the Client Secret

1. On the Overview page, look for the **Client ID** and **Tenant ID**. **Copy these now.**
2. In the left menu, click **Certificates & secrets**.
3. Click **+ New client secret**.
   * **Description:** `MCP-Key`
   * **Expires:** 180 days (standard).

4. Click **Add**.
5. **⚠️ IMPORTANT:** Copy the **Value** column immediately. You will never see this clear-text value again after you leave this screen.

#### 3. Assign Permissions (RBAC)

The App Registration is now a "user" without any rights. You must give it permission to see your resources.

1. Search for **Subscriptions** in the portal.
2. Select your active Subscription.
3. Click **Access Control (IAM)** in the left sidebar.
4. Click **+ Add** -> **Add role assignment**.
5. **Role:** Select `Reader` (to list things) or `Contributor` (if you want the MCP server to create/delete resources).
6. **Members:** Click **+ Select members** and search for the name of the App Registration you created in Step 1.
7. Click **Review + assign**.

### Part 2: Environment Variables

In your Ubuntu container, the Azure MCP server uses the `DefaultAzureCredential` library. It automatically looks for specific environment variable names.

#### 1. The Standard Variables

Run these commands in your terminal (replace the values with your actual IDs):

```bash
# The ID of your Azure Active Directory
export AZURE_TENANT_ID="00000000-0000-0000-0000-000000000000"

# The Application (client) ID from the App Registration
export AZURE_CLIENT_ID="00000000-0000-0000-0000-000000000000"

# The Secret VALUE you generated
export AZURE_CLIENT_SECRET="your-secret-value-here"

# The Subscription ID where your resources live
export AZURE_SUBSCRIPTION_ID="00000000-0000-0000-0000-000000000000"
```

#### 2. Automating with a `.env` file

Instead of typing these every time you open a new terminal, create a `.env` file in your `azure-mcp-python` folder:

```bash
cat <<EOF > .env
AZURE_TENANT_ID="xxx"
AZURE_CLIENT_ID="xxx"
AZURE_CLIENT_SECRET="xxx"
AZURE_SUBSCRIPTION_ID="xxx"
EOF
```

Then, before running your script, load them:

```bash
export $(xargs < .env)
```

### Credential Mapping Reference

| Variable | Source in Azure Portal | Purpose |
| --- | --- | --- |
| `AZURE_TENANT_ID` | App Registration -> Overview | Identifies your organization. |
| `AZURE_CLIENT_ID` | App Registration -> Overview | The "Username" for the server. |
| `AZURE_CLIENT_SECRET` | App Registration -> Certificates & Secrets | The "Password" for the server. |
| `AZURE_SUBSCRIPTION_ID` | Subscriptions -> Overview | Tells MCP which subscription to manage. |

---

## �💻 Implementation (`client.py`)

The following script connects to the server and lists all Resource Groups. It includes logic to handle "Headless mode" warnings often found in Docker/VSCode containers.

```python
import asyncio
import os
import json
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

async def main():
    # Configure the server path
    server_params = StdioServerParameters(
        command="npx",
        args=["-y", "@azure/mcp@latest", "server", "start"],
        env=os.environ.copy()
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            # The official Azure tool name for listing groups
            tool_name = "group_list"
            
            try:
                result = await session.call_tool(tool_name, arguments={})
                data = json.loads(result.content[0].text)
                
                groups = data.get("results", {}).get("groups", [])
                print(f"Successfully retrieved {len(groups)} Resource Groups.")
                
            except Exception as e:
                print(f"Error calling {tool_name}: {e}")

if __name__ == "__main__":
    asyncio.run(main())

```

---

## 🛠 Troubleshooting

| Issue | Cause | Solution |
| --- | --- | --- |
| `Unsupported engine` | Node.js version < 20 | Upgrade Node using the NodeSource script provided above. |
| `Missing required package` | ARM64 architecture | Run `npm install @azure/mcp-linux-arm64`. |
| `Invalid JSONRPCMessage` | Headless display warnings | System warnings (e.g., "No X display") are polluting stdout. The client will usually ignore these and still function, but you can redirect stderr to `/dev/null`. |
| `Tool not found` | Namespace mismatch | Use `session.list_tools()` to print available tools. Common names use underscores (e.g., `group_list`). |

---

## 📖 License

This project is for demonstration purposes. Azure MCP is maintained by Microsoft.
