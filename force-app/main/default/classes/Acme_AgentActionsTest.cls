@IsTest
private class Acme_AgentActionsTest {
    @IsTest
    static void verifyMatchesEmailAndOrder() {
        Acme_VerifyCustomer.Request ok = new Acme_VerifyCustomer.Request();
        ok.email = ' Jamie@Example.com ';
        ok.orderNumber = 'a-1001';
        Acme_VerifyCustomer.Request bad = new Acme_VerifyCustomer.Request();
        bad.email = 'sam@example.com';
        bad.orderNumber = 'A-1001';

        List<Acme_VerifyCustomer.Response> res = Acme_VerifyCustomer.verify(
            new List<Acme_VerifyCustomer.Request>{ ok, bad });

        Assert.isTrue(res[0].verified);
        Assert.areEqual('A-1001', res[0].canonicalOrderNumber);
        Assert.areEqual('Jamie', res[0].customerFirstName);
        Assert.isFalse(res[1].verified);
        Assert.areEqual('', res[1].verifiedEmail);
    }

    @IsTest
    static void orderStatusCoversAllBranches() {
        List<Acme_GetOrderStatus.Request> reqs = new List<Acme_GetOrderStatus.Request>();
        for (String num : new List<String>{ 'A-1001', 'A-1002', 'A-2001' }) {
            Acme_GetOrderStatus.Request r = new Acme_GetOrderStatus.Request();
            r.orderNumber = num;
            r.customerEmail = 'jamie@example.com';
            reqs.add(r);
        }
        List<Acme_GetOrderStatus.Response> res = Acme_GetOrderStatus.getStatus(reqs);
        Assert.areEqual('Shipped', res[0].status);
        Assert.areEqual('Processing', res[1].status);
        Assert.areEqual('Delivered', res[2].status);
    }

    @IsTest
    static void createCaseReturnsCaseNumber() {
        Acme_CreateSupportCase.Request r = new Acme_CreateSupportCase.Request();
        r.orderNumber = 'A-1001';
        r.customerEmail = 'jamie@example.com';
        r.subject = 'Jacket arrived torn';
        r.description = 'Left sleeve seam is torn.';
        List<Acme_CreateSupportCase.Response> res = Acme_CreateSupportCase.createCase(
            new List<Acme_CreateSupportCase.Request>{ r });
        Assert.isTrue(res[0].success);
        Assert.isTrue(res[0].caseNumber.startsWith('CS-'));
    }
}
