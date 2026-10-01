#!/bin/bash
# Installs the command-line tools the investigator runs through Claude's Bash tool:
# git + ripgrep (reading repos), Node 20 + the Salesforce CLI (read-only org access),
# and jq. The AWS CLI is already on Amazon Linux 2023. The Claude Code CLI itself is
# bundled inside the claude-agent-sdk Python package, so it needs nothing extra.
set -euo pipefail
dnf install -y -q git jq ripgrep >/dev/null 2>&1 || dnf install -y -q git jq >/dev/null
if ! command -v node >/dev/null 2>&1; then
  curl -fsSL https://rpm.nodesource.com/setup_20.x | bash - >/dev/null
  dnf install -y -q nodejs >/dev/null
fi
if ! command -v sf >/dev/null 2>&1; then
  npm install --global @salesforce/cli >/dev/null 2>&1
fi
mkdir -p /var/app/workspace && chown webapp:webapp /var/app/workspace
