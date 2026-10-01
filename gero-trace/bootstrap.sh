#!/bin/bash
# Stand up Gero Trace end to end from a machine that has the FundingMetrics tooling
# (aws + eb CLIs with account credentials, sf CLI authenticated to the org, gh CLI signed in).
#
#   ADMIN_PASSWORD='…' ANTHROPIC_API_KEY=sk-ant-… SF_ORG=<alias of prod or sandbox> bash gero-trace/bootstrap.sh
#
# Steps (each can be re-run; pass STEPS="github aws webhooks salesforce" to pick):
#   github      create the issues repo + labels (GITHUB_ISSUES_REPO, default FundingMetrics/gero-trace)
#   aws         Elastic Beanstalk app + env + RDS + HTTPS + DNS (server/deploy/eb-create.sh), read-only IAM policy on the instance role
#   webhooks    GitHub webhooks (Issues + Pull requests) on the issues repo and every enabled repo in systems.yaml
#   salesforce  deploy the SFDX project with its Apex tests, assign the permission set to you
#   finish      print the two manual steps (External Credential ApiKey, Connected App for read-only org access)
#
# Optional env: GITHUB_TOKEN (PAT for the server; otherwise set GITHUB_APP_* later with eb setenv), DOMAIN (trace.geroai.fund),
#               ENV_NAME (Gero-Trace), REGION (us-east-1), EB_INSTANCE_ROLE (aws-elasticbeanstalk-ec2-role), SKIP_DNS=1
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
STEPS="${STEPS:-github aws webhooks salesforce finish}"
GITHUB_ISSUES_REPO="${GITHUB_ISSUES_REPO:-FundingMetrics/gero-trace}"
DOMAIN="${DOMAIN:-trace.geroai.fund}"
ENV_NAME="${ENV_NAME:-Gero-Trace}"
REGION="${REGION:-us-east-1}"
EB_INSTANCE_ROLE="${EB_INSTANCE_ROLE:-aws-elasticbeanstalk-ec2-role}"
export GITHUB_ISSUES_REPO DOMAIN ENV_NAME REGION

# Generated once here so the same values reach EB, GitHub and (by hand) Salesforce.
export SALESFORCE_API_KEY="${SALESFORCE_API_KEY:-$(python3 -c 'import secrets;print(secrets.token_urlsafe(36))')}"
export GITHUB_WEBHOOK_SECRET="${GITHUB_WEBHOOK_SECRET:-$(python3 -c 'import secrets;print(secrets.token_urlsafe(32))')}"
STATE="$HERE/.bootstrap-state"
mkdir -p "$STATE"; chmod 700 "$STATE"
echo "SALESFORCE_API_KEY=$SALESFORCE_API_KEY" > "$STATE/secrets.env"
echo "GITHUB_WEBHOOK_SECRET=$GITHUB_WEBHOOK_SECRET" >> "$STATE/secrets.env"
chmod 600 "$STATE/secrets.env"

need() { command -v "$1" >/dev/null 2>&1 || { echo "missing: $1 ($2)" >&2; exit 1; }; }
has() { [[ " $STEPS " == *" $1 "* ]]; }

echo "== Gero Trace bootstrap  (steps: $STEPS)"
echo "   generated secrets saved to $STATE/secrets.env (keep private; delete when done)"

# ---------------------------------------------------------------- github
if has github; then
  need gh "https://cli.github.com"
  echo "== GitHub: issues repo $GITHUB_ISSUES_REPO"
  if ! gh repo view "$GITHUB_ISSUES_REPO" >/dev/null 2>&1; then
    gh repo create "$GITHUB_ISSUES_REPO" --private --description "Gero Trace: investigations reported from Salesforce, and Claude's fixes" >/dev/null
    echo "   created"
  else
    echo "   exists"
  fi
  while IFS='|' read -r name color desc; do
    gh label create "$name" --repo "$GITHUB_ISSUES_REPO" --color "$color" --description "$desc" --force >/dev/null
  done <<'LABELS'
claude-report|1d76db|Filed from Salesforce through Gero Trace
needs-approval|fbca04|Waiting for engineering to decide whether Claude should fix it
approved-for-fix|0e8a16|Claude may implement the proposed fix and open a draft PR
wont-fix|cfd3d7|No code change for this report
LABELS
  echo "   labels ready"
fi

# ---------------------------------------------------------------- aws
if has aws; then
  need aws "pip install awscli"; need eb "pip install awsebcli"
  : "${ADMIN_PASSWORD:?set ADMIN_PASSWORD}"
  echo "== AWS: Elastic Beanstalk $ENV_NAME in $REGION"
  if aws elasticbeanstalk describe-environments --region "$REGION" --environment-names "$ENV_NAME" --query 'Environments[?Status!=`Terminated`].Status' --output text 2>/dev/null | grep -q .; then
    echo "   environment exists; deploying current code instead"
    (cd "$HERE/server" && bash deploy/eb-deploy.sh "$ENV_NAME")
    (cd "$HERE/server" && eb setenv "SALESFORCE_API_KEY=$SALESFORCE_API_KEY" "GITHUB_WEBHOOK_SECRET=$GITHUB_WEBHOOK_SECRET" "GITHUB_ISSUES_REPO=$GITHUB_ISSUES_REPO" --timeout 20 >/dev/null)
  else
    (cd "$HERE/server" && bash deploy/eb-create.sh)
  fi
  echo "== AWS: read-only policy on instance role $EB_INSTANCE_ROLE"
  aws iam put-role-policy --role-name "$EB_INSTANCE_ROLE" --policy-name GeroTraceReadOnly \
    --policy-document "file://$HERE/server/deploy/aws-readonly-policy.json"
  echo "   attached"
fi

# ---------------------------------------------------------------- webhooks
if has webhooks; then
  need gh "https://cli.github.com"; need python3 python
  echo "== GitHub webhooks -> https://$DOMAIN/api/webhooks/github"
  REPOS=$(python3 - "$HERE/server/config/systems.yaml" <<'PY'
import sys, yaml
d = yaml.safe_load(open(sys.argv[1])) or {}
print("\n".join(r["name"] for r in d.get("repos", []) if r.get("enabled", True) and r.get("name")))
PY
)
  for repo in $GITHUB_ISSUES_REPO $REPOS; do
    existing=$(gh api "repos/$repo/hooks" --jq ".[] | select(.config.url==\"https://$DOMAIN/api/webhooks/github\") | .id" 2>/dev/null || true)
    if [ -n "$existing" ]; then
      gh api -X PATCH "repos/$repo/hooks/$existing" -f "config[url]=https://$DOMAIN/api/webhooks/github" -f "config[content_type]=json" -f "config[secret]=$GITHUB_WEBHOOK_SECRET" -f 'events[]=issues' -f 'events[]=pull_request' >/dev/null && echo "   updated $repo"
    else
      gh api -X POST "repos/$repo/hooks" -f name=web -F active=true -f "config[url]=https://$DOMAIN/api/webhooks/github" -f "config[content_type]=json" -f "config[secret]=$GITHUB_WEBHOOK_SECRET" -f 'events[]=issues' -f 'events[]=pull_request' >/dev/null && echo "   created $repo" || echo "   could not add a webhook on $repo (admin rights needed); add it by hand"
    fi
  done
fi

# ---------------------------------------------------------------- salesforce
if has salesforce; then
  need sf "npm install -g @salesforce/cli"
  : "${SF_ORG:?set SF_ORG to the sf alias of the target org (prod or sandbox)}"
  echo "== Salesforce: deploying to $SF_ORG"
  if [ "$DOMAIN" != "trace.geroai.fund" ]; then
    sed -i.bak "s#https://trace.geroai.fund#https://$DOMAIN#" "$HERE/salesforce/force-app/main/default/namedCredentials/Gero_Trace.namedCredential-meta.xml" && rm -f "$HERE/salesforce/force-app/main/default/namedCredentials/"*.bak
  fi
  (cd "$HERE/salesforce" && sf project deploy start --target-org "$SF_ORG" --test-level RunSpecifiedTests --tests ClaudeTraceControllerTest --wait 30)
  sf org assign permset --name Claude_Trace_User --target-org "$SF_ORG" >/dev/null && echo "   permission set assigned to you"
fi

# ---------------------------------------------------------------- finish
if has finish; then
cat <<MSG

== Two things only a person can do in Salesforce Setup ($SF_ORG)

1. External Credential API key
   Setup > Named Credentials > External Credentials > Gero Trace > Principals > "Service" > Edit
   add an Authentication Parameter   Name: ApiKey   Value: $SALESFORCE_API_KEY

2. Read-only org access for the investigator (so Claude can run SOQL and read logs)
   a. Create an integration user, e.g. claude.trace@fundingmetrics.com, with a read-only profile/permission set:
      Read on the objects people ask about, View All Data (field history), View Setup and Configuration, API Enabled.
   b. Setup > App Manager > New Connected App: enable OAuth, "Use digital signatures" with a certificate
      (openssl req -x509 -newkey rsa:2048 -nodes -keyout server.key -out server.crt -days 3650 -subj /CN=gero-trace),
      scopes api + refresh_token + web; Manage > Edit Policies > Permitted Users = Admin approved users are pre-authorized;
      add the integration user's profile.
   c. cd $HERE/server && eb setenv SF_USERNAME=claude.trace@fundingmetrics.com SF_CLIENT_ID=<consumer key> \\
        SF_JWT_KEY="\$(awk 'BEGIN{ORS="\\\\n"}{print}' server.key)"

Then add the "Ask Claude (Why did this happen?)" component to the Opportunity / Account / Lead / Case record pages in
Lightning App Builder and ask the first question. The admin page is https://$DOMAIN (ADMIN_PASSWORD).
Worker logs: cd $HERE/server && eb logs $ENV_NAME
MSG
fi
