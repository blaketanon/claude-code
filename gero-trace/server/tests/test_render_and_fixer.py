from types import SimpleNamespace

from conftest import FINDINGS

from gero_trace.fixer import branch_name, pull_request_body, target_repo
from gero_trace.render import engineering_report, issue_body, issue_title, to_html


def _inv(**over):
    base = dict(id="abc123", question="Why did the offer amount change on Acme?", record_id="006x", record_object="Opportunity",
                record_url="https://fm.my.salesforce.com/006x", asked_by={"name": "Dana", "email": "d@x.com"}, reporter_comment=None,
                claude_session_id="sess-1", confidence="High", answer_markdown=FINDINGS["answer_markdown"], findings=FINDINGS,
                issue_number=12, issue_url="https://github.com/FundingMetrics/gero-trace/issues/12")
    base.update(over)
    return SimpleNamespace(**base)


def test_to_html_sanitises():
    html = to_html("**bold** and <script>alert(1)</script> [link](https://x.y) and [bad](javascript:alert(1))\n\n- a\n- b")
    assert "<strong>bold</strong>" in html
    assert "<script" not in html
    assert 'href="https://x.y"' in html and "javascript:" not in html
    assert "<li>a</li>" in html
    assert to_html("") == ""


def test_engineering_report_sections():
    md = engineering_report(_inv(), FINDINGS)
    for h in ("# Offer amount reset", "## Question", "## Answer given to the user", "## What happened", "## Root cause",
              "## Evidence", "## Affected systems", "## Proposed fix", "## Open questions", "## Technical notes"):
        assert h in md, h
    assert "OpportunityFieldHistory" in md and "`app/callback.py`" in md and "pytest tests/test_callback.py" in md


def test_report_without_fix():
    f = dict(FINDINGS, needs_fix=False, fix_type="training", proposed_fix={"repo": "", "summary": "", "files": [], "steps": [], "risk": "", "tests": "", "rollback": ""})
    md = engineering_report(_inv(findings=f), f)
    assert "_No code change proposed._" in md


def test_issue_title_and_body():
    assert issue_title(_inv(), FINDINGS) == "Offer amount reset by Model 8 callback"
    assert len(issue_title(_inv(), {"title": "x" * 300})) <= 120
    body = issue_body(_inv(), "REPORT", "approved-for-fix", "https://trace.test")
    assert "approved-for-fix" in body and "https://trace.test/#/investigations/abc123" in body and body.endswith("REPORT")


def test_target_repo_and_branch():
    systems = {"repos": [{"name": "FundingMetrics/FM_Model8"}, {"name": "FundingMetrics/brokerd"}]}
    assert target_repo(_inv(), systems) == "FundingMetrics/FM_Model8"
    assert target_repo(_inv(findings=dict(FINDINGS, proposed_fix=dict(FINDINGS["proposed_fix"], repo="brokerd"))), systems) == "FundingMetrics/brokerd"
    assert target_repo(_inv(findings=dict(FINDINGS, proposed_fix=dict(FINDINGS["proposed_fix"], repo="salesforce"))), systems) is None
    assert target_repo(_inv(findings=dict(FINDINGS, proposed_fix=dict(FINDINGS["proposed_fix"], repo="Other/unknown"))), systems) is None
    b = branch_name(_inv())
    assert b.startswith("claude/fix-12-offer-amount-reset") and len(b) <= 80


def test_pr_body():
    summary = {"summary": "Guarded the write.", "files_changed": ["app/callback.py"], "tests_run": "pytest -q", "tests_passed": True, "risk": "low", "rollback": "revert", "notes": ""}
    body = pull_request_body(_inv(), summary, SimpleNamespace(session_id="s", cost_usd=1.234))
    assert "issues/12" in body and "`app/callback.py`" in body and "Result: passed" in body and "$1.23" in body
