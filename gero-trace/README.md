# Gero Trace — "Ask Claude why this happened" for Salesforce

Lets any Salesforce user ask, in plain words, why something happened ("why did this deal get declined this
morning?", "who changed the offer amount and why?"). Claude investigates the way an engineer would, across the
Salesforce org, the FundingMetrics code repositories and AWS logs, writes a plain-language answer back into
Salesforce, and lets the user **report it to engineering** with one click. The report becomes a GitHub issue with
a full technical write-up; when an engineer adds the `approved-for-fix` label, Claude implements the fix and opens
a draft pull request.

```
 Salesforce                         AWS (Elastic Beanstalk, same pattern as Gero DevLog)                  GitHub
 ──────────                         ───────────────────────────────────────────────────                   ──────
 Ask Claude LWC ─▶ Apex ─▶ Named  ─▶  web (Flask)  ──▶ Postgres (RDS) ◀── worker (Claude Agent SDK)
 on any record     Credential          /api/…           investigations          │ reads: repos/ (git clones),
      ▲                                  │                                       │        salesforce/ (sf retrieve),
      │ polls every 5s                   │                                       │        sf data query, aws logs …
      └──────────────────────────────────┘                                       ▼
 "Report to engineering" ─────────────▶ POST /report ────────────────────────▶ issue in FundingMetrics/gero-trace
                                                                                   │ label approved-for-fix
 Admin page (engineering) ◀───────────▶ /api/admin/…  ◀── webhook ─────────────────┘
      approve / decline / retry                          worker: clone repo → Claude fixes → draft PR → comment on issue
```

## What is in this folder

| Path | What |
|---|---|
| [`salesforce/`](salesforce/) | SFDX project: `Claude_Investigation__c` object, `ClaudeTraceController`/`ClaudeTraceService` Apex (with tests), the `claudeAsk` Lightning web component, Named + External Credential, `Claude_Trace_User` permission set, tab. |
| [`server/`](server/) | Flask web app + worker, Python 3.12, deployed to Elastic Beanstalk like Gero DevLog. The worker runs the [Claude Agent SDK](https://code.claude.com/docs/en/agent-sdk) (the Claude Code harness) in a read-only workspace. |
| [`server/config/systems.yaml`](server/config/systems.yaml) | **The systems map.** Which repos Claude may read, what each one does, where it runs, which log groups matter. Edit this; it is rendered into Claude's instructions. |
| [`server/gero_trace/guard.py`](server/gero_trace/guard.py) | The read-only shell guard: every Bash command Claude runs during an investigation must pass it. |
| [`server/gero_trace/prompts.py`](server/gero_trace/prompts.py) | Investigator and fixer instructions, and the JSON schemas their structured results must match. |
| [`server/deploy/`](server/deploy/) | `run-local.sh`, `eb-create.sh`, `eb-deploy.sh`, and the read-only IAM policy for the instance role. |
| [`github/claude-fix-on-label.yml`](github/claude-fix-on-label.yml) | Optional per-repo GitHub Actions variant of the fixer. |

## How a question flows

1. **Ask.** The user types a question in the *Ask Claude* component on a record page (or the home page / utility bar).
   Apex sends it to `POST /api/investigations` with the record id, object, a small snapshot of common fields, and who asked.
   A `Claude_Investigation__c` record is created (`INV-00042`) so the question and answer live in Salesforce too.
2. **Investigate.** The worker claims the job and runs Claude with the investigator instructions in a workspace containing
   shallow clones of every enabled repo, the org's retrieved metadata (Apex, Flows, validation rules, objects…), `SYSTEMS.md`,
   and read-only CLI access: `sf data query` / `sf apex get log` against the org, `aws logs filter-log-events` and friends,
   `git log`/`blame`/`show`, `rg`. Each Bash command passes through the guard; anything that writes is refused and Claude is told why.
   Every tool call and intermediate note is stored as an event (visible on the admin page).
3. **Answer.** Claude must finish with a structured result: a plain-language `answer_markdown` for the user, plus
   `what_happened`, `root_cause`, `evidence`, `affected_systems`, `needs_fix`, `proposed_fix` (repo, files, steps, risk, tests, rollback)
   for engineers. The server renders the engineering report deterministically from those fields. The LWC, polling every
   5 seconds, shows the answer with a confidence badge; the record is updated too.
4. **Report.** If the user clicks *Report this to engineering* (optionally adding a comment), `POST /api/investigations/{id}/report`
   files a GitHub issue in `GITHUB_ISSUES_REPO` with the full report, labels `claude-report`, `needs-approval`, and assigns
   `GITHUB_DEFAULT_ASSIGNEE`. The Salesforce record gets the issue link.
5. **Approve.** Add the `approved-for-fix` label on the issue (or click *Approve* on the admin page). The webhook marks the fix approved.
6. **Fix.** The worker clones the target repo (from `proposed_fix.repo`, validated against `systems.yaml`), creates a branch
   `claude/fix-<issue>-<slug>`, runs Claude with the fixer instructions (edits allowed; push/deploy commands refused), then
   commits, pushes and opens a **draft pull request** itself, and comments on the issue. Nothing is deployed. Merging or
   closing the PR updates the status back to Salesforce (`Fix_Status__c`, `Fix_PR_URL__c`).

Budgets: `INVESTIGATION_BUDGET_USD` (default $8) and `INVESTIGATION_MAX_TURNS` (80) cap each investigation;
`FIX_BUDGET_USD` ($15) / `FIX_MAX_TURNS` (120) cap each fix. Costs are recorded per investigation.

## Setup

### 1. Server on AWS (≈ 20 minutes, mostly waiting for RDS)

```bash
cd server
ADMIN_PASSWORD='…' ANTHROPIC_API_KEY=sk-ant-… GITHUB_TOKEN=ghp_… bash deploy/eb-create.sh
```

This mirrors `claude-change-log/server/deploy/eb-create.sh`: application `Gero_Trace`, environment `Gero-Trace`, Postgres 16
on RDS (deletion policy Snapshot), HTTPS on the existing `*.geroai.fund` certificate and a `trace.geroai.fund` DNS record.
The script prints the generated `SALESFORCE_API_KEY` and `GITHUB_WEBHOOK_SECRET`; keep them for steps 2 and 3.
Later deployments: `bash deploy/eb-deploy.sh` (runs the tests first).

The `.platform/hooks/prebuild/01_install_tools.sh` hook installs git, ripgrep, jq, Node 20 and the Salesforce CLI on the
instance. The Claude Code CLI is bundled inside the `claude-agent-sdk` package. The Procfile runs two processes: `web`
(gunicorn) and `worker` (`python -m gero_trace.worker`). One `t3.medium` is enough to start; the worker runs one
investigation at a time (`WORKER_CONCURRENCY`).

**AWS access for the investigator.** Attach [`deploy/aws-readonly-policy.json`](server/deploy/aws-readonly-policy.json)
to the environment's EC2 instance profile (`aws-elasticbeanstalk-ec2-role` or a dedicated one). It grants CloudWatch Logs read,
`Describe*`/`List*`/`Get*` on EB, Lambda, ECS, RDS, EC2, CloudTrail `LookupEvents` and DynamoDB reads. Secrets Manager, SSM and IAM
are deliberately absent and the guard also refuses them.

**Claude access.** Either `ANTHROPIC_API_KEY`, or `CLAUDE_CODE_USE_BEDROCK=1` to use the AWS account's Bedrock access through the instance role.
Model defaults to `claude-opus-5-5` (`ANTHROPIC_MODEL`), effort `high` (`CLAUDE_EFFORT`).

### 2. GitHub

- **Credentials.** Preferred: create a GitHub App on the FundingMetrics org (permissions: Contents read/write, Issues read/write,
  Pull requests read/write, Metadata read), install it on the repos in `systems.yaml` plus the issues repo, and set
  `GITHUB_APP_ID`, `GITHUB_APP_INSTALLATION_ID`, `GITHUB_APP_PRIVATE_KEY` (`eb setenv`, PEM with `\n` for newlines).
  Simpler: a fine-grained PAT in `GITHUB_TOKEN` with the same scopes.
- **Issues repo.** `GITHUB_ISSUES_REPO` (default `FundingMetrics/gero-trace`; create it, and create the labels
  `claude-report`, `needs-approval`, `approved-for-fix`, `wont-fix`). Reports from every system land here so there is one queue.
- **Webhook** on the issues repo *and* on each repo Claude may fix: payload URL `https://trace.geroai.fund/api/webhooks/github`,
  content type JSON, secret = `GITHUB_WEBHOOK_SECRET`, events *Issues* and *Pull requests*. A GitHub App can carry the webhook instead.

### 3. Salesforce

```bash
cd salesforce
sf project deploy start --target-org <prod-or-sandbox-alias> --test-level RunSpecifiedTests --tests ClaudeTraceControllerTest
```

Then, in Setup:

1. **Named Credentials → External Credentials → Gero Trace → principal "Service"** → add an authentication parameter
   named `ApiKey` with the `SALESFORCE_API_KEY` value. (The named credential sends it as the `X-Api-Key` header.)
   If the server is not at `https://trace.geroai.fund`, change the URL on the *Gero Trace* named credential.
2. Assign the **Claude Trace User** permission set to the people who may ask questions (it includes the external credential principal access).
3. **Lightning App Builder** → add *Ask Claude (Why did this happen?)* to the Opportunity, Account, Lead and Case record pages
   (and the home page or utility bar for questions that are not about one record).

**Read-only org access for the investigator** (the server side of Salesforce):

1. Create an integration user (e.g. `claude.trace@fundingmetrics.com`) with a profile/permission set that has **Read** on the
   objects users ask about, *View All Data* if field history across records is needed, *View Setup and Configuration*
   (for `SetupAuditTrail`, Apex logs, metadata retrieve) and *API Enabled*. No create/edit/delete.
2. Create a Connected App with *Use digital signatures* and a certificate, enable OAuth scopes `api`, `refresh_token`,
   `web`; set *Admin approved users are pre-authorized* and add the integration user's profile.
3. `eb setenv SF_USERNAME=… SF_CLIENT_ID=<consumer key> SF_JWT_KEY="$(awk 'BEGIN{ORS="\\n"}{print}' server.key)"`.
   The worker logs in with `sf org login jwt` at start and retrieves the org's metadata into the workspace every
   `SF_METADATA_SYNC_MINUTES` (default 3 hours).

### 4. The systems map

Edit [`server/config/systems.yaml`](server/config/systems.yaml). Every enabled repo is cloned into the workspace and refreshed
every `REPO_SYNC_MINUTES`. The descriptions are what Claude reads to decide where to look, so say what each system does for
the business and how it touches Salesforce (which objects/fields it writes, which endpoints Salesforce calls). Add CloudWatch
log groups under `logs:` for anything that runs on AWS. It ships pre-filled with the active FundingMetrics repositories; the
descriptions are first guesses from the repo names and should be corrected.

## Running locally

```bash
cd server
cp .env.example .env    # set ANTHROPIC_API_KEY at least; GitHub/Salesforce are optional for a first try
set -a; source .env; set +a
bash deploy/run-local.sh          # web on http://127.0.0.1:5056 + worker; SQLite in ./instance
python -m pytest                  # 75 tests: API, webhook, admin, bash guard, rendering, fixer, worker
```

Submit a question without Salesforce:

```bash
curl -s -X POST http://127.0.0.1:5056/api/investigations -H "X-Api-Key: $SALESFORCE_API_KEY" -H 'Content-Type: application/json' \
  -d '{"question":"Why did the offer amount on Acme change yesterday afternoon?","record_object":"Opportunity","record_id":"006…","asked_by":{"name":"You"}}'
```

## Security model

- **Two Claude roles, two guards.** The investigator has no Edit/Write tools and a read-only Bash allow-list (`guard.py`):
  `git` read sub-commands only, `sf data query`/`sf apex get log`/describes only, `aws` `describe-*`/`get-*`/`list-*`/`filter-*`/
  `lookup-*` only (Secrets Manager, SSM and IAM refused), no redirections, no `$(…)`, no interpreters, no `curl`. The fixer may edit
  files in its clone and run tests, but `git push`, deploy tools and infrastructure-changing `aws`/`sf` commands are refused; the
  worker itself commits, pushes and opens the draft PR.
- **Credentials never reach Claude's shell.** Clone URLs with tokens are set only for the fetch and replaced afterwards; the
  Salesforce JWT key lives in a temp file for the duration of `sf org login`; the instructions tell Claude never to output secrets.
- **Record data, logs and code are data, not instructions** (stated in the prompt). Repository `CLAUDE.md`/settings are ignored
  during investigations (`setting_sources=[]`) and read only during fixes.
- **Humans approve every change.** Nothing is deployed; a fix is a draft PR plus an issue comment. Approval is a label added by
  someone with write access to the issues repo, or the admin page.
- **Auth.** Salesforce calls carry `X-Api-Key`; the admin page uses `ADMIN_PASSWORD` with a session cookie, CSRF token and login rate limit;
  the webhook verifies `X-Hub-Signature-256`. HTTPS is forced behind the load balancer (`HTTPS_ONLY=1`).

## API (for other callers)

| Endpoint | Auth | Notes |
|---|---|---|
| `POST /api/investigations` | API key | `{question, record_id?, record_object?, record_url?, record_context?, asked_by?}` → 202 |
| `GET /api/investigations/<id>` | API key | status `queued|running|answered|reported|failed`, `answer_markdown`, `answer_html`, `report_markdown`, `confidence`, `needs_fix`, `cost_usd`, `issue{url,number}`, `fix{status,pr_url}` |
| `POST /api/investigations/<id>/report` | API key | `{comment?, reported_by?}` → creates the issue; idempotent |
| `POST /api/webhooks/github` | HMAC | `issues` labeled/unlabeled/closed/reopened, `pull_request` closed |
| `GET /api/admin/investigations`, `GET …/<id>`, `POST …/<id>/approve|decline|retry`, `GET /api/admin/stats`, `GET /api/admin/systems` | admin session | used by the admin page |

## Operating notes

- Worker logs: `eb logs Gero-Trace` → `/var/log/worker.stdout.log`. Each investigation's events are also on the admin page.
- A failed investigation (budget, turns, crash) can be retried from the admin page; a failed fix too.
- If Claude keeps hitting the budget, raise `INVESTIGATION_BUDGET_USD` or improve `systems.yaml` so it searches less.
- Retire a repo by setting `enabled: false`; it is left on disk but no longer synced or listed.
- To let Claude read an application database, provide a read-only connection string in `READONLY_DATABASE_URLS`;
  `psql` is then allowed for `SELECT` statements only.
