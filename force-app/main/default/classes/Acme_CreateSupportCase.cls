/**
 * Agent action: open a support case for a verified order.
 * Inserts in USER_MODE and links the Case to the order's contact and the Order itself.
 */
public with sharing class Acme_CreateSupportCase {
    public class Request {
        @InvocableVariable(required=true label='Order Number') public String orderNumber;
        @InvocableVariable(required=true label='Customer Email') public String customerEmail;
        @InvocableVariable(required=true label='Subject') public String subject;
        @InvocableVariable(required=true label='Description') public String description;
    }

    public class Response {
        @InvocableVariable(label='Success') public Boolean success;
        @InvocableVariable(label='Case Number') public String caseNumber;
    }

    @InvocableMethod(label='Acme: Create Support Case' description='Opens a support case for a customer order')
    public static List<Response> createCase(List<Request> requests) {
        List<Response> results = new List<Response>();
        for (Request req : requests) {
            Response res = new Response();
            res.success = false;
            res.caseNumber = '';
            String orderNumber = req.orderNumber == null ? '' : req.orderNumber.trim().toUpperCase();
            String email = req.customerEmail == null ? '' : req.customerEmail.trim().toLowerCase();
            List<Order> found = [
                SELECT Id, BillToContactId
                FROM Order
                WHERE External_Order_Number__c = :orderNumber AND BillToContact.Email = :email
                WITH USER_MODE
                LIMIT 1
            ];
            if (!found.isEmpty() && String.isNotBlank(req.subject)) {
                Case c = new Case(
                    Subject = req.subject.left(255),
                    Description = req.description,
                    ContactId = found[0].BillToContactId,
                    Order__c = found[0].Id,
                    Origin = 'Web'
                );
                Database.SaveResult sr = Database.insert(c, false, AccessLevel.USER_MODE);
                if (sr.isSuccess()) {
                    res.success = true;
                    res.caseNumber = [SELECT CaseNumber FROM Case WHERE Id = :sr.getId() WITH USER_MODE].CaseNumber;
                }
            }
            results.add(res);
        }
        return results;
    }
}
