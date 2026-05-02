#!/bin/bash

echo "======================================"
echo "Starting Hephaestus API Pipeline: $(date)"
echo "======================================"

# Check for optional limit parameter for development/debugging
LIMIT_ARG=""
if [ ! -z "$1" ]; then
  LIMIT_ARG="--limit $1"
  echo "⚠️ DEV MODE ACTIVE: Limiting processing to $1 companies."
fi

# 1. Seed the database (Pulls the active US Equity roster from Alpaca)
echo "Ensuring core companies are tracked..."
python3 backend/seed_db.py $LIMIT_ARG

# 2. Update Financial Metrics (Pulls Market Cap, Prices, and Sectors from Yahoo)
echo "Updating live financial metrics..."
python3 backend/update_metrics.py $LIMIT_ARG

# 3. Export the local DB to the static JSON file
echo "Exporting database to docs/dashboard_data.json..."
python3 backend/export.py

# 4. Git Automation: Push the static file to GitHub
echo "Pushing updates to GitHub..."
git add docs/dashboard_data.json
git add docs/index.html
if ! git diff --cached --quiet; then
  git commit -m "Automated dashboard update: $(date +'%Y-%m-%d')"
  git push origin main || true
else
  echo "No changes to commit."
fi

git push origin main || true

echo "Pipeline complete."
echo "======================================"
