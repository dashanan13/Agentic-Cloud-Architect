"""
Post-install compatibility patch for agent-framework-azure-ai 1.0.0rc6.

agent-framework-core 1.0.0 (stable) renamed / removed several symbols that
agent-framework-azure-ai 1.0.0rc6 still references.  This script patches the
installed package in-place so all three breakages are resolved:

  1. BaseContextProvider  -> ContextProvider
  2. OpenAIResponsesOptions -> OpenAIChatOptions
  3. agent_framework_openai._assistants_client (module removed) -> stub
  4. agent_framework_openai._shared.OpenAIBase (class removed) -> stub
  5. ChatResponseUpdate kwarg model_id= -> model= (_chat_client.py line ~1016)

Run once after pip install, before the application starts.
"""
import pathlib

SITE = pathlib.Path("/usr/local/lib/python3.12/site-packages")

# --------------------------------------------------------------------------- #
# 1. Stub the removed _assistants_client module
# --------------------------------------------------------------------------- #
stub_client = SITE / "agent_framework_openai/_assistants_client.py"
stub_client.write_text(
    "# Compatibility stub — OpenAI Assistants API removed in stable release\n"
    "from typing import Any\n\n"
    "class OpenAIAssistantsClient:\n"
    "    def __class_getitem__(cls, item: Any) -> Any:\n"
    "        return cls\n\n"
    "class OpenAIAssistantsOptions:\n"
    "    def __class_getitem__(cls, item: Any) -> Any:\n"
    "        return cls\n"
)

# --------------------------------------------------------------------------- #
# 2. Add removed OpenAIBase class to _shared.py
# --------------------------------------------------------------------------- #
shared = SITE / "agent_framework_openai/_shared.py"
content = shared.read_text()
if "class OpenAIBase" not in content:
    shared.write_text(content + "\n# Compatibility stub\nclass OpenAIBase:\n    pass\n")

# --------------------------------------------------------------------------- #
# 3. Fix renamed symbols across the azure-ai package source files
# --------------------------------------------------------------------------- #
az_pkg = SITE / "agent_framework_azure_ai"
for src_file in az_pkg.glob("*.py"):
    text = src_file.read_text()
    patched = (
        text
        .replace("BaseContextProvider", "ContextProvider")
        .replace("OpenAIResponsesOptions", "OpenAIChatOptions")
        .replace("model_id=event_data.model", "model=event_data.model")
    )
    if patched != text:
        src_file.write_text(patched)

# --------------------------------------------------------------------------- #
# 4. Remove stale bytecode so Python recompiles from patched source
# --------------------------------------------------------------------------- #
for pyc in (az_pkg / "__pycache__").glob("*.pyc"):
    pyc.unlink()

print("agent-framework-azure-ai patched OK")
