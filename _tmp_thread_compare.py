#!/usr/bin/env python3
"""Compare messages on two threads for the same project."""
import sys, os
sys.path.insert(0, "/app")
os.chdir("/app")

from Agents.AzureAIFoundry.foundry_messages import list_thread_messages
from settings_server import load_app_settings

settings = load_app_settings()

ORIGINAL = "thread_gpbHiHQzN2N1c3HadyflcWva"
CURRENT  = "thread_gbARz5jbZWf23tbj77qDjla6"

for label, tid in [("ORIGINAL (user chatted on)", ORIGINAL), ("CURRENT (resolved by backend)", CURRENT)]:
    result = list_thread_messages(settings, thread_id=tid, limit=2400)
    msgs = result.get("messages", [])
    print(f"\n=== {label}: {tid} => {len(msgs)} messages ===")
    for i, m in enumerate(msgs[:30]):
        role = m.get("role", "?")
        content = str(m.get("content", ""))
        preview = content[:140].replace("\n", " ")
        print(f"  [{i:02d}] {role:10s} len={len(content):5d}  {preview}")
    if len(msgs) > 30:
        print(f"  ... and {len(msgs)-30} more")
