"""Static checks for every test spec in tests/. Exits non-zero on any problem."""
import sys

import yaml

problems = []


def fail(path, msg):
    problems.append(f"{path}: {msg}")


def check_testing_center(path, security):
    spec = yaml.safe_load(open(path))
    if spec.get("subjectType") != "AGENT" or not spec.get("subjectName"):
        fail(path, "missing subjectType AGENT / subjectName")
    seen = set()
    for i, c in enumerate(spec.get("testCases") or [], 1):
        u = (c.get("utterance") or "").strip()
        if not u:
            fail(path, f"case {i}: empty utterance")
        if not (c.get("expectedOutcome") or "").strip():
            fail(path, f"case {i}: missing expectedOutcome")
        if security and ("expectedTopic" in c or "expectedActions" in c):
            fail(path, f"case {i}: security cases assert behavior only")
        roles = [t.get("role") for t in c.get("conversationHistory") or []]
        if roles and roles != ["user", "agent"] * (len(roles) // 2):
            fail(path, f"case {i}: conversationHistory must alternate user/agent and end on agent")
        texts = [u, c.get("expectedOutcome") or ""] + [t.get("message") or "" for t in c.get("conversationHistory") or []]
        if any("\n" in t for t in texts):
            fail(path, f"case {i}: raw newline in a string")
        if u in seen:
            fail(path, f"case {i}: duplicate utterance")
        seen.add(u)


def check_rag(path):
    for c in yaml.safe_load(open(path))["cases"]:
        if c["kind"] == "answerable" and not (c.get("source") and c.get("must_include")):
            fail(path, f"{c['id']}: answerable case needs source and must_include")
        if c["kind"] == "route" and not c.get("expect_route"):
            fail(path, f"{c['id']}: route case needs expect_route")


def check_gold(path, key, field):
    items = yaml.safe_load(open(path))[key]
    if len({i[field] for i in items}) != len(items):
        fail(path, "duplicate questions")


check_testing_center("tests/Acme_Order_Support-testing-center.yaml", security=False)
check_testing_center("tests/Acme_Order_Support-security.yaml", security=True)
check_rag("tests/rag-eval.yaml")
check_gold("tests/retrieval-gold.yaml", "questions", "q")
check_gold("tests/routing-set.yaml", "utterances", "u")

for p in problems:
    print(f"::error::{p}")
print(f"spec validation: {len(problems)} problem(s)")
sys.exit(1 if problems else 0)
