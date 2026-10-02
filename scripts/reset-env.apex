// Reset the demo org to a known state before an eval run, so one run can't leak into the next.
// Run: sf apex run --file scripts/reset-env.apex --target-org <alias>
// Pair with scripts/seed-acme-orders.apex (re-seeds order status, carrier, dates). This script:
//   1. clears the cross-session verification lockout on the demo orders
//   2. closes open Cases on the demo customers (handoff, return and support cases from test runs),
//      except the showcase Case referenced by the walkthrough's recorded transcript
// Scoped to the 'Acme Test Customers' account, so nothing else in the org is touched.
Set<String> keepCaseNumbers = new Set<String>{ '00001026' };

List<Account> accts = [SELECT Id FROM Account WHERE Name = 'Acme Test Customers' LIMIT 1];
if (accts.isEmpty()) {
    System.debug('No demo account; nothing to reset.');
    return;
}
Id acctId = accts[0].Id;

List<Order> orders = [
    SELECT Id FROM Order
    WHERE AccountId = :acctId AND (Failed_Verification_Count__c > 0 OR Verification_Locked_Until__c != null)
];
for (Order o : orders) {
    o.Failed_Verification_Count__c = 0;
    o.Verification_Locked_Until__c = null;
}
update orders;

List<Case> cases = [
    SELECT Id FROM Case
    WHERE IsClosed = false AND CaseNumber NOT IN :keepCaseNumbers
      AND (Contact.AccountId = :acctId OR Order__r.AccountId = :acctId OR Subject LIKE 'Agent handoff:%')
];
for (Case c : cases) {
    c.Status = 'Closed';
}
update cases;

System.debug('RESET orders_unlocked=' + orders.size() + ' cases_closed=' + cases.size());
