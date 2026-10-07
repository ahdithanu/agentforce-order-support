"""Regression check for the verification lockout, run live against real Apex.

Usage: python3 scripts/lockout_eval.py <org-alias> [runs=5] > tests/results/lockout-eval.json

Replays three wrong email/order pairs in one chat, several times, because the defect it guards
against is intermittent: the agent replying "those details don't match" WITHOUT calling
verify_customer, which skips the attempt counter and delays the lockout by a turn.

Each run passes only if, on every one of the three attempts:
  - verify_customer was called in that turn, with the values the customer gave
  - the session counter moved 0->1, 1->2, 2->3
  - attempts 1 and 2 reply with the exact fixed failure sentence
and the third failure hands off to the escalation subagent in the same turn.
The demo environment is reset before each run so the per-order lockout can't interfere.
"""
import glob
import json
import os
import re
import subprocess
import sys

BUNDLE = os.environ.get("BUNDLE", "Acme_Order_Support")
ORG = sys.argv[1]
RUNS = int(sys.argv[2]) if len(sys.argv) > 2 else 5
SENTENCE = "Those details don't match our records."
OPENING = "Where is my order?"
ATTEMPTS = [("sam@example.com", "A-1001", "sam@example.com and A-1001"),
            ("jamie@example.org", "A-1001", "Try jamie@example.org and A-1001"),
            ("jamie@example.com", "A-1009", "Okay, jamie@example.com and A-1009")]


def sf(args):
    out = subprocess.run(["sf", *args, "--json"], capture_output=True, text=True).stdout
    return json.loads(re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", out) or "{}")


def turn_facts(trace_file):
    """What actually happened in one turn, from its trace."""
    facts = {"verify_inputs": None, "counter": None, "to": []}
    for st in json.load(open(trace_file)).get("plan", []):
        data = st.get("data") or {}
        if st.get("type") == "FunctionStep":
            fn = st.get("function")
            fn = json.loads(fn) if isinstance(fn, str) else (fn or {})
            if fn.get("name") == "verify_customer":
                facts["verify_inputs"] = fn.get("input") or {}
        elif st.get("type") == "VariableUpdateStep":
            for u in data.get("variable_updates", []):
                if u.get("variable_name") == "failed_attempts":
                    facts["counter"] = [u.get("variable_past_value"), u.get("variable_new_value")]
        elif st.get("type") == "TransitionStep":
            facts["to"].append(data.get("to_agent"))
    return facts


runs = []
for n in range(1, RUNS + 1):
    sf(["apex", "run", "--file", "scripts/reset-env.apex", "-o", ORG])
    start = sf(["agent", "preview", "start", "--authoring-bundle", BUNDLE, "--use-live-actions", "-o", ORG])
    sid = (start.get("result") or {}).get("sessionId")
    if not sid:
        runs.append({"run": n, "pass": False, "error": start.get("message")})
        continue
    replies = []
    for utterance in [OPENING] + [a[2] for a in ATTEMPTS]:
        resp = sf(["agent", "preview", "send", "--session-id", sid, "--authoring-bundle", BUNDLE,
                   "--utterance", utterance, "-o", ORG])
        replies.append(" ".join(m.get("message", "") for m in (resp.get("result") or {}).get("messages") or []))
    end = sf(["agent", "preview", "end", "--session-id", sid, "--authoring-bundle", BUNDLE, "-o", ORG])
    traces = sorted(glob.glob(os.path.join((end.get("result") or {}).get("tracesPath") or "", "traces", "*.json")),
                    key=os.path.getmtime)
    facts = [turn_facts(t) for t in traces]
    checks, problems = [], []
    for i, (email, order, _) in enumerate(ATTEMPTS, start=1):
        f = facts[i] if i < len(facts) else {"verify_inputs": None, "counter": None, "to": []}
        called = f["verify_inputs"] is not None
        right_values = called and str(f["verify_inputs"].get("email", "")).lower() == email \
            and str(f["verify_inputs"].get("orderNumber", "")).upper() == order
        counted = f["counter"] is not None and [str(v).split(".")[0] for v in f["counter"]] == [str(i - 1), str(i)]
        sentence = SENTENCE in replies[i] if i < 3 else None
        handed_off = "escalation" in f["to"] if i == 3 else None
        checks.append({"attempt": i, "verify_called": called, "right_values": right_values, "counter_moved": counted,
                       "fixed_sentence": sentence, "handed_off": handed_off, "reply": replies[i][:160]})
        for label, ok in (("verify not called", called), ("wrong values sent", right_values or not called),
                          ("counter did not move", counted or not called),
                          ("fixed sentence missing", sentence is not False), ("no handoff on third failure", handed_off is not False)):
            if not ok:
                problems.append(f"attempt {i}: {label}")
    runs.append({"run": n, "pass": not problems, "problems": problems, "attempts": checks})

sf(["apex", "run", "--file", "scripts/reset-env.apex", "-o", ORG])
print(json.dumps({"bundle": BUNDLE, "runs": len(runs), "passed": sum(r["pass"] for r in runs), "results": runs}, indent=1))
