"""The systems map (config/systems.yaml) and the on-disk workspace the investigator reads:
    <workspace>/repos/<path>      shallow clones of the FundingMetrics repos, refreshed periodically
    <workspace>/salesforce        retrieved Salesforce metadata (Apex, Flows, objects, validation rules…)
    <workspace>/SYSTEMS.md        a rendered copy of the map so Claude can re-read it with the Read tool
"""
import logging
import os
import subprocess
import time

import yaml

log = logging.getLogger(__name__)


def load_systems(path):
    if not os.path.exists(path):
        return {"repos": [], "salesforce": {}, "aws": {}}
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    data.setdefault("repos", [])
    data.setdefault("salesforce", {})
    data.setdefault("aws", {})
    return data


def enabled_repos(systems):
    return [r for r in systems.get("repos", []) if r.get("enabled", True) and r.get("name")]


def render_systems_md(systems, workspace):
    sf = systems.get("salesforce", {})
    aws = systems.get("aws", {})
    lines = ["# Systems map", "", "Everything below is read-only. Paths are relative to the workspace root.", ""]
    lines += ["## Salesforce", "", (sf.get("description") or "").strip(), "",
              f"- Retrieved metadata: `{sf.get('metadata_path', 'salesforce')}/` (Apex classes and triggers, Flows, objects, fields, validation rules, layouts…)",
              f"- CLI org alias: `{sf.get('org_alias', 'trace')}` → `sf data query --target-org {sf.get('org_alias', 'trace')} -q \"SOQL\" --json`", ""]
    lines += ["## Repositories", "", "| Repo | Path | Runs on | What it does |", "|---|---|---|---|"]
    for r in enabled_repos(systems):
        present = os.path.isdir(os.path.join(workspace, "repos", r.get("path") or r["name"].split("/")[-1]))
        lines.append(f"| {r['name']} | `repos/{r.get('path') or r['name'].split('/')[-1]}/`{'' if present else ' (not synced yet)'} | {r.get('runtime', '?')} | {(r.get('description') or '').strip()} |")
    lines += ["", "## AWS", "", (aws.get("description") or "").strip(), "",
              f"- Account `{aws.get('account', '?')}`, region `{aws.get('region', 'us-east-1')}`"]
    logs = [(r["name"], g) for r in enabled_repos(systems) for g in (r.get("logs") or [])]
    if logs:
        lines += ["- Known log groups:"] + [f"  - {n}: `{g}`" for n, g in logs]
    cl = systems.get("change_log")
    if cl:
        lines += ["", "## Change log", "", f"{cl.get('url', '')} — {(cl.get('description') or '').strip()}"]
    return "\n".join(lines) + "\n"


class Workspace:
    def __init__(self, root, systems, github=None):
        self.root = root
        self.systems = systems
        self.github = github
        self.repos_dir = os.path.join(root, "repos")
        os.makedirs(self.repos_dir, exist_ok=True)
        self._last_repo_sync = 0
        self._last_sf_sync = 0

    def write_systems_md(self):
        with open(os.path.join(self.root, "SYSTEMS.md"), "w", encoding="utf-8") as f:
            f.write(render_systems_md(self.systems, self.root))

    # ---- repos -------------------------------------------------------------
    def repo_dir(self, repo):
        return os.path.join(self.repos_dir, repo.get("path") or repo["name"].split("/")[-1])

    def sync_repos(self, force=False, max_age_minutes=30):
        if not force and time.time() - self._last_repo_sync < max_age_minutes * 60:
            return
        for repo in enabled_repos(self.systems):
            try:
                self.sync_repo(repo)
            except Exception as e:  # noqa: BLE001
                log.warning("repo sync failed for %s: %s", repo["name"], _scrub(str(e)))
        self._last_repo_sync = time.time()
        self.write_systems_md()

    def sync_repo(self, repo):
        dest = self.repo_dir(repo)
        url = self.github.clone_url(repo["name"]) if self.github else f"https://github.com/{repo['name']}.git"
        if os.path.isdir(os.path.join(dest, ".git")):
            run(["git", "-C", dest, "remote", "set-url", "origin", url], timeout=30)
            run(["git", "-C", dest, "fetch", "--depth", "200", "--prune", "origin"], timeout=600)
            head = run(["git", "-C", dest, "rev-parse", "--abbrev-ref", "origin/HEAD"], timeout=30).stdout.strip() or "origin/main"
            run(["git", "-C", dest, "reset", "--hard", head], timeout=120)
        else:
            run(["git", "clone", "--depth", "200", url, dest], timeout=900)
        # Never leave credentials in the on-disk config.
        run(["git", "-C", dest, "remote", "set-url", "origin", f"https://github.com/{repo['name']}.git"], timeout=30)
        log.info("synced %s", repo["name"])

    # ---- salesforce ----------------------------------------------------------
    def sync_salesforce(self, sf, force=False, max_age_minutes=180):
        if not sf or not sf.configured:
            return
        if not force and time.time() - self._last_sf_sync < max_age_minutes * 60:
            return
        dest = os.path.join(self.root, self.systems.get("salesforce", {}).get("metadata_path", "salesforce"))
        try:
            sf.retrieve_metadata(dest)
            self._last_sf_sync = time.time()
        except Exception as e:  # noqa: BLE001
            log.warning("salesforce metadata retrieve failed: %s", _scrub(str(e)))


def run(cmd, timeout=120, cwd=None, env=None):
    r = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(f"{_scrub(' '.join(cmd[:3]))} failed ({r.returncode}): {_scrub(r.stderr.strip()[-800:])}")
    return r


def _scrub(s):
    """Remove tokens embedded in clone URLs from anything we log."""
    import re
    return re.sub(r"https://[^/\s:]+:[^@\s]+@", "https://***@", s or "")
