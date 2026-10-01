#!/bin/bash
# Deploy the current server/ code to the Gero-Trace environment.   Usage: bash deploy/eb-deploy.sh [env-name]
set -euo pipefail
cd "$(dirname "$0")/.."
ENV_NAME="${1:-${ENV_NAME:-Gero-Trace}}"
PY="${PY:-$( [ -x .venv/bin/python ] && echo .venv/bin/python || echo python3 )}"
"$PY" -m pytest -q tests
VERSION=$("$PY" -c 'import gero_trace;print(gero_trace.__version__)')
LABEL="v$VERSION-$(date +%Y%m%d-%H%M)"
eb deploy "$ENV_NAME" --label "$LABEL" --timeout 20
aws elasticbeanstalk describe-environments --environment-names "$ENV_NAME" --query 'Environments[0].[Status,Health,VersionLabel]' --output text
echo "deployed $LABEL"
