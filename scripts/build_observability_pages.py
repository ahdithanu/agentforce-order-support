"""Refresh the observability dashboard pages from tests/results/observability.json.

Usage: python3 scripts/build_observability_pages.py
Rewrites the tiles, the raw-JSON block, and the chart data in docs/observability.html
and docs/observability/index.html. Run after scripts/observability_report.py.
"""
import json
import re

d = json.load(open("tests/results/observability.json"))
pub, prev = d["published"], d["preview"]
names = {"AnswerQuestionsWithKnowledge": "knowledge search"}
actions = []
for name in set(pub["actions"]) | set(prev["actions"]):
    p, q = pub["actions"].get(name) or {}, prev["actions"].get(name) or {}
    actions.append({"name": names.get(name, name), "prev_p50": q.get("p50_ms"), "prev_p90": q.get("p90_ms"),
                    "pub_p50": p.get("p50_ms")})
actions.sort(key=lambda a: -(a["pub_p50"] or a["prev_p50"] or 0))
chart = {"actions": actions, "turn": {"pub": pub["turn_latency_ms"], "prev": prev["turn_latency_ms"]},
         "topics": pub["topics"], "topics_prev": prev["topics"]}
errors = sum(a["errors"] for part in (pub, prev) for a in part["actions"].values())
lat = pub["turn_latency_ms"]
tiles = f"""<div class="tiles">
      <div class="tile"><b>{pub['sessions']}</b><span>sessions ({pub['turns']} turns)</span></div>
      <div class="tile"><b>{round(pub['containment_rate'] * 100)}%</b><span>containment: no human handoff</span></div>
      <div class="tile"><b>{lat['p50'] / 1000:.1f} s</b><span>turn latency p50 (p90 {lat['p90'] / 1000:.1f} s)</span></div>
      <div class="tile"><b>{errors}</b><span>action errors across all traced calls</span></div>
    </div>"""
generated = d["generated"].replace("T", " ")

for path in ("docs/observability.html", "docs/observability/index.html"):
    html = open(path).read()
    html = re.sub(r'<div class="tiles">.*?</div>\s*</section>', lambda m: tiles + "\n  </section>", html, count=1, flags=re.S)
    html = re.sub(r"Generated [0-9-]+ [0-9:]+\.", f"Generated {generated}.", html)
    html = re.sub(r"(overflow-x:auto\">)\{.*?\}(</pre>)", lambda m: m.group(1) + json.dumps(d, indent=1) + m.group(2), html, count=1, flags=re.S)
    html = re.sub(r"const D = .*?;\n", lambda m: "const D = " + json.dumps(chart) + ";\n", html, count=1)
    open(path, "w").write(html)
    print("updated", path)
