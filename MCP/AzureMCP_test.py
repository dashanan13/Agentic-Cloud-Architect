"""
Azure MCP Explorer
==================
Connects to the Azure MCP Server, introspects every tool and its sub-commands
via the "learn" mechanism, then writes a structured Markdown reference doc.
Also runs a set of real example calls and appends them to the doc.

Usage:
    python azure_mcp_explorer.py

Requires .env (or exported env vars):
    AZURE_TENANT_ID, AZURE_CLIENT_ID, AZURE_CLIENT_SECRET
    AZURE_SUBSCRIPTION_ID  (recommended)
    OUTPUT_PATH            (optional, default: azure_mcp_reference.md)

Dependencies:
    pip install mcp azure-identity python-dotenv
Node.js LTS must be installed (MCP server runs via npx).
"""

import asyncio
import json
import logging
import os
import re
import sys
import textwrap
import warnings
from datetime import datetime, timezone
from pathlib import Path

from azure.identity import ClientSecretCredential
from dotenv import load_dotenv
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

# ── Silence noisy but harmless warnings ────────────────────────────────────────
# 1. asyncio "Event loop is closed" on subprocess cleanup at exit
warnings.filterwarnings("ignore", category=ResourceWarning)

# 2. MCP JSONRPC parse errors from non-JSON lines (e.g. display-mode messages)
#    We silence only the mcp.client.stdio logger, not everything.
logging.getLogger("mcp.client.stdio").setLevel(logging.CRITICAL)
logging.getLogger("mcp").setLevel(logging.CRITICAL)
logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# ── Config ─────────────────────────────────────────────────────────────────────
load_dotenv()

TENANT_ID       = os.environ["AZURE_TENANT_ID"]
CLIENT_ID       = os.environ["AZURE_CLIENT_ID"]
CLIENT_SECRET   = os.environ["AZURE_CLIENT_SECRET"]
SUBSCRIPTION_ID = os.getenv("AZURE_SUBSCRIPTION_ID", "")
OUTPUT_FILE     = Path(os.getenv("OUTPUT_PATH", "azure_mcp_reference.md"))


# ── Helpers ────────────────────────────────────────────────────────────────────

def _text(content) -> str:
    """Extract plain text from MCP content list."""
    if isinstance(content, list):
        return "\n".join(getattr(i, "text", str(i)) for i in content)
    return str(content)


def _try_json(raw: str):
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None


def _json_block(obj) -> str:
    return "```json\n" + json.dumps(obj, indent=2) + "\n```"


def _anchor(name: str) -> str:
    return re.sub(r"[^a-z0-9-]", "-", name.lower()).strip("-")


async def call_tool(session: ClientSession, tool: str, args: dict) -> str:
    result = await session.call_tool(tool, args)
    return _text(result.content)


async def learn_tool(session: ClientSession, tool: str, subtool: str = "") -> str:
    args = {"command": "learn"}
    if subtool:
        args["tool"] = subtool
    return await call_tool(session, tool, args)


# ── Parse "learn" output ───────────────────────────────────────────────────────

def parse_learn_output(raw: str) -> list[dict]:
    """
    Parse the free-text or JSON output of a `learn` call into a list:
      [{ "name": str, "description": str, "parameters": [...] }]
    """
    parsed = _try_json(raw)
    if isinstance(parsed, list):
        return parsed
    if isinstance(parsed, dict) and "tools" in parsed:
        return parsed["tools"]

    sub_commands: list[dict] = []
    current: dict | None = None

    for line in raw.splitlines():
        line = line.rstrip()
        # Subcommand heading: "  some-command - Description text"
        m = re.match(r"^\s{0,4}([a-z][a-z0-9_-]+)\s*[-–]\s*(.+)$", line)
        if m and not line.strip().startswith("-"):
            if current:
                sub_commands.append(current)
            current = {
                "name": m.group(1),
                "description": m.group(2).strip(),
                "parameters": [],
            }
        elif current and re.match(r"^\s+[-*]\s+", line):
            param_text = re.sub(r"^\s+[-*]\s+", "", line)
            required   = "(required)" in param_text.lower()
            pname_m    = re.match(r"([A-Za-z_][A-Za-z0-9_-]*)", param_text)
            pname      = pname_m.group(1) if pname_m else param_text[:30]
            current["parameters"].append({
                "name": pname,
                "required": required,
                "description": param_text,
            })

    if current:
        sub_commands.append(current)
    return sub_commands


# ── Markdown builders ──────────────────────────────────────────────────────────

def md_tool_section(tool_name: str, learn_raw: str, tool_description: str = "") -> str:
    lines: list[str] = []
    lines.append(f"\n## `{tool_name}`")
    lines.append("")
    if tool_description:
        lines.append(tool_description)
        lines.append("")

    sub_commands = parse_learn_output(learn_raw)

    if not sub_commands:
        # Tool returned free-form text — preserve it verbatim
        lines.append("**Learn output:**")
        lines.append("")
        lines.append("```")
        lines.append(learn_raw.strip())
        lines.append("```")
        lines.append("")
        return "\n".join(lines)

    # ── Sub-commands summary table ─────────────────────────────────────────────
    lines.append("### Sub-commands")
    lines.append("")
    lines.append("| Command | Description |")
    lines.append("|---------|-------------|")
    for sc in sub_commands:
        name = f"`{sc.get('name', '')}`"
        desc = sc.get("description", "").replace("|", "\\|")
        lines.append(f"| {name} | {desc} |")
    lines.append("")

    # ── Per-subcommand detail ──────────────────────────────────────────────────
    for sc in sub_commands:
        sc_name = sc.get("name", "")
        lines.append(f"#### `{sc_name}`")
        lines.append("")
        desc = sc.get("description", "")
        if desc:
            lines.append(desc)
            lines.append("")

        params = sc.get("parameters", [])
        if params:
            lines.append("**Parameters**")
            lines.append("")
            lines.append("| Parameter | Required | Description |")
            lines.append("|-----------|:--------:|-------------|")
            for p in params:
                req_icon = "✅" if p.get("required") else "⬜"
                pdesc    = str(p.get("description", "")).replace("|", "\\|")
                lines.append(f"| `{p['name']}` | {req_icon} | {pdesc} |")
            lines.append("")

        # Build a minimal illustrative example call
        example_args: dict = {"command": sc_name}
        for p in params:
            if p.get("required"):
                pn = p["name"]
                if re.search(r"subscri", pn, re.I):
                    example_args[pn] = SUBSCRIPTION_ID or "<subscription-id>"
                elif re.search(r"resource.?group|rg", pn, re.I):
                    example_args[pn] = "<resource-group-name>"
                else:
                    example_args[pn] = f"<{pn}>"

        lines.append("**Example call**")
        lines.append("")
        lines.append("```python")
        lines.append(f"result = await call_tool(session, \"{tool_name}\",")
        # Pretty-indent the args dict
        args_str = json.dumps(example_args, indent=4)
        lines.append(f"    {args_str})")
        lines.append("```")
        lines.append("")

    return "\n".join(lines)


def md_examples_section(examples: list[dict]) -> str:
    lines: list[str] = [
        "",
        "---",
        "",
        "# Live Example Calls",
        "",
        "_These calls were executed against a real Azure subscription during script run._",
        "",
    ]
    for ex in examples:
        lines.append(f"## {ex['title']}")
        lines.append("")
        lines.append(f"**Tool:** `{ex['tool']}`")
        lines.append("")
        lines.append("**Args:**")
        lines.append("")
        lines.append(_json_block(ex["args"]))
        lines.append("")
        lines.append("**Result:**")
        lines.append("")
        result_obj = _try_json(ex["result"])
        if result_obj:
            lines.append(_json_block(result_obj))
        else:
            lines.append("```")
            lines.append(ex["result"].strip())
            lines.append("```")
        lines.append("")
        lines.append("---")
        lines.append("")
    return "\n".join(lines)


# ── Main ───────────────────────────────────────────────────────────────────────

async def _main():
    # Verify Service Principal credentials
    credential = ClientSecretCredential(
        tenant_id=TENANT_ID, client_id=CLIENT_ID, client_secret=CLIENT_SECRET
    )
    token = credential.get_token("https://management.azure.com/.default")
    print(f"✅ Credentials OK (token expires {token.expires_on})")

    mcp_env = {
        **os.environ,
        "AZURE_CLIENT_ID":     CLIENT_ID,
        "AZURE_CLIENT_SECRET": CLIENT_SECRET,
        "AZURE_TENANT_ID":     TENANT_ID,
    }

    server_params = StdioServerParameters(
        command="npx",
        args=["-y", "@azure/mcp@latest", "server", "start"],
        env=mcp_env,
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            print("✅ MCP session ready")

            tools_response = await session.list_tools()
            tools      = tools_response.tools
            tool_names = [t.name for t in tools]
            tool_desc  = {t.name: (t.description or "") for t in tools}
            print(f"   Found {len(tool_names)} tools\n")

            # ── Phase 1: Introspect all tools ──────────────────────────────
            print("📖 Introspecting tools...")
            tool_sections: list[str] = []
            for i, name in enumerate(tool_names):
                print(f"   [{i+1:02d}/{len(tool_names)}] {name} ...", end="", flush=True)
                try:
                    raw     = await learn_tool(session, name)
                    section = md_tool_section(name, raw, tool_desc[name])
                    print(" ✓")
                except Exception as e:
                    print(f" ✗ ({e})")
                    section = f"\n## `{name}`\n\n_Could not introspect: {e}_\n"
                tool_sections.append(section)

            # ── Phase 2: Live example calls ────────────────────────────────
            print("\n🚀 Running live example calls...")
            examples: list[dict] = []

            async def run_example(title: str, tool: str, args: dict):
                print(f"   • {title} ...", end="", flush=True)
                try:
                    result = await call_tool(session, tool, args)
                    examples.append({"title": title, "tool": tool, "args": args, "result": result})
                    print(" ✓")
                except Exception as e:
                    print(f" ✗ ({e})")
                    examples.append({"title": title, "tool": tool, "args": args, "result": f"Error: {e}"})

            sub = {"subscription": SUBSCRIPTION_ID} if SUBSCRIPTION_ID else {}

            await run_example("List Subscriptions",          "subscription_list", {})
            await run_example("List Resource Groups",        "group_list",        {"command": "list",         **sub})
            await run_example("List App Service Web Apps",   "appservice",        {"command": "list",         **sub})
            await run_example("List Function Apps",          "functionapp",       {"command": "list",         **sub})
            await run_example("List Key Vaults",             "keyvault",          {"command": "list",         **sub})
            await run_example("List Storage Accounts",       "storage",           {"command": "account-list", **sub})
            await run_example("List AKS Clusters",           "aks",               {"command": "list",         **sub})
            await run_example("Get Advisor Recommendations", "advisor",           {"command": "list",         **sub})
            await run_example("List Cosmos DB Accounts",     "cosmos",            {"command": "account-list", **sub})
            await run_example("List SQL Servers",            "sql",               {"command": "server-list",  **sub})

            # ── Phase 3: Assemble Markdown ─────────────────────────────────
            print("\n📝 Assembling Markdown...")
            now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

            # Table of contents
            toc_lines = ["## Table of Contents", "", "### Tools", ""]
            for name in tool_names:
                toc_lines.append(f"- [`{name}`](#{_anchor(name)})")
            toc_lines += ["", "### Live Examples", ""]
            for ex in examples:
                toc_lines.append(f"- [{ex['title']}](#{_anchor(ex['title'])})")
            toc = "\n".join(toc_lines)

            header = textwrap.dedent(f"""\
                # Azure MCP Server — Tool Reference

                > **Generated:** {now}  
                > **Tools discovered:** {len(tool_names)}  
                > **Subscription:** `{SUBSCRIPTION_ID or "not set"}`  
                > **Source:** `@azure/mcp@latest`

                This document was auto-generated by introspecting every tool exposed by the
                Azure MCP Server using the built-in `learn` mechanism, then running a set of
                live calls against a real Azure subscription.

                Each tool section contains:
                - A summary table of available sub-commands
                - Per-subcommand parameter tables (✅ required / ⬜ optional)
                - A ready-to-copy Python example call

                ---

            """)

            full_doc = (
                header
                + toc
                + "\n\n---\n\n# Tool Reference\n"
                + "".join(tool_sections)
                + md_examples_section(examples)
            )

            OUTPUT_FILE.write_text(full_doc, encoding="utf-8")
            size_kb = OUTPUT_FILE.stat().st_size // 1024
            lines   = len(full_doc.splitlines())
            print(f"\n✅ Written → {OUTPUT_FILE.resolve()}")
            print(f"   {size_kb} KB  |  {lines:,} lines  |  {len(tool_sections)} tool sections  |  {len(examples)} examples")


def main():
    # Use a fresh event loop and close it cleanly to suppress ResourceWarning
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(_main())
    except KeyboardInterrupt:
        print("\n⚠️  Interrupted")
        sys.exit(1)
    finally:
        # Drain pending tasks so subprocess transports close cleanly
        pending = asyncio.all_tasks(loop)
        if pending:
            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        loop.close()


if __name__ == "__main__":
    main()