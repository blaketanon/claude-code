"""Small GitHub REST client. Authenticates as a GitHub App installation (preferred: scoped to the
FundingMetrics org, short-lived tokens) or with a personal access token."""
import logging
import time

import requests

log = logging.getLogger(__name__)
API = "https://api.github.com"


class GitHubError(Exception):
    pass


class GitHub:
    def __init__(self, token=None, app_id=None, installation_id=None, private_key=None, session=None):
        self._pat = token or None
        self._app_id = app_id
        self._installation_id = installation_id
        self._private_key = private_key
        self._inst_token = None
        self._inst_expiry = 0
        self.http = session or requests.Session()
        self.http.headers.update({"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
                                  "User-Agent": "gero-trace"})

    @classmethod
    def from_config(cls, cfg):
        gh = cls(token=cfg.get("GITHUB_TOKEN"), app_id=cfg.get("GITHUB_APP_ID"),
                 installation_id=cfg.get("GITHUB_APP_INSTALLATION_ID"), private_key=cfg.get("GITHUB_APP_PRIVATE_KEY"))
        return gh if gh.configured else None

    @property
    def configured(self):
        return bool(self._pat or (self._app_id and self._installation_id and self._private_key))

    # ---- auth ------------------------------------------------------------
    def token(self):
        if self._pat:
            return self._pat
        if self._inst_token and time.time() < self._inst_expiry - 60:
            return self._inst_token
        import jwt  # PyJWT
        now = int(time.time())
        app_jwt = jwt.encode({"iat": now - 30, "exp": now + 540, "iss": str(self._app_id)}, self._private_key, algorithm="RS256")
        r = self.http.post(f"{API}/app/installations/{self._installation_id}/access_tokens",
                           headers={"Authorization": f"Bearer {app_jwt}"}, timeout=30)
        if r.status_code >= 300:
            raise GitHubError(f"installation token: HTTP {r.status_code} {r.text[:200]}")
        data = r.json()
        self._inst_token = data["token"]
        self._inst_expiry = time.time() + 3600
        return self._inst_token

    def clone_url(self, repo):
        """HTTPS clone URL with credentials embedded (used by the worker; never logged)."""
        tok = self.token()
        user = "x-access-token" if not self._pat else "oauth2"
        return f"https://{user}:{tok}@github.com/{repo}.git"

    def _req(self, method, path, **kw):
        kw.setdefault("timeout", 30)
        headers = kw.pop("headers", {})
        headers["Authorization"] = f"Bearer {self.token()}"
        r = self.http.request(method, path if path.startswith("http") else API + path, headers=headers, **kw)
        if r.status_code >= 300:
            raise GitHubError(f"{method} {path}: HTTP {r.status_code} {r.text[:300]}")
        return r.json() if r.text else {}

    # ---- issues ----------------------------------------------------------
    def create_issue(self, repo, title, body, labels=(), assignees=()):
        payload = {"title": title, "body": body, "labels": list(labels)}
        if assignees:
            payload["assignees"] = [a for a in assignees if a]
        try:
            return self._req("POST", f"/repos/{repo}/issues", json=payload)
        except GitHubError as e:
            if assignees and "422" in str(e):   # unknown assignee — retry without
                payload.pop("assignees", None)
                return self._req("POST", f"/repos/{repo}/issues", json=payload)
            raise

    def comment(self, repo, number, body):
        return self._req("POST", f"/repos/{repo}/issues/{number}/comments", json={"body": body})

    def set_labels(self, repo, number, add=(), remove=()):
        for lab in remove:
            try:
                self._req("DELETE", f"/repos/{repo}/issues/{number}/labels/{lab}")
            except GitHubError:
                pass
        if add:
            self._req("POST", f"/repos/{repo}/issues/{number}/labels", json={"labels": list(add)})

    def get_issue(self, repo, number):
        return self._req("GET", f"/repos/{repo}/issues/{number}")

    # ---- repos / PRs -------------------------------------------------------
    def default_branch(self, repo):
        return self._req("GET", f"/repos/{repo}")["default_branch"]

    def create_pull(self, repo, head, base, title, body, draft=True):
        return self._req("POST", f"/repos/{repo}/pulls", json={"title": title, "head": head, "base": base, "body": body, "draft": draft})

    def list_pulls_for_head(self, repo, head):
        owner = repo.split("/")[0]
        return self._req("GET", f"/repos/{repo}/pulls", params={"head": f"{owner}:{head}", "state": "all"})
