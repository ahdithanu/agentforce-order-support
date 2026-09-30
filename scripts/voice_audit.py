"""Voice-readiness audit: run the same conversations through two bundles and score each reply
against voice rules (text-proxy checks; real voice QA still needs a phone channel).

Usage: python3 scripts/voice_audit.py <org-alias> > tests/results/voice-audit.json
Actions are simulated, so no records are created.
"""
import json
import re
import subprocess
import sys

ORG = sys.argv[1]
BUNDLES = ["Acme_Order_Support", "Acme_Order_Support_Voice"]
CONVERSATIONS = {
    "order_status": ["Where is my order?", "It's order A-1001, email jamie@example.com"],
    "policy_shipping_cost": ["How much is express shipping?"],
    "policy_refund_timing": ["How long do refunds take?"],
    "damaged_item": ["My jacket arrived torn", "Order A-1001, jamie@example.com",
                     "The left sleeve seam ripped. Please open a case."],
}
READBACK = re.compile(r"(to confirm|confirm(ing)?|just to (check|make sure)|is that (right|correct)|you said)", re.I)
FILLER = re.compile(r"(one moment|let me (check|look|pull)|checking now|hang (on|with me)|bear with me)", re.I)


def sf(args):
    out = subprocess.run(["sf", *args, "--json"], capture_output=True, text=True).stdout
    return json.loads(re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", out) or "{}")


def score(reply):
    sentences = [s for s in re.split(r"(?<=[.!?])\s+", reply.strip()) if s]
    return {
        "sentences": len(sentences),
        "too_long": len(sentences) > 3,
        "raw_symbols": bool(re.search(r"\$\d|\d+\.\d{2}|https?://|\[\d+\]", reply)),
        "digit_runs": len(re.findall(r"\d{4,}", reply)),
        "list_or_markdown": bool(re.search(r"(^|\n)\s*([-*•]|\d+\.)\s|\*\*|#", reply)),
    }


results = {}
for bundle in BUNDLES:
    rows = []
    for name, turns in CONVERSATIONS.items():
        start = sf(["agent", "preview", "start", "--authoring-bundle", bundle, "--simulate-actions", "-o", ORG])
        sid = (start.get("result") or {}).get("sessionId")
        if not sid:
            rows.append({"conversation": name, "error": start.get("message")})
            continue
        for i, text in enumerate(turns):
            resp = sf(["agent", "preview", "send", "--session-id", sid, "--authoring-bundle", bundle,
                       "--utterance", text, "-o", ORG])
            reply = " ".join(m.get("message", "") for m in (resp.get("result") or {}).get("messages") or [])
            row = {"conversation": name, "turn": i + 1, "user": text, "reply": reply, **score(reply)}
            if name in ("order_status", "damaged_item") and i == 1:
                row["read_back"] = bool(READBACK.search(reply))
            if name.startswith("policy") or (name == "damaged_item" and i == 2):
                row["filler"] = bool(FILLER.search(reply))
            rows.append(row)
        sf(["agent", "preview", "end", "--session-id", sid, "--authoring-bundle", bundle, "-o", ORG])
    ok = [r for r in rows if "error" not in r]
    results[bundle] = {
        "rows": rows,
        "summary": {
            "replies": len(ok),
            "avg_sentences": round(sum(r["sentences"] for r in ok) / max(1, len(ok)), 2),
            "too_long": sum(r["too_long"] for r in ok),
            "raw_symbols": sum(r["raw_symbols"] for r in ok),
            "digit_runs": sum(r["digit_runs"] for r in ok),
            "list_or_markdown": sum(r["list_or_markdown"] for r in ok),
            "read_back": f'{sum(r.get("read_back", False) for r in ok)}/{sum("read_back" in r for r in ok)}',
            "filler": f'{sum(r.get("filler", False) for r in ok)}/{sum("filler" in r for r in ok)}',
        },
    }
print(json.dumps(results, indent=1))
