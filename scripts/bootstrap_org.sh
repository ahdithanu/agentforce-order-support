#!/usr/bin/env bash
# Rebuild the whole Acme Order Support demo (text + voice agents) in a new Agentforce org.
#
# Usage:
#   scripts/bootstrap_org.sh <org-alias> [--publish]
#       [--text-channel <MessagingChannel DeveloperName>] [--voice-channel <DeveloperName>]
#       [--embed-origin https://you.github.io --embed-sites <CustomSite>[,<CustomSite>...]]
#
# Needs: Salesforce CLI 2.139+, python3 (pyyaml), an authenticated org alias with Agentforce,
#        Data Cloud, and an active Einstein Agent User. A fresh Agentforce LabBox has all of these.
# Idempotent where the platform allows it: re-running reuses the data library and seed data,
# and skips CORS / iframe-allowlist entries that already exist.
set -euo pipefail

ORG="${1:?usage: scripts/bootstrap_org.sh <org-alias> [--publish] [--text-channel X] [--voice-channel Y] [--embed-origin URL --embed-sites A,B]}"
shift
PUBLISH="" TEXT_CHANNEL="" VOICE_CHANNEL="" EMBED_ORIGIN="" EMBED_SITES=""
while [ $# -gt 0 ]; do
  case "$1" in
    --publish) PUBLISH=1 ;;
    --text-channel) TEXT_CHANNEL="$2"; shift ;;
    --voice-channel) VOICE_CHANNEL="$2"; shift ;;
    --embed-origin) EMBED_ORIGIN="$2"; shift ;;
    --embed-sites) EMBED_SITES="$2"; shift ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done

cd "$(dirname "$0")/.."
BUNDLES=(Acme_Order_Support Acme_Order_Support_Voice)
LIB_DEV=Acme_Store_Policies

step() { printf '\n== %s\n' "$*"; }
json() { python3 -c "import sys,json; d=json.load(sys.stdin); print(eval(sys.argv[1]))" "$1"; }
q() { sf data query --json --target-org "$ORG" --query "$1"; }

step "1/9 Check the org"
sf org display --json --target-org "$ORG" | json "'connected' if d.get('status')==0 else sys.exit('org alias not authenticated: $ORG')"
AGENT_USER=$(q "SELECT Username FROM User WHERE Profile.UserLicense.Name = 'Einstein Agent' AND IsActive = true ORDER BY CreatedDate LIMIT 1" \
  | json "(d['result']['records'] or [{}])[0].get('Username') or sys.exit('no active Einstein Agent User in this org')")
echo "Einstein Agent User: $AGENT_USER"

step "2/9 Deploy data model, Apex actions (with tests), permission sets"
sf project deploy start --json --target-org "$ORG" --wait 30 \
  --metadata CustomField:Order.External_Order_Number__c CustomField:Order.Fulfillment_Status__c \
             CustomField:Order.Carrier__c CustomField:Order.Tracking_Number__c CustomField:Order.Estimated_Delivery__c \
             CustomField:Order.Failed_Verification_Count__c CustomField:Order.Verification_Locked_Until__c \
             CustomField:Case.Order__c ApexClass:Acme_VerifyCustomer ApexClass:Acme_GetOrderStatus \
             ApexClass:Acme_CreateSupportCase ApexClass:Acme_RecordHandoff ApexClass:Acme_AgentActionsTest \
             PermissionSet:Acme_Order_Support_Agent PermissionSet:Acme_Order_Data_Admin \
             ServicePresenceStatus:Acme_Available_for_Chat PermissionSet:Acme_Human_Agent \
  --test-level RunSpecifiedTests --tests Acme_AgentActionsTest | json "d['result']['status']"

step "3/9 Grant access (agent user: actions + Data Cloud; admin: seed data)"
sf org assign permset --json --target-org "$ORG" --name Acme_Order_Support_Agent --on-behalf-of "$AGENT_USER" >/dev/null || true
sf org assign permset --json --target-org "$ORG" --name GenieUserEnhancedSecurity --on-behalf-of "$AGENT_USER" >/dev/null || true
sf org assign permsetlicense --json --target-org "$ORG" --name GenieDataPlatformStarterPsl --on-behalf-of "$AGENT_USER" >/dev/null || true
sf org assign permset --json --target-org "$ORG" --name Acme_Order_Data_Admin >/dev/null || true
sf org assign permset --json --target-org "$ORG" --name Acme_Human_Agent >/dev/null || true
q "SELECT PermissionSet.Name FROM PermissionSetAssignment WHERE Assignee.Username = '$AGENT_USER'" \
  | json "sorted(r['PermissionSet']['Name'] for r in d['result']['records'])"

step "4/9 Seed demo orders"
sf apex run --json --target-org "$ORG" --file scripts/seed-acme-orders.apex | json "'seeded' if d['result']['success'] else sys.exit(d['result'].get('exceptionMessage'))"

step "5/9 Policy knowledge base (Agentforce Data Library)"
LIB_ID=$(sf agent adl list --json --target-org "$ORG" | json "next((l.get('libraryId') or l.get('id') for l in (d.get('result') or {}).get('libraries', []) if l.get('developerName')=='$LIB_DEV'), '')")
if [ -z "$LIB_ID" ]; then
  LIB_ID=$(sf agent adl create --json --target-org "$ORG" --name "Acme Store Policies" --developer-name "$LIB_DEV" --source-type sfdrive | json "d['result']['libraryId']")
  sf agent adl upload --json -i "$LIB_ID" --target-org "$ORG" --wait 15 \
    --file knowledge/Acme_Returns_and_Exchanges.html --file knowledge/Acme_Shipping_and_Delivery.html \
    --file knowledge/Acme_Warranty_and_Care.html | json "d['result'].get('status')"
fi
echo "Library: $LIB_ID"

step "5b/9 Omni-Channel routing flow for human handoff (org-specific ids filled in)"
CHANNEL_ID=$(q "SELECT Id FROM ServiceChannel WHERE DeveloperName = 'sfdc_livemessage'" | json "(d['result']['records'] or [{}])[0].get('Id') or sys.exit('no Messaging service channel (enable Messaging first)')")
QUEUE_ID=$(q "SELECT Id FROM Group WHERE Type = 'Queue' AND DeveloperName = 'Agentforce_Service_Queue'" | json "(d['result']['records'] or [{}])[0].get('Id') or sys.exit('no Agentforce_Service_Queue queue')")
ROUTING_ID=$(q "SELECT Id FROM QueueRoutingConfig ORDER BY CreatedDate LIMIT 1" | json "(d['result']['records'] or [{}])[0].get('Id') or sys.exit('no routing configuration')")
python3 - force-app/main/default/flows/Acme_Route_To_Service_Queue.flow-meta.xml "$CHANNEL_ID" "$QUEUE_ID" "$ROUTING_ID" <<'PY'
import re, sys
path, channel, queue, routing = sys.argv[1:]
s = open(path).read()
s = re.sub(r"(<name>serviceChannelId</name>\s*<value>\s*<stringValue>)[^<]*", rf"\g<1>{channel}", s)
s = re.sub(r"(<name>queueId</name>\s*<value>\s*<stringValue>)[^<]*", rf"\g<1>{queue}", s)
s = re.sub(r"(<name>routingConfigId</name>\s*<value>\s*<stringValue>)[^<]*", rf"\g<1>{routing}", s)
open(path, "w").write(s)
print("routing flow ->", channel, queue, routing)
PY
sf project deploy start --json --target-org "$ORG" --metadata Flow:Acme_Route_To_Service_Queue --wait 10 | json "d['result']['status']"

step "6/9 Point both agents at this org (agent user + library)"
for B in "${BUNDLES[@]}"; do
  python3 - "force-app/main/default/aiAuthoringBundles/$B/$B.agent" "$AGENT_USER" "ARFPC_$LIB_ID" <<'PY'
import re, sys
path, user, rag = sys.argv[1:]
s = open(path).read()
s = re.sub(r'(default_agent_user: )"[^"]*"', rf'\1"{user}"', s, count=1)
s = re.sub(r'(rag_feature_config_id: )"[^"]*"', rf'\1"{rag}"', s, count=1)
open(path, "w").write(s)
print("updated", path)
PY
done

step "7/9 Validate and deploy both agents (draft)"
for B in "${BUNDLES[@]}"; do
  sf agent validate authoring-bundle --json --api-name "$B" --target-org "$ORG" | json "'$B valid' if d.get('status')==0 else sys.exit(d.get('message'))"
  sf project deploy start --json --target-org "$ORG" --metadata "AiAuthoringBundle:$B" --wait 15 | json "'$B ' + d['result']['status']"
done

if [ -n "$PUBLISH" ]; then
  step "8/9 Publish and activate both agents"
  for B in "${BUNDLES[@]}"; do
    sf agent publish authoring-bundle --json --api-name "$B" --target-org "$ORG" | json "'$B published' if d.get('status')==0 else sys.exit(d.get('message'))"
    VERSION=$(q "SELECT MAX(VersionNumber) v FROM BotVersion WHERE BotDefinition.DeveloperName = '$B'" | json "d['result']['records'][0]['v']")
    sf agent activate --json --api-name "$B" --version "$VERSION" --target-org "$ORG" | json "'$B active v' + str(d['result']['version'])"
  done
else
  step "8/9 Skipped publish (draft only). Re-run with --publish to make them live."
fi

step "9/9 Channels and embedding"
route() {  # route <MessagingChannel DeveloperName> <bot DeveloperName>
  local CH_ID BOT_ID
  CH_ID=$(q "SELECT Id FROM MessagingChannel WHERE DeveloperName = '$1'" | json "(d['result']['records'] or [{}])[0].get('Id') or sys.exit('no messaging channel named $1')")
  BOT_ID=$(q "SELECT Id FROM BotDefinition WHERE DeveloperName = '$2'" | json "(d['result']['records'] or [{}])[0].get('Id') or sys.exit('bot $2 not found; publish first')")
  sf data update record --json --target-org "$ORG" --sobject MessagingChannel --record-id "$CH_ID" --values "SessionHandlerId='$BOT_ID'" >/dev/null
  echo "channel $1 -> $2 ($BOT_ID)"
}
[ -n "$TEXT_CHANNEL" ] && route "$TEXT_CHANNEL" Acme_Order_Support
[ -n "$VOICE_CHANNEL" ] && route "$VOICE_CHANNEL" Acme_Order_Support_Voice
if [ -n "$EMBED_ORIGIN" ]; then
  HOST="${EMBED_ORIGIN#https://}"; HOST="${HOST%%/*}"
  if [ "$(q "SELECT COUNT() FROM CorsWhitelistEntry WHERE UrlPattern = '$EMBED_ORIGIN'" | json "d['result']['totalSize']")" = "0" ]; then
    sf data create record --json --target-org "$ORG" --sobject CorsWhitelistEntry --values "UrlPattern='$EMBED_ORIGIN'" >/dev/null
    echo "CORS: added $EMBED_ORIGIN"
  else
    echo "CORS: $EMBED_ORIGIN already allowed"
  fi
  IFS=',' read -r -a SITES <<< "$EMBED_SITES"
  for SITE in "${SITES[@]}"; do
    [ -z "$SITE" ] && continue
    TMP=".tmp-site-$SITE"; rm -rf "$TMP"
    sf project retrieve start --json --target-org "$ORG" --metadata "CustomSite:$SITE" --target-metadata-dir "$TMP" --unzip >/dev/null
    F=$(find "$TMP" -name "$SITE.site" | head -1)
    [ -z "$F" ] && { echo "site $SITE not found"; rm -rf "$TMP"; continue; }
    CHANGED=$(python3 - "$F" "$HOST" <<'PY'
import re, sys
path, host = sys.argv[1:]
s = open(path).read()
if f"<url>{host}</url>" in s:
    print("no"); sys.exit()
block = f"    <siteIframeWhiteListUrls>\n        <url>{host}</url>\n    </siteIframeWhiteListUrls>"
existing = re.findall(r"    <siteIframeWhiteListUrls>\n        <url>[^<]+</url>\n    </siteIframeWhiteListUrls>", s)
s = s.replace(existing[-1], existing[-1] + "\n" + block, 1) if existing else s.replace("</CustomSite>", block + "\n</CustomSite>", 1)
open(path, "w").write(s)
print("yes")
PY
)
    if [ "$CHANGED" = "yes" ]; then
      sf project deploy start --json --target-org "$ORG" --metadata-dir "$(dirname "$(dirname "$F")")" --wait 10 | json "'site $SITE: iframe allowlist + $HOST (' + d['result']['status'] + ')'"
    else
      echo "site $SITE: $HOST already allowed"
    fi
    rm -rf "$TMP"
  done
fi
[ -z "$TEXT_CHANNEL$VOICE_CHANNEL$EMBED_ORIGIN" ] && echo "No channel or embed options given; skipped."

cat <<EOF

Done. Next:
  python3 scripts/preview_scenarios.py $ORG --live                              # text agent smoke test (creates one demo Case)
  BUNDLE=Acme_Order_Support_Voice python3 scripts/preview_scenarios.py $ORG     # voice agent, simulated actions
  python3 scripts/rag_eval.py $ORG                                              # policy RAG eval (read-only)
EOF
