"""Mine Agentforce session traces for failures and draft new eval cases from them.

Usage: python3 scripts/trace_miner.py <org-alias> [--hours 24] [--env published|preview|all]

The "production -> evals" step of the improvement loop. Reads session tracing from Data Cloud
(ssot__AiAgentSession / Interaction / InteractionStep / InteractionMessage, via `sf api request rest`),
rebuilds each turn (user message, reply, subagent, actions, Trust Layer verdicts), flags the
suspicious ones, and writes to traces/ (gitignored, because published traffic can include
anything a visitor typed):
  traces/mined-<date>.json        every flagged turn, with the evidence behind each flag
  traces/candidates-<date>.yaml   draft eval cases, deduped against tests/*.yaml; a person sets
                                  the expected value and copies approved cases into the suites
  traces/digest-<date>.md         counts and examples per flag; the input for the reviewer agent
Emails, phone numbers and long digit runs are masked before anything is written.

Flags (per turn unless noted):
  ungrounded       Trust Layer groundedness verdict other than GROUNDED
  low_adherence    InstructionAdherence below HIGH
  action_error     an action errored or returned a non-success status
  verify_failed    verify_customer returned verified=false
  fake_progress    reply claims to be checking/verifying but no action ran in that turn
  escalated        the turn ran in the escalation subagent (unrequested_escalation: the user
                   didn't ask for a person, so a lockout, misroute or dead end sent them there)
  off_topic        routed to off_topic or ambiguous_question
  kb_decline       store_policies reply says the policies don't cover it (knowledge gap)
  slow             turn took longer than SLOW_MS
  rephrase         the user repeated themselves (the previous reply didn't land)
  dropped          (session) last turn unresolved and the session expired or was abandoned
"""
import argparse
import json
import os
import re
import subprocess
import tempfile
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

import yaml

ACME_TOPICS = {"identity_verification", "order_status", "support_case", "store_policies", "escalation", "returns"}
SLOW_MS = 8000
GROUNDED_OK = {"GROUNDED", "SMALL_TALK"}  # SMALL_TALK = clarifying/greeting turns with nothing to ground
PROGRESS = re.compile(r"(one moment|i will now (?:verify|check|look)|let me (?:check|look|verify)|i'?ll (?:check|look up|verify)"
                      r"|checking (?:that|your)|un momento|voy a (?:verificar|revisar))", re.I)
HUMAN_ASK = re.compile(r"(human|person|agent|representative|someone|real person|customer service|transfer|connect me|persona|humano|agente)", re.I)
DECLINE = re.compile(r"(don'?t have|do not have|not (?:in|covered|listed|mentioned|included)|couldn'?t find|could not find"
                     r"|no information|isn'?t (?:covered|listed|mentioned)|unable to find|no tengo|no encuentro|no aparece"
                     r"|no hay información|no está (?:incluid|cubiert|mencionad)|no se menciona)", re.I)

ap = argparse.ArgumentParser()
ap.add_argument("org")
ap.add_argument("--hours", type=float, default=24)
ap.add_argument("--env", choices=["published", "preview", "all"], default="published")
ap.add_argument("--out", default="traces")
args = ap.parse_args()


def _rest(*rest_args):
    out = subprocess.run(["sf", "api", "request", "rest", *rest_args, "--target-org", args.org],
                         capture_output=True, text=True).stdout
    return json.JSONDecoder().raw_decode(out[out.find("{"):])[0]


def sql(query):
    """Run a Data Cloud SQL query and page through every row (responses are capped by size)."""
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
        json.dump({"sql": query}, fh)
        body = fh.name
    d = _rest("/services/data/v67.0/ssot/query-sql", "--method", "POST", "--body", f"@{body}")
    os.unlink(body)
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
    m = re.match(r"^(.*?)_(PlannerPreview-[0-9a-f-]+|16[A-Za-z0-9]{13})$", topic or "")
    if not m:
        return topic, None
    return m.group(1), "preview" if m.group(2).startswith("PlannerPreview") else "published"


def mask(text):
    text = re.sub(r"[\w.+-]+@[\w-]+(\.[\w-]+)+", "<email>", text or "")
    text = re.sub(r"(?<![\d-])\+?\d(?:[\d\s().]|-(?!\d\d(?:\D|$)))*\d{4,}", lambda m: m.group(0) if re.fullmatch(r"\d{4}", m.group(0)) else "<number>", text)
    return re.sub(r"\d{7,}", "<number>", text)


def norm(text):
    return re.sub(r"[^a-z0-9áéíóúñü ]+", "", (text or "").lower()).strip()


def jaccard(a, b):
    a, b = set(norm(a).split()), set(norm(b).split())
    return len(a & b) / len(a | b) if a and b else 0.0


def parse(value):
    try:
        return json.loads(value) if value and value != "NOT_SET" else {}
    except json.JSONDecodeError:
        return {}


def verdict(step_output, key):
    m = re.search(rf"{key}[:=]\s*value=(\w+)|{key}=(\w+)", parse(step_output).get("mgr.sensitive.step.result", ""))
    return (m.group(1) or m.group(2)) if m else None


# --- load ---------------------------------------------------------------------------------
since = datetime.now(timezone.utc) - timedelta(hours=args.hours)
sessions = {r["id"]: r for r in sql(
    'SELECT "ssot__Id__c" AS id, "ssot__AiAgentSessionEndType__c" AS end_type, "ssot__StartTimestamp__c" AS started '
    'FROM "ssot__AiAgentSession__dlm"') if ts(r["started"]) and ts(r["started"]) >= since}
interactions = [r for r in sql(
    'SELECT "ssot__Id__c" AS id, "ssot__AiAgentSessionId__c" AS session, "ssot__TopicApiName__c" AS topic, '
    '"ssot__AiAgentInteractionType__c" AS type, "ssot__StartTimestamp__c" AS started, "ssot__EndTimestamp__c" AS ended '
    'FROM "ssot__AiAgentInteraction__dlm"') if r["session"] in sessions]
wanted = {i["id"] for i in interactions}
steps = defaultdict(list)
for st in sql('SELECT "ssot__AiAgentInteractionId__c" AS interaction, "ssot__AiAgentInteractionStepType__c" AS type, '
              '"ssot__Name__c" AS name, "ssot__InputValueText__c" AS input, "ssot__OutputValueText__c" AS output, '
              '"ssot__ErrorMessageText__c" AS error FROM "ssot__AiAgentInteractionStep__dlm"'):
    if st["interaction"] in wanted:
        steps[st["interaction"]].append(st)
messages = defaultdict(lambda: {"Input": [], "Output": []})
for m in sql('SELECT "ssot__AiAgentInteractionId__c" AS interaction, "ssot__AiAgentInteractionMessageType__c" AS type, '
             '"ssot__ContentText__c" AS text, "ssot__MessageSentTimestamp__c" AS sent FROM "ssot__AiAgentInteractionMessage__dlm"'):
    if m["interaction"] in wanted and m["type"] in ("Input", "Output"):
        messages[m["interaction"]][m["type"]].append(m)

# Session environment from the topic suffix; keep only sessions that touched this agent's topics.
env_of, by_session = {}, defaultdict(list)
for it in interactions:
    base, env = split_topic(it["topic"])
    it["route"] = base if base != "NOT_SET" else None
    by_session[it["session"]].append(it)
    if base in ACME_TOPICS and env:
        env_of.setdefault(it["session"], env)
keep = {s for s, e in env_of.items() if args.env == "all" or e == args.env}

# --- rebuild turns and flag ---------------------------------------------------------------
flagged, session_flags = [], Counter()
for sid in sorted(keep, key=lambda s: sessions[s]["started"]):
    turns = sorted((i for i in by_session[sid] if i["type"] == "TURN"), key=lambda i: i["started"] or "")
    prev_user, prev_resolved = None, True
    rows = []
    for n, it in enumerate(turns, 1):
        msgs = messages[it["id"]]
        user = " ".join(m["text"] for m in sorted(msgs["Input"], key=lambda m: m["sent"] or "") if m["text"] != "NOT_SET")
        reply = " ".join(m["text"] for m in sorted(msgs["Output"], key=lambda m: m["sent"] or "") if m["text"] != "NOT_SET")
        actions, flags, evidence = [], [], {}
        grounded = adherence = resolution = None
        for st in steps[it["id"]]:
            if st["type"] == "ACTION_STEP":
                resp = parse(st["output"]).get("actionResponse", {})
                actions.append(st["name"])
                if (st["error"] not in (None, "NOT_SET")) or resp.get("__action_execution_status__", "success") != "success":
                    flags.append("action_error")
                    detail = st["error"] if st["error"] not in (None, "NOT_SET") else \
                        "; ".join(e.get("message", "") for e in resp.get("errors") or []) or resp.get("error_message", "")
                    evidence["action_error"] = f'{st["name"]}: {str(detail)[:160]}'
                if st["name"] == "verify_customer" and resp.get("verified") is False:
                    flags.append("verify_failed")
            elif st["name"] == "Atlas__GroundednessValidationPrompt":
                m = re.search(r"category=(\w+)", parse(st["output"]).get("mgr.sensitive.step.result", ""))
                grounded = m.group(1) if m else grounded
            elif st["name"] == "InstructionAdherence":
                adherence = verdict(st["output"], "InstructionAdherence")
                resolution = verdict(st["output"], "TaskResolution")
        route = it["route"]
        if grounded and grounded not in GROUNDED_OK:
            flags.append("ungrounded")
            evidence["ungrounded"] = grounded
        if adherence and adherence != "HIGH":
            flags.append("low_adherence")
            evidence["low_adherence"] = adherence
        if route == "escalation":
            flags.append("escalated" if HUMAN_ASK.search(user) else "unrequested_escalation")
        if not actions and route not in ("store_policies", None) and PROGRESS.search(reply):
            flags.append("fake_progress")
        if route in ("off_topic", "ambiguous_question"):
            flags.append("off_topic")
        if route == "store_policies" and DECLINE.search(reply):
            flags.append("kb_decline")
        ms = int((ts(it["ended"]) - ts(it["started"])).total_seconds() * 1000) if ts(it["ended"]) and ts(it["started"]) else None
        if ms and ms > SLOW_MS:
            flags.append("slow")
        if prev_user and not prev_resolved and jaccard(user, prev_user) >= 0.5:
            flags.append("rephrase")
        rows.append({"session": sid, "started": sessions[sid]["started"], "turn": n, "route": route, "user": mask(user), "reply": mask(reply)[:400],
                     "actions": actions, "grounded": grounded, "adherence": adherence, "resolution": resolution,
                     "latency_ms": ms, "flags": sorted(set(flags)), "evidence": evidence})
        prev_user, prev_resolved = user, resolution != "UNRESOLVED"
    if rows and rows[-1]["resolution"] == "UNRESOLVED" and sessions[sid]["end_type"] == "EXPIRED":
        rows[-1]["flags"].append("dropped")
    flagged += [r for r in rows if r["flags"]]
    for r in rows:
        for f in r["flags"]:
            session_flags[f] += 1

# --- draft eval cases, deduped against the existing suites --------------------------------
known = set()
for path in ("tests/routing-set.yaml", "tests/rag-eval.yaml", "tests/rag-eval-es.yaml",
             "tests/Acme_Order_Support-testing-center.yaml", "tests/Acme_Order_Support-security.yaml"):
    try:
        blob = yaml.safe_load(open(path)) or {}
    except FileNotFoundError:
        continue
    for case in blob.get("cases") or blob.get("testCases") or blob.get("utterances") or []:
        known.add(norm(case.get("u") or case.get("utterance") or case.get("question") or ""))

candidates, seen = [], set()
for r in flagged:
    key = norm(r["user"])
    if not key or key in known or key in seen:
        continue
    seen.add(key)
    base = {"utterance": r["user"], "observed_route": r["route"], "flags": r["flags"],
            "source": f'{r["session"]}#{r["turn"]}'}
    if set(r["flags"]) & {"kb_decline", "ungrounded"} and r["route"] == "store_policies":
        candidates.append({"suite": "rag-eval", **base, "kind": "TODO answerable|unanswerable",
                           "must_include": "TODO if answerable"})
    elif set(r["flags"]) & {"off_topic", "rephrase", "unrequested_escalation"}:
        candidates.append({"suite": "routing-set", **base, "expected_route": "TODO"})
    elif set(r["flags"]) & {"fake_progress", "action_error", "ungrounded"}:
        candidates.append({"suite": "testing-center", **base, "expectedOutcome": "TODO"})
    # escalated-on-request, slow, low_adherence, verify_failed and dropped stay in the digest only:
    # they are signals to read, not cases with a checkable expected answer.

# --- write --------------------------------------------------------------------------------
os.makedirs(args.out, exist_ok=True)
stamp = datetime.now().strftime("%Y-%m-%d")
summary = {"generated": datetime.now().isoformat(timespec="seconds"), "window_hours": args.hours, "env": args.env,
           "sessions": len(keep), "turns": sum(1 for s in keep for i in by_session[s] if i["type"] == "TURN"),
           "flagged_turns": len(flagged), "flags": dict(session_flags.most_common()), "candidates": len(candidates)}
json.dump({"summary": summary, "turns": flagged}, open(f"{args.out}/mined-{stamp}.json", "w"), indent=1, ensure_ascii=False)
yaml.safe_dump({"note": "Drafts from production traces. Set every TODO, then copy approved cases into tests/.",
                "candidates": candidates}, open(f"{args.out}/candidates-{stamp}.yaml", "w"), sort_keys=False,
               allow_unicode=True, width=120)

lines = [f"# Trace digest {stamp}", "",
         f"{summary['sessions']} {args.env} sessions in the last {args.hours:g} h, {summary['turns']} turns, "
         f"{summary['flagged_turns']} flagged, {len(candidates)} new candidate cases.", "",
         "| Flag | Turns |", "|---|---|"] + [f"| {f} | {n} |" for f, n in session_flags.most_common()] + [""]
by_flag = defaultdict(list)
for r in flagged:
    for f in r["flags"]:
        by_flag[f].append(r)
for f, rows in sorted(by_flag.items(), key=lambda kv: -len(kv[1])):
    lines += [f"## {f} ({len(rows)})", ""]
    for r in rows[:6]:
        ev = f' · {r["evidence"][f]}' if f in r["evidence"] else ""
        lines.append(f'- `{r["session"][:8]}#{r["turn"]}` route={r["route"]} actions={",".join(r["actions"]) or "-"}{ev}\n'
                     f'  - user: {r["user"][:200]!r}\n  - agent: {r["reply"][:200]!r}')
    lines.append("")
open(f"{args.out}/digest-{stamp}.md", "w").write("\n".join(lines))
print(json.dumps(summary, indent=1))
