#!/bin/bash
# Local development with SQLite: web on http://127.0.0.1:5056, worker in the same terminal.
# Needs ANTHROPIC_API_KEY for real investigations; without GitHub/Salesforce config the worker
# still runs (no repos, no org) which is enough to try the flow end to end.
set -euo pipefail
cd "$(dirname "$0")/.."
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q -r requirements.txt
export ADMIN_PASSWORD="${ADMIN_PASSWORD:-local-admin-pw}"
export SALESFORCE_API_KEY="${SALESFORCE_API_KEY:-local-api-key}"
export SECRET_KEY="${SECRET_KEY:-dev}"
export PUBLIC_URL="${PUBLIC_URL:-http://127.0.0.1:5056}"
export WORKSPACE_DIR="${WORKSPACE_DIR:-$PWD/workspace}"
echo "admin page:  $PUBLIC_URL   password: $ADMIN_PASSWORD"
echo "api key:     $SALESFORCE_API_KEY"
.venv/bin/python -m gero_trace.worker &
WORKER=$!
trap 'kill $WORKER 2>/dev/null || true' EXIT
PORT=5056 .venv/bin/python application.py
