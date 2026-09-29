@IsTest
private class Acme_AgentActionsTest {
    // Runs as a dedicated user holding both Acme permission sets, so the tests prove
    // the USER_MODE queries work with exactly the access the agent is granted.
    static User actingUser() {
        return [SELECT Id FROM User WHERE Username LIKE 'acme.agent.test.%@example.com' LIMIT 1];
    }

    @TestSetup
    static void seed() {
        User u = new User(
            Username = 'acme.agent.test.' + System.currentTimeMillis() + '@example.com',
            LastName = 'AgentTest', Email = 'acme.agent.test@example.com', Alias = 'acmetst',
            ProfileId = [SELECT Id FROM Profile WHERE Name = 'Standard User' LIMIT 1].Id,
            TimeZoneSidKey = 'America/Los_Angeles', LocaleSidKey = 'en_US',
            EmailEncodingKey = 'UTF-8', LanguageLocaleKey = 'en_US'
        );
        System.runAs(new User(Id = UserInfo.getUserId())) {
            insert u;
            List<PermissionSetAssignment> psas = new List<PermissionSetAssignment>();
            for (PermissionSet ps : [SELECT Id FROM PermissionSet WHERE Name IN ('Acme_Order_Data_Admin', 'Acme_Order_Support_Agent')]) {
                psas.add(new PermissionSetAssignment(AssigneeId = u.Id, PermissionSetId = ps.Id));
            }
            insert psas;
        }
        System.runAs(u) {
            seedData();
        }
    }

    static void seedData() {
        Account acct = new Account(Name = 'Acme Test Customers');
        insert acct;
        Contact jamie = new Contact(FirstName = 'Jamie', LastName = 'Rivera', Email = 'jamie@example.com', AccountId = acct.Id);
        insert jamie;
        insert new Order(
            AccountId = acct.Id, BillToContactId = jamie.Id, EffectiveDate = Date.today(), Status = 'Draft',
            External_Order_Number__c = 'A-1001', Fulfillment_Status__c = 'Shipped', Carrier__c = 'UPS',
            Tracking_Number__c = '1Z999AA10123456784', Estimated_Delivery__c = Date.today().addDays(3)
        );
    }

    @IsTest
    static void verifyMatchesEmailAndOrderAndFailsClosed() {
        System.runAs(actingUser()) {
            verifyMatchesEmailAndOrderAndFailsClosedBody();
        }
    }

    static void verifyMatchesEmailAndOrderAndFailsClosedBody() {
        Acme_VerifyCustomer.Request ok = new Acme_VerifyCustomer.Request();
        ok.email = ' Jamie@Example.com ';
        ok.orderNumber = 'a-1001';
        Acme_VerifyCustomer.Request wrongEmail = new Acme_VerifyCustomer.Request();
        wrongEmail.email = 'sam@example.com';
        wrongEmail.orderNumber = 'A-1001';
        Acme_VerifyCustomer.Request noOrder = new Acme_VerifyCustomer.Request();
        noOrder.email = 'jamie@example.com';
        noOrder.orderNumber = 'A-9999';

        List<Acme_VerifyCustomer.Response> res = Acme_VerifyCustomer.verify(
            new List<Acme_VerifyCustomer.Request>{ ok, wrongEmail, noOrder });

        Assert.isTrue(res[0].verified);
        Assert.areEqual('A-1001', res[0].canonicalOrderNumber);
        Assert.areEqual('Jamie', res[0].customerFirstName);
        Assert.isFalse(res[1].verified);
        Assert.areEqual('', res[1].verifiedEmail);
        Assert.isFalse(res[2].verified);
        Assert.areEqual(0, res[0].failedAttempts);
        Assert.areEqual(1, res[1].failedAttempts);
    }

    @IsTest
    static void failedAttemptsAccumulateFromBoundCount() {
        System.runAs(actingUser()) {
            Acme_VerifyCustomer.Request third = new Acme_VerifyCustomer.Request();
            third.email = 'wrong@example.com';
            third.orderNumber = 'A-1001';
            third.priorFailedAttempts = 2;
            Acme_VerifyCustomer.Response res = Acme_VerifyCustomer.verify(
                new List<Acme_VerifyCustomer.Request>{ third })[0];
            Assert.isFalse(res.verified);
            Assert.areEqual(Acme_VerifyCustomer.MAX_ATTEMPTS, res.failedAttempts);
        }
    }

    @IsTest
    static void orderStatusOnlyForMatchingEmail() {
        System.runAs(actingUser()) {
            orderStatusOnlyForMatchingEmailBody();
        }
    }

    static void orderStatusOnlyForMatchingEmailBody() {
        Acme_GetOrderStatus.Request mine = new Acme_GetOrderStatus.Request();
        mine.orderNumber = 'A-1001';
        mine.customerEmail = 'jamie@example.com';
        Acme_GetOrderStatus.Request notMine = new Acme_GetOrderStatus.Request();
        notMine.orderNumber = 'A-1001';
        notMine.customerEmail = 'sam@example.com';

        List<Acme_GetOrderStatus.Response> res = Acme_GetOrderStatus.getStatus(
            new List<Acme_GetOrderStatus.Request>{ mine, notMine });
        Assert.areEqual('Shipped', res[0].status);
        Assert.areEqual('UPS', res[0].carrier);
        Assert.areEqual('', res[1].status);
    }

    @IsTest
    static void createCaseLinksContactAndOrder() {
        System.runAs(actingUser()) {
            createCaseLinksContactAndOrderBody();
        }
    }

    static void createCaseLinksContactAndOrderBody() {
        Acme_CreateSupportCase.Request r = new Acme_CreateSupportCase.Request();
        r.orderNumber = 'A-1001';
        r.customerEmail = 'jamie@example.com';
        r.subject = 'Jacket arrived torn';
        r.description = 'Left sleeve seam is torn.';
        Acme_CreateSupportCase.Request bad = new Acme_CreateSupportCase.Request();
        bad.orderNumber = 'A-1001';
        bad.customerEmail = 'sam@example.com';
        bad.subject = 'Should not be created';
        bad.description = 'Email does not match the order.';

        List<Acme_CreateSupportCase.Response> res = Acme_CreateSupportCase.createCase(
            new List<Acme_CreateSupportCase.Request>{ r, bad });

        Assert.isTrue(res[0].success);
        Case created = [SELECT CaseNumber, Order__r.External_Order_Number__c, Contact.Email FROM Case WHERE CaseNumber = :res[0].caseNumber];
        Assert.areEqual('A-1001', created.Order__r.External_Order_Number__c);
        Assert.areEqual('jamie@example.com', created.Contact.Email);
        Assert.isFalse(res[1].success);
    }
}
