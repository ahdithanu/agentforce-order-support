/**
 * Agent action: open a support case for a verified order.
 * STUB: returns a mock case number and performs no DML. A real version would
 * insert a Case (AccessLevel.USER_MODE) linked to the Contact and Order.
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
        Integer seq = 1;
        for (Request req : requests) {
            Response res = new Response();
            res.success = String.isNotBlank(req.orderNumber) && String.isNotBlank(req.subject);
            res.caseNumber = res.success ? 'CS-' + String.valueOf(40000 + seq) : '';
            seq++;
            results.add(res);
        }
        return results;
    }
}
