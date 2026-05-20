#!/bin/bash
set -euo pipefail

cd "$(dirname "$0")"

echo "======================================"
echo "Starting Hephaestus API Pipeline: $(date)"
echo "======================================"

LIMIT_ARG=()
if [ "${1:-}" != "" ]; then
  LIMIT_ARG=(--limit "$1")
  echo "DEV MODE ACTIVE: Limiting processing to $1 companies."
fi

echo "Ensuring core companies are tracked..."
python3 backend/seed_db.py "${LIMIT_ARG[@]}"

echo "Updating live financial metrics..."
python3 backend/update_metrics.py "${LIMIT_ARG[@]}"

echo "Auditing supply-chain relationship quality..."
python3 backend/audit_data_quality.py --fail-on-warnings

echo "Exporting database to docs/dashboard_data.json..."
python3 backend/export.py

echo "Checking for dashboard changes..."
git add docs/dashboard_data.json
if ! git diff --cached --quiet; then
  git commit -m "Automated dashboard update: $(date +'%Y-%m-%d')"
  git push origin main
else
  echo "No changes to commit."
fi

echo "Pipeline complete."
echo "======================================"
