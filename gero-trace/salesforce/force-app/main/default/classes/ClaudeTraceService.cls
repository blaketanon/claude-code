/**
 * HTTP client for the Gero Trace investigation service (server/ in the gero-trace repo).
 * All calls go through the Gero_Trace Named Credential, which adds the X-Api-Key header.
 *
 * Contract (mirrored by server/gero_trace/api.py):
 *   POST /api/investigations                 -> 202 {id, status}
 *   GET  /api/investigations/{id}            -> 200 {id, status, answer_markdown, answer_html, report_markdown, ...}
 *   POST /api/investigations/{id}/report     -> 200 {issue_url, issue_number}
 */
public with sharing class ClaudeTraceService {
    public static final String NAMED_CREDENTIAL = 'callout:Gero_Trace';
    public static final Integer TIMEOUT_MS = 60000;

    public class ServiceException extends Exception {}

    /** Result of GET /api/investigations/{id}. */
    public class Investigation {
        @AuraEnabled public String id;
        @AuraEnabled public String status;          // queued | running | answered | failed | reported
        @AuraEnabled public String progress;        // short human-readable line while running
        @AuraEnabled public String answerMarkdown;
        @AuraEnabled public String answerHtml;
        @AuraEnabled public String reportMarkdown;
        @AuraEnabled public String confidence;      // High | Medium | Low
        @AuraEnabled public Boolean needsFix;
        @AuraEnabled public Decimal costUsd;
        @AuraEnabled public String error;
        @AuraEnabled public String issueUrl;
        @AuraEnabled public Integer issueNumber;
        @AuraEnabled public String fixStatus;       // null | approved | running | pr_open | merged | failed | declined
        @AuraEnabled public String fixPrUrl;
    }

    /** Submit a question. Returns the service's job id. */
    public static String submit(String question, Id recordId, String objectApiName, Map<String, Object> recordContext) {
        Map<String, Object> body = new Map<String, Object>{
            'question' => question,
            'record_id' => recordId == null ? null : String.valueOf(recordId),
            'record_object' => objectApiName,
            'record_url' => recordId == null ? null : URL.getOrgDomainUrl().toExternalForm() + '/' + recordId,
            'record_context' => recordContext,
            'org_id' => UserInfo.getOrganizationId(),
            'asked_by' => new Map<String, Object>{
                'sf_user_id' => UserInfo.getUserId(),
                'name' => UserInfo.getName(),
                'email' => UserInfo.getUserEmail()
            }
        };
        HttpResponse res = send('POST', '/api/investigations', body);
        Map<String, Object> parsed = (Map<String, Object>) JSON.deserializeUntyped(res.getBody());
        return (String) parsed.get('id');
    }

    /** Fetch current status / answer for a job. */
    public static Investigation get(String jobId) {
        HttpResponse res = send('GET', '/api/investigations/' + EncodingUtil.urlEncode(jobId, 'UTF-8'), null);
        return parse(res.getBody());
    }

    /** Turn the answer into a GitHub issue. Returns the updated investigation (with issueUrl). */
    public static Investigation report(String jobId, String comment) {
        Map<String, Object> body = new Map<String, Object>{
            'comment' => comment,
            'reported_by' => new Map<String, Object>{
                'sf_user_id' => UserInfo.getUserId(),
                'name' => UserInfo.getName(),
                'email' => UserInfo.getUserEmail()
            }
        };
        HttpResponse res = send('POST', '/api/investigations/' + EncodingUtil.urlEncode(jobId, 'UTF-8') + '/report', body);
        return parse(res.getBody());
    }

    @TestVisible
    private static Investigation parse(String json) {
        Map<String, Object> p = (Map<String, Object>) System.JSON.deserializeUntyped(json);
        Investigation inv = new Investigation();
        inv.id = (String) p.get('id');
        inv.status = (String) p.get('status');
        inv.progress = (String) p.get('progress');
        inv.answerMarkdown = (String) p.get('answer_markdown');
        inv.answerHtml = (String) p.get('answer_html');
        inv.reportMarkdown = (String) p.get('report_markdown');
        inv.confidence = (String) p.get('confidence');
        inv.needsFix = p.get('needs_fix') == true;
        inv.costUsd = p.get('cost_usd') == null ? null : Decimal.valueOf(String.valueOf(p.get('cost_usd')));
        inv.error = (String) p.get('error');
        Map<String, Object> issue = (Map<String, Object>) p.get('issue');
        if (issue != null) {
            inv.issueUrl = (String) issue.get('url');
            inv.issueNumber = issue.get('number') == null ? null : Integer.valueOf(String.valueOf(issue.get('number')));
        }
        Map<String, Object> fix = (Map<String, Object>) p.get('fix');
        if (fix != null) {
            inv.fixStatus = (String) fix.get('status');
            inv.fixPrUrl = (String) fix.get('pr_url');
        }
        return inv;
    }

    private static HttpResponse send(String method, String path, Map<String, Object> body) {
        HttpRequest req = new HttpRequest();
        req.setEndpoint(NAMED_CREDENTIAL + path);
        req.setMethod(method);
        req.setTimeout(TIMEOUT_MS);
        req.setHeader('Accept', 'application/json');
        if (body != null) {
            req.setHeader('Content-Type', 'application/json');
            req.setBody(JSON.serialize(body));
        }
        HttpResponse res;
        try {
            res = new Http().send(req);
        } catch (Exception e) {
            throw new ServiceException('Could not reach the Gero Trace service: ' + e.getMessage());
        }
        if (res.getStatusCode() < 200 || res.getStatusCode() >= 300) {
            String detail = res.getBody();
            try {
                Map<String, Object> err = (Map<String, Object>) JSON.deserializeUntyped(detail);
                if (err.containsKey('message')) detail = (String) err.get('message');
            } catch (Exception ignore) {}
            throw new ServiceException('Gero Trace returned HTTP ' + res.getStatusCode() + ': ' + detail.abbreviate(500));
        }
        return res;
    }
}
