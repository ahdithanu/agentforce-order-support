/**
 * Agent action: record context for a human handoff, right before the agent escalates.
 * Creates a Case with the agent's reason and factual summary (linked to the customer's Contact
 * and Order when the customer was verified) and links it to the live chat's MessagingSession,
 * so the human who accepts the chat sees the Case next to the conversation.
 *
 * Best effort by design: any failure still returns recorded = true, because a logging problem
 * must never stop a customer from reaching a person. The MessagingSession id comes from the
 * agent's linked RoutableId variable (set by the platform), never from the model.
 */
public with sharing class Acme_RecordHandoff {
    public class Request {
        @InvocableVariable(label='Messaging Session Id' description='Bound from the platform RoutableId')
        public String messagingSessionId;
        @InvocableVariable(required=true label='Reason' description='Why the customer needs a person, a few words')
        public String reason;
        @InvocableVariable(required=true label='Summary' description='Factual summary of the need and what was done')
        public String summary;
        @InvocableVariable(label='Verified Order Number') public String verifiedOrderNumber;
        @InvocableVariable(label='Verified Email') public String verifiedEmail;
    }

    public class Response {
        @InvocableVariable(label='Recorded') public Boolean recorded;
        @InvocableVariable(label='Case Number') public String caseNumber;
    }

    @InvocableMethod(label='Acme: Record Handoff' description='Creates a handoff Case with the agent summary and links it to the chat session')
    public static List<Response> record(List<Request> requests) {
        List<Response> results = new List<Response>();
        for (Request req : requests) {
            Response res = new Response();
            res.recorded = true;
            res.caseNumber = '';
            try {
                Case c = new Case(
                    Subject = ('Agent handoff: ' + (String.isBlank(req.reason) ? 'customer asked for a person' : req.reason)).left(255),
                    Description = req.summary,
                    Origin = 'Web'
                );
                String orderNumber = req.verifiedOrderNumber == null ? '' : req.verifiedOrderNumber.trim().toUpperCase();
                String email = req.verifiedEmail == null ? '' : req.verifiedEmail.trim().toLowerCase();
                if (String.isNotBlank(orderNumber) && String.isNotBlank(email)) {
                    List<Order> found = [
                        SELECT Id, BillToContactId FROM Order
                        WHERE External_Order_Number__c = :orderNumber AND BillToContact.Email = :email
                        WITH USER_MODE LIMIT 1
                    ];
                    if (!found.isEmpty()) {
                        c.ContactId = found[0].BillToContactId;
                        c.Order__c = found[0].Id;
                    }
                }
                Database.SaveResult sr = Database.insert(c, false, AccessLevel.USER_MODE);
                if (sr.isSuccess()) {
                    res.caseNumber = [SELECT CaseNumber FROM Case WHERE Id = :sr.getId() WITH USER_MODE].CaseNumber;
                    if (String.isNotBlank(req.messagingSessionId) && req.messagingSessionId.startsWith('0Mw')) {
                        // Linking the platform-owned session record is done in system mode: the id is
                        // trusted (platform-bound) and the agent user needs no edit access to sessions.
                        Database.update(new MessagingSession(Id = req.messagingSessionId, CaseId = sr.getId()), false, AccessLevel.SYSTEM_MODE);
                    }
                }
            } catch (Exception e) {
                System.debug(LoggingLevel.WARN, 'Handoff record failed: ' + e.getMessage());
            }
            results.add(res);
        }
        return results;
    }
}
