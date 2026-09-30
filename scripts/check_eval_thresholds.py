"""Fail the nightly job when live eval results drop below thresholds.

Usage: python3 scripts/check_eval_thresholds.py --rag results.json [--rag-es results.json] [--routing results.json]
Writes a Markdown summary to $GITHUB_STEP_SUMMARY when set.
"""
import argparse
import json
import os
import sys

p = argparse.ArgumentParser()
p.add_argument("--rag")
p.add_argument("--rag-es")
p.add_argument("--routing")
p.add_argument("--min-answer", type=float, default=0.9)
p.add_argument("--min-abstain", type=float, default=1.0)
p.add_argument("--min-routing", type=float, default=0.9)
a = p.parse_args()

rows, failed = [], False


def rag(path, label):
    global failed
    rs = json.load(open(path))
    ans = [r for r in rs if r.get("kind") == "answerable" and "error" not in r]
    un = [r for r in rs if r.get("kind") == "unanswerable" and "error" not in r]
    acc = sum(r["answer_correct"] for r in ans) / max(1, len(ans))
    abst = sum(r["abstained"] for r in un) / max(1, len(un))
    ok = acc >= a.min_answer and abst >= a.min_abstain
    failed |= not ok
    rows.append((label, f"answers {sum(r['answer_correct'] for r in ans)}/{len(ans)}, declined {sum(r['abstained'] for r in un)}/{len(un)}", ok))


if a.rag:
    rag(a.rag, "RAG (English)")
if a.rag_es:
    rag(a.rag_es, "RAG (Spanish)")
if a.routing:
    d = json.load(open(a.routing))["rows"]
    ok_rows = [r for r in d if "error" not in r]
    acc = sum(r["correct"] for r in ok_rows) / max(1, len(ok_rows))
    ok = acc >= a.min_routing
    failed |= not ok
    rows.append(("Routing", f"{sum(r['correct'] for r in ok_rows)}/{len(ok_rows)} correct", ok))

md = "| Eval | Result | Status |\n|---|---|---|\n" + "\n".join(f"| {n} | {r} | {'pass' if ok else 'FAIL'} |" for n, r, ok in rows)
print(md)
if os.environ.get("GITHUB_STEP_SUMMARY"):
    with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as fh:
        fh.write("## Nightly live evals\n\n" + md + "\n")
sys.exit(1 if failed else 0)
