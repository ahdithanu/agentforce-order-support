# Acme Order Support: an Agentforce service agent

[![CI](https://github.com/ahdithanu/agentforce-order-support/actions/workflows/ci.yml/badge.svg)](https://github.com/ahdithanu/agentforce-order-support/actions/workflows/ci.yml)

A customer-facing Agentforce agent for a fictional apparel brand, built end to end with Agent Script and the Salesforce CLI. It checks the customer's identity, answers order status from real `Order` records, opens a linked `Case` only after the customer confirms, locks verification after three failed attempts, and hands off to a human.

**Try it live:** https://ahdithanu.github.io/agentforce-order-support/ (open the chat in the corner and use `jamie@example.com` with order `A-1001`).
**Knowledge (RAG):** policy questions ("Do you ship to Canada?") are answered from the documents in `knowledge/` via an Agentforce Data Library.
**Voice demo:** https://ahdithanu.github.io/agentforce-order-support/voice/ (talk to the voice variant; mic-enabled Enhanced Chat)
**Retrieval lab:** https://ahdithanu.github.io/agentforce-order-support/retrieval-lab/ (recall@k for keyword, vector, hybrid, and embeddings; chunking strategies; paraphrase robustness)
**Observability:** https://ahdithanu.github.io/agentforce-order-support/observability/ (session tracing: containment, latency breakdown, topics, production vs preview traffic)
**Visual walkthrough:** the same page, covering architecture, the layers of protection, debugging stories, KPIs and the red-team grade.

All data is fictional. The agent runs in an Agentforce LabBox, a Salesforce Developer Edition org.

## What's here

| Path | What it is |
|---|---|
| `force-app/main/default/aiAuthoringBundles/Acme_Order_Support/` | The agent, in Agent Script (`.agent`) |
| `force-app/main/default/classes/Acme_*.cls` | Invocable Apex actions (USER_MODE, fail closed, ownership re-check) and tests |
| `force-app/main/default/objects/` | Order fulfillment fields (external-ID order number) and the `Case.Order__c` lookup |
| `force-app/main/default/permissionsets/` | Least-privilege access for the agent user, plus a data-admin set |
| `tests/*-testing-center.yaml` | 11-case functional regression suite (Testing Center) |
| `tests/*-security.yaml` | 12 OWASP LLM Top 10 cases, each tied to a construct in the agent |
| `scripts/preview_scenarios.py` | Scripted multi-turn preview sessions (simulated or `--live`) |
| `scripts/kpi_report.py` | Deployment-health KPIs from traces (containment, tool errors, groundedness, latency) |
| `scripts/redteam_preview.py` | Red-team runner: security cases sent through preview |
| `knowledge/` | Fictional Acme policy docs (returns, shipping, warranty and care) indexed in the Data Library `Acme_Store_Policies` |
| `tests/rag-eval.yaml`, `scripts/rag_eval.py` | 18-case RAG eval: answerable (facts + source), unanswerable (must decline), routing near-misses |
| `scripts/retrieval_lab.py`, `tests/retrieval-gold.yaml` | Offline retrieval lab: 24 labeled questions, 5 chunking strategies, BM25 / TF-IDF / LSA / embeddings / hybrid RRF, recall@k and MRR |
| `scripts/routing_eval.py`, `tests/routing-set.yaml` | Router experiment: 30 labeled utterances, default LLM router vs Einstein HyperClassifier, accuracy and routing latency |
| `scripts/compile_agent.mjs`, `scripts/validate_specs.py`, `.github/workflows/ci.yml` | CI: compile the agent with the public AgentScript SDK, validate every test spec, retrieval recall floor |
| `scripts/observability_report.py` | Production observability from Agentforce session tracing (Data Cloud STDM): sessions, containment, turn vs action latency, topics, Trust Layer steps; separates published from preview traffic |
| `force-app/.../Acme_Order_Support_Voice/`, `scripts/voice_audit.py` | Voice variant (default voice, ECv2 surface, HyperClassifier router, spoken-form and read-back rules) and a text-proxy voice-readiness audit |
| `force-app/main/default/flows/` | `Acme_Start_Return` (returns policy) and `Acme_Route_To_Service_Queue` (Omni-Channel routing for handoffs) |
| `scripts/bootstrap_org.sh` | One-command rebuild of the whole demo in any Agentforce org |
| `scripts/agent_api_client.py` | Headless Agent API client (OAuth client credentials) |
| `scripts/seed-acme-orders.apex` | Idempotent demo data |
| `docs/` | Agent spec, interview walkthrough, and the GitHub Pages site |

## Results

- **Live preview on real Apex:** all 7 scenarios passed. A wrong email was refused, the lockout went 1 → 2 → 3 → handoff, and a real Case was linked to its Order. Groundedness was 92%.
- **Testing Center regression:** topic 9/9, actions 4/4, outcome 10/10.
- **OWASP red team:** grade A (10 judged, 0 failures). One soft finding was fixed and re-verified.
- **RAG eval (live retrieval):** retrieval 12/12, correct source 12/12, answer facts 12/12, declined 4/4 unanswerable, routing 2/2, retrieval p50 about 2 s. Run 1 retrieved 0/12 because the agent user had the Data Cloud license but not the Data Cloud User permission set; the agent declined rather than inventing a policy.
- **Routing experiment (30 utterances):** default router 30/30 at p50 688 ms; HyperClassifier 26/30 at 243 ms with the original route descriptions, 29/30 at 241 ms after moving the order-vs-policy boundary into the descriptions. Default kept for text; HyperClassifier is the voice option.
- **Human handoff with context:** the agent records a handoff Case (reason + summary, linked to the verified contact and order) before escalating; the chat routes through an Omni-Channel flow to the service queue, and the Case is linked to the live messaging session so the rep sees it. Live: session Waiting in Agentforce Service Queue with the handoff Case attached.
- **Returns via Flow:** `Acme_Start_Return` (autolaunched Flow) enforces the policy (delivered, within 30 days) and opens a Return case; an admin can change the rules without code. Live: delivered order returned (Case created), undelivered order refused with the Flow's reason.
- **Cross-session lockout:** five failed checks against one order lock it for 30 minutes across every chat, even for correct details (Apex test plus a live six-chat check).
- **Coded read-back (voice):** the verify tool exists only after the caller confirms the read-back; 3/3 live runs, up from 1/3 as an instruction.
- **Voice readiness (text-proxy audit, 7 replies):** voice variant cut replies over 3 sentences from 2 to 0 and raw digit runs from 7 to 0 ("A, ten oh one", "June seventeenth"); read-back 1/2. Filler phrases belong in the voice config (`outbound_filler_sentences`), played while an action runs, not in reply text. Phone-number wiring is a UI-only step (Agent Builder, Connections, Voice).
- **Public web chat:** Enhanced Chat on an Experience Cloud site and on GitHub Pages, routed to the active agent (v6).

## Run it yourself

Requires Salesforce CLI 2.131+ and an Agentforce-enabled org with an Einstein Agent User.

```bash
sf org login web --alias my-org
sf project deploy start --target-org my-org --metadata CustomField ApexClass PermissionSet --test-level RunSpecifiedTests --tests Acme_AgentActionsTest
sf org assign permset --name Acme_Order_Support_Agent --on-behalf-of <einstein-agent-user> --target-org my-org
sf org assign permset --name Acme_Order_Data_Admin --target-org my-org
sf apex run --file scripts/seed-acme-orders.apex --target-org my-org
# set access.default_agent_user in the .agent file to your Einstein Agent User, then:
sf agent validate authoring-bundle --api-name Acme_Order_Support --target-org my-org
sf project deploy start --target-org my-org --metadata AiAuthoringBundle:Acme_Order_Support
python3 scripts/preview_scenarios.py my-org          # simulated actions
python3 scripts/preview_scenarios.py my-org --live   # real Apex
```

## If the demo org is gone (backup plan)

The live chat runs in a time-limited Agentforce LabBox (expires about Nov 13, 2026). Everything else is independent of it:

- **Still works without the org:** this repo, the walkthrough, the recorded conversations and screenshots on the Pages site (the page detects when the chat can't load and says so), the retrieval lab, the observability snapshot, the CI checks, and every eval result under `tests/results/`.
- **Rebuild in any Agentforce org in one command** (a new LabBox, a Developer Edition with Agentforce and Data Cloud, or a sandbox):

  ```bash
  sf org login web --alias new-org
  scripts/bootstrap_org.sh new-org            # deploy + permissions + seed data + knowledge library + both agents as drafts
  scripts/bootstrap_org.sh new-org --publish \
      --text-channel <text MessagingChannel> --voice-channel <voice MessagingChannel> \
      --embed-origin https://you.github.io --embed-sites <chat CustomSite>,<voice chat CustomSite>
  ```

  The script finds the org's Einstein Agent User, deploys the data model, Apex (with tests) and permission sets, grants Data Cloud access, seeds the demo orders, creates or reuses the policy data library, points the `.agent` file at both, then validates and deploys. It covers both the text and voice agents, routes the channels you name to them, and adds the embed origin to CORS and to each chat site's iframe allowlist (skipping entries that exist). It has been run end to end against the current org.
- **Voice demo routing:** the `Agentforce_Voice` messaging channel points at the `Acme_Order_Support_Voice` bot; its previous `SessionHandlerId` was the org's preinstalled VoicePlant agent. `ahdithanu.github.io` is on the `ESW_Agentforce_Voice_*` site's iframe allowlist.
- **Undo the public demo in the current org** (point the web chat back at the org's original agent): set `MessagingChannel.Salesforce_Agent_V2.SessionHandlerId` back to that agent's `BotDefinition` Id, and remove the `https://ahdithanu.github.io` CORS entry and the matching iframe-allowlist URL on the `ESW_Salesforce_Agent_Web_*` site.

## Operations

- Web chat routing: the `Salesforce_Agent_V2` messaging channel's `SessionHandlerId` points at the `Acme_Order_Support` bot. To give the chat back to the org's previous agent, set it to that agent's `BotDefinition` Id.
- Citations: for file-based (SFDRIVE) libraries, Enhanced Chat renders a "[1] / Sources" citation itself, even with `citations_enabled: False`, a non-displayable summary, and citation sources filtered from the agent. The link is a presigned S3 URL: read-only, one policy file, 20-minute expiry. Accepted for public policy docs; for private docs, use a Knowledge-article library or a custom retriever instead.
- Embedding the chat on GitHub Pages took two org changes: `https://ahdithanu.github.io` on the CORS allowlist (`CorsWhitelistEntry`), and `ahdithanu.github.io` in the chat site's **Trusted Domains for Inline Frames** (`CustomSite.siteIframeWhiteListUrls` on `ESW_Salesforce_Agent_Web_*`). Without the second, the widget fails silently because of the site's `frame-ancestors` CSP.
