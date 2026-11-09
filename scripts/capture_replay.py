"""Record demo conversations against the agent (live actions) for the auto-playing replay page.

Usage: python3 scripts/capture_replay.py <org-alias> > docs/demo/transcripts.json
Run scripts/seed-acme-orders.apex first so order dates are current, and scripts/reset-env.apex
after, since these runs open real Cases and count failed verifications.

For every turn it keeps the customer message, the agent's reply, and what actually ran (from the
trace): the subagent, each action with the few output fields worth showing, and a handoff.
Nothing is rewritten except that the preview's stand-in text for a handoff is dropped, because the
handoff itself is shown as an event.
"""
import glob
import json
import os
import re
import subprocess
import sys
from datetime import date

BUNDLE = os.environ.get("BUNDLE", "Acme_Order_Support")
ORG = sys.argv[1]
SCENARIOS = [
    {"id": "status", "title": "Where is my order?", "note": "Verifies first, then reads the real Order record.",
     "turns": ["Hi, where is my order?", "jamie@example.com and A-1001"]},
    {"id": "case", "title": "Damaged item to a Case", "note": "Confirms before it writes anything.",
     "turns": ["The jacket from my order arrived torn", "jamie@example.com, order A-1001",
               "The left sleeve seam is ripped open. Please open a case.", "Yes, go ahead", "Yes"]},
    {"id": "return", "title": "Return, decided by a Flow", "note": "The return policy is enforced in a Flow, not the prompt.",
     "turns": ["I'd like to return my order", "sam@example.com and A-2001", "The boots are too small",
               "Yes, please start the return"]},
    {"id": "lockout", "title": "Three wrong attempts", "note": "Every attempt is checked in Apex; the third hands off.",
     "turns": ["Where is my order?", "sam@example.com and A-1001", "Try jamie@example.org and A-1001",
               "Okay, jamie@example.com and A-1009"]},
    {"id": "policy", "title": "Policy questions", "note": "Answered from the policy documents, or declined.",
     "turns": ["Do you ship to Canada?", "Do you ship to Australia?"]},
    {"id": "spanish", "title": "In Spanish", "note": "Replies in the customer's language.",
     "turns": ["¿Hacen envíos a Canadá?"]},
]
SHOW = {  # action -> output fields worth showing on the replay
    "verify_customer": ["verified", "failedAttempts"],
    "get_order_status": ["status", "carrier", "trackingNumber", "estimatedDelivery"],
    "create_support_case": ["caseNumber"],
    "start_return_flow": ["eligible", "returnCaseNumber"],
    "record_handoff_context": ["caseNumber"],
}
HANDOFF_STANDIN = re.compile(r"^\s*User requested escalation to human\.?\s*\|?\s*$", re.I)


def sf(args):
    out = subprocess.run(["sf", *args, "--json"], capture_output=True, text=True).stdout
    return json.loads(re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", out) or "{}")


def turn_facts(trace_file):
    subagent, actions, handoff = None, [], False
    for st in json.load(open(trace_file)).get("plan", []):
        data = st.get("data") or {}
        if st.get("type") == "TransitionStep":
            to = data.get("to_agent")
            if to == "__human__":
                handoff = True
            elif to:
                subagent = to
        elif st.get("type") == "FunctionStep":
            fn = st.get("function")
            fn = json.loads(fn) if isinstance(fn, str) else (fn or {})
            name = fn.get("name") or ""
            out = fn.get("output") or {}
            if "Knowledge" in name:
                actions.append({"name": "search_policies", "out": {}})
            elif name in SHOW:
                actions.append({"name": name, "out": {k: out[k] for k in SHOW[name] if out.get(k) not in (None, "")}})
    return {"subagent": subagent, "actions": actions, "handoff": handoff}


ONLY = sys.argv[2:]  # optional scenario ids to record
recorded = []
for sc in SCENARIOS:
    if ONLY and sc["id"] not in ONLY:
        continue
    start = sf(["agent", "preview", "start", "--authoring-bundle", BUNDLE, "--use-live-actions", "-o", ORG])
    sid = (start.get("result") or {}).get("sessionId")
    if not sid:
        raise SystemExit(f"{sc['id']}: {start.get('message')}")
    replies = []
    for utterance in sc["turns"]:
        resp = sf(["agent", "preview", "send", "--session-id", sid, "--authoring-bundle", BUNDLE,
                   "--utterance", utterance, "-o", ORG])
        replies.append([m.get("message", "") for m in (resp.get("result") or {}).get("messages") or []])
    end = sf(["agent", "preview", "end", "--session-id", sid, "--authoring-bundle", BUNDLE, "-o", ORG])
    traces = sorted(glob.glob(os.path.join((end.get("result") or {}).get("tracesPath") or "", "traces", "*.json")),
                    key=os.path.getmtime)
    turns = []
    for i, utterance in enumerate(sc["turns"]):
        facts = turn_facts(traces[i]) if i < len(traces) else {"subagent": None, "actions": [], "handoff": False}
        agent = [m for m in replies[i] if m and not HANDOFF_STANDIN.match(m)]
        turns.append({"user": utterance, "agent": agent, **facts})
    recorded.append({k: sc[k] for k in ("id", "title", "note")} | {"turns": turns})
    print(f"recorded {sc['id']}", file=sys.stderr)

print(json.dumps({"recorded": date.today().isoformat(), "agent": BUNDLE, "scenarios": recorded}, indent=1, ensure_ascii=False))
