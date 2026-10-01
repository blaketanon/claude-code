import os
import tempfile

import pytest

os.environ.setdefault("SQLITE_PATH", os.path.join(tempfile.mkdtemp(prefix="gero-trace-test-"), "test.db"))


class FakeGitHub:
    configured = True

    def __init__(self):
        self.issues = []
        self.comments = []
        self.labels = []
        self.pulls = []

    def create_issue(self, repo, title, body, labels=(), assignees=()):
        n = len(self.issues) + 1
        self.issues.append({"repo": repo, "title": title, "body": body, "labels": list(labels), "assignees": list(assignees)})
        return {"number": n, "html_url": f"https://github.com/{repo}/issues/{n}"}

    def comment(self, repo, number, body):
        self.comments.append((repo, number, body))

    def set_labels(self, repo, number, add=(), remove=()):
        self.labels.append((repo, number, list(add), list(remove)))

    def default_branch(self, repo):
        return "main"

    def clone_url(self, repo):
        return f"https://x-access-token:secret@github.com/{repo}.git"

    def create_pull(self, repo, head, base, title, body, draft=True):
        n = len(self.pulls) + 1
        self.pulls.append({"repo": repo, "head": head, "base": base, "title": title, "body": body, "draft": draft})
        return {"number": n, "html_url": f"https://github.com/{repo}/pull/{n}"}


@pytest.fixture()
def app():
    from gero_trace import create_app
    from gero_trace import db
    from gero_trace.models import Base
    app = create_app({
        "TESTING": True, "SECRET_KEY": "test", "ADMIN_PASSWORD": "admin-pw-123", "SALESFORCE_API_KEY": "sf-key-456",
        "GITHUB_WEBHOOK_SECRET": "hook-secret", "GITHUB_ISSUES_REPO": "FundingMetrics/gero-trace", "PUBLIC_URL": "https://trace.test",
        "GITHUB_DEFAULT_ASSIGNEE": "blaketanon",
    })
    app.extensions["github"] = FakeGitHub()
    with app.app_context():
        Base.metadata.drop_all(db.engine)
        Base.metadata.create_all(db.engine)
        db.Session.remove()
    yield app


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def sf(client):
    """Client that sends the Salesforce API key."""
    class C:
        def __getattr__(self, name):
            fn = getattr(client, name)
            def call(*a, **kw):
                headers = kw.pop("headers", {})
                headers["X-Api-Key"] = "sf-key-456"
                return fn(*a, headers=headers, **kw)
            return call
    return C()


@pytest.fixture()
def admin(client):
    r = client.post("/api/login", json={"password": "admin-pw-123"})
    assert r.status_code == 200
    csrf = r.get_json()["csrf"]
    class C:
        def __getattr__(self, name):
            fn = getattr(client, name)
            def call(*a, **kw):
                headers = kw.pop("headers", {})
                headers["X-CSRF-Token"] = csrf
                return fn(*a, headers=headers, **kw)
            return call
    return C()


FINDINGS = {
    "title": "Offer amount reset by Model 8 callback", "answer_markdown": "**Short answer:** the scoring service rewrote the amount.",
    "confidence": "High", "what_happened": "10:02 ET user set amount; 10:03 ET callback overwrote it.", "root_cause": "Callback writes Amount unconditionally.",
    "evidence": [{"source": "OpportunityFieldHistory", "detail": "Amount 50000 -> 42000 by Integration User", "reference": "006xx"}],
    "affected_systems": ["Salesforce", "FundingMetrics/FM_Model8"], "needs_fix": True, "fix_type": "code",
    "proposed_fix": {"repo": "FundingMetrics/FM_Model8", "summary": "Only write Amount when unset.", "files": ["app/callback.py"],
                     "steps": ["guard the write"], "risk": "low", "tests": "pytest tests/test_callback.py", "rollback": "revert"},
    "open_questions": [], "technical_notes": "n/a",
}


def answered(app, **over):
    """Insert an answered investigation directly."""
    from gero_trace.db import Session
    from gero_trace.models import Investigation
    from gero_trace.render import engineering_report, to_html
    with app.app_context():
        s = Session()
        inv = Investigation(question="Why did the offer amount change on Acme?", status="answered", findings=FINDINGS,
                            answer_markdown=FINDINGS["answer_markdown"], answer_html=to_html(FINDINGS["answer_markdown"]),
                            confidence="High", needs_fix=True, cost_usd=2.5, record_id="006000000000001", record_object="Opportunity",
                            asked_by={"name": "Dana Ops", "email": "dana@fundingmetrics.com"})
        for k, v in over.items():
            setattr(inv, k, v)
        inv.report_markdown = engineering_report(inv, FINDINGS)
        s.add(inv)
        s.commit()
        return inv.id
