#!/usr/bin/env bash
# Sets the four GitHub Actions secrets that .github/workflows/ci.yml needs.
#
# Run this AFTER `gh auth login`.
#
# Every value is piped straight into `gh secret set` — nothing is echoed, written to
# disk, or passed as a command-line argument (argv is visible to other processes).
# Safe to run while recording.
#
#   bash scripts/setup-ci-secrets.sh
#
set -euo pipefail

GH="gh"
command -v "$GH" >/dev/null 2>&1 || GH="/c/Program Files/GitHub CLI/gh.exe"
[ -x "$GH" ] || command -v "$GH" >/dev/null 2>&1 || {
  echo "error: gh not found. Install it, or set GH=/path/to/gh.exe" >&2
  exit 1
}

PY=".venv/Scripts/python.exe"
[ -x "$PY" ] || PY="python3"
command -v "$PY" >/dev/null 2>&1 || PY="python"

"$GH" auth status >/dev/null 2>&1 || {
  echo "error: gh is not authenticated. Run: gh auth login" >&2
  exit 1
}

[ -f .vercel/project.json ] || {
  echo "error: .vercel/project.json missing. Run: vercel link" >&2
  exit 1
}

echo "Setting VERCEL_ORG_ID and VERCEL_PROJECT_ID from .vercel/project.json ..."
"$PY" -c "import json;print(json.load(open('.vercel/project.json'))['orgId'],end='')" \
  | "$GH" secret set VERCEL_ORG_ID
"$PY" -c "import json;print(json.load(open('.vercel/project.json'))['projectId'],end='')" \
  | "$GH" secret set VERCEL_PROJECT_ID

# The production alias the deploy job polls for /health. Only prompt when there is a
# real terminal — otherwise `read` blocks forever with no visible prompt.
CLINIC_URL_DEFAULT="https://clinicops-sepia.vercel.app"
clinic_url=""
if [ -t 0 ]; then
  read -rp "CLINIC_URL [$CLINIC_URL_DEFAULT]: " clinic_url
fi
printf '%s' "${clinic_url:-$CLINIC_URL_DEFAULT}" | "$GH" secret set CLINIC_URL
echo "  CLINIC_URL set."

# VERCEL_TOKEN can only come from you: https://vercel.com/account/tokens
if [ -t 0 ]; then
  echo
  read -rsp "Paste VERCEL_TOKEN (input hidden, Enter when done): " vercel_token
  echo
  if [ -n "$vercel_token" ]; then
    printf '%s' "$vercel_token" | "$GH" secret set VERCEL_TOKEN
    unset vercel_token
    echo "  VERCEL_TOKEN set."
  else
    echo "  no token entered, VERCEL_TOKEN left unchanged." >&2
  fi
else
  echo
  echo "No terminal available, so VERCEL_TOKEN was not prompted for. Set it with:"
  echo "  gh secret set VERCEL_TOKEN      # paste the token, Enter, then Ctrl+D"
fi

echo
echo "Secrets now on the repo:"
"$GH" secret list
