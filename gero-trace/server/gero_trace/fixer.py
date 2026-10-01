"""Implements an approved fix: fresh clone of the target repo, a branch, Claude makes the change,
then the worker commits, pushes and opens a draft pull request linked to the issue."""
import logging
import os
import re
import shutil
import tempfile

from . import investigator
from .render import engineering_report
from .systems import run

log = logging.getLogger(__name__)


def target_repo(inv, systems):
    """Which repository to change: the report's proposed_fix.repo, validated against the systems map."""
    fix = (inv.findings or {}).get("proposed_fix") or {}
    want = (fix.get("repo") or "").strip()
    if not want or want.lower() in ("salesforce", "none", "n/a", ""):
        return None
    names = {r["name"].lower(): r["name"] for r in systems.get("repos", []) if r.get("name")}
    if want.lower() in names:
        return names[want.lower()]
    # allow a bare repo name
    for full in names.values():
        if full.split("/")[-1].lower() == want.split("/")[-1].lower():
            return full
    return None


def branch_name(inv):
    slug = re.sub(r"[^a-z0-9]+", "-", ((inv.findings or {}).get("title") or inv.question)[:40].lower()).strip("-")
    return f"claude/fix-{inv.issue_number or inv.id[:8]}-{slug}"[:80].rstrip("-")


def apply_fix(inv, cfg, systems, github, on_event):
    repo = target_repo(inv, systems)
    if not repo:
        raise RuntimeError("the report does not name a repository this service can change (proposed_fix.repo). "
                           "Salesforce configuration and data changes are made by hand.")
    report_md = inv.report_markdown or engineering_report(inv, inv.findings)
    base = github.default_branch(repo)
    branch = branch_name(inv)
    work = tempfile.mkdtemp(prefix="gero-fix-")
    repo_dir = os.path.join(work, repo.split("/")[-1])
    try:
        on_event("status", f"cloning {repo} ({base})")
        run(["git", "clone", "--depth", "50", "--branch", base, github.clone_url(repo), repo_dir], timeout=900)
        run(["git", "-C", repo_dir, "remote", "set-url", "origin", f"https://github.com/{repo}.git"], timeout=30)
        run(["git", "-C", repo_dir, "config", "user.name", "Claude (Gero Trace)"], timeout=30)
        run(["git", "-C", repo_dir, "config", "user.email", "claude-trace@fundingmetrics.com"], timeout=30)
        run(["git", "-C", repo_dir, "checkout", "-b", branch], timeout=30)

        on_event("status", "Claude is implementing the fix")
        outcome = investigator.run_fix(inv, cfg, repo_dir, repo, report_md, on_event)
        summary = outcome.findings or {}
        if outcome.error and not summary:
            raise RuntimeError(outcome.error)
        status = run(["git", "-C", repo_dir, "status", "--porcelain"], timeout=30).stdout.strip()
        if not summary.get("changed", bool(status)) or not status:
            return {"changed": False, "summary": summary, "cost_usd": outcome.cost_usd, "session_id": outcome.session_id,
                    "message": summary.get("notes") or summary.get("summary") or "Claude made no changes."}

        # Commit and push as the service, never from inside Claude's shell.
        run(["git", "-C", repo_dir, "add", "-A"], timeout=60)
        title = (summary.get("title") or f"Fix: {((inv.findings or {}).get('title') or inv.question)[:60]}").strip()[:72]
        body = (summary.get("summary") or "").strip()
        msg = f"{title}\n\n{body}\n\nResolves {inv.issue_url or 'Gero Trace investigation ' + inv.id}\n"
        run(["git", "-C", repo_dir, "commit", "-q", "-m", msg], timeout=60)
        run(["git", "-C", repo_dir, "push", "-u", github.clone_url(repo), branch], timeout=600)
        run(["git", "-C", repo_dir, "remote", "set-url", "origin", f"https://github.com/{repo}.git"], timeout=30)

        pr_body = pull_request_body(inv, summary, outcome)
        pr = github.create_pull(repo, head=branch, base=base, title=title, body=pr_body, draft=bool(cfg.get("GITHUB_PR_DRAFT", True)))
        return {"changed": True, "summary": summary, "cost_usd": outcome.cost_usd, "session_id": outcome.session_id,
                "branch": branch, "repo": repo, "pr_url": pr.get("html_url"), "pr_number": pr.get("number")}
    finally:
        shutil.rmtree(work, ignore_errors=True)


def pull_request_body(inv, summary, outcome):
    files = "\n".join(f"- `{f}`" for f in summary.get("files_changed") or []) or "_see diff_"
    tests = (summary.get("tests_run") or "").strip() or "_none run_"
    passed = "passed" if summary.get("tests_passed") else "**did not pass or were not run** — check before merging"
    parts = [
        f"Automated fix for {inv.issue_url or 'a Gero Trace investigation'} (investigation `{inv.id}`).",
        "",
        "## What changed",
        "",
        (summary.get("summary") or "").strip() or "_No summary provided._",
        "",
        "## Files",
        "",
        files,
        "",
        "## Tests",
        "",
        f"```\n{tests}\n```",
        f"Result: {passed}",
        "",
        f"**Risk:** {summary.get('risk', 'unknown')}  ",
        f"**Rollback:** {(summary.get('rollback') or 'revert this pull request').strip()}",
    ]
    if summary.get("notes"):
        parts += ["", "## Notes for the reviewer", "", summary["notes"].strip()]
    parts += ["", "---", f"_Opened by Gero Trace. Claude session `{outcome.session_id or '?'}`, cost ${(outcome.cost_usd or 0):.2f}. "
              "Review before merging; nothing is deployed automatically._"]
    return "\n".join(parts)
