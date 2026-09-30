"""Evaluate the Acme policy RAG path end to end through `sf agent preview --use-live-actions`.

Usage: python3 scripts/rag_eval.py <org-alias> > tests/results/rag-eval-run-1.json
Retrieval is read-only, so live actions are safe here. One fresh session per question.

Metrics:
  retrieval_hit    answerable: the knowledge action ran and returned a non-empty summary
  source_hit       answerable: the expected document appears in the citations/summary
  answer_correct   answerable: the reply contains every must_include group
  abstained        unanswerable: the reply declines instead of inventing a policy
  route_correct    route: the first subagent entered matches expect_route
"""
import glob
import json
import os
import re
import subprocess
import sys

import yaml

BUNDLE = "Acme_Order_Support"
ORG = sys.argv[1]
DECLINE = re.compile(r"(don'?t have|do not have|not (?:in|covered|listed|mentioned|included|available in)|couldn'?t find|could not find|no information|isn'?t (?:covered|listed|mentioned)|unable to find|connect you)", re.I)


def sf(args):
    out = subprocess.run(["sf", *args, "--json"], capture_output=True, text=True).stdout
    return json.loads(re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", out) or "{}")


def trace_facts(trace_dir):
    route, knowledge = None, []
    for f in sorted(glob.glob(os.path.join(trace_dir or "", "traces", "*.json")), key=os.path.getmtime):
        for st in json.load(open(f)).get("plan", []):
            t, data = st.get("type"), st.get("data") or {}
            if t == "TransitionStep" and route is None:
                route = data.get("to_agent")
            if t == "FunctionStep":
                fn = st.get("function")
                fn = json.loads(fn) if isinstance(fn, str) else (fn or {})
                if "Knowledge" in str(fn.get("name")) or "search_policies" in str(fn.get("name")):
                    knowledge.append({"latency_ms": int(st.get("executionLatency") or 0),
                                      "output": json.dumps(fn.get("output") or {})[:4000]})
    return route, knowledge


cases = yaml.safe_load(open("tests/rag-eval.yaml"))["cases"]
results = []
for c in cases:
    start = sf(["agent", "preview", "start", "--authoring-bundle", BUNDLE, "--use-live-actions", "-o", ORG])
    sid = (start.get("result") or {}).get("sessionId")
    if not sid:
        results.append({**c, "error": start.get("message")})
        continue
    resp = sf(["agent", "preview", "send", "--session-id", sid, "--authoring-bundle", BUNDLE,
               "--utterance", c["question"], "-o", ORG])
    msgs = (resp.get("result") or {}).get("messages") or []
    reply = " ".join(m.get("message", "") for m in msgs)
    end = sf(["agent", "preview", "end", "--session-id", sid, "--authoring-bundle", BUNDLE, "-o", ORG])
    route, knowledge = trace_facts((end.get("result") or {}).get("tracesPath"))
    r = {"id": c["id"], "kind": c["kind"], "question": c["question"], "reply": reply, "route": route,
         "knowledge_calls": len(knowledge), "knowledge_latency_ms": [k["latency_ms"] for k in knowledge]}
    blob = " ".join(k["output"] for k in knowledge)
    if c["kind"] == "answerable":
        low = reply.lower()
        r["retrieval_hit"] = bool(knowledge) and len(blob) > 60
        r["source_hit"] = c["source"].lower() in blob.lower() or c["source"].replace("_", " ").lower() in blob.lower()
        r["answer_correct"] = all(any(v.lower() in low for v in group) for group in c["must_include"])
    elif c["kind"] == "unanswerable":
        r["abstained"] = bool(DECLINE.search(reply))
    else:
        r["route_correct"] = route == c["expect_route"]
        r["expect_route"] = c["expect_route"]
    results.append(r)

print(json.dumps(results, indent=1))
