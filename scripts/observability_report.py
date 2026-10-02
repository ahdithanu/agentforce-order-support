"""Agent observability from Agentforce session tracing (Data Cloud STDM objects).

Usage: python3 scripts/observability_report.py <org-alias>
Reads ssot__AiAgentSession/Interaction/InteractionStep via the Data Cloud SQL API (through
`sf api request rest`, so no token handling here), keeps sessions that touched an
Acme_Order_Support topic, and splits them by where the traffic came from:
  published  - topic suffix is a published planner id (real channels, Testing Center)
  preview    - topic suffix is PlannerPreview-<uuid> (developer preview and eval runs)
Writes tests/results/observability.json (aggregates only; no conversation text).
"""
import json
import re
import statistics
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict
from datetime import datetime

ORG = sys.argv[1]
ACME_TOPICS = {"identity_verification", "order_status", "support_case", "store_policies", "escalation"}
GENERIC = {"off_topic", "ambiguous_question", "Prompt_Injection", "NOT_SET"}


def _rest(*rest_args):
    out = subprocess.run(["sf", "api", "request", "rest", *rest_args, "--target-org", ORG],
                         capture_output=True, text=True).stdout
    return json.JSONDecoder().raw_decode(out[out.find("{"):])[0]


def sql(query):
    """Run a Data Cloud SQL query and page through every row (responses are capped by size)."""
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
        json.dump({"sql": query}, fh)
        body = fh.name
    d = _rest("/services/data/v67.0/ssot/query-sql", "--method", "POST", "--body", f"@{body}")
    if "metadata" not in d:
        raise SystemExit(f"Data Cloud query failed: {str(d)[:300]}")
    cols = [m["name"] for m in d["metadata"]]
    rows, total, qid = list(d["data"]), d["status"]["rowCount"], d["status"]["queryId"]
    while len(rows) < total:
        page = _rest(f"/services/data/v67.0/ssot/query-sql/{qid}/rows?offset={len(rows)}&rowLimit=5000")
        if not page.get("data"):
            raise SystemExit(f"Data Cloud paging stopped at {len(rows)}/{total} rows")
        rows += page["data"]
    return [dict(zip(cols, row)) for row in rows]


def ts(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value and value != "NOT_SET" else None


def split_topic(topic):
    """'store_policies_PlannerPreview-<uuid>' -> ('store_policies', 'preview')."""
    m = re.match(r"^(.*?)_(PlannerPreview-[0-9a-f-]+|16[A-Za-z0-9]{13})$", topic or "")
    if not m:
        return topic, None
    return m.group(1), "preview" if m.group(2).startswith("PlannerPreview") else "published"


def pct(values, q):
    values = sorted(values)
    return values[min(len(values) - 1, int(round(q * (len(values) - 1))))] if values else None


sessions = {r["id"]: r for r in sql('SELECT "ssot__Id__c" AS id, "ssot__AiAgentChannelType__c" AS channel, '
                                    '"ssot__RelatedMessagingSessionId__c" AS msg, "ssot__AiAgentSessionEndType__c" AS end_type, '
                                    '"ssot__StartTimestamp__c" AS started FROM "ssot__AiAgentSession__dlm"')}
interactions = sql('SELECT "ssot__Id__c" AS id, "ssot__AiAgentSessionId__c" AS session, "ssot__TopicApiName__c" AS topic, '
                   '"ssot__AiAgentInteractionType__c" AS type, "ssot__StartTimestamp__c" AS started, '
                   '"ssot__EndTimestamp__c" AS ended FROM "ssot__AiAgentInteraction__dlm"')
steps = sql('SELECT "ssot__AiAgentInteractionId__c" AS interaction, "ssot__AiAgentInteractionStepType__c" AS type, '
            '"ssot__Name__c" AS name, "ssot__StartTimestamp__c" AS started, "ssot__EndTimestamp__c" AS ended, '
            'CASE WHEN "ssot__ErrorMessageText__c" <> \'NOT_SET\' THEN 1 ELSE 0 END AS error '
            'FROM "ssot__AiAgentInteractionStep__dlm"')

# Classify sessions: Acme if any interaction hit an Acme topic; env from the topic suffix.
session_env, session_topics = {}, defaultdict(list)
for it in interactions:
    base, env = split_topic(it["topic"])
    session_topics[it["session"]].append(base)
    if base in ACME_TOPICS and env:
        session_env.setdefault(it["session"], env)
acme_sessions = {s: e for s, e in session_env.items()}

steps_by_interaction = defaultdict(list)
for st in steps:
    steps_by_interaction[st["interaction"]].append(st)

report = {}
for env in ("published", "preview"):
    sids = {s for s, e in acme_sessions.items() if e == env}
    its = [i for i in interactions if i["session"] in sids]
    turns = [i for i in its if i["type"] == "TURN"]
    turn_ms = [int((ts(i["ended"]) - ts(i["started"])).total_seconds() * 1000) for i in turns if ts(i["ended"]) and ts(i["started"])]
    topics = Counter(split_topic(i["topic"])[0] for i in turns if i["topic"] != "NOT_SET")
    escalated = {s for s in sids if "escalation" in session_topics[s]}
    guarded = {s for s in sids if "Prompt_Injection" in session_topics[s]}
    action_ms, action_n, action_err, trust = defaultdict(list), Counter(), Counter(), Counter()
    for i in its:
        for st in steps_by_interaction[i["id"]]:
            if st["type"] == "ACTION_STEP":
                action_n[st["name"]] += 1
                action_err[st["name"]] += int(st["error"] or 0)
                if ts(st["ended"]) and ts(st["started"]):
                    action_ms[st["name"]].append(int((ts(st["ended"]) - ts(st["started"])).total_seconds() * 1000))
            elif st["type"] in ("CLASSIFIER_STEP", "TRUST_GUARDRAILS_STEP") or st["name"] == "Atlas__GroundednessValidationPrompt":
                trust[st["name"]] += 1
    days = Counter(ts(sessions[s]["started"]).strftime("%Y-%m-%d %H:00") for s in sids if s in sessions and ts(sessions[s]["started"]))
    report[env] = {
        "sessions": len(sids),
        "turns": len(turns),
        "turns_per_session": round(len(turns) / len(sids), 2) if sids else 0,
        "escalation_rate": round(len(escalated) / len(sids), 3) if sids else 0,
        "containment_rate": round(1 - len(escalated) / len(sids), 3) if sids else 0,
        "injection_blocked_sessions": len(guarded),
        "turn_latency_ms": {"p50": pct(turn_ms, 0.5), "p90": pct(turn_ms, 0.9), "max": max(turn_ms) if turn_ms else None},
        "topics": dict(topics.most_common()),
        "actions": {n: {"calls": action_n[n], "errors": action_err[n],
                        "p50_ms": pct(action_ms[n], 0.5), "p90_ms": pct(action_ms[n], 0.9)} for n in action_n},
        "trust_layer_steps": dict(trust),
        "sessions_by_hour": dict(sorted(days.items())),
    }

out = {"generated": datetime.now().isoformat(timespec="seconds"), "org_sessions_total": len(sessions),
       "acme_sessions": len(acme_sessions), **report}
with open("tests/results/observability.json", "w") as fh:
    json.dump(out, fh, indent=1)
print(json.dumps({k: (v if not isinstance(v, dict) else {kk: vv for kk, vv in v.items() if kk not in ("sessions_by_hour",)})
                  for k, v in out.items()}, indent=1))
