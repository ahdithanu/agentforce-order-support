"""Score the agent's router on tests/routing-set.yaml via preview (simulated actions).

Usage: python3 scripts/routing_eval.py <org-alias> <label> > tests/results/routing-<label>.json
Records the first subagent transitioned to and the routing latency (user message -> first
transition) from the local trace. One fresh session per utterance.
"""
import glob
import json
import os
import re
import subprocess
import sys

import yaml

BUNDLE = "Acme_Order_Support"
ORG, LABEL = sys.argv[1], sys.argv[2]


def sf(args):
    out = subprocess.run(["sf", *args, "--json"], capture_output=True, text=True).stdout
    return json.loads(re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", out) or "{}")


def first_route(trace_dir):
    for f in sorted(glob.glob(os.path.join(trace_dir or "", "traces", "*.json")), key=os.path.getmtime):
        plan = json.load(open(f)).get("plan", [])
        start = next((int(s["startExecutionTime"]) for s in plan if s.get("type") == "UserInputStep"), None)
        for s in plan:
            if s.get("type") == "TransitionStep":
                return (s.get("data") or {}).get("to_agent"), (int(s["startExecutionTime"]) - start) if start else None
            if s.get("type") == "PlannerResponseStep":
                return "(answered in router)", None
    return None, None


items = yaml.safe_load(open("tests/routing-set.yaml"))["utterances"]
rows = []
for it in items:
    start = sf(["agent", "preview", "start", "--authoring-bundle", BUNDLE, "--simulate-actions", "-o", ORG])
    sid = (start.get("result") or {}).get("sessionId")
    if not sid:
        rows.append({**it, "error": start.get("message")})
        continue
    sf(["agent", "preview", "send", "--session-id", sid, "--authoring-bundle", BUNDLE, "--utterance", it["u"], "-o", ORG])
    end = sf(["agent", "preview", "end", "--session-id", sid, "--authoring-bundle", BUNDLE, "-o", ORG])
    route, ms = first_route((end.get("result") or {}).get("tracesPath"))
    rows.append({"u": it["u"], "expected": it["route"], "actual": route, "correct": route == it["route"],
                 "near_miss": bool(it.get("near_miss")), "routing_ms": ms})
print(json.dumps({"label": LABEL, "rows": rows}, indent=1))
