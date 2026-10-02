#!/usr/bin/env bash
# One turn of the improvement loop: reset the environment, mine production traces into draft
# eval cases, then have a second (read-only) agent review the traces and propose fixes.
#
#   scripts/self_improve.sh <org-alias> [hours=24] [env=published]
#
# Writes traces/digest-<date>.md, traces/candidates-<date>.yaml and traces/proposals-<date>.md
# (all gitignored). Nothing is deployed or published: a person approves candidate cases into
# tests/ and applies proposed fixes in a draft, then the regression suites decide.
set -euo pipefail
cd "$(dirname "$0")/.."

ORG="${1:?usage: scripts/self_improve.sh <org-alias> [hours] [env]}"
HOURS="${2:-24}"
ENV_SCOPE="${3:-published}"
STAMP="$(date +%Y-%m-%d)"

echo "== 1/3 Reset the demo environment (unlock demo orders, close test Cases)"
sf apex run --file scripts/reset-env.apex --target-org "$ORG" --json \
  | python3 -c "import json,sys,re; d=json.load(sys.stdin)['result']; print(*(re.findall(r'RESET orders_unlocked=\d+ cases_closed=\d+', d['logs']) or ['reset failed: ' + str(d.get('exceptionMessage'))]))"

echo "== 2/3 Mine traces from the last ${HOURS}h (${ENV_SCOPE})"
python3 scripts/trace_miner.py "$ORG" --hours "$HOURS" --env "$ENV_SCOPE"

echo "== 3/3 Second agent reviews the traces (read-only)"
if command -v claude > /dev/null; then
  if claude -p "$(cat scripts/trace_review_prompt.md)" --allowedTools "Read" "Grep" "Glob" \
       > "traces/proposals-${STAMP}.md" 2>&1; then
    echo "Proposals: traces/proposals-${STAMP}.md"
  else
    echo "Review failed: $(head -c 200 "traces/proposals-${STAMP}.md"). Sign in once with 'claude setup-token'."
    echo "Digest for a manual review: traces/digest-${STAMP}.md"
  fi
else
  echo "Claude Code CLI not found; skipping the review. Digest: traces/digest-${STAMP}.md"
fi
