# Acme Order Support: an Agentforce service agent

A customer-facing Agentforce agent for a fictional apparel brand, built end to end with Agent Script and the Salesforce CLI. It checks the customer's identity, answers order status from real `Order` records, opens a linked `Case` only after the customer confirms, locks verification after three failed attempts, and hands off to a human.

**Try it live:** https://ahdithanu.github.io/agentforce-order-support/ (open the chat in the corner and use `jamie@example.com` with order `A-1001`).
**Knowledge (RAG):** policy questions ("Do you ship to Canada?") are answered from the documents in `knowledge/` via an Agentforce Data Library.
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
| `scripts/agent_api_client.py` | Headless Agent API client (OAuth client credentials) |
| `scripts/seed-acme-orders.apex` | Idempotent demo data |
| `docs/` | Agent spec, interview walkthrough, and the GitHub Pages site |

## Results

- **Live preview on real Apex:** all 7 scenarios passed. A wrong email was refused, the lockout went 1 → 2 → 3 → handoff, and a real Case was linked to its Order. Groundedness was 92%.
- **Testing Center regression:** topic 9/9, actions 4/4, outcome 10/10.
- **OWASP red team:** grade A (10 judged, 0 failures). One soft finding was fixed and re-verified.
- **RAG eval (live retrieval):** retrieval 12/12, correct source 12/12, answer facts 12/12, declined 4/4 unanswerable, routing 2/2, retrieval p50 about 2 s. Run 1 retrieved 0/12 because the agent user had the Data Cloud license but not the Data Cloud User permission set; the agent declined rather than inventing a policy.
- **Public web chat:** Enhanced Chat on an Experience Cloud site and on GitHub Pages, routed to the active agent (v3).

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

## Operations

- Web chat routing: the `Salesforce_Agent_V2` messaging channel's `SessionHandlerId` points at the `Acme_Order_Support` bot. To give the chat back to the org's previous agent, set it to that agent's `BotDefinition` Id.
- Citations: for file-based (SFDRIVE) libraries, Enhanced Chat renders a "[1] / Sources" citation itself, even with `citations_enabled: False`, a non-displayable summary, and citation sources filtered from the agent. The link is a presigned S3 URL: read-only, one policy file, 20-minute expiry. Accepted for public policy docs; for private docs, use a Knowledge-article library or a custom retriever instead.
- Embedding the chat on GitHub Pages took two org changes: `https://ahdithanu.github.io` on the CORS allowlist (`CorsWhitelistEntry`), and `ahdithanu.github.io` in the chat site's **Trusted Domains for Inline Frames** (`CustomSite.siteIframeWhiteListUrls` on `ESW_Salesforce_Agent_Web_*`). Without the second, the widget fails silently because of the site's `frame-ancestors` CSP.
