# Synthetic STDM fixture

`stdm_synthetic.json` is hand authored test data, not a Salesforce export. No visitor conversations, credentials or real Salesforce IDs are included. Email addresses use `example.com`; phone numbers use fictional 555 exchanges. These values intentionally retain PII shapes to verify masking. Do not replace this file with a production export.

The top level contains a timezone aware ISO 8601 `as_of` timestamp and four arrays:

| Array | Required fields per record |
| :--- | :--- |
| `ssot__AiAgentSession__dlm` | `ssot__Id__c`, `ssot__AiAgentSessionEndType__c`, `ssot__StartTimestamp__c` |
| `ssot__AiAgentInteraction__dlm` | `ssot__Id__c`, `ssot__AiAgentSessionId__c`, `ssot__TopicApiName__c`, `ssot__AiAgentInteractionType__c`, `ssot__StartTimestamp__c`, `ssot__EndTimestamp__c` |
| `ssot__AiAgentInteractionStep__dlm` | `ssot__AiAgentInteractionId__c`, `ssot__AiAgentInteractionStepType__c`, `ssot__Name__c`, `ssot__InputValueText__c`, `ssot__OutputValueText__c`, `ssot__ErrorMessageText__c` |
| `ssot__AiAgentInteractionMessage__dlm` | `ssot__AiAgentInteractionId__c`, `ssot__AiAgentInteractionMessageType__c`, `ssot__ContentText__c`, `ssot__MessageSentTimestamp__c` |

Every field is a string or null. Step input and output values are JSON encoded strings, as returned by the live query. `NOT_SET` is retained where the miner already treats it as absent. Extra fields are allowed; missing required tables or fields are rejected. Both sources use the projection in `scripts/trace_sources.py` to produce the same internal record keys.

The arrays are deliberately out of order. Session and interaction IDs join records; message timestamps establish text order. A nonturn routing interaction establishes the Acme environment for subsequent off topic turns. Other examples exercise an action failure, a false verification result, Trust Layer verdicts, a repeated unresolved request, a policy decline, a requested handoff, an unrequested handoff and an expired unresolved session. A successful action at exactly the slow threshold supplies a negative control. Malformed step JSON, missing values, old sessions, an unrelated agent and orphan records exercise exclusions.

The default run's five candidates retain TODO expectations for review. Express shipping and the functional verification utterance are already in the committed eval suites and are excluded, including the latter's masked email. Repeated utterances within the fixture are also excluded. The preview session tests Spanish text and the `PlannerPreview` topic suffix. The fixture clock controls selection and file names independently of the current date.
