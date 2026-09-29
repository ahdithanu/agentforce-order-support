# Acme Order Support: FDE build walkthrough

What I built on the night of Sept 28, 2026 in an Agentforce LabBox, mapped to the Salesforce FDE role. Everything below is in this repo and was run for real. Numbers come from `tests/reports/kpi-report.html`.

## 60-second version

"I built a customer-facing Agentforce service agent for a fictional apparel brand. It verifies the customer, answers order-status questions from real Order records, opens a linked Case after the customer confirms, and hands off to a human. It's written in Agent Script and source-controlled. It runs against a custom data model with least-privilege permissions, and it has a functional and security eval suite plus a KPI report built from traces. The most useful moment: the first preview looked plausible but was completely broken. The agent user couldn't see the Apex, so the platform withheld the tool, and the model covered for it by saying 'verifying, one moment'. I found it in the traces, fixed it in both the permissions and the prompt, and made that failure mode a tracked metric: unbacked progress claims went from 3 to 0 and tool-availability errors from 8 to 0."

## Architecture

```
Customer (Enhanced Chat) -> agent_router (start_agent)
  |- identity_verification --verify--> Acme_VerifyCustomer (Apex, USER_MODE)
  |    `- sets customer_verified, verified_email, verified_order_number
  |- order_status   (runs Acme_GetOrderStatus deterministically; inputs bound from verified state)
  |- support_case   (open_case -> Acme_CreateSupportCase; asks the user to confirm; one case per session)
  |- escalation     (@utils.escalate -> Omni-Channel queue)
  `- off_topic / ambiguous_question (platform guardrail subagents)

Data: Order + External_Order_Number__c (external ID), Fulfillment_Status__c, Carrier__c,
      Tracking_Number__c, Estimated_Delivery__c; Case.Order__c lookup; BillToContact.Email = identity
Access: Acme_Order_Support_Agent permission set (read Order/Contact, create Case, class access)
        Acme_Order_Data_Admin permission set (integration/seed/tests); Einstein Agent User runs actions
```

## Decisions I'd defend

| Decision | Why |
|---|---|
| Order actions are **hidden** (`available when customer_verified == True`), not just discouraged in the prompt | Verification is a security boundary. The prompt injection test (U6) can't call a tool that doesn't exist for the model. |
| Order and Case inputs are **bound from verified state**, not filled in by the LLM | The model can't be talked into looking up someone else's order after verifying as Jamie (security case SEC-03). |
| Apex **re-checks** that the email matches the order in the status and case actions | Defense in depth: the action doesn't trust that the agent only calls it after verification. |
| Verification **fails closed** and never says which field was wrong | Stops people guessing which email goes with which order. |
| **Lockout after 3 failed checks**: Apex returns the count, the agent passes it back in from saved state, hides verification at 3, and hands off to a person | Rules that must always hold go in code. The model can't reset the count. Live: 1 → 2 → 3 → handoff. |
| `WITH USER_MODE` and `AccessLevel.USER_MODE` everywhere | The agent user's permissions are the real boundary. Tests run as a least-privilege user to prove it. |
| Order status runs **deterministically** (`run`) and the model is told the exact values | No chance the model skips the lookup or paraphrases the tracking number. |
| Customer confirms before a Case is created, and there's one Case per session | Writes are consequential; also blocks the "open five more cases" abuse (SEC-07). |
| A separate customer-facing order number field (external ID) instead of `OrderNumber` | The customer knows `A-1001`, not the autonumber. It's also the upsert key for the commerce integration. |
| Draft-first: validate, deploy draft, preview; publish and activate only as explicit release steps | Matches how you'd ship in a customer org with change control. |

## Three stories (situation, what I did, result)

**1. "It looked like it worked, but it was faking it."**
The first preview answered politely: "I'll verify your details, one moment." The trace showed `verify_customer` withheld with `NO_USER_ACCESS`, because the Einstein Agent User had no access to the Apex class. With no tool available, the model made up progress, and in one session it even said "details don't match" without checking anything. **Fix:** a least-privilege permission set, plus a system-prompt rule: "Never say you're checking unless you call the tool this turn; if the tool isn't available, say so and offer a person." **Result:** withheld-tool events 8 → 0, unbacked progress claims 3 → 0, and both are now tracked KPIs. *Lesson: plan for tools that fail or disappear at runtime, and give the model an honest fallback when they do.*

**2. The tests failed even for the admin.**
Moving from sample data to real Order data, all 3 Apex tests failed with "fields being inaccessible on Order". That's because new custom fields have no field-level access for anyone until you grant it, and user-mode writes enforce that. **Fix:** instead of giving admins broad access, I added a data-admin permission set and made the tests run as a dedicated Standard User holding only the two Acme permission sets. **Result:** 3/3 pass, and the tests now prove the agent's real access works, not the admin's.

**3. Simulated actions gave a false pass.**
In simulated mode, the wrong-email scenario (U3) "verified" and returned FedEx tracking, but the data says UPS. Simulated mode makes up tool outputs, so it validates routing, not logic. **Result:** I use simulated runs for routing and conversation flow, live runs against seeded data for logic, and the Testing Center suite for regression. The live run then refused the wrong email (`verified: false`, nothing shared): verification success was 33% live, versus 100% in simulation.

**4. Tests need testing.**
The first Testing Center run against the live agent failed 4 of 11 cases. None were agent bugs. A successful verification moves to `order_status` in the same turn. Testing Center reports definition names (`verify_customer`), not in-conversation names. The confirmation step correctly stopped Case creation. And the off-topic subagent is designed not to acknowledge the request. **Fix:** corrected the spec, not the agent. **Result:** topic 9/9, actions 4/4, outcome 10/10. One case hangs at random per run, so CI needs per-case timeouts and retries.

Bonus setup story: the CLI's OAuth login timed out twice. Node 26 had it listening only on IPv6 (`[::1]:1717`), and the browser kept reusing a different org's session. I fixed it with `NODE_OPTIONS=--dns-result-order=ipv4first`, then switched to the Labs device flow so credentials never went through a browser or chat. Being able to fix environment problems is half the job when you're working on-site with customers.

## Mapping to the job description

| JD line | Evidence in this repo |
|---|---|
| Design agent intelligence: prompts, reasoning, tool calls | `Acme_Order_Support.agent`: rules enforced in code vs. left to the model, bound inputs, deterministic lookup, confirmation |
| Explain why a prompt failed and what you'd change | Story 1, found from the trace, fixed in two layers, tracked as a metric |
| Own components end-to-end, deployed, validated, observable | Local compile, then org validation, draft deploy, preview traces, KPI report |
| Data modeling, APIs, integration patterns | Order extension with an external ID upsert key, Case→Order lookup, idempotent seed, USER_MODE Apex |
| Validated live, not just simulated | 7/7 live scenarios on real Apex: wrong email refused, lockout, real Case 00001026 linked to its Order, 92% groundedness |
| Evaluate AI outputs with engineering rigor | `tests/*-testing-center.yaml` (11 functional), `tests/*-security.yaml` (12 OWASP cases, each tied to a specific construct in the agent) |
| Agent performance dashboards and KPI reporting | `scripts/kpi_report.py`: containment, escalation, tool errors, unbacked claims, groundedness, safety, latency |
| POCs from sketch to deployable in days | Empty folder to deployed, tested agent in one evening |
| Surface platform gaps to product | The `DurableId` field in the LabBox doc query doesn't exist on Organization in v67; `sf org login sfdx-url --sfdx-url-stdin` needs `-`; no simulate mode in `sf agent test run` |

## How I'd take it to production

1. **Channel:** connect to the existing Enhanced Chat channel and `Agentforce_Service_Queue`, and pass the chat's customer context through so logged-in customers can skip verification.
2. **Integration:** swap the seed data for the commerce system, using an upsert on `External_Order_Number__c` via a Named Credential and a platform-event or scheduled sync. Put the carrier API behind External Services if we want live tracking.
3. **Hardening:** session lockout is done. Next: a lockout per order that persists across chats, rate limits, and a review queue for Cases the agent created.
4. **Evals in CI:** deploy both suites to a sandbox, run them on every change to the agent file, and block the release on any critical security failure.
5. **Observability:** turn on Session Tracing and Agent Analytics, feed the same KPIs from production sessions (containment, escalations with reasons, tool errors), and review weekly with the Deployment Strategist against the customer's business KPI (cost per contact, CSAT).

## Likely questions, short answers

- **Why Agent Script instead of Builder?** You can diff and review it, compile it locally, and deploy it through CI. Builder is great for discovery with the customer; code is what you maintain.
- **Service vs. employee agent?** A service agent is customer-facing, runs as the Einstein Agent User, needs that license, and can escalate over Messaging. An employee agent runs as the logged-in user and can't have `default_agent_user` or escalation.
- **Where does the LLM decide vs. code?** The LLM handles intent, routing, extracting email and order number, and writing the Case subject and description. Code handles access to actions, which inputs go to the tools, the ownership check in Apex, and the confirmation gate.
- **Biggest risk left?** The Case description is an injection sink that a human reads later (SEC-06). Mitigations: tell the agent to describe only the customer's problem, show agents clearly that the text is customer-written, and never let the description trigger automation.
