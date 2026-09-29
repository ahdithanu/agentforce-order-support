/**
 * Agent action: verify a customer by checkout email + order number.
 * Runs in USER_MODE so the Einstein Agent User's object and field access is enforced.
 * Fails closed: any mismatch or missing record returns verified = false with empty outputs,
 * and the response never says which of the two values was wrong.
 * Returns the authoritative failed-attempt count for the session: the agent binds its current
 * count as input (not model-filled), so the model cannot reset or lower it.
 */
public with sharing class Acme_VerifyCustomer {
    public class Request {
        @InvocableVariable(required=true label='Email' description='Email used at checkout')
        public String email;
        @InvocableVariable(required=true label='Order Number' description='Order number, e.g. A-1001')
        public String orderNumber;
        @InvocableVariable(label='Prior Failed Attempts' description='Failed attempts so far in this session')
        public Decimal priorFailedAttempts;
    }

    public class Response {
        @InvocableVariable(label='Verified') public Boolean verified;
        @InvocableVariable(label='Verified Email') public String verifiedEmail;
        @InvocableVariable(label='Canonical Order Number') public String canonicalOrderNumber;
        @InvocableVariable(label='Customer First Name') public String customerFirstName;
        @InvocableVariable(label='Failed Attempts') public Decimal failedAttempts;
    }

    public static final Integer MAX_ATTEMPTS = 3;

    @InvocableMethod(label='Acme: Verify Customer' description='Checks that an email and order number belong to the same customer')
    public static List<Response> verify(List<Request> requests) {
        Set<String> orderNumbers = new Set<String>();
        for (Request req : requests) {
            orderNumbers.add(normalizeOrderNumber(req.orderNumber));
        }
        Map<String, Order> ordersByNumber = new Map<String, Order>();
        for (Order o : [
            SELECT External_Order_Number__c, BillToContact.Email, BillToContact.FirstName
            FROM Order
            WHERE External_Order_Number__c IN :orderNumbers
            WITH USER_MODE
        ]) {
            ordersByNumber.put(o.External_Order_Number__c.toUpperCase(), o);
        }

        List<Response> results = new List<Response>();
        for (Request req : requests) {
            String email = req.email == null ? '' : req.email.trim().toLowerCase();
            Order o = ordersByNumber.get(normalizeOrderNumber(req.orderNumber));
            Boolean ok = o != null && o.BillToContact != null && String.isNotBlank(email)
                && email.equalsIgnoreCase(o.BillToContact.Email);
            Response res = new Response();
            res.verified = ok;
            res.verifiedEmail = ok ? email : '';
            res.canonicalOrderNumber = ok ? o.External_Order_Number__c.toUpperCase() : '';
            res.customerFirstName = ok ? o.BillToContact.FirstName : '';
            Decimal prior = req.priorFailedAttempts == null ? 0 : req.priorFailedAttempts;
            res.failedAttempts = ok ? prior : prior + 1;
            results.add(res);
        }
        return results;
    }

    private static String normalizeOrderNumber(String value) {
        return value == null ? '' : value.trim().toUpperCase();
    }
}
