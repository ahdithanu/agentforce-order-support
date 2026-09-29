"""Mode C2 red-team: send each security case through `sf agent preview` in a fresh session.

Usage: python3 scripts/redteam_preview.py <org-alias> > tests/results/security-c2-run-1.json
Only the USER turns of a case are sent (history user turns, then the utterance); the live agent
supplies its own replies. Actions are simulated. Collect-first: judging happens afterwards.
"""
import glob
import json
import os
import re
import subprocess
import sys
import time

import yaml

BUNDLE = "Acme_Order_Support"
ORG = sys.argv[1]
SPEC = "tests/Acme_Order_Support-security.yaml"


def sf(args):
    out = subprocess.run(["sf", *args, "--json"], capture_output=True, text=True).stdout
    return json.loads(re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", out) or "{}")


def case_ids(path):
    """SEC-xx id, severity, category, and surface from the comment above each case."""
    meta, pending = [], None
    for line in open(path):
        m = re.match(r"\s*#\s*(SEC-\d+)\s+(\w+)\s+(LLM\d+)\s*\|\s*(.*)", line)
        if m:
            pending = {"id": m.group(1), "severity": m.group(2), "category": m.group(3), "surface": m.group(4).strip()}
        elif line.strip().startswith("- utterance:") and pending:
            meta.append(pending)
            pending = None
    return meta


def trace_summary(trace_dir):
    calls, path, withheld = [], [], set()
    for f in sorted(glob.glob(os.path.join(trace_dir or "", "traces", "*.json")), key=os.path.getmtime):
        for st in json.load(open(f)).get("plan", []):
            t, data = st.get("type"), st.get("data") or {}
            if t == "FunctionStep":
                fn = st.get("function")
                fn = json.loads(fn) if isinstance(fn, str) else (fn or {})
                calls.append(fn.get("name"))
            elif t == "TransitionStep":
                path.append(data.get("to_agent"))
            elif t == "EnabledToolsStep":
                for w in data.get("runtime_withheld_actions") or []:
                    withheld.add(w.get("developer_name"))
    return {"actions": calls, "path": path, "withheld": sorted(withheld)}


spec = yaml.safe_load(open(SPEC))["testCases"]
meta = case_ids(SPEC)
results = []
for case, m in zip(spec, meta):
    turns = [t["message"] for t in case.get("conversationHistory") or [] if t.get("role") == "user"]
    turns.append(case["utterance"])
    start = sf(["agent", "preview", "start", "--authoring-bundle", BUNDLE, "--simulate-actions", "-o", ORG])
    sid = (start.get("result") or {}).get("sessionId")
    if not sid:
        results.append({**m, "error": start.get("message")})
        continue
    convo = []
    for text in turns:
        resp = sf(["agent", "preview", "send", "--session-id", sid, "--authoring-bundle", BUNDLE,
                   "--utterance", text, "-o", ORG])
        msgs = (resp.get("result") or {}).get("messages") or []
        convo.append({"user": text, "agent": " | ".join(x.get("message", "") for x in msgs) or resp.get("message")})
    end = sf(["agent", "preview", "end", "--session-id", sid, "--authoring-bundle", BUNDLE, "-o", ORG])
    results.append({**m, "expectedOutcome": case["expectedOutcome"], "turns": convo,
                    "trace": trace_summary((end.get("result") or {}).get("tracesPath"))})
    time.sleep(1)

print(json.dumps(results, indent=1))
