#!/bin/bash
set -euo pipefail

cd "$(dirname "$0")/.."

LIMIT_ARG=()
if [ "${1:-}" != "" ]; then
  LIMIT_ARG=(--limit "$1")
  echo "DEV MODE ACTIVE: Limiting rebuild to $1 companies."
fi

echo "Rebuilding local SQLite database..."
rm -f backend/supply_chain.db

python3 backend/database.py
python3 backend/seed_db.py "${LIMIT_ARG[@]}"
python3 backend/update_metrics.py "${LIMIT_ARG[@]}"
python3 backend/seed_edges.py
python3 backend/audit_data_quality.py --fail-on-warnings
python3 backend/export.py

echo "Rebuild complete. Dashboard data refreshed at docs/dashboard_data.json"
