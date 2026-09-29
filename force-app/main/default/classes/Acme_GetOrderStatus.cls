/**
 * Agent action: look up status and shipping details for a verified order.
 * STUB: returns mock data and performs no DML.
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
            Response res = new Response();
            if (orderNumber == 'A-1001') {
                res.status = 'Shipped';
                res.carrier = 'UPS';
                res.trackingNumber = '1Z999AA10123456784';
                res.estimatedDelivery = 'October 2, 2026';
            } else if (orderNumber == 'A-1002') {
                res.status = 'Processing';
                res.carrier = '';
                res.trackingNumber = '';
                res.estimatedDelivery = 'October 6, 2026';
            } else {
                res.status = 'Delivered';
                res.carrier = 'USPS';
                res.trackingNumber = '9400100000000000000000';
                res.estimatedDelivery = 'September 25, 2026';
            }
            results.add(res);
        }
        return results;
    }
}
