#!/bin/bash
set -euo pipefail

cd "$(dirname "$0")"

echo "======================================"
echo "Starting Hephaestus API Pipeline: $(date)"
echo "======================================"

# This script commits and pushes main. Committing onto another branch would leave
# the run's output unpublished while `git push origin main` reports up-to-date.
CURRENT_BRANCH="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)"
if [ "$CURRENT_BRANCH" != "main" ]; then
  echo "Refusing to run the publishing pipeline on branch '$CURRENT_BRANCH'; check out main first."
  exit 1
fi

LIMIT_ARG=()
if [ "${1:-}" != "" ]; then
  LIMIT_ARG=(--limit "$1")
  echo "DEV MODE ACTIVE: Limiting processing to $1 companies."
fi

REVIEW_MODELS="${HEPHAESTUS_REVIEW_MODELS:-qwen2.5:7b-instruct,llama3.1:8b,mistral:7b-instruct}"
REVIEW_LIMIT="${HEPHAESTUS_REVIEW_LIMIT:-200}"
REVIEW_MAX_SECONDS="${HEPHAESTUS_REVIEW_MAX_SECONDS:-3300}"
REVIEW_MIN_CONFIDENCE="${HEPHAESTUS_REVIEW_MIN_CONFIDENCE:-0.85}"
REVIEW_CONSENSUS_MIN_VOTES="${HEPHAESTUS_REVIEW_CONSENSUS_MIN_VOTES:-2}"
REVIEW_CONSENSUS_MIN_RATIO="${HEPHAESTUS_REVIEW_CONSENSUS_MIN_RATIO:-0.66}"
RUN_OLLAMA_REVIEW="${HEPHAESTUS_RUN_OLLAMA_REVIEW:-1}"

echo "Checking local database readiness..."
if ! python3 backend/db_health.py; then
  echo "Initializing local database schema..."
  python3 backend/database.py
fi

echo "Ensuring core companies are tracked..."
python3 backend/seed_db.py "${LIMIT_ARG[@]}"

echo "Updating live financial metrics..."
python3 backend/update_metrics.py "${LIMIT_ARG[@]}"

echo "Reapplying persisted edge review decisions..."
python3 backend/edge_review_decisions.py apply

if [ "$RUN_OLLAMA_REVIEW" = "1" ]; then
  if command -v ollama >/dev/null 2>&1; then
    # Probe the daemon once. Without this, an unreachable daemon looks identical to
    # "every model is missing" and the run silently publishes unreviewed edges.
    OLLAMA_LIST_FILE="$(mktemp)"
    if ! ollama list >"$OLLAMA_LIST_FILE" 2>&1; then
      echo "Ollama is installed but the local service is not responding:"
      cat "$OLLAMA_LIST_FILE"
      rm -f "$OLLAMA_LIST_FILE"
      echo "Start it with 'ollama serve', or set HEPHAESTUS_RUN_OLLAMA_REVIEW=0 to skip review deliberately."
      exit 1
    fi

    MISSING_MODELS=()
    IFS=',' read -ra MODEL_LIST <<< "$REVIEW_MODELS"
    for MODEL in "${MODEL_LIST[@]}"; do
      MODEL="$(echo "$MODEL" | xargs)"
      if [ -n "$MODEL" ] && ! awk '{print $1}' "$OLLAMA_LIST_FILE" | grep -Fx "$MODEL" >/dev/null; then
        MISSING_MODELS+=("$MODEL")
      fi
    done
    rm -f "$OLLAMA_LIST_FILE"

    if [ "${#MISSING_MODELS[@]}" -gt 0 ]; then
      echo "Skipping Ollama review because required model(s) are missing: ${MISSING_MODELS[*]}"
    else
      echo "Reviewing pending AI edges with consensus models: $REVIEW_MODELS..."
      python3 backend/review_edges_with_ollama.py \
        --models "$REVIEW_MODELS" \
        --status pending \
        --limit "$REVIEW_LIMIT" \
        --apply \
        --max-seconds "$REVIEW_MAX_SECONDS" \
        --min-approve "$REVIEW_MIN_CONFIDENCE" \
        --min-reverse "$REVIEW_MIN_CONFIDENCE" \
        --min-reject "$REVIEW_MIN_CONFIDENCE" \
        --consensus-min-votes "$REVIEW_CONSENSUS_MIN_VOTES" \
        --consensus-min-ratio "$REVIEW_CONSENSUS_MIN_RATIO" \
        --report reports/ollama_edge_review_pipeline.csv
    fi
  else
    echo "Skipping Ollama review because ollama is not available."
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

echo "Publishing change feeds..."
python3 backend/generate_change_feed.py

echo "Checking for dashboard changes..."
git add docs/dashboard_data.json docs/link_history.json data/edge_review_decisions.json
git add docs/changes.json docs/feed.xml
if ! git diff --cached --quiet; then
  git commit -m "Automated dashboard update: $(date +'%Y-%m-%d')"
  # The scheduled workflow may have published since this run started. Unrelated
  # local edits are stashed around the rebase instead of aborting after the commit.
  git pull --rebase --autostash origin main || { git rebase --abort 2>/dev/null || true; echo "Rebase onto origin/main failed; the commit is local only."; exit 1; }
  git push origin main
else
  echo "No changes to commit."
fi

echo "Pipeline complete."
echo "======================================"
