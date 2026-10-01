@IsTest
private class ClaudeTraceControllerTest {

    @IsTest
    static void askCreatesRecordAndJob() {
        ClaudeTraceServiceMock mock = new ClaudeTraceServiceMock();
        Test.setMock(HttpCalloutMock.class, mock);
        Account a = new Account(Name = 'Acme Plumbing');
        insert a;

        Test.startTest();
        ClaudeTraceController.Result r = ClaudeTraceController.ask('Why did this account get flagged yesterday?', a.Id, 'Account');
        Test.stopTest();

        System.assertNotEquals(null, r.investigationId);
        System.assertEquals('Queued', r.status);
        System.assertEquals(false, r.isTerminal);
        Claude_Investigation__c inv = [SELECT Job_Id__c, Record_Id__c, Record_Object__c FROM Claude_Investigation__c WHERE Id = :r.investigationId];
        System.assertEquals('job-123', inv.Job_Id__c);
        System.assertEquals(String.valueOf(a.Id), inv.Record_Id__c);
        System.assertEquals('Account', inv.Record_Object__c);
        System.assertEquals(1, mock.requests.size());
        System.assert(mock.requests[0].getBody().contains('"record_object":"Account"'));
        System.assert(mock.requests[0].getBody().contains('Acme Plumbing'), 'record context should include the Name');
    }

    @IsTest
    static void askRejectsEmptyQuestion() {
        Test.setMock(HttpCalloutMock.class, new ClaudeTraceServiceMock());
        try {
            ClaudeTraceController.ask('why', null, null);
            System.assert(false, 'expected exception');
        } catch (AuraHandledException e) {
            System.assert(true);
        }
    }

    @IsTest
    static void askSurfacesServiceErrors() {
        ClaudeTraceServiceMock mock = new ClaudeTraceServiceMock();
        mock.statusCode = 503;
        Test.setMock(HttpCalloutMock.class, mock);
        try {
            ClaudeTraceController.ask('Why did the deal get declined this morning?', null, null);
            System.assert(false, 'expected exception');
        } catch (AuraHandledException e) {
            System.assert(true);
        }
        System.assertEquals(0, [SELECT COUNT() FROM Claude_Investigation__c]);
    }

    @IsTest
    static void pollUpdatesAnswer() {
        ClaudeTraceServiceMock mock = new ClaudeTraceServiceMock();
        mock.status = 'answered';
        Test.setMock(HttpCalloutMock.class, mock);
        Claude_Investigation__c inv = new Claude_Investigation__c(Question__c = 'Why?', Job_Id__c = 'job-123', Status__c = 'Running');
        insert inv;

        Test.startTest();
        ClaudeTraceController.Result r = ClaudeTraceController.poll(inv.Id);
        Test.stopTest();

        System.assertEquals('Answered', r.status);
        System.assertEquals(true, r.isTerminal);
        System.assertEquals('High', r.confidence);
        System.assert(r.answerHtml.contains('Because'));
        inv = [SELECT Status__c, Answer__c, Report__c, Cost_USD__c, Needs_Fix__c FROM Claude_Investigation__c WHERE Id = :inv.Id];
        System.assertEquals('Answered', inv.Status__c);
        System.assertEquals(1.25, inv.Cost_USD__c);
        System.assertEquals(true, inv.Needs_Fix__c);
        System.assert(inv.Report__c.startsWith('# Report'));
    }

    @IsTest
    static void pollDoesNotCallOutWhenTerminal() {
        ClaudeTraceServiceMock mock = new ClaudeTraceServiceMock();
        Test.setMock(HttpCalloutMock.class, mock);
        Claude_Investigation__c inv = new Claude_Investigation__c(Question__c = 'Why?', Job_Id__c = 'job-123', Status__c = 'Failed', Error__c = 'budget');
        insert inv;
        ClaudeTraceController.Result r = ClaudeTraceController.poll(inv.Id);
        System.assertEquals('Failed', r.status);
        System.assertEquals(0, mock.requests.size());
    }

    @IsTest
    static void reportCreatesIssue() {
        ClaudeTraceServiceMock mock = new ClaudeTraceServiceMock();
        mock.status = 'answered';
        Test.setMock(HttpCalloutMock.class, mock);
        Claude_Investigation__c inv = new Claude_Investigation__c(Question__c = 'Why?', Job_Id__c = 'job-123', Status__c = 'Answered', Answer__c = 'x');
        insert inv;

        Test.startTest();
        ClaudeTraceController.Result r = ClaudeTraceController.reportIssue(inv.Id, 'This blocked two fundings today.');
        Test.stopTest();

        System.assertEquals('Reported', r.status);
        System.assertEquals(7, r.issueNumber);
        System.assert(r.issueUrl.endsWith('/issues/7'));
        System.assertEquals('awaiting_approval', r.fixStatus);
        System.assert(mock.requests[0].getEndpoint().endsWith('/api/investigations/job-123/report'));
        System.assert(mock.requests[0].getBody().contains('blocked two fundings'));
        inv = [SELECT Reporter_Comment__c, Issue_URL__c FROM Claude_Investigation__c WHERE Id = :inv.Id];
        System.assertEquals('This blocked two fundings today.', inv.Reporter_Comment__c);
        System.assertNotEquals(null, inv.Issue_URL__c);
    }

    @IsTest
    static void reportRequiresAnswered() {
        Test.setMock(HttpCalloutMock.class, new ClaudeTraceServiceMock());
        Claude_Investigation__c inv = new Claude_Investigation__c(Question__c = 'Why?', Job_Id__c = 'job-123', Status__c = 'Running');
        insert inv;
        try {
            ClaudeTraceController.reportIssue(inv.Id, 'x');
            System.assert(false, 'expected exception');
        } catch (AuraHandledException e) {
            System.assert(true);
        }
    }

    @IsTest
    static void recentListsByRecordAndUser() {
        Account a = new Account(Name = 'Acme');
        insert a;
        insert new List<Claude_Investigation__c>{
            new Claude_Investigation__c(Question__c = 'One', Job_Id__c = 'j1', Status__c = 'Answered', Record_Id__c = a.Id),
            new Claude_Investigation__c(Question__c = 'Two', Job_Id__c = 'j2', Status__c = 'Answered')
        };
        System.assertEquals(1, ClaudeTraceController.recent(a.Id, 10).size());
        System.assertEquals(2, ClaudeTraceController.recent(null, 10).size());
    }

    @IsTest
    static void statusLabelsAndParse() {
        System.assertEquals('Running', ClaudeTraceController.statusLabel('running'));
        System.assertEquals('Queued', ClaudeTraceController.statusLabel('weird'));
        ClaudeTraceService.Investigation i = ClaudeTraceService.parse('{"id":"a","status":"answered","needs_fix":false,"cost_usd":"0.5","issue":{"url":"u","number":3},"fix":{"status":"pr_open","pr_url":"p"}}');
        System.assertEquals('a', i.id);
        System.assertEquals(0.5, i.costUsd);
        System.assertEquals(3, i.issueNumber);
        System.assertEquals('pr_open', i.fixStatus);
        System.assertEquals('p', i.fixPrUrl);
    }
}
