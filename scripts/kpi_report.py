"""Agent deployment-health KPIs from preview traces.

Usage: python3 scripts/kpi_report.py tests/preview-run-1.json [tests/preview-run-2.json ...]
Writes tests/reports/kpi-report.html and prints a JSON summary per run.
Each run file is the output of scripts/preview_scenarios.py.
"""
import glob
import html
import json
import os
import re
import statistics
import sys

PROGRESS_CLAIM = re.compile(r"\b(i will now|i'll now|let me|one moment|checking|verifying|looking (it|that) up)\b", re.I)


def load_steps(trace_dir):
    """Return one list of plan steps per trace file (one trace per planner turn)."""
    turns = []
    for path in sorted(glob.glob(os.path.join(trace_dir or "", "traces", "*.json"))):
        with open(path) as fh:
            turns.append(json.load(fh).get("plan", []))
    return turns


def parse_json(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def run_metrics(run):
    m = {"sessions": 0, "turns": 0, "escalated": 0, "verify_calls": 0, "verify_success": 0,
         "cases_created": 0, "withheld": 0, "unbacked_claims": 0, "grounded": 0, "graded": 0,
         "min_safety": 1.0, "latency": {}}
    for name, session in run.items():
        if "error" in session:
            continue
        m["sessions"] += 1
        m["turns"] += len(session["turns"])
        escalated = False
        for steps in load_steps(session.get("tracesPath")):
            called = False
            response = ""
            for st in steps:
                t = st.get("type")
                data = st.get("data") or {}
                if t == "TransitionStep" and data.get("to_agent") == "escalation":
                    escalated = True
                elif t == "EnabledToolsStep":
                    m["withheld"] += len(data.get("runtime_withheld_actions") or [])
                elif t == "FunctionStep":
                    fn = parse_json(st.get("function")) or {}
                    fname = fn.get("name", "unknown") if isinstance(fn, dict) else "unknown"
                    called = True
                    m["latency"].setdefault(fname, []).append(int(st.get("executionLatency") or 0))
                    out = fn.get("output") or {} if isinstance(fn, dict) else {}
                    if fname == "verify_customer":
                        m["verify_calls"] += 1
                        m["verify_success"] += 1 if out.get("verified") is True else 0
                    if fname == "create_support_case" and out.get("success") is True:
                        m["cases_created"] += 1
                elif t == "PlannerResponseStep":
                    response = parse_json(st.get("message")) or ""
                    score = parse_json(st.get("safetyScore")) or {}
                    if isinstance(score, dict) and "safety_score" in score:
                        m["min_safety"] = min(m["min_safety"], float(score["safety_score"]))
                elif t == "OutputEvaluationStep" and parse_json(st.get("evaluationType")) == "groundedness":
                    m["graded"] += 1
                    m["grounded"] += 1 if parse_json(st.get("category")) == "GROUNDED" else 0
            if response and not called and PROGRESS_CLAIM.search(str(response)):
                m["unbacked_claims"] += 1
        m["escalated"] += 1 if escalated else 0
    pct = lambda a, b: round(100 * a / b, 1) if b else None
    return {
        "sessions": m["sessions"],
        "turns": m["turns"],
        "containment_pct": pct(m["sessions"] - m["escalated"], m["sessions"]),
        "escalation_pct": pct(m["escalated"], m["sessions"]),
        "verify_calls": m["verify_calls"],
        "verify_success_pct": pct(m["verify_success"], m["verify_calls"]),
        "cases_created": m["cases_created"],
        "tool_withheld_events": m["withheld"],
        "unbacked_progress_claims": m["unbacked_claims"],
        "groundedness_pct": pct(m["grounded"], m["graded"]),
        "min_safety_score": round(m["min_safety"], 4),
        "action_latency_ms": {k: {"p50": int(statistics.median(v)), "max": max(v), "n": len(v)}
                              for k, v in sorted(m["latency"].items())},
    }


ROWS = [
    ("containment_pct", "Containment", "%", "higher", "Sessions resolved without a human handoff"),
    ("escalation_pct", "Escalation rate", "%", "context", "Scripted to be 1 of 6 (U4)"),
    ("tool_withheld_events", "Tool-availability errors", "", "lower", "Actions withheld at runtime (e.g. NO_USER_ACCESS)"),
    ("unbacked_progress_claims", "Unbacked progress claims", "", "lower", "Says it is checking/verifying without calling a tool"),
    ("verify_calls", "Verification calls", "", "context", "verify_customer invocations"),
    ("verify_success_pct", "Verification success", "%", "context", "Share of verify calls that returned verified=true"),
    ("cases_created", "Cases created", "", "context", "create_support_case success=true"),
    ("groundedness_pct", "Groundedness", "%", "higher", "Platform groundedness evaluator verdicts"),
    ("min_safety_score", "Min safety score", "", "higher", "Lowest per-response safety score"),
]


def render(runs):
    names = list(runs)
    head = "".join(f"<th>{html.escape(n)}</th>" for n in names)
    body = ""
    for key, label, unit, better, hint in ROWS:
        vals = [runs[n][key] for n in names]
        cells = ""
        for i, v in enumerate(vals):
            cls = ""
            if i and better != "context" and v is not None and vals[0] is not None and v != vals[0]:
                improved = (v > vals[0]) if better == "higher" else (v < vals[0])
                cls = "up" if improved else "down"
            shown = "n/a" if v is None else f"{v}{unit}"
            cells += f'<td class="{cls}">{shown}</td>'
        body += f"<tr><th scope=row>{label}<small>{html.escape(hint)}</small></th>{cells}</tr>"
    lat = ""
    for n in names:
        for action, s in runs[n]["action_latency_ms"].items():
            lat += f"<tr><td>{html.escape(n)}</td><td><code>{action}</code></td><td>{s['n']}</td><td>{s['p50']} ms</td><td>{s['max']} ms</td></tr>"
    return f"""<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>Agent Health Report</title><style>
:root{{--bg:#fbfbfa;--fg:#1d1d1b;--muted:#6b6b66;--line:#e4e4df;--up:#1f7a4d;--down:#b3261e;--card:#fff}}
@media (prefers-color-scheme:dark){{:root{{--bg:#161615;--fg:#ecece8;--muted:#9a9a93;--line:#2c2c29;--up:#5cc28d;--down:#f08a80;--card:#1e1e1c}}}}
body{{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,-apple-system,sans-serif}}
main{{max-width:880px;margin:0 auto;padding:32px 16px}} h1{{font-size:22px;margin:0 0 4px}} p.sub{{color:var(--muted);margin:0 0 24px}}
table{{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line);border-radius:8px;overflow:hidden;margin-bottom:28px}}
th,td{{padding:10px 12px;border-bottom:1px solid var(--line);text-align:left;font-variant-numeric:tabular-nums}}
thead th{{font-size:13px;color:var(--muted);font-weight:600}} th small{{display:block;color:var(--muted);font-weight:400;font-size:12px}}
td.up{{color:var(--up);font-weight:600}} td.down{{color:var(--down);font-weight:600}} h2{{font-size:16px;margin:0 0 8px}}
.wrap{{overflow-x:auto}}</style></head><body><main>
<h1>Acme Order Support: deployment health</h1>
<p class=sub>Computed from Agentforce preview traces (six scripted scenarios per run). Green/red marks change versus the first run.</p>
<div class=wrap><table><thead><tr><th>Metric</th>{head}</tr></thead><tbody>{body}</tbody></table></div>
<h2>Action latency</h2><div class=wrap><table><thead><tr><th>Run</th><th>Action</th><th>Calls</th><th>p50</th><th>Max</th></tr></thead><tbody>{lat or '<tr><td colspan=5>No action calls</td></tr>'}</tbody></table></div>
</main></body></html>"""


if __name__ == "__main__":
    runs = {}
    for path in sys.argv[1:]:
        with open(path) as fh:
            runs[os.path.splitext(os.path.basename(path))[0]] = run_metrics(json.load(fh))
    os.makedirs("tests/reports", exist_ok=True)
    with open("tests/reports/kpi-report.html", "w") as fh:
        fh.write(render(runs))
    print(json.dumps(runs, indent=1))
