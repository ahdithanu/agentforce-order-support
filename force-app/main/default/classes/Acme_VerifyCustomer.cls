/**
 * Agent action: verify a customer by checkout email + order number.
 * STUB: returns mock data and performs no DML. Replace the body with a
 * WITH USER_MODE query against Order/Contact for a real implementation.
 */
public with sharing class Acme_VerifyCustomer {
    public class Request {
        @InvocableVariable(required=true label='Email' description='Email used at checkout')
        public String email;
        @InvocableVariable(required=true label='Order Number' description='Order number, e.g. A-1001')
        public String orderNumber;
    }

    public class Response {
        @InvocableVariable(label='Verified') public Boolean verified;
        @InvocableVariable(label='Verified Email') public String verifiedEmail;
        @InvocableVariable(label='Canonical Order Number') public String canonicalOrderNumber;
        @InvocableVariable(label='Customer First Name') public String customerFirstName;
    }

    // Mock "database": order number -> checkout email.
    private static final Map<String, String> ORDERS = new Map<String, String>{
        'A-1001' => 'jamie@example.com',
        'A-1002' => 'jamie@example.com',
        'A-2001' => 'sam@example.com'
    };
    private static final Map<String, String> FIRST_NAMES = new Map<String, String>{
        'jamie@example.com' => 'Jamie',
        'sam@example.com' => 'Sam'
    };

    @InvocableMethod(label='Acme: Verify Customer' description='Checks that an email and order number belong to the same customer')
    public static List<Response> verify(List<Request> requests) {
        List<Response> results = new List<Response>();
        for (Request req : requests) {
            String email = req.email == null ? '' : req.email.trim().toLowerCase();
            String orderNumber = req.orderNumber == null ? '' : req.orderNumber.trim().toUpperCase();
            Response res = new Response();
            res.verified = ORDERS.containsKey(orderNumber) && ORDERS.get(orderNumber) == email;
            res.verifiedEmail = res.verified ? email : '';
            res.canonicalOrderNumber = res.verified ? orderNumber : '';
            res.customerFirstName = res.verified ? FIRST_NAMES.get(email) : '';
            results.add(res);
        }
        return results;
    }
}
