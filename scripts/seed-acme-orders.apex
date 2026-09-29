// Idempotent demo data for the Acme Order Support agent.
// Run: sf apex run --file scripts/seed-acme-orders.apex --target-org <alias>
Account acct;
List<Account> existing = [SELECT Id FROM Account WHERE Name = 'Acme Test Customers' LIMIT 1];
if (existing.isEmpty()) { acct = new Account(Name = 'Acme Test Customers'); insert acct; } else { acct = existing[0]; }

Map<String, Contact> contacts = new Map<String, Contact>();
for (Contact c : [SELECT Id, Email FROM Contact WHERE AccountId = :acct.Id AND Email IN ('jamie@example.com','sam@example.com')]) {
    contacts.put(c.Email.toLowerCase(), c);
}
List<Contact> newContacts = new List<Contact>();
if (!contacts.containsKey('jamie@example.com')) newContacts.add(new Contact(FirstName='Jamie', LastName='Rivera', Email='jamie@example.com', AccountId=acct.Id));
if (!contacts.containsKey('sam@example.com')) newContacts.add(new Contact(FirstName='Sam', LastName='Lee', Email='sam@example.com', AccountId=acct.Id));
insert newContacts;
for (Contact c : newContacts) contacts.put(c.Email.toLowerCase(), c);

List<Order> orders = new List<Order>{
    new Order(External_Order_Number__c='A-1001', AccountId=acct.Id, BillToContactId=contacts.get('jamie@example.com').Id, EffectiveDate=Date.today().addDays(-4), Status='Draft', Fulfillment_Status__c='Shipped', Carrier__c='UPS', Tracking_Number__c='1Z999AA10123456784', Estimated_Delivery__c=Date.today().addDays(2)),
    new Order(External_Order_Number__c='A-1002', AccountId=acct.Id, BillToContactId=contacts.get('jamie@example.com').Id, EffectiveDate=Date.today().addDays(-1), Status='Draft', Fulfillment_Status__c='Processing', Estimated_Delivery__c=Date.today().addDays(6)),
    new Order(External_Order_Number__c='A-2001', AccountId=acct.Id, BillToContactId=contacts.get('sam@example.com').Id, EffectiveDate=Date.today().addDays(-10), Status='Draft', Fulfillment_Status__c='Delivered', Carrier__c='USPS', Tracking_Number__c='9400100000000000000000', Estimated_Delivery__c=Date.today().addDays(-3))
};
upsert orders Order.External_Order_Number__c;
System.debug('Seeded ' + orders.size() + ' orders for account ' + acct.Id);
