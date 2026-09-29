/**
 * Agent action: status and shipping details for a verified order.
 * Re-checks that the order belongs to the verified email (defense in depth: the action
 * does not trust that the agent only calls it after verification).
 */
public with sharing class Acme_GetOrderStatus {
    public class Request {
        @InvocableVariable(required=true label='Order Number') public String orderNumber;
        @InvocableVariable(required=true label='Customer Email') public String customerEmail;
    }

    public class Response {
        @InvocableVariable(label='Status') public String status;
        @InvocableVariable(label='Carrier') public String carrier;
        @InvocableVariable(label='Tracking Number') public String trackingNumber;
        @InvocableVariable(label='Estimated Delivery') public String estimatedDelivery;
    }

    @InvocableMethod(label='Acme: Get Order Status' description='Returns status, carrier, tracking number, and estimated delivery for an order')
    public static List<Response> getStatus(List<Request> requests) {
        List<Response> results = new List<Response>();
        for (Request req : requests) {
            String orderNumber = req.orderNumber == null ? '' : req.orderNumber.trim().toUpperCase();
            String email = req.customerEmail == null ? '' : req.customerEmail.trim().toLowerCase();
            List<Order> found = [
                SELECT Fulfillment_Status__c, Carrier__c, Tracking_Number__c, Estimated_Delivery__c
                FROM Order
                WHERE External_Order_Number__c = :orderNumber AND BillToContact.Email = :email
                WITH USER_MODE
                LIMIT 1
            ];
            Response res = new Response();
            if (found.isEmpty()) {
                res.status = '';
                res.carrier = '';
                res.trackingNumber = '';
                res.estimatedDelivery = '';
            } else {
                Order o = found[0];
                res.status = o.Fulfillment_Status__c == null ? '' : o.Fulfillment_Status__c;
                res.carrier = o.Carrier__c == null ? '' : o.Carrier__c;
                res.trackingNumber = o.Tracking_Number__c == null ? '' : o.Tracking_Number__c;
                res.estimatedDelivery = o.Estimated_Delivery__c == null
                    ? '' : o.Estimated_Delivery__c.format();
            }
            results.add(res);
        }
        return results;
    }
}
