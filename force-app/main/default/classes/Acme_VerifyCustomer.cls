/**
 * Agent action: verify a customer by checkout email + order number.
 * Runs in USER_MODE so the Einstein Agent User's object and field access is enforced.
 * Fails closed: any mismatch or missing record returns verified = false with empty outputs,
 * and the response never says which of the two values was wrong.
 * Returns the authoritative failed-attempt count for the session: the agent binds its current
 * count as input (not model-filled), so the model cannot reset or lower it.
 *
 * Cross-session lockout: failed checks are also counted on the Order itself. After
 * ORDER_LOCK_THRESHOLD failures the order is locked for ORDER_LOCK_MINUTES, across every chat,
 * and even correct details are refused. A lockout returns MAX_ATTEMPTS so the agent's existing
 * handoff path runs. The counter is written in SYSTEM_MODE on purpose: it is a security control,
 * and the agent user should not need edit access to Orders to maintain it.
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
    public static final Integer ORDER_LOCK_THRESHOLD = 5;
    public static final Integer ORDER_LOCK_MINUTES = 30;

    @InvocableMethod(label='Acme: Verify Customer' description='Checks that an email and order number belong to the same customer')
    public static List<Response> verify(List<Request> requests) {
        Set<String> orderNumbers = new Set<String>();
        for (Request req : requests) {
            orderNumbers.add(normalizeOrderNumber(req.orderNumber));
        }
        Map<String, Order> ordersByNumber = new Map<String, Order>();
        for (Order o : [
            SELECT Id, External_Order_Number__c, BillToContact.Email, BillToContact.FirstName,
                   Failed_Verification_Count__c, Verification_Locked_Until__c
            FROM Order
            WHERE External_Order_Number__c IN :orderNumbers
            WITH USER_MODE
        ]) {
            ordersByNumber.put(o.External_Order_Number__c.toUpperCase(), o);
        }

        Map<Id, Order> counterUpdates = new Map<Id, Order>();
        List<Response> results = new List<Response>();
        for (Request req : requests) {
            String email = req.email == null ? '' : req.email.trim().toLowerCase();
            Order o = ordersByNumber.get(normalizeOrderNumber(req.orderNumber));
            Decimal prior = req.priorFailedAttempts == null ? 0 : req.priorFailedAttempts;
            Boolean locked = o != null && o.Verification_Locked_Until__c != null
                && o.Verification_Locked_Until__c > Datetime.now();
            Boolean matches = o != null && o.BillToContact != null && String.isNotBlank(email)
                && email.equalsIgnoreCase(o.BillToContact.Email);
            Boolean ok = matches && !locked;

            Response res = new Response();
            res.verified = ok;
            res.verifiedEmail = ok ? email : '';
            res.canonicalOrderNumber = ok ? o.External_Order_Number__c.toUpperCase() : '';
            res.customerFirstName = ok ? o.BillToContact.FirstName : '';

            if (locked) {
                res.failedAttempts = Math.max(prior + 1, MAX_ATTEMPTS);
            } else if (ok) {
                res.failedAttempts = prior;
                if (o.Failed_Verification_Count__c != null && o.Failed_Verification_Count__c > 0) {
                    counterUpdates.put(o.Id, new Order(Id = o.Id, Failed_Verification_Count__c = 0));
                }
            } else {
                res.failedAttempts = prior + 1;
                if (o != null) {
                    Decimal count = (o.Failed_Verification_Count__c == null ? 0 : o.Failed_Verification_Count__c) + 1;
                    Order upd = new Order(Id = o.Id, Failed_Verification_Count__c = count);
                    if (count >= ORDER_LOCK_THRESHOLD) {
                        upd.Failed_Verification_Count__c = 0;
                        upd.Verification_Locked_Until__c = Datetime.now().addMinutes(ORDER_LOCK_MINUTES);
                        res.failedAttempts = Math.max(prior + 1, MAX_ATTEMPTS);
                    }
                    o.Failed_Verification_Count__c = upd.Failed_Verification_Count__c;
                    counterUpdates.put(o.Id, upd);
                }
            }
            results.add(res);
        }
        if (!counterUpdates.isEmpty()) {
            Database.update(counterUpdates.values(), false, AccessLevel.SYSTEM_MODE);
        }
        return results;
    }

    private static String normalizeOrderNumber(String value) {
        return value == null ? '' : value.trim().toUpperCase();
    }
}
