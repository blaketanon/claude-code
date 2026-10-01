#!/bin/bash
# One-time: create the Gero_Trace Elastic Beanstalk application + environment with an attached Postgres RDS,
# the same way Gero DevLog was created. Run from server/. Requires: eb CLI, aws CLI with FundingMetrics credentials.
#
#   ADMIN_PASSWORD='...' ANTHROPIC_API_KEY=sk-ant-... GITHUB_TOKEN=ghp_... bash deploy/eb-create.sh
#
# Optional env: REGION (us-east-1)  ENV_NAME (Gero-Trace)  DOMAIN (trace.geroai.fund)  CERT_ARN  HOSTED_ZONE_ID
#               INSTANCE (t3.medium)  DB_INSTANCE (db.t4g.micro)  SKIP_DNS=1
#               GITHUB_APP_ID / GITHUB_APP_INSTALLATION_ID / GITHUB_APP_PRIVATE_KEY  (instead of GITHUB_TOKEN)
#               SF_USERNAME / SF_CLIENT_ID / SF_JWT_KEY  (Salesforce read-only access; can be set later with eb setenv)
set -euo pipefail
cd "$(dirname "$0")/.."

APP_NAME="Gero_Trace"
ENV_NAME="${ENV_NAME:-Gero-Trace}"
REGION="${REGION:-us-east-1}"
PLATFORM="${PLATFORM:-Python 3.12 running on 64bit Amazon Linux 2023}"
INSTANCE="${INSTANCE:-t3.medium}"          # the worker clones repos and runs Claude; give it some room
DB_INSTANCE="${DB_INSTANCE:-db.t4g.micro}"
DOMAIN="${DOMAIN:-trace.geroai.fund}"
CERT_ARN="${CERT_ARN-arn:aws:acm:us-east-1:807547241110:certificate/7d5e4812-e3d4-49dd-a66a-956135b788e7}"   # *.geroai.fund
HOSTED_ZONE_ID="${HOSTED_ZONE_ID:-Z08221342D5NF1UQ15FQO}"   # geroai.fund

: "${ADMIN_PASSWORD:?set ADMIN_PASSWORD (admin page sign-in)}"
SECRET_KEY="${SECRET_KEY:-$(python3 -c 'import secrets;print(secrets.token_hex(32))')}"
SALESFORCE_API_KEY="${SALESFORCE_API_KEY:-$(python3 -c 'import secrets;print(secrets.token_urlsafe(36))')}"
GITHUB_WEBHOOK_SECRET="${GITHUB_WEBHOOK_SECRET:-$(python3 -c 'import secrets;print(secrets.token_urlsafe(32))')}"
DB_USER="${DB_USER:-trace}"
DB_PASSWORD="${DB_PASSWORD:-$(python3 -c 'import secrets,string;a=string.ascii_letters+string.digits;print("".join(secrets.choice(a) for _ in range(28)))')}"

echo "== eb init ($APP_NAME, $PLATFORM, $REGION)"
eb init "$APP_NAME" --platform "$PLATFORM" --region "$REGION" >/dev/null

ENVVARS="SECRET_KEY=$SECRET_KEY,ADMIN_PASSWORD=$ADMIN_PASSWORD,SALESFORCE_API_KEY=$SALESFORCE_API_KEY,GITHUB_WEBHOOK_SECRET=$GITHUB_WEBHOOK_SECRET,HTTPS_ONLY=1,PUBLIC_URL=https://$DOMAIN"
for v in ANTHROPIC_API_KEY ANTHROPIC_MODEL GITHUB_TOKEN GITHUB_APP_ID GITHUB_APP_INSTALLATION_ID GITHUB_ISSUES_REPO GITHUB_DEFAULT_ASSIGNEE SF_USERNAME SF_CLIENT_ID SF_LOGIN_URL CLAUDE_CODE_USE_BEDROCK; do
  [ -n "${!v:-}" ] && ENVVARS="$ENVVARS,$v=${!v}"
done
# PEM keys contain commas/newlines that --envvars cannot carry; they are set afterwards with eb setenv.

echo "== eb create $ENV_NAME (10-15 minutes because of the RDS instance)"
eb create "$ENV_NAME" \
  --instance_type "$INSTANCE" \
  --elb-type application \
  --min-instances 1 --max-instances 1 \
  --database --database.engine postgres --database.version 16 \
  --database.instance "$DB_INSTANCE" --database.size 20 \
  --database.username "$DB_USER" --database.password "$DB_PASSWORD" \
  --envvars "$ENVVARS" \
  --tags "Project=Gero_Trace,Team=Engineering" \
  --timeout 30

wait_ready() {
  for _ in $(seq 1 90); do
    [ "$(aws elasticbeanstalk describe-environments --region "$REGION" --environment-names "$ENV_NAME" --query 'Environments[0].Status' --output text)" = "Ready" ] && return 0
    sleep 10
  done
  echo "environment did not become Ready in time" >&2; return 1
}

echo "== keep the database if the environment is ever terminated"
wait_ready
aws elasticbeanstalk update-environment --region "$REGION" --environment-name "$ENV_NAME" \
  --option-settings Namespace=aws:rds:dbinstance,OptionName=DBDeletionPolicy,Value=Snapshot >/dev/null

if [ -n "${GITHUB_APP_PRIVATE_KEY:-}" ] || [ -n "${SF_JWT_KEY:-}" ]; then
  echo "== PEM keys"
  sleep 15; wait_ready
  ARGS=()
  [ -n "${GITHUB_APP_PRIVATE_KEY:-}" ] && ARGS+=("GITHUB_APP_PRIVATE_KEY=$(printf '%s' "$GITHUB_APP_PRIVATE_KEY" | awk 'BEGIN{ORS="\\n"}{print}')")
  [ -n "${SF_JWT_KEY:-}" ] && ARGS+=("SF_JWT_KEY=$(printf '%s' "$SF_JWT_KEY" | awk 'BEGIN{ORS="\\n"}{print}')")
  eb setenv "${ARGS[@]}" --timeout 20
fi

if [ -n "$CERT_ARN" ]; then
  echo "== HTTPS listener"
  sleep 15; wait_ready
  aws elasticbeanstalk update-environment --region "$REGION" --environment-name "$ENV_NAME" --option-settings \
    "Namespace=aws:elbv2:listener:443,OptionName=ListenerEnabled,Value=true" \
    "Namespace=aws:elbv2:listener:443,OptionName=Protocol,Value=HTTPS" \
    "Namespace=aws:elbv2:listener:443,OptionName=SSLCertificateArns,Value=$CERT_ARN" \
    "Namespace=aws:elbv2:listener:443,OptionName=SSLPolicy,Value=ELBSecurityPolicy-TLS13-1-2-2021-06" >/dev/null
fi
sleep 15; wait_ready

CNAME=$(aws elasticbeanstalk describe-environments --region "$REGION" --environment-names "$ENV_NAME" --query 'Environments[0].CNAME' --output text)
if [ -n "$HOSTED_ZONE_ID" ] && [ -z "${SKIP_DNS:-}" ]; then
  echo "== DNS: $DOMAIN -> $CNAME"
  aws route53 change-resource-record-sets --hosted-zone-id "$HOSTED_ZONE_ID" --change-batch "{\"Changes\":[{\"Action\":\"UPSERT\",\"ResourceRecordSet\":{\"Name\":\"$DOMAIN\",\"Type\":\"CNAME\",\"TTL\":300,\"ResourceRecords\":[{\"Value\":\"$CNAME\"}]}}]}" >/dev/null
fi

cat <<MSG

Done.
  Admin page:        https://$DOMAIN            (ADMIN_PASSWORD)
  Salesforce key:    $SALESFORCE_API_KEY
                     -> Setup > Named Credentials > External Credentials > Gero Trace > principal "Service" > parameter ApiKey
  GitHub webhook:    https://$DOMAIN/api/webhooks/github   secret: $GITHUB_WEBHOOK_SECRET   events: Issues, Pull requests
  Logs:              eb logs $ENV_NAME      (worker output is in /var/log/worker.stdout.log)
  Instance role:     attach a read-only policy (CloudWatch Logs read, EB/Lambda/RDS describe, CloudTrail lookup) to the
                     environment's EC2 instance profile so the investigator can read AWS. See README "AWS access".
MSG
