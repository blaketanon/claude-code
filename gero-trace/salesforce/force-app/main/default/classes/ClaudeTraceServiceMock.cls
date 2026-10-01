@IsTest
public class ClaudeTraceServiceMock implements HttpCalloutMock {
    public Integer statusCode = 200;
    public String status = 'queued';
    public Boolean withIssue = false;
    public List<HttpRequest> requests = new List<HttpRequest>();

    public HttpResponse respond(HttpRequest req) {
        requests.add(req);
        HttpResponse res = new HttpResponse();
        res.setHeader('Content-Type', 'application/json');
        res.setStatusCode(statusCode);
        if (statusCode >= 400) {
            res.setBody('{"error":"boom","message":"service unavailable"}');
            return res;
        }
        if (req.getMethod() == 'POST' && req.getEndpoint().endsWith('/api/investigations')) {
            res.setStatusCode(202);
            res.setBody('{"id":"job-123","status":"queued"}');
            return res;
        }
        Map<String, Object> body = new Map<String, Object>{
            'id' => 'job-123',
            'status' => status,
            'answer_markdown' => status == 'queued' ? null : '**Because** the stage changed.',
            'answer_html' => status == 'queued' ? null : '<p><b>Because</b> the stage changed.</p>',
            'report_markdown' => status == 'queued' ? null : '# Report\nRoot cause…',
            'confidence' => status == 'queued' ? null : 'High',
            'needs_fix' => true,
            'cost_usd' => 1.25,
            'error' => null
        };
        if (withIssue || req.getEndpoint().endsWith('/report')) {
            body.put('status', 'reported');
            body.put('issue', new Map<String, Object>{ 'url' => 'https://github.com/FundingMetrics/gero-trace/issues/7', 'number' => 7 });
            body.put('fix', new Map<String, Object>{ 'status' => 'awaiting_approval', 'pr_url' => null });
        }
        res.setBody(JSON.serialize(body));
        return res;
    }
}
