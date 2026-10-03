"""Mine Agentforce session traces for failures and draft new eval cases from them.

Usage:
  python3 scripts/trace_miner.py <org-alias> [--hours 24] [--env published|preview|all]
  python3 scripts/trace_miner.py --fixture tests/fixtures/stdm_synthetic.json

Both sources feed the same reconstruction, flags and candidate generation. Fixtures use their
as_of timestamp for the window and output names, so they do not age out after the org expires.

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
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

import yaml

if __package__:
    from .trace_sources import DataCloudSource, FixtureSource
else:
    from trace_sources import DataCloudSource, FixtureSource

ROOT = Path(__file__).resolve().parent.parent
SUITE_NAMES = (
    "routing-set.yaml", "rag-eval.yaml", "rag-eval-es.yaml",
    "Acme_Order_Support-testing-center.yaml", "Acme_Order_Support-security.yaml",
)

ACME_TOPICS = {"identity_verification", "order_status", "support_case", "store_policies", "escalation", "returns"}
SLOW_MS = 8000
GROUNDED_OK = {"GROUNDED", "SMALL_TALK"}  # SMALL_TALK = clarifying/greeting turns with nothing to ground
PROGRESS = re.compile(r"(one moment|i will now (?:verify|check|look)|let me (?:check|look|verify)|i'?ll (?:check|look up|verify)"
                      r"|checking (?:that|your)|un momento|voy a (?:verificar|revisar))", re.I)
HUMAN_ASK = re.compile(r"(human|person|agent|representative|someone|real person|customer service|transfer|connect me|persona|humano|agente)", re.I)
DECLINE = re.compile(r"(don'?t have|do not have|not (?:in|covered|listed|mentioned|included)|couldn'?t find|could not find"
                     r"|no information|isn'?t (?:covered|listed|mentioned)|unable to find|no tengo|no encuentro|no aparece"
                     r"|no hay información|no está (?:incluid|cubiert|mencionad)|no se menciona)", re.I)


def ts(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value and value != "NOT_SET" else None


def split_topic(topic):
    m = re.match(r"^(.*?)_(PlannerPreview-[0-9a-f-]+|16[A-Za-z0-9]{13})$", topic or "")
    if not m:
        return topic, None
    return m.group(1), "preview" if m.group(2).startswith("PlannerPreview") else "published"


def mask(text):
    text = re.sub(r"[\w.+-]+@[\w-]+(\.[\w-]+)+", "<email>", text or "")
    text = re.sub(r"(?<!\w)(?:\+?1[\s.-]?)?(?:\(\d{3}\)|\d{3})[\s.-]?\d{3}[\s.-]?\d{4}(?!\w)", "<number>", text)
    text = re.sub(r"(?<![\d-])\+?\d(?:[\d\s().]|-(?!\d\d(?:\D|$)))*\d{4,}", lambda m: m.group(0) if re.fullmatch(r"\d{4}", m.group(0)) else "<number>", text)
    return re.sub(r"\d{7,}", "<number>", text)


def norm(text):
    return " ".join(re.sub(r"[^a-z0-9áéíóúñü\s]+", "", (text or "").lower()).split())


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


def reconstruct(records, since, env="published"):
    """Rebuild and flag turns from source independent records without mutating them."""
    sessions = {r["id"]: r for r in records.sessions if ts(r["started"]) and ts(r["started"]) >= since}
    interactions = [dict(r) for r in records.interactions if r["session"] in sessions]
    wanted = {i["id"] for i in interactions}
    steps = defaultdict(list)
    for step in records.steps:
        if step["interaction"] in wanted:
            steps[step["interaction"]].append(step)
    messages = defaultdict(lambda: {"Input": [], "Output": []})
    for message in records.messages:
        if message["interaction"] in wanted and message["type"] in ("Input", "Output"):
            messages[message["interaction"]][message["type"]].append(message)

    # Session environment from the topic suffix; keep only sessions that touched this agent's topics.
    env_of, by_session = {}, defaultdict(list)
    for it in interactions:
        base, topic_env = split_topic(it["topic"])
        it["route"] = base if base != "NOT_SET" else None
        by_session[it["session"]].append(it)
        if base in ACME_TOPICS and topic_env:
            env_of.setdefault(it["session"], topic_env)
    keep = {s for s, e in env_of.items() if env == "all" or e == env}

    # --- rebuild turns and flag ---------------------------------------------------------------
    all_rows = []
    for sid in sorted(keep, key=lambda s: (sessions[s]["started"], s)):
        turns = sorted((i for i in by_session[sid] if i["type"] == "TURN"), key=lambda i: (i["started"] or "", i["id"]))
        prev_user, prev_resolved = None, True
        rows = []
        for n, it in enumerate(turns, 1):
            msgs = messages[it["id"]]
            user = " ".join(m["text"] for m in sorted(msgs["Input"], key=lambda m: (m["sent"] or "", m["text"] or "")) if m["text"] not in (None, "NOT_SET"))
            reply = " ".join(m["text"] for m in sorted(msgs["Output"], key=lambda m: (m["sent"] or "", m["text"] or "")) if m["text"] not in (None, "NOT_SET"))
            actions, flags, evidence = [], [], {}
            grounded = adherence = resolution = None
            for st in sorted(steps[it["id"]], key=lambda st: tuple(st[k] or "" for k in ("type", "name", "output", "error", "input"))):
                if st["type"] == "ACTION_STEP":
                    resp = parse(st["output"]).get("actionResponse", {})
                    actions.append(st["name"])
                    if (st["error"] not in (None, "NOT_SET")) or resp.get("__action_execution_status__", "success") != "success":
                        flags.append("action_error")
                        detail = st["error"] if st["error"] not in (None, "NOT_SET") else \
                            "; ".join(e.get("message", "") for e in resp.get("errors") or []) or resp.get("error_message", "")
                        evidence["action_error"] = mask(f'{st["name"]}: {detail}')[:160]
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
        all_rows.extend(rows)
    return all_rows, len(keep)


def known_utterances(suite_dir=ROOT / "tests"):
    """Normalize suite text with the same masking used for reconstructed user turns."""
    known = set()
    for name in SUITE_NAMES:
        path = Path(suite_dir) / name
        try:
            blob = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except FileNotFoundError:
            continue
        for case in blob.get("cases") or blob.get("testCases") or blob.get("utterances") or []:
            known.add(norm(mask(case.get("u") or case.get("utterance") or case.get("question") or "")))
    return known


def draft_candidates(flagged, known):
    """Draft reviewable cases; digest only signals cannot suppress a later candidate."""
    candidates, seen = [], set()
    for r in flagged:
        key = norm(r["user"])
        if not key or key in known or key in seen:
            continue
        base = {"utterance": r["user"], "observed_route": r["route"], "flags": r["flags"],
                "source": f'{r["session"]}#{r["turn"]}'}
        if set(r["flags"]) & {"kb_decline", "ungrounded"} and r["route"] == "store_policies":
            candidates.append({"suite": "rag-eval", **base, "kind": "TODO answerable|unanswerable",
                               "must_include": "TODO if answerable"})
        elif set(r["flags"]) & {"off_topic", "rephrase", "unrequested_escalation"}:
            candidates.append({"suite": "routing-set", **base, "expected_route": "TODO"})
        elif set(r["flags"]) & {"fake_progress", "action_error", "ungrounded"}:
            candidates.append({"suite": "testing-center", **base, "expectedOutcome": "TODO"})
        else:
            continue
        seen.add(key)
        # escalated-on-request, slow, low_adherence, verify_failed and dropped stay in the digest only:
        # they are signals to read, not cases with a checkable expected answer.

    return candidates


def mine(records, *, as_of, hours=24, env="published", suite_dir=ROOT / "tests", source="data_cloud"):
    rows, session_count = reconstruct(records, as_of - timedelta(hours=hours), env)
    flagged = [r for r in rows if r["flags"]]
    counts = Counter(flag for row in flagged for flag in row["flags"])
    summary = {
        "generated": as_of.isoformat(timespec="seconds"), "window_hours": hours, "env": env,
        "source": source, "sessions": session_count, "turns": len(rows), "flagged_turns": len(flagged),
        "flags": dict(counts.most_common()),
    }
    candidates = draft_candidates(flagged, known_utterances(suite_dir))
    summary["candidates"] = len(candidates)
    return {"summary": summary, "turns": flagged}, candidates


def digest(mined):
    summary, flagged = mined["summary"], mined["turns"]
    stamp = summary["generated"][:10]
    lines = [f"# Trace digest {stamp}", "",
             f"{summary['sessions']} {summary['env']} sessions in the last {summary['window_hours']:g} h, {summary['turns']} turns, "
             f"{summary['flagged_turns']} flagged, {summary['candidates']} new candidate cases.", "",
             f"Source: {summary['source']}. Reference time: {summary['generated']}.", "",
             "| Flag | Turns |", "|---|---|"] + [f"| {f} | {n} |" for f, n in summary["flags"].items()] + [""]
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
    return "\n".join(lines)


def write_outputs(mined, candidates, out):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    stamp = mined["summary"]["generated"][:10]
    (out / f"mined-{stamp}.json").write_text(json.dumps(mined, indent=1, ensure_ascii=False), encoding="utf-8")
    label = "synthetic fixture" if mined["summary"]["source"] == "fixture" else "production"
    candidate_doc = {
        "note": f"Drafts from {label} traces. Set every TODO, then copy approved cases into tests/.",
        "candidates": candidates,
    }
    (out / f"candidates-{stamp}.yaml").write_text(
        yaml.safe_dump(candidate_doc, sort_keys=False, allow_unicode=True, width=120), encoding="utf-8",
    )
    (out / f"digest-{stamp}.md").write_text(digest(mined), encoding="utf-8")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("org", nargs="?", help="Salesforce org alias (live mode)")
    ap.add_argument("--fixture", type=Path, help="Synthetic STDM JSON fixture (offline mode, no Salesforce CLI)")
    ap.add_argument("--hours", type=float, default=24)
    ap.add_argument("--env", choices=["published", "preview", "all"], default="published")
    ap.add_argument("--out", default="traces")
    args = ap.parse_args(argv)
    if bool(args.org) == bool(args.fixture):
        ap.error("provide exactly one of an org alias or --fixture")
    if not 0 < args.hours < float("inf"):
        ap.error("--hours must be a finite positive number")
    try:
        if args.fixture:
            source = FixtureSource(args.fixture)
            as_of = source.as_of
        else:
            source = DataCloudSource(args.org)
            # Keep local output dates aligned with self_improve.sh's date command.
            # The timezone aware value still compares correctly with STDM UTC timestamps.
            as_of = datetime.now().astimezone()
        mined, candidates = mine(source.load(), as_of=as_of, hours=args.hours, env=args.env,
                                 source="fixture" if args.fixture else "data_cloud")
        write_outputs(mined, candidates, args.out)
    except (OSError, ValueError) as exc:
        ap.error(str(exc))
    print(json.dumps(mined["summary"], indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
