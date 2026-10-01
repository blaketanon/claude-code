"""Worker claim + finish logic with a stubbed investigator (no Claude calls)."""
from conftest import FINDINGS


def test_claim_and_finish(app, sf, monkeypatch):
    from gero_trace import investigator, worker
    from gero_trace.db import Session
    from gero_trace.models import Investigation

    r = sf.post("/api/investigations", json={"question": "Why did the offer amount change on Acme yesterday?", "asked_by": {"name": "Dana"}})
    inv_id = r.get_json()["id"]

    with app.app_context():
        kind, inv = worker.claim("test-worker")
        assert kind == "investigate" and inv.id == inv_id
        assert worker.claim("test-worker") == (None, None)      # nothing else queued

        class Outcome:
            findings = FINDINGS; text = "done"; cost_usd = 3.21; num_turns = 17; session_id = "sess"; subtype = "success"; error = None

        def fake_run(inv, cfg, root, on_event):
            on_event("tool", "$ sf data query ...")
            on_event("text", "Found it.")
            return Outcome()
        monkeypatch.setattr(investigator, "run_investigation", fake_run)
        worker.investigate(inv, worker.cfg_dict(), type("W", (), {"root": "/tmp"})())

    d = sf.get(f"/api/investigations/{inv_id}").get_json()
    assert d["status"] == "answered" and d["confidence"] == "High" and d["cost_usd"] == 3.21
    assert "<strong>Short answer:</strong>" in d["answer_html"]
    assert d["report_markdown"].startswith("# Offer amount reset")
    with app.app_context():
        row = Session().get(Investigation, inv_id)
        kinds = [e.kind for e in row.events]
        assert "tool" in kinds and "text" in kinds and row.events[-1].text.startswith("answered")


def test_failed_run(app, sf, monkeypatch):
    from gero_trace import investigator, worker
    r = sf.post("/api/investigations", json={"question": "Why did this account get flagged?", "asked_by": {"name": "Dana"}})
    inv_id = r.get_json()["id"]
    with app.app_context():
        kind, inv = worker.claim("w")

        class Outcome:
            findings = None; text = None; cost_usd = 8.0; num_turns = 80; session_id = "s"; subtype = "error_max_budget_usd"; error = "Claude stopped: error_max_budget_usd"
        monkeypatch.setattr(investigator, "run_investigation", lambda *a, **k: Outcome())
        worker.investigate(inv, worker.cfg_dict(), type("W", (), {"root": "/tmp"})())
    d = sf.get(f"/api/investigations/{inv_id}").get_json()
    assert d["status"] == "failed" and "budget" in d["error"]


def test_fix_flow_with_fake_apply(app, sf, monkeypatch):
    from conftest import answered
    from gero_trace import fixer, worker
    inv_id = answered(app, status="reported", fix_status="approved", issue_repo="FundingMetrics/gero-trace", issue_number=3,
                      issue_url="https://github.com/FundingMetrics/gero-trace/issues/3")
    gh = app.extensions["github"]
    with app.app_context():
        kind, inv = worker.claim("w")
        assert kind == "fix"
        monkeypatch.setattr(fixer, "apply_fix", lambda inv, cfg, systems, github, on_event: {
            "changed": True, "repo": "FundingMetrics/FM_Model8", "branch": "claude/fix-3-x", "pr_url": "https://github.com/FundingMetrics/FM_Model8/pull/9",
            "pr_number": 9, "summary": {"title": "Guard"}, "cost_usd": 4.0})
        worker.fix(inv, worker.cfg_dict(), {"repos": []}, gh)
    d = sf.get(f"/api/investigations/{inv_id}").get_json()
    assert d["fix"]["status"] == "pr_open" and d["fix"]["pr_url"].endswith("/pull/9")
    assert gh.comments and "pull/9" in gh.comments[0][2]
