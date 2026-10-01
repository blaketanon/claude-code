/**
 * Apex backing the claudeAsk Lightning web component.
 *
 * Flow: ask() creates the job on the service and a Claude_Investigation__c record;
 * the component then calls poll() every few seconds until the status is terminal;
 * reportIssue() turns an answer into a GitHub issue for engineering.
 *
 * Callouts are always made before any DML in the same transaction.
 */
public with sharing class ClaudeTraceController {
    private static final Set<String> TERMINAL = new Set<String>{ 'answered', 'failed', 'reported' };
    private static final Set<String> CONTEXT_FIELDS = new Set<String>{ 'Name', 'StageName', 'Status', 'Amount', 'Type', 'OwnerId', 'LastModifiedDate', 'LastModifiedById', 'CreatedDate' };

    public class Result {
        @AuraEnabled public Id investigationId;
        @AuraEnabled public String name;
        @AuraEnabled public String question;
        @AuraEnabled public String status;          // Queued | Running | Answered | Reported | Failed
        @AuraEnabled public String progress;
        @AuraEnabled public String answerHtml;
        @AuraEnabled public String answerMarkdown;
        @AuraEnabled public String confidence;
        @AuraEnabled public Boolean needsFix;
        @AuraEnabled public String error;
        @AuraEnabled public String issueUrl;
        @AuraEnabled public Integer issueNumber;
        @AuraEnabled public String fixStatus;
        @AuraEnabled public String fixPrUrl;
        @AuraEnabled public Datetime createdDate;
        @AuraEnabled public String createdByName;
        @AuraEnabled public Boolean isTerminal;
    }

    /** Submit a question, optionally about a record. */
    @AuraEnabled
    public static Result ask(String question, Id recordId, String objectApiName) {
        if (String.isBlank(question) || question.trim().length() < 8) {
            throw new AuraHandledException('Please describe what happened in at least a few words.');
        }
        Map<String, Object> context = recordContext(recordId, objectApiName);
        String jobId;
        try {
            jobId = ClaudeTraceService.submit(question.trim(), recordId, objectApiName, context);
        } catch (ClaudeTraceService.ServiceException e) {
            throw new AuraHandledException(e.getMessage());
        }
        Claude_Investigation__c inv = new Claude_Investigation__c(
            Question__c = question.trim(),
            Job_Id__c = jobId,
            Status__c = 'Queued',
            Record_Id__c = recordId == null ? null : String.valueOf(recordId),
            Record_Object__c = objectApiName
        );
        insert inv;
        return load(inv.Id);
    }

    /** Refresh one investigation from the service (only while it is still running). */
    @AuraEnabled
    public static Result poll(Id investigationId) {
        Claude_Investigation__c inv = query(investigationId);
        Boolean stillRunning = inv.Status__c != 'Answered' && inv.Status__c != 'Failed' && inv.Status__c != 'Reported';
        Boolean watchingFix = inv.Status__c == 'Reported' && String.isBlank(inv.Fix_PR_URL__c);
        if (stillRunning || watchingFix) {
            sync(inv);
        }
        return toResult(inv);
    }

    /** Report an answered investigation to engineering as a GitHub issue. */
    @AuraEnabled
    public static Result reportIssue(Id investigationId, String comment) {
        Claude_Investigation__c inv = query(investigationId);
        if (inv.Status__c != 'Answered') {
            throw new AuraHandledException('Only answered investigations can be reported.');
        }
        ClaudeTraceService.Investigation remote;
        try {
            remote = ClaudeTraceService.report(inv.Job_Id__c, comment);
        } catch (ClaudeTraceService.ServiceException e) {
            throw new AuraHandledException(e.getMessage());
        }
        inv.Reporter_Comment__c = comment;
        apply(inv, remote);
        update inv;
        return toResult(inv);
    }

    /** Previous investigations for this record (or, with no record, the current user's recent ones). */
    @AuraEnabled(cacheable=true)
    public static List<Result> recent(Id recordId, Integer max) {
        Integer lim = max == null || max < 1 ? 10 : Math.min(max, 50);
        List<Claude_Investigation__c> rows = recordId != null
            ? [SELECT Id, Name, Question__c, Status__c, Answer_Rich__c, Answer__c, Confidence__c, Needs_Fix__c, Error__c,
                      Issue_URL__c, Issue_Number__c, Fix_Status__c, Fix_PR_URL__c, CreatedDate, CreatedBy.Name
               FROM Claude_Investigation__c WHERE Record_Id__c = :String.valueOf(recordId)
               WITH USER_MODE ORDER BY CreatedDate DESC LIMIT :lim]
            : [SELECT Id, Name, Question__c, Status__c, Answer_Rich__c, Answer__c, Confidence__c, Needs_Fix__c, Error__c,
                      Issue_URL__c, Issue_Number__c, Fix_Status__c, Fix_PR_URL__c, CreatedDate, CreatedBy.Name
               FROM Claude_Investigation__c WHERE CreatedById = :UserInfo.getUserId()
               WITH USER_MODE ORDER BY CreatedDate DESC LIMIT :lim];
        List<Result> out = new List<Result>();
        for (Claude_Investigation__c r : rows) out.add(toResult(r));
        return out;
    }

    // ---------------------------------------------------------------- helpers

    private static Result load(Id investigationId) {
        return toResult(query(investigationId));
    }

    private static Claude_Investigation__c query(Id investigationId) {
        List<Claude_Investigation__c> rows = [
            SELECT Id, Name, Question__c, Job_Id__c, Status__c, Answer__c, Answer_Rich__c, Report__c, Confidence__c,
                   Needs_Fix__c, Cost_USD__c, Error__c, Issue_URL__c, Issue_Number__c, Fix_Status__c, Fix_PR_URL__c,
                   Reporter_Comment__c, CreatedDate, CreatedBy.Name
            FROM Claude_Investigation__c WHERE Id = :investigationId WITH USER_MODE LIMIT 1];
        if (rows.isEmpty()) throw new AuraHandledException('Investigation not found.');
        return rows[0];
    }

    private static void sync(Claude_Investigation__c inv) {
        ClaudeTraceService.Investigation remote;
        try {
            remote = ClaudeTraceService.get(inv.Job_Id__c);
        } catch (ClaudeTraceService.ServiceException e) {
            // Leave the record alone; the component will try again on the next poll.
            inv.Error__c = e.getMessage().abbreviate(32000);
            return;
        }
        String before = inv.Status__c + '|' + inv.Fix_Status__c + '|' + inv.Fix_PR_URL__c;
        apply(inv, remote);
        String after = inv.Status__c + '|' + inv.Fix_Status__c + '|' + inv.Fix_PR_URL__c;
        if (before != after || (inv.Status__c == 'Answered' && inv.Answer__c == null)) {
            update inv;
        }
    }

    @TestVisible
    private static void apply(Claude_Investigation__c inv, ClaudeTraceService.Investigation remote) {
        if (remote == null) return;
        inv.Status__c = statusLabel(remote.status);
        if (remote.answerMarkdown != null) inv.Answer__c = remote.answerMarkdown.abbreviate(131000);
        if (remote.answerHtml != null) inv.Answer_Rich__c = remote.answerHtml.abbreviate(131000);
        if (remote.reportMarkdown != null) inv.Report__c = remote.reportMarkdown.abbreviate(131000);
        if (remote.confidence != null) inv.Confidence__c = remote.confidence;
        if (remote.needsFix != null) inv.Needs_Fix__c = remote.needsFix;
        if (remote.costUsd != null) inv.Cost_USD__c = remote.costUsd;
        inv.Error__c = remote.error == null ? null : remote.error.abbreviate(32000);
        if (remote.issueUrl != null) inv.Issue_URL__c = remote.issueUrl;
        if (remote.issueNumber != null) inv.Issue_Number__c = remote.issueNumber;
        if (remote.fixStatus != null) inv.Fix_Status__c = remote.fixStatus;
        if (remote.fixPrUrl != null) inv.Fix_PR_URL__c = remote.fixPrUrl;
    }

    @TestVisible
    private static String statusLabel(String remote) {
        if (remote == 'queued') return 'Queued';
        if (remote == 'running') return 'Running';
        if (remote == 'answered') return 'Answered';
        if (remote == 'reported') return 'Reported';
        if (remote == 'failed') return 'Failed';
        return 'Queued';
    }

    private static Result toResult(Claude_Investigation__c inv) {
        Result r = new Result();
        r.investigationId = inv.Id;
        r.name = inv.Name;
        r.question = inv.Question__c;
        r.status = inv.Status__c;
        r.answerHtml = inv.Answer_Rich__c;
        r.answerMarkdown = inv.Answer__c;
        r.confidence = inv.Confidence__c;
        r.needsFix = inv.Needs_Fix__c;
        r.error = inv.Error__c;
        r.issueUrl = inv.Issue_URL__c;
        r.issueNumber = inv.Issue_Number__c == null ? null : Integer.valueOf(inv.Issue_Number__c);
        r.fixStatus = inv.Fix_Status__c;
        r.fixPrUrl = inv.Fix_PR_URL__c;
        r.createdDate = inv.CreatedDate;
        r.createdByName = inv.CreatedBy == null ? null : inv.CreatedBy.Name;
        r.isTerminal = TERMINAL.contains(String.valueOf(inv.Status__c).toLowerCase());
        if (r.status == 'Running') r.progress = 'Claude is reading Salesforce history, logs and the code…';
        return r;
    }

    /**
     * A small, safe snapshot of the record the question is about, so the investigator starts with
     * the right names and ids. Only common, non-sensitive fields are included; Claude reads anything
     * else it needs through its own read-only Salesforce access on the server.
     */
    @TestVisible
    private static Map<String, Object> recordContext(Id recordId, String objectApiName) {
        Map<String, Object> ctx = new Map<String, Object>();
        if (recordId == null || String.isBlank(objectApiName)) return ctx;
        Schema.SObjectType t = Schema.getGlobalDescribe().get(objectApiName);
        if (t == null) return ctx;
        Map<String, Schema.SObjectField> fields = t.getDescribe().fields.getMap();
        List<String> select = new List<String>{ 'Id' };
        for (String f : CONTEXT_FIELDS) {
            Schema.SObjectField sf = fields.get(f.toLowerCase());
            if (sf != null && sf.getDescribe().isAccessible()) select.add(f);
        }
        try {
            List<SObject> rows = Database.query(
                'SELECT ' + String.join(select, ',') + ' FROM ' + String.escapeSingleQuotes(objectApiName)
                + ' WHERE Id = :recordId WITH USER_MODE LIMIT 1');
            if (!rows.isEmpty()) {
                for (String f : select) ctx.put(f, rows[0].get(f));
            }
        } catch (Exception ignore) {}
        return ctx;
    }
}
