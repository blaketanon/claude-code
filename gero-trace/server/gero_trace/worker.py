"""The worker process: claims queued investigations and approved fixes from the database and runs them.

    python -m gero_trace.worker

Runs next to the web process (Procfile `worker:`), or on its own instance. Safe to run several: jobs are
claimed with an atomic UPDATE. Keeps the workspace (repos + Salesforce metadata) fresh between jobs.
"""
import logging
import os
import signal
import socket
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import update

from .config import Config
from .db import Session, init_standalone
from .github_client import GitHub
from .models import Investigation, InvestigationEvent, utcnow
from .render import engineering_report, to_html
from .salesforce import SalesforceAccess
from .systems import Workspace, load_systems

log = logging.getLogger("gero_trace.worker")
STOP = threading.Event()


def cfg_dict():
    return {k: getattr(Config, k) for k in dir(Config) if k.isupper()}


def record_event(inv_id, phase, kind, text):
    s = Session()
    try:
        s.add(InvestigationEvent(investigation_id=inv_id, phase=phase, kind=kind, text=str(text)[:20000]))
        if kind in ("status", "tool"):
            s.execute(update(Investigation).where(Investigation.id == inv_id).values(progress=str(text)[:250], updated_at=utcnow()))
        s.commit()
    except Exception:  # noqa: BLE001
        s.rollback()
        log.exception("could not record event")
    finally:
        Session.remove()


def claim(worker_name):
    """Atomically take one job. Returns (kind, Investigation) or (None, None)."""
    s = Session()
    try:
        row = s.query(Investigation).filter_by(status="queued").order_by(Investigation.created_at).first()
        if row:
            n = s.execute(update(Investigation).where(Investigation.id == row.id, Investigation.status == "queued")
                          .values(status="running", worker=worker_name, started_at=utcnow(), progress="Claude is starting…")).rowcount
            s.commit()
            if n == 1:
                s.refresh(row)
                return "investigate", row
        row = s.query(Investigation).filter_by(fix_status="approved").order_by(Investigation.approved_at).first()
        if row:
            n = s.execute(update(Investigation).where(Investigation.id == row.id, Investigation.fix_status == "approved")
                          .values(fix_status="running", worker=worker_name)).rowcount
            s.commit()
            if n == 1:
                s.refresh(row)
                return "fix", row
        return None, None
    finally:
        Session.remove()


def investigate(inv, cfg, workspace):
    from . import investigator
    inv_id = inv.id
    record_event(inv_id, "investigate", "status", "Reading Salesforce history, logs and the code…")
    try:
        outcome = investigator.run_investigation(inv, cfg, workspace.root, on_event=lambda k, t: record_event(inv_id, "investigate", k, t))
    except Exception as e:  # noqa: BLE001
        finish_failed(inv_id, f"investigator crashed: {e}", traceback.format_exc())
        return
    s = Session()
    try:
        row = s.get(Investigation, inv_id)
        row.cost_usd = outcome.cost_usd
        row.num_turns = outcome.num_turns
        row.claude_session_id = outcome.session_id
        row.finished_at = utcnow()
        f = outcome.findings
        if f:
            row.findings = f
            row.answer_markdown = f.get("answer_markdown") or outcome.text or ""
            row.answer_html = to_html(row.answer_markdown)
            row.confidence = f.get("confidence")
            row.needs_fix = bool(f.get("needs_fix"))
            row.report_markdown = engineering_report(row, f)
            row.status = "answered"
            row.progress = None
            row.error = None if not outcome.error else f"finished with a warning: {outcome.error}"
            s.add(InvestigationEvent(investigation_id=inv_id, phase="investigate", kind="status",
                                     text=f"answered ({f.get('confidence')}, ${outcome.cost_usd or 0:.2f}, {outcome.num_turns} turns)"))
        else:
            row.status = "failed"
            row.progress = None
            row.error = outcome.error or "Claude finished without a structured answer."
            if outcome.text:
                row.answer_markdown = outcome.text
                row.answer_html = to_html(outcome.text)
            s.add(InvestigationEvent(investigation_id=inv_id, phase="investigate", kind="error", text=row.error))
        s.commit()
    finally:
        Session.remove()


def finish_failed(inv_id, error, detail=None):
    s = Session()
    try:
        row = s.get(Investigation, inv_id)
        row.status = "failed"
        row.progress = None
        row.error = error[:20000]
        row.finished_at = utcnow()
        s.add(InvestigationEvent(investigation_id=inv_id, phase="investigate", kind="error", text=(detail or error)[:20000]))
        s.commit()
    finally:
        Session.remove()


def fix(inv, cfg, systems, github):
    from .fixer import apply_fix
    inv_id = inv.id
    if github is None:
        set_fix(inv_id, "failed", error="GitHub is not configured on the worker")
        return
    record_event(inv_id, "fix", "status", "Starting the fix")
    try:
        result = apply_fix(inv, cfg, systems, github, on_event=lambda k, t: record_event(inv_id, "fix", k, t))
    except Exception as e:  # noqa: BLE001
        log.exception("fix failed")
        set_fix(inv_id, "failed", error=str(e))
        comment_issue(github, inv, f"Claude could not implement the fix: {e}\n\nRetry from the Gero Trace admin page or remove and re-add the approval label.")
        return
    if result.get("changed"):
        set_fix(inv_id, "pr_open", repo=result["repo"], branch=result["branch"], pr_url=result["pr_url"], pr_number=result["pr_number"],
                summary=result["summary"], cost=result["cost_usd"])
        comment_issue(github, inv, f"Draft pull request opened: {result['pr_url']}\n\nReview it before merging; nothing is deployed automatically.")
    else:
        set_fix(inv_id, "failed", summary=result.get("summary"), cost=result.get("cost_usd"), error=result.get("message"))
        comment_issue(github, inv, f"Claude did not change any code:\n\n{result.get('message')}")


def set_fix(inv_id, status, repo=None, branch=None, pr_url=None, pr_number=None, summary=None, cost=None, error=None):
    s = Session()
    try:
        row = s.get(Investigation, inv_id)
        row.fix_status = status
        if repo: row.fix_repo = repo
        if branch: row.fix_branch = branch
        if pr_url: row.fix_pr_url = pr_url
        if pr_number: row.fix_pr_number = pr_number
        if summary is not None: row.fix_summary = summary
        if cost is not None: row.fix_cost_usd = cost
        row.fix_error = error
        s.add(InvestigationEvent(investigation_id=inv_id, phase="fix", kind="error" if error else "status",
                                 text=error or f"fix {status}" + (f": {pr_url}" if pr_url else "")))
        s.commit()
    finally:
        Session.remove()


def comment_issue(github, inv, text):
    if not (github and inv.issue_repo and inv.issue_number):
        return
    try:
        github.comment(inv.issue_repo, inv.issue_number, text)
    except Exception as e:  # noqa: BLE001
        log.warning("could not comment on issue: %s", e)


def main():
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = cfg_dict()
    init_standalone(cfg["SQLALCHEMY_DATABASE_URI"])
    worker_name = f"{socket.gethostname()}:{os.getpid()}"
    systems = load_systems(cfg["SYSTEMS_FILE"])
    github = GitHub.from_config(cfg)
    sf = SalesforceAccess.from_config(cfg)
    workspace = Workspace(cfg["WORKSPACE_DIR"], systems, github)
    workspace.write_systems_md()
    if github is None:
        log.warning("GitHub is not configured: repos will not sync and issues/PRs cannot be created")
    if not sf.configured:
        log.warning("Salesforce is not configured (SF_USERNAME/SF_CLIENT_ID/SF_JWT_KEY, sf CLI): investigator will have no org access")
    else:
        try:
            sf.login()
        except Exception as e:  # noqa: BLE001
            log.warning("salesforce login failed: %s", e)

    def _stop(*_):
        STOP.set()
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    pool = ThreadPoolExecutor(max_workers=max(1, cfg["WORKER_CONCURRENCY"]))
    busy = []
    log.info("worker %s ready (model %s, budget $%.2f)", worker_name, cfg["ANTHROPIC_MODEL"], cfg["INVESTIGATION_BUDGET_USD"])
    while not STOP.is_set():
        busy = [f for f in busy if not f.done()]
        if len(busy) < cfg["WORKER_CONCURRENCY"]:
            try:
                workspace.sync_repos(max_age_minutes=cfg["REPO_SYNC_MINUTES"])
                workspace.sync_salesforce(sf, max_age_minutes=cfg["SF_METADATA_SYNC_MINUTES"])
            except Exception as e:  # noqa: BLE001
                log.warning("workspace sync: %s", e)
            kind, inv = claim(worker_name)
            if kind == "investigate":
                log.info("investigating %s", inv.id)
                busy.append(pool.submit(investigate, inv, cfg, workspace))
                continue
            if kind == "fix":
                log.info("fixing %s", inv.id)
                busy.append(pool.submit(fix, inv, cfg, systems, github))
                continue
        STOP.wait(cfg["WORKER_POLL_SECONDS"])
    log.info("worker stopping; waiting for running jobs")
    pool.shutdown(wait=True)


if __name__ == "__main__":
    main()
