import json
import time
import urllib.request
from datetime import datetime

URL = "http://localhost:3000/api/chat/architecture"
PROJECT_ID = "Azure-proj-20260306-0006-20260307000635641"

CASES = [
    ("on-topic", "simple", "what is Azure Front Door used for?"),
    ("on-topic", "simple", "difference between Azure SQL and Cosmos DB?"),
    ("on-topic", "simple", "can i use app gateway with front door?"),
    ("on-topic", "simple", "when should i use private endpoints?"),
    ("on-topic", "simple", "is service bus better than event hubs for commands?"),
    ("on-topic", "deep-dive", "please review my architecture and tell me what is missing"),
    ("on-topic", "deep-dive", "analyze my canvas for security gaps"),
    ("on-topic", "deep-dive", "what should i add to improve reliability for this design?"),
    ("on-topic", "canvas", "do you see resources on my canvas?"),
    ("on-topic", "canvas", "what did we decide so far about this project?"),
    ("middle", "ambiguous", "should i host this api on app service or functions?"),
    ("middle", "ambiguous", "we need this fast and cheap, what architecture direction do you suggest?"),
    ("middle", "ambiguous", "can you help me design the backend if users are global?"),
    ("middle", "ambiguous", "is aws better than azure for this use case?"),
    ("middle", "ambiguous", "i need guidance but i am not sure what to ask first"),
    ("off-topic", "non-azure", "so do you know about the war in iran"),
    ("off-topic", "non-azure", "write me a python quicksort function"),
    ("off-topic", "non-azure", "give me a recipe for pasta"),
    ("off-topic", "non-azure", "who will win the next world cup?"),
    ("off-topic", "non-azure", "tell me a joke about cats"),
]


def call(message: str) -> dict:
    payload = json.dumps({"message": message, "projectId": PROJECT_ID}).encode()
    req = urllib.request.Request(URL, data=payload, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=150) as resp:
        return json.loads(resp.read().decode())


results = []
for idx, (bucket, kind, prompt) in enumerate(CASES, 1):
    started = time.time()
    try:
        out = call(prompt)
        msg = str(out.get("message", "")).strip()
        flat = " ".join(msg.split())
        meta = out.get("meta", {}) if isinstance(out, dict) else {}
        intent = meta.get("intent")
        foundry = bool(((meta.get("connections") or {}).get("azureFoundry") or {}).get("connected"))
        used_tool = bool(meta.get("tool"))
        chars = len(flat)

        has_refusal = "cannot assist" in flat.lower() or "can't assist" in flat.lower()
        mentions_azure_arch = any(
            token in flat.lower()
            for token in ["azure", "architecture", "canvas", "front door", "app gateway", "vnet", "subnet"]
        )

        if bucket == "off-topic":
            expected = "redirect"
            passed = (not foundry) and (chars < 260) and (not has_refusal)
        elif kind == "deep-dive":
            expected = "deep-dive"
            passed = (intent == "architecture") and foundry and (chars > 500)
        elif kind == "simple":
            expected = "concise"
            passed = (intent == "conversational") and foundry and (chars <= 360)
        elif kind == "canvas":
            expected = "canvas-aware"
            passed = foundry and mentions_azure_arch and (chars <= 650)
        else:
            expected = "balanced"
            passed = (intent in ("conversational", "architecture")) and (chars <= 900)

        results.append(
            {
                "id": idx,
                "bucket": bucket,
                "kind": kind,
                "prompt": prompt,
                "expected": expected,
                "pass": bool(passed),
                "intent": intent,
                "foundry": foundry,
                "tool": used_tool,
                "chars": chars,
                "preview": flat[:220],
                "ms": int((time.time() - started) * 1000),
            }
        )
    except Exception as exc:
        results.append(
            {
                "id": idx,
                "bucket": bucket,
                "kind": kind,
                "prompt": prompt,
                "expected": "n/a",
                "pass": False,
                "intent": "error",
                "foundry": False,
                "tool": False,
                "chars": 0,
                "preview": f"ERROR: {exc}",
                "ms": int((time.time() - started) * 1000),
            }
        )

summary = {
    "timestamp": datetime.utcnow().isoformat() + "Z",
    "total": len(results),
    "passed": sum(1 for r in results if r["pass"]),
    "failed": sum(1 for r in results if not r["pass"]),
    "by_bucket": {},
}

for bucket in ("on-topic", "middle", "off-topic"):
    subset = [r for r in results if r["bucket"] == bucket]
    summary["by_bucket"][bucket] = {
        "total": len(subset),
        "passed": sum(1 for r in subset if r["pass"]),
        "failed": sum(1 for r in subset if not r["pass"]),
    }

report = {"summary": summary, "results": results}
with open("/tmp/chat_spectrum_results.json", "w", encoding="utf-8") as fh:
    json.dump(report, fh, indent=2)

print("Spectrum test complete")
print(json.dumps(summary, indent=2))
print("\nFailures:")
for row in results:
    if not row["pass"]:
        print(
            f"- #{row['id']} [{row['bucket']}/{row['kind']}] "
            f"chars={row['chars']} intent={row['intent']} foundry={row['foundry']} :: {row['prompt']}"
        )
print("\nSaved: /tmp/chat_spectrum_results.json")
