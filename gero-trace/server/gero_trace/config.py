"""All configuration comes from environment variables so the same code runs locally (SQLite)
and on Elastic Beanstalk (Postgres through the RDS_* variables EB injects)."""
import os
import secrets


def _bool(name, default=False):
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def _int(name, default):
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


def _float(name, default):
    try:
        return float(os.environ.get(name, "") or default)
    except ValueError:
        return default


def _pem(name):
    """PEM keys arrive through env vars with literal \\n; turn them back into newlines."""
    return (os.environ.get(name) or "").replace("\\n", "\n").strip()


def database_url():
    if os.environ.get("DATABASE_URL"):
        url = os.environ["DATABASE_URL"]
        if url.startswith("postgres://"):
            url = "postgresql+psycopg://" + url[len("postgres://"):]
        elif url.startswith("postgresql://"):
            url = "postgresql+psycopg://" + url[len("postgresql://"):]
        return url
    if os.environ.get("RDS_HOSTNAME"):
        from urllib.parse import quote_plus
        return "postgresql+psycopg://{u}:{p}@{h}:{port}/{db}".format(
            u=quote_plus(os.environ["RDS_USERNAME"]), p=quote_plus(os.environ["RDS_PASSWORD"]),
            h=os.environ["RDS_HOSTNAME"], port=os.environ.get("RDS_PORT", "5432"), db=os.environ["RDS_DB_NAME"])
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.environ.get("SQLITE_PATH") or os.path.join(base, "instance", "gero_trace.db")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return "sqlite:///" + path


class Config:
    APP_NAME = "Gero Trace"
    SECRET_KEY = os.environ.get("SECRET_KEY") or secrets.token_hex(32)
    SQLALCHEMY_DATABASE_URI = database_url()
    HTTPS_ONLY = _bool("HTTPS_ONLY", False)
    SESSION_COOKIE_NAME = "gero_trace"
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = HTTPS_ONLY
    PERMANENT_SESSION_LIFETIME = 60 * 60 * 24 * 14
    MAX_CONTENT_LENGTH = 4 * 1024 * 1024
    PUBLIC_URL = os.environ.get("PUBLIC_URL", "").rstrip("/")

    # Who may call the API / admin UI
    ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
    SALESFORCE_API_KEY = os.environ.get("SALESFORCE_API_KEY", "")
    LOGIN_MAX_ATTEMPTS = 8
    LOGIN_WINDOW_SECONDS = 600

    # Claude
    ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
    ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-opus-5-5")
    CLAUDE_EFFORT = os.environ.get("CLAUDE_EFFORT", "high")
    INVESTIGATION_BUDGET_USD = _float("INVESTIGATION_BUDGET_USD", 8.0)
    INVESTIGATION_MAX_TURNS = _int("INVESTIGATION_MAX_TURNS", 80)
    FIX_BUDGET_USD = _float("FIX_BUDGET_USD", 15.0)
    FIX_MAX_TURNS = _int("FIX_MAX_TURNS", 120)

    # GitHub
    GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
    GITHUB_APP_ID = os.environ.get("GITHUB_APP_ID", "")
    GITHUB_APP_INSTALLATION_ID = os.environ.get("GITHUB_APP_INSTALLATION_ID", "")
    GITHUB_APP_PRIVATE_KEY = _pem("GITHUB_APP_PRIVATE_KEY")
    GITHUB_ISSUES_REPO = os.environ.get("GITHUB_ISSUES_REPO", "FundingMetrics/gero-trace")
    GITHUB_WEBHOOK_SECRET = os.environ.get("GITHUB_WEBHOOK_SECRET", "")
    GITHUB_APPROVE_LABEL = os.environ.get("GITHUB_APPROVE_LABEL", "approved-for-fix")
    GITHUB_DECLINE_LABEL = os.environ.get("GITHUB_DECLINE_LABEL", "wont-fix")
    GITHUB_REPORT_LABELS = [s for s in os.environ.get("GITHUB_REPORT_LABELS", "claude-report,needs-approval").split(",") if s]
    GITHUB_DEFAULT_ASSIGNEE = os.environ.get("GITHUB_DEFAULT_ASSIGNEE", "")
    GITHUB_PR_DRAFT = _bool("GITHUB_PR_DRAFT", True)

    # Salesforce (read-only integration user, JWT bearer flow)
    SF_USERNAME = os.environ.get("SF_USERNAME", "")
    SF_CLIENT_ID = os.environ.get("SF_CLIENT_ID", "")
    SF_JWT_KEY = _pem("SF_JWT_KEY")
    SF_LOGIN_URL = os.environ.get("SF_LOGIN_URL", "https://login.salesforce.com")
    SF_ORG_ALIAS = os.environ.get("SF_ORG_ALIAS", "trace")

    # Read-only database access the investigator may use through psql (optional)
    READONLY_DATABASE_URLS = os.environ.get("READONLY_DATABASE_URLS", "")  # "name=postgres://ro:...;name2=..."

    # Workspace on disk
    WORKSPACE_DIR = os.path.abspath(os.environ.get("WORKSPACE_DIR", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "workspace")))
    SYSTEMS_FILE = os.path.abspath(os.environ.get("SYSTEMS_FILE", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config", "systems.yaml")))
    REPO_SYNC_MINUTES = _int("REPO_SYNC_MINUTES", 30)
    SF_METADATA_SYNC_MINUTES = _int("SF_METADATA_SYNC_MINUTES", 180)
    WORKER_POLL_SECONDS = _int("WORKER_POLL_SECONDS", 5)
    WORKER_CONCURRENCY = _int("WORKER_CONCURRENCY", 1)
