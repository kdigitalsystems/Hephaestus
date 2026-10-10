#!/bin/bash
set -euo pipefail

cd "$(dirname "$0")/.."

LIMIT_ARG=()
if [ "${1:-}" != "" ]; then
  LIMIT_ARG=(--limit "$1")
  echo "DEV MODE ACTIVE: Limiting rebuild to $1 companies."
fi

# Review decisions made through review_edges.py live only in SQLite until they are
# exported. Persist them before the database is deleted so the rebuild can replay them.
# This database may be older than the tracked file (the nightly run's decisions arrive
# with a pull), so the export only adds or updates and never drops or overwrites a
# decision the file holds a newer verdict on.
if python3 backend/db_health.py >/dev/null 2>&1; then
  echo "Persisting current review decisions before rebuild..."
  python3 backend/edge_review_decisions.py export --only-newer
fi

echo "Rebuilding local SQLite database..."
rm -f backend/supply_chain.db backend/supply_chain.db-wal backend/supply_chain.db-shm

python3 backend/database.py
python3 backend/seed_db.py "${LIMIT_ARG[@]}"
python3 backend/update_metrics.py "${LIMIT_ARG[@]}"
python3 backend/seed_edges.py

# The same order as run_pipeline.sh and the scheduled workflow. Skipping the review-page
# step left its decisions out, and publishing from the decisions file before cleanup had
# rejected anything brought rejected links back.
echo "Reapplying persisted edge review decisions..."
python3 backend/edge_review_decisions.py apply

echo "Applying decisions from the review page..."
python3 backend/apply_human_review.py

python3 backend/cleanup_reviewed_edges.py

echo "Persisting reviewed edge decisions..."
python3 backend/edge_review_decisions.py export

python3 backend/audit_data_quality.py --fail-on-warnings
python3 backend/export.py
python3 backend/repair_dashboard_from_decisions.py
python3 backend/validate_dashboard_data.py
python3 backend/generate_change_feed.py
python3 backend/generate_static_pages.py
python3 backend/split_dashboard.py
python3 backend/write_status.py

echo "Rebuild complete. Dashboard data refreshed at docs/dashboard_data.json"
