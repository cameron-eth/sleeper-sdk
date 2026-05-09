#!/usr/bin/env bash
# Apply branch protection to `main` via the GitHub REST API.
#
# Required:  GH_TOKEN  — a personal access token with `repo` scope (admin on this repo)
# Optional:  REPO      — defaults to cameron-eth/sleeper-sdk
#
# Usage:
#   GH_TOKEN=ghp_xxx ./scripts/protect_main.sh
#
# What this enforces on `main`:
#   1. Pull requests required (no direct pushes)
#   2. The "Tests" workflow must pass on every PR before merge
#   3. Stale review approvals dismissed when new commits are pushed
#   4. Conversations must be resolved before merge
#   5. No force-pushes, no branch deletion
#
# This script is idempotent — re-running it just re-applies the same rules.

set -euo pipefail

REPO="${REPO:-cameron-eth/sleeper-sdk}"

if [[ -z "${GH_TOKEN:-}" ]]; then
  echo "ERROR: GH_TOKEN env var not set." >&2
  echo "Generate one at: https://github.com/settings/tokens (needs 'repo' scope)" >&2
  exit 1
fi

# The "Tests" check name is composed by GitHub Actions from the workflow's
# `jobs.<id>.name` field — for our matrix it produces one check per Python
# version. Both must pass.
read -r -d '' BODY <<'JSON' || true
{
  "required_status_checks": {
    "strict": true,
    "contexts": [
      "pytest (Python 3.11)",
      "pytest (Python 3.12)"
    ]
  },
  "enforce_admins": false,
  "required_pull_request_reviews": {
    "dismiss_stale_reviews": true,
    "require_code_owner_reviews": false,
    "required_approving_review_count": 0
  },
  "restrictions": null,
  "required_linear_history": false,
  "allow_force_pushes": false,
  "allow_deletions": false,
  "required_conversation_resolution": true
}
JSON

echo "Applying branch protection to ${REPO}@main..."
HTTP_STATUS=$(curl -sS -o /tmp/protect-response.json -w "%{http_code}" \
  -X PUT \
  -H "Authorization: Bearer ${GH_TOKEN}" \
  -H "Accept: application/vnd.github+json" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  "https://api.github.com/repos/${REPO}/branches/main/protection" \
  -d "${BODY}")

if [[ "${HTTP_STATUS}" -ge 200 && "${HTTP_STATUS}" -lt 300 ]]; then
  echo "✅ Branch protection applied (HTTP ${HTTP_STATUS})"
  echo "Verify at: https://github.com/${REPO}/settings/branches"
else
  echo "❌ Failed (HTTP ${HTTP_STATUS}). Response:" >&2
  cat /tmp/protect-response.json >&2
  exit 1
fi
