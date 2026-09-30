#!/usr/bin/env bash
# Rebuild the whole Acme Order Support demo in a new Agentforce org.
#
# Usage:  scripts/bootstrap_org.sh <org-alias> [--publish]
# Needs:  Salesforce CLI 2.139+, python3 (pyyaml), an authenticated org alias with Agentforce,
#         Data Cloud, and an active Einstein Agent User. A fresh Agentforce LabBox has all of these.
# Idempotent where the platform allows it: re-running reuses the data library and seed data.
set -euo pipefail

ORG="${1:?usage: scripts/bootstrap_org.sh <org-alias> [--publish]}"
PUBLISH="${2:-}"
cd "$(dirname "$0")/.."
AGENT=force-app/main/default/aiAuthoringBundles/Acme_Order_Support/Acme_Order_Support.agent
LIB_DEV=Acme_Store_Policies

step() { printf '\n== %s\n' "$*"; }
json() { python3 -c "import sys,json; d=json.load(sys.stdin); print(eval(sys.argv[1]))" "$1"; }

step "1/8 Check the org"
sf org display --json --target-org "$ORG" | json "'connected' if d.get('status')==0 else sys.exit('org alias not authenticated: $ORG')"
AGENT_USER=$(sf data query --json --target-org "$ORG" --query \
  "SELECT Username FROM User WHERE Profile.UserLicense.Name = 'Einstein Agent' AND IsActive = true ORDER BY CreatedDate LIMIT 1" \
  | json "(d['result']['records'] or [{}])[0].get('Username') or sys.exit('no active Einstein Agent User in this org')")
echo "Einstein Agent User: $AGENT_USER"

step "2/8 Deploy data model, Apex actions (with tests), permission sets"
sf project deploy start --json --target-org "$ORG" --wait 30 \
  --metadata CustomField:Order.External_Order_Number__c CustomField:Order.Fulfillment_Status__c \
             CustomField:Order.Carrier__c CustomField:Order.Tracking_Number__c CustomField:Order.Estimated_Delivery__c \
             CustomField:Case.Order__c ApexClass:Acme_VerifyCustomer ApexClass:Acme_GetOrderStatus \
             ApexClass:Acme_CreateSupportCase ApexClass:Acme_AgentActionsTest \
             PermissionSet:Acme_Order_Support_Agent PermissionSet:Acme_Order_Data_Admin \
  --test-level RunSpecifiedTests --tests Acme_AgentActionsTest | json "d['result']['status']"

step "3/8 Grant access (agent user: actions + Data Cloud; admin: seed data)"
sf org assign permset --json --target-org "$ORG" --name Acme_Order_Support_Agent --on-behalf-of "$AGENT_USER" >/dev/null || true
sf org assign permset --json --target-org "$ORG" --name GenieUserEnhancedSecurity --on-behalf-of "$AGENT_USER" >/dev/null || true
sf org assign permsetlicense --json --target-org "$ORG" --name GenieDataPlatformStarterPsl --on-behalf-of "$AGENT_USER" >/dev/null || true
sf org assign permset --json --target-org "$ORG" --name Acme_Order_Data_Admin >/dev/null || true
sf data query --json --target-org "$ORG" --query \
  "SELECT PermissionSet.Name FROM PermissionSetAssignment WHERE Assignee.Username = '$AGENT_USER'" \
  | json "sorted(r['PermissionSet']['Name'] for r in d['result']['records'])"

step "4/8 Seed demo orders"
sf apex run --json --target-org "$ORG" --file scripts/seed-acme-orders.apex | json "'seeded' if d['result']['success'] else sys.exit(d['result'].get('exceptionMessage'))"

step "5/8 Policy knowledge base (Agentforce Data Library)"
LIB_ID=$(sf agent adl list --json --target-org "$ORG" | json "next((l.get('libraryId') or l.get('id') for l in (d.get('result') or {}).get('libraries', []) if l.get('developerName')=='$LIB_DEV'), '')")
if [ -z "$LIB_ID" ]; then
  LIB_ID=$(sf agent adl create --json --target-org "$ORG" --name "Acme Store Policies" --developer-name "$LIB_DEV" --source-type sfdrive | json "d['result']['libraryId']")
  sf agent adl upload --json -i "$LIB_ID" --target-org "$ORG" --wait 15 \
    --file knowledge/Acme_Returns_and_Exchanges.html --file knowledge/Acme_Shipping_and_Delivery.html \
    --file knowledge/Acme_Warranty_and_Care.html | json "d['result'].get('status')"
fi
echo "Library: $LIB_ID"

step "6/8 Point the agent at this org (agent user + library)"
python3 - "$AGENT" "$AGENT_USER" "ARFPC_$LIB_ID" <<'PY'
import re, sys
path, user, rag = sys.argv[1:]
s = open(path).read()
s = re.sub(r'(default_agent_user: )"[^"]*"', rf'\1"{user}"', s, count=1)
s = re.sub(r'(rag_feature_config_id: )"[^"]*"', rf'\1"{rag}"', s, count=1)
open(path, "w").write(s)
print("updated", path)
PY

step "7/8 Validate and deploy the agent (draft)"
sf agent validate authoring-bundle --json --api-name Acme_Order_Support --target-org "$ORG" | json "'valid' if d.get('status')==0 else sys.exit(d.get('message'))"
sf project deploy start --json --target-org "$ORG" --metadata AiAuthoringBundle:Acme_Order_Support --wait 15 | json "d['result']['status']"

if [ "$PUBLISH" = "--publish" ]; then
  step "8/8 Publish and activate"
  sf agent publish authoring-bundle --json --api-name Acme_Order_Support --target-org "$ORG" | json "'published' if d.get('status')==0 else sys.exit(d.get('message'))"
  VERSION=$(sf data query --json --target-org "$ORG" --query \
    "SELECT MAX(VersionNumber) v FROM BotVersion WHERE BotDefinition.DeveloperName = 'Acme_Order_Support'" | json "d['result']['records'][0]['v']")
  sf agent activate --json --api-name Acme_Order_Support --version "$VERSION" --target-org "$ORG" | json "'active v' + str(d['result']['version'])"
else
  step "8/8 Skipped publish (draft only). Re-run with --publish to make it live."
fi

cat <<EOF

Done. Next:
  python3 scripts/preview_scenarios.py $ORG --live      # end-to-end smoke test (creates one demo Case)
  python3 scripts/rag_eval.py $ORG                        # policy RAG eval (read-only)
Web chat: route a Messaging channel to Acme_Order_Support (SessionHandlerId = the bot's BotDefinition Id),
and for an external site add its origin to CORS and to the chat site's Trusted Domains for Inline Frames.
EOF
