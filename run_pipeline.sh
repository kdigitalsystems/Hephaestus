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

REVIEW_MODEL="${HEPHAESTUS_REVIEW_MODEL:-qwen2.5:14b-instruct}"
REVIEW_LIMIT="${HEPHAESTUS_REVIEW_LIMIT:-200}"
REVIEW_MAX_SECONDS="${HEPHAESTUS_REVIEW_MAX_SECONDS:-3300}"
REVIEW_MIN_CONFIDENCE="${HEPHAESTUS_REVIEW_MIN_CONFIDENCE:-0.85}"
RUN_OLLAMA_REVIEW="${HEPHAESTUS_RUN_OLLAMA_REVIEW:-1}"

echo "Ensuring core companies are tracked..."
python3 backend/seed_db.py "${LIMIT_ARG[@]}"

echo "Updating live financial metrics..."
python3 backend/update_metrics.py "${LIMIT_ARG[@]}"

echo "Reapplying persisted edge review decisions..."
python3 backend/edge_review_decisions.py apply

if [ "$RUN_OLLAMA_REVIEW" = "1" ]; then
  if command -v ollama >/dev/null 2>&1 && ollama list | awk '{print $1}' | grep -Fx "$REVIEW_MODEL" >/dev/null; then
    echo "Reviewing pending AI edges with $REVIEW_MODEL..."
    python3 backend/review_edges_with_ollama.py \
      --model "$REVIEW_MODEL" \
      --status pending \
      --limit "$REVIEW_LIMIT" \
      --apply \
      --max-seconds "$REVIEW_MAX_SECONDS" \
      --min-approve "$REVIEW_MIN_CONFIDENCE" \
      --min-reverse "$REVIEW_MIN_CONFIDENCE" \
      --min-reject "$REVIEW_MIN_CONFIDENCE" \
      --report reports/ollama_edge_review_pipeline.csv
  else
    echo "Skipping Ollama review because $REVIEW_MODEL is not available."
  fi
fi

echo "Cleaning up non-supply reviewed edges before publishing..."
python3 backend/cleanup_reviewed_edges.py

echo "Persisting reviewed edge decisions..."
python3 backend/edge_review_decisions.py export

echo "Auditing supply-chain relationship quality..."
python3 backend/audit_data_quality.py --fail-on-warnings

echo "Exporting database to docs/dashboard_data.json..."
python3 backend/export.py

echo "Repairing export from persisted approved decisions..."
python3 backend/repair_dashboard_from_decisions.py

echo "Validating published dashboard data..."
python3 backend/validate_dashboard_data.py

echo "Checking for dashboard changes..."
git add docs/dashboard_data.json docs/link_history.json data/edge_review_decisions.json
if ! git diff --cached --quiet; then
  git commit -m "Automated dashboard update: $(date +'%Y-%m-%d')"
  git push origin main
else
  echo "No changes to commit."
fi

echo "Pipeline complete."
echo "======================================"
