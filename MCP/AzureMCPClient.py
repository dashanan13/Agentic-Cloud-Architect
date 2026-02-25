import asyncio
import os
import sys
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

async def run_azure_mcp():
    # Use 'npx' but redirect stderr to dev/null to stop 
    # non-JSON warnings from confusing the parser
    server_params = StdioServerParameters(
        command="npx",
        args=["-y", "@azure/mcp@latest", "server", "start"],
        env=os.environ.copy()
    )

    print("--- Connecting to Azure MCP Server ---")
    
    try:
        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()

                target_tool = "group_list"
                print(f"Executing: {target_tool}...")
                
                result = await session.call_tool(target_tool, arguments={})
                
                print("\n--- Azure Response (Formatted) ---")
                # The response is a JSON string in result.content[0].text
                import json
                data = json.loads(result.content[0].text)
                
                # Print just the names for clarity
                groups = data.get("results", {}).get("groups", [])
                for g in groups:
                    print(f" - {g['name']} ({g['location']})")
                
                print(f"\nTotal Groups found: {len(groups)}")

    except Exception as e:
        # If the server sends non-JSON data, we catch it here
        if "validation error" in str(e).lower():
            pass # Skip the headless display warnings
        else:
            print(f"\nError: {e}")

if __name__ == "__main__":
    try:
        asyncio.run(run_azure_mcp())
    except KeyboardInterrupt:
        pass
    except RuntimeError as e:
        if "Event loop is closed" not in str(e):
            raise