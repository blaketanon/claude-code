"""JSON API.

  Salesforce (X-Api-Key):
    POST /api/investigations                  ask a question           -> 202 Investigation
    GET  /api/investigations/<id>             status / answer          -> 200 Investigation
    POST /api/investigations/<id>/report      file as a GitHub issue   -> 200 Investigation
  GitHub:
    POST /api/webhooks/github                 issue labelled / PR merged (HMAC-signed)
  Admin page (session cookie):
    POST /api/login, POST /api/logout, GET /api/me
    GET  /api/admin/investigations[?status=&q=&limit=]
    GET  /api/admin/investigations/<id>       full detail + events
    POST /api/admin/investigations/<id>/approve | /decline | /retry
    GET  /api/admin/systems                   the systems map the investigator uses
"""
import hashlib
import hmac
import logging
from datetime import datetime, timezone

from flask import Blueprint, current_app, jsonify, request, session
from sqlalchemy import or_

from . import auth
from .auth import admin_required, api_key_required
from .db import Session
from .models import Investigation, InvestigationEvent, utcnow
from .render import engineering_report, issue_body, issue_title

bp = Blueprint("api", __name__, url_prefix="/api")
log = logging.getLogger(__name__)


def body():
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def err(code, message, status=400):
    return jsonify(error=code, message=message), status


def _str(v, n):
    return None if v is None else str(v).replace("\x00", "")[:n]


def _person(d):
    if not isinstance(d, dict):
        return None
    return {k: _str(d.get(k), 255) for k in ("sf_user_id", "name", "email") if d.get(k)}


def add_event(inv, phase, kind, text):
    inv.events.append(InvestigationEvent(phase=phase, kind=kind, text=str(text)[:20000]))


# --------------------------------------------------------------------------- Salesforce

@bp.post("/investigations")
@api_key_required
def create_investigation():
    d = body()
    q = (d.get("question") or "").strip()
    if len(q) < 8:
        return err("bad_request", "question is required (at least a few words)")
    if len(q) > 20000:
        return err("bad_request", "question is too long")
    inv = Investigation(
        question=q,
        org_id=_str(d.get("org_id"), 18),
        record_id=_str(d.get("record_id"), 18),
        record_object=_str(d.get("record_object"), 80),
        record_url=_str(d.get("record_url"), 512),
        record_context=d.get("record_context") if isinstance(d.get("record_context"), dict) else None,
        asked_by=_person(d.get("asked_by")),
        status="queued",
        progress="Waiting for an investigator…",
    )
    add_event(inv, "system", "status", f"queued by {((inv.asked_by or {}).get('name')) or 'unknown'}")
    s = Session()
    s.add(inv)
    s.commit()
    return jsonify(inv.to_api()), 202


@bp.get("/investigations/<inv_id>")
@api_key_required
def get_investigation(inv_id):
    inv = Session().get(Investigation, inv_id)
    if not inv:
        return err("not_found", "no such investigation", 404)
    return jsonify(inv.to_api())


@bp.post("/investigations/<inv_id>/report")
@api_key_required
def report_investigation(inv_id):
    s = Session()
    inv = s.get(Investigation, inv_id)
    if not inv:
        return err("not_found", "no such investigation", 404)
    if inv.status == "reported" and inv.issue_url:
        return jsonify(inv.to_api())            # idempotent
    if inv.status != "answered":
        return err("conflict", f"investigation is {inv.status}, not answered", 409)
    d = body()
    inv.reporter_comment = _str(d.get("comment"), 20000)
    inv.reported_by = _person(d.get("reported_by")) or inv.asked_by

    gh = current_app.extensions.get("github")
    if gh is None:
        from .github_client import GitHub
        gh = GitHub.from_config(current_app.config)
    if gh is None:
        return err("not_configured", "GitHub is not configured on the server (GITHUB_TOKEN or GitHub App settings)", 503)

    cfg = current_app.config
    report_md = engineering_report(inv, inv.findings)
    inv.report_markdown = report_md
    repo = cfg["GITHUB_ISSUES_REPO"]
    try:
        issue = gh.create_issue(repo, issue_title(inv, inv.findings),
                                issue_body(inv, report_md, cfg["GITHUB_APPROVE_LABEL"], cfg["PUBLIC_URL"]),
                                labels=cfg["GITHUB_REPORT_LABELS"],
                                assignees=[cfg["GITHUB_DEFAULT_ASSIGNEE"]] if cfg["GITHUB_DEFAULT_ASSIGNEE"] else ())
    except Exception as e:  # noqa: BLE001
        log.exception("issue creation failed")
        return err("github_error", f"could not create the GitHub issue: {e}", 502)
    inv.issue_repo = repo
    inv.issue_number = issue.get("number")
    inv.issue_url = issue.get("html_url")
    inv.status = "reported"
    inv.fix_status = "awaiting_approval" if inv.needs_fix or (inv.findings or {}).get("proposed_fix") else None
    add_event(inv, "system", "status", f"reported as {inv.issue_url} by {(inv.reported_by or {}).get('name') or 'unknown'}")
    s.commit()
    return jsonify(inv.to_api())


# --------------------------------------------------------------------------- GitHub webhook

@bp.post("/webhooks/github")
def github_webhook():
    secret = current_app.config.get("GITHUB_WEBHOOK_SECRET", "")
    if not secret:
        return err("not_configured", "GITHUB_WEBHOOK_SECRET is not set", 503)
    sig = request.headers.get("X-Hub-Signature-256", "")
    expected = "sha256=" + hmac.new(secret.encode(), request.get_data(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return err("unauthorized", "bad signature", 401)
    event = request.headers.get("X-GitHub-Event", "")
    payload = request.get_json(silent=True) or {}
    cfg = current_app.config
    s = Session()

    if event == "issues":
        issue = payload.get("issue") or {}
        repo = (payload.get("repository") or {}).get("full_name")
        inv = s.query(Investigation).filter_by(issue_repo=repo, issue_number=issue.get("number")).first()
        if not inv:
            return jsonify(ok=True, ignored="not a Gero Trace issue")
        action = payload.get("action")
        label = (payload.get("label") or {}).get("name")
        actor = (payload.get("sender") or {}).get("login")
        if action == "labeled" and label == cfg["GITHUB_APPROVE_LABEL"]:
            approve(inv, actor)
        elif action == "labeled" and label == cfg["GITHUB_DECLINE_LABEL"]:
            decline(inv, actor, "labelled " + label)
        elif action == "unlabeled" and label == cfg["GITHUB_APPROVE_LABEL"] and inv.fix_status == "approved":
            decline(inv, actor, "approval label removed")
        elif action == "closed" and inv.fix_status in ("awaiting_approval", "approved"):
            decline(inv, actor, "issue closed")
        elif action == "reopened" and inv.fix_status == "declined":
            inv.fix_status = "awaiting_approval"
            add_event(inv, "system", "status", f"issue reopened by {actor}")
        s.commit()
        return jsonify(ok=True, fix_status=inv.fix_status)

    if event == "pull_request":
        pr = payload.get("pull_request") or {}
        repo = (payload.get("repository") or {}).get("full_name")
        branch = (pr.get("head") or {}).get("ref")
        inv = s.query(Investigation).filter_by(fix_repo=repo, fix_branch=branch).first()
        if inv and payload.get("action") == "closed":
            if pr.get("merged"):
                inv.fix_status = "merged"
                add_event(inv, "system", "status", f"fix PR merged: {pr.get('html_url')}")
            else:
                inv.fix_status = "declined"
                add_event(inv, "system", "status", f"fix PR closed without merge: {pr.get('html_url')}")
            s.commit()
        return jsonify(ok=True)

    return jsonify(ok=True, ignored=event)


def approve(inv, actor):
    if inv.fix_status in ("pr_open", "merged", "running"):
        return
    inv.fix_status = "approved"
    inv.approved_by = actor
    inv.approved_at = utcnow()
    inv.fix_error = None
    add_event(inv, "system", "status", f"fix approved by {actor}")


def decline(inv, actor, why):
    if inv.fix_status in ("pr_open", "merged"):
        return
    inv.fix_status = "declined"
    add_event(inv, "system", "status", f"fix declined by {actor}: {why}")


# --------------------------------------------------------------------------- admin

@bp.post("/login")
def login():
    ok, why = auth.login(body().get("password", ""))
    if not ok:
        return err("unauthorized", why, 401)
    return jsonify(ok=True, csrf=session["csrf"])


@bp.post("/logout")
def logout():
    session.clear()
    return jsonify(ok=True)


@bp.get("/me")
def me():
    if auth.admin_ok():
        return jsonify(admin=True, csrf=session.get("csrf"), app=current_app.config["APP_NAME"],
                       approve_label=current_app.config["GITHUB_APPROVE_LABEL"])
    return jsonify(admin=False, app=current_app.config["APP_NAME"])


def _csrf():
    if not auth.csrf_ok():
        return err("forbidden", "missing CSRF token", 403)
    return None


@bp.get("/admin/investigations")
@admin_required
def admin_list():
    q = Session().query(Investigation)
    if request.args.get("status"):
        q = q.filter(Investigation.status == request.args["status"])
    if request.args.get("fix_status"):
        q = q.filter(Investigation.fix_status == request.args["fix_status"])
    if request.args.get("q"):
        like = f"%{request.args['q']}%"
        q = q.filter(or_(Investigation.question.ilike(like), Investigation.answer_markdown.ilike(like), Investigation.record_id.ilike(like)))
    limit = min(int(request.args.get("limit", 100) or 100), 500)
    rows = q.order_by(Investigation.created_at.desc()).limit(limit).all()
    return jsonify(items=[r.to_api() | {"asked_by": r.asked_by, "record_object": r.record_object, "record_id": r.record_id} for r in rows])


@bp.get("/admin/investigations/<inv_id>")
@admin_required
def admin_detail(inv_id):
    inv = Session().get(Investigation, inv_id)
    if not inv:
        return err("not_found", "no such investigation", 404)
    d = inv.to_admin()
    d["events"] = [e.to_dict() for e in inv.events]
    return jsonify(d)


@bp.post("/admin/investigations/<inv_id>/approve")
@admin_required
def admin_approve(inv_id):
    if (bad := _csrf()):
        return bad
    s = Session()
    inv = s.get(Investigation, inv_id)
    if not inv:
        return err("not_found", "no such investigation", 404)
    if inv.status not in ("answered", "reported"):
        return err("conflict", "nothing to approve yet", 409)
    approve(inv, "admin page")
    gh = current_app.extensions.get("github")
    if inv.issue_url and gh:
        try:
            gh.set_labels(inv.issue_repo, inv.issue_number, add=[current_app.config["GITHUB_APPROVE_LABEL"]], remove=["needs-approval"])
        except Exception as e:  # noqa: BLE001
            add_event(inv, "system", "error", f"could not update issue labels: {e}")
    s.commit()
    return jsonify(inv.to_admin())


@bp.post("/admin/investigations/<inv_id>/decline")
@admin_required
def admin_decline(inv_id):
    if (bad := _csrf()):
        return bad
    s = Session()
    inv = s.get(Investigation, inv_id)
    if not inv:
        return err("not_found", "no such investigation", 404)
    decline(inv, "admin page", body().get("reason") or "declined on the admin page")
    s.commit()
    return jsonify(inv.to_admin())


@bp.post("/admin/investigations/<inv_id>/retry")
@admin_required
def admin_retry(inv_id):
    if (bad := _csrf()):
        return bad
    s = Session()
    inv = s.get(Investigation, inv_id)
    if not inv:
        return err("not_found", "no such investigation", 404)
    what = body().get("what", "investigation")
    if what == "fix":
        if inv.fix_status not in ("failed", "declined"):
            return err("conflict", "fix is not in a retryable state", 409)
        inv.fix_status = "approved"
        inv.fix_error = None
        add_event(inv, "system", "status", "fix retried from the admin page")
    else:
        if inv.status not in ("failed", "answered"):
            return err("conflict", "investigation is not in a retryable state", 409)
        inv.status = "queued"
        inv.error = None
        inv.progress = "Waiting for an investigator…"
        add_event(inv, "system", "status", "investigation retried from the admin page")
    s.commit()
    return jsonify(inv.to_admin())


@bp.get("/admin/systems")
@admin_required
def admin_systems():
    from .systems import load_systems
    return jsonify(load_systems(current_app.config["SYSTEMS_FILE"]))


@bp.get("/admin/stats")
@admin_required
def admin_stats():
    s = Session()
    out = {}
    for st in ("queued", "running", "answered", "reported", "failed"):
        out[st] = s.query(Investigation).filter_by(status=st).count()
    out["awaiting_approval"] = s.query(Investigation).filter_by(fix_status="awaiting_approval").count()
    out["pr_open"] = s.query(Investigation).filter_by(fix_status="pr_open").count()
    out["now"] = datetime.now(timezone.utc).isoformat()
    return jsonify(out)
