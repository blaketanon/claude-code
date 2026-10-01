"""Two ways in:
  * Salesforce (and other machines) send `X-Api-Key: <SALESFORCE_API_KEY>` — the Named Credential adds it.
  * People use the admin page with ADMIN_PASSWORD (a session cookie). Login is rate limited per IP.
"""
import hmac
import secrets
import time
from collections import defaultdict, deque
from functools import wraps

from flask import current_app, jsonify, request, session

_attempts = defaultdict(deque)


def init_app(app):
    @app.before_request
    def _https_redirect():
        if app.config.get("HTTPS_ONLY") and request.headers.get("X-Forwarded-Proto", "https") == "http" and request.path != "/healthz":
            from flask import redirect
            return redirect(request.url.replace("http://", "https://", 1), code=301)

    @app.after_request
    def _headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        if app.config.get("HTTPS_ONLY"):
            resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return resp


def _eq(a, b):
    return bool(a) and bool(b) and hmac.compare_digest(str(a).encode(), str(b).encode())


def api_key_ok():
    return _eq(request.headers.get("X-Api-Key"), current_app.config.get("SALESFORCE_API_KEY"))


def admin_ok():
    return bool(session.get("admin")) and _eq(session.get("admin"), current_app.config.get("ADMIN_PASSWORD")[:8] if current_app.config.get("ADMIN_PASSWORD") else "")


def api_key_required(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        if api_key_ok() or admin_ok():
            return fn(*a, **kw)
        return jsonify(error="unauthorized", message="missing or invalid X-Api-Key"), 401
    return wrapper


def admin_required(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        if admin_ok():
            return fn(*a, **kw)
        return jsonify(error="unauthorized", message="sign in first"), 401
    return wrapper


def too_many_attempts(ip):
    now = time.time()
    q = _attempts[ip]
    while q and now - q[0] > current_app.config["LOGIN_WINDOW_SECONDS"]:
        q.popleft()
    return len(q) >= current_app.config["LOGIN_MAX_ATTEMPTS"]


def record_attempt(ip):
    _attempts[ip].append(time.time())


def login(password):
    ip = request.headers.get("X-Forwarded-For", request.remote_addr or "?").split(",")[0].strip()
    if too_many_attempts(ip):
        return False, "too many attempts, try again later"
    expected = current_app.config.get("ADMIN_PASSWORD")
    if not expected or not _eq(password, expected):
        record_attempt(ip)
        return False, "wrong password"
    session.clear()
    session.permanent = True
    session["admin"] = expected[:8]
    session["csrf"] = secrets.token_urlsafe(24)
    return True, None


def csrf_ok():
    return _eq(request.headers.get("X-CSRF-Token"), session.get("csrf"))
