import hashlib
import hmac
import json

from conftest import answered


def test_healthz(client):
    assert client.get("/healthz").get_json()["ok"] is True


def test_api_requires_key(client):
    assert client.post("/api/investigations", json={"question": "why did this happen today"}).status_code == 401
    assert client.get("/api/investigations/abc").status_code == 401


def test_submit_and_get(sf):
    r = sf.post("/api/investigations", json={"question": "Why was the deal declined this morning?", "record_id": "006000000000001",
                                             "record_object": "Opportunity", "asked_by": {"name": "Dana", "email": "d@x.com"},
                                             "record_context": {"Name": "Acme"}})
    assert r.status_code == 202
    d = r.get_json()
    assert d["status"] == "queued" and d["id"]
    g = sf.get(f"/api/investigations/{d['id']}").get_json()
    assert g["question"].startswith("Why was") and g["issue"] is None and g["fix"] is None


def test_submit_validates(sf):
    assert sf.post("/api/investigations", json={"question": "why"}).status_code == 400
    assert sf.post("/api/investigations", json={}).status_code == 400


def test_report_creates_issue(app, sf):
    inv_id = answered(app)
    r = sf.post(f"/api/investigations/{inv_id}/report", json={"comment": "Blocked two fundings.", "reported_by": {"name": "Dana"}})
    assert r.status_code == 200, r.get_json()
    d = r.get_json()
    assert d["status"] == "reported"
    assert d["issue"]["number"] == 1 and d["issue"]["url"].endswith("/issues/1")
    assert d["fix"]["status"] == "awaiting_approval"
    gh = app.extensions["github"]
    issue = gh.issues[0]
    assert issue["repo"] == "FundingMetrics/gero-trace"
    assert issue["title"] == "Offer amount reset by Model 8 callback"
    assert "approved-for-fix" in issue["body"] and "Blocked two fundings." in issue["body"]
    assert "## Proposed fix" in issue["body"] and "FundingMetrics/FM_Model8" in issue["body"]
    assert issue["assignees"] == ["blaketanon"]
    # idempotent
    r2 = sf.post(f"/api/investigations/{inv_id}/report", json={})
    assert r2.status_code == 200 and len(gh.issues) == 1


def test_report_requires_answered(app, sf):
    inv_id = answered(app, status="running")
    assert sf.post(f"/api/investigations/{inv_id}/report", json={}).status_code == 409


def _signed(payload, secret="hook-secret"):
    body = json.dumps(payload).encode()
    sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return body, sig


def test_webhook_signature(client):
    body, _ = _signed({"action": "labeled"})
    r = client.post("/api/webhooks/github", data=body, headers={"X-Hub-Signature-256": "sha256=bad", "X-GitHub-Event": "issues", "Content-Type": "application/json"})
    assert r.status_code == 401


def test_webhook_label_approves_and_declines(app, sf, client):
    inv_id = answered(app)
    sf.post(f"/api/investigations/{inv_id}/report", json={})
    payload = {"action": "labeled", "label": {"name": "approved-for-fix"}, "issue": {"number": 1},
               "repository": {"full_name": "FundingMetrics/gero-trace"}, "sender": {"login": "blaketanon"}}
    body, sig = _signed(payload)
    r = client.post("/api/webhooks/github", data=body, headers={"X-Hub-Signature-256": sig, "X-GitHub-Event": "issues", "Content-Type": "application/json"})
    assert r.status_code == 200 and r.get_json()["fix_status"] == "approved"
    d = sf.get(f"/api/investigations/{inv_id}").get_json()
    assert d["fix"]["status"] == "approved"

    payload["action"] = "unlabeled"
    body, sig = _signed(payload)
    r = client.post("/api/webhooks/github", data=body, headers={"X-Hub-Signature-256": sig, "X-GitHub-Event": "issues", "Content-Type": "application/json"})
    assert r.get_json()["fix_status"] == "declined"


def test_webhook_pr_merged(app, sf, client):
    inv_id = answered(app, fix_status="pr_open", fix_repo="FundingMetrics/FM_Model8", fix_branch="claude/fix-1-x")
    payload = {"action": "closed", "pull_request": {"merged": True, "head": {"ref": "claude/fix-1-x"}, "html_url": "u"},
               "repository": {"full_name": "FundingMetrics/FM_Model8"}}
    body, sig = _signed(payload)
    client.post("/api/webhooks/github", data=body, headers={"X-Hub-Signature-256": sig, "X-GitHub-Event": "pull_request", "Content-Type": "application/json"})
    assert sf.get(f"/api/investigations/{inv_id}").get_json()["fix"]["status"] == "merged"


def test_admin_login_and_list(client, admin, app, sf):
    assert app.test_client().get("/api/admin/investigations").status_code == 401
    answered(app)
    r = admin.get("/api/admin/investigations")
    assert r.status_code == 200 and len(r.get_json()["items"]) == 1
    assert app.test_client().post("/api/login", json={"password": "nope"}).status_code == 401


def test_admin_approve_decline_retry(app, admin, sf):
    inv_id = answered(app)
    # approve needs csrf
    r = app.test_client().post(f"/api/admin/investigations/{inv_id}/approve")
    assert r.status_code == 401
    r = admin.post(f"/api/admin/investigations/{inv_id}/approve", json={})
    assert r.status_code == 200 and r.get_json()["fix"]["status"] == "approved"
    r = admin.post(f"/api/admin/investigations/{inv_id}/decline", json={"reason": "not now"})
    assert r.get_json()["fix"]["status"] == "declined"
    r = admin.post(f"/api/admin/investigations/{inv_id}/retry", json={"what": "fix"})
    assert r.get_json()["fix"]["status"] == "approved"
    d = admin.get(f"/api/admin/investigations/{inv_id}").get_json()
    assert any("approved" in e["text"] for e in d["events"])
    assert admin.get("/api/admin/stats").get_json()["answered"] == 1
