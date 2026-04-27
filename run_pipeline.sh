#!/bin/bash

# Navigate to the project directory (Change this to your actual path)
# cd /path/to/your/project/repository-name

echo "======================================"
echo "Starting Supply Chain Pipeline: $(date)"
echo "======================================"

# 1. Activate your virtual environment (if you are using one)
# source venv/bin/activate

# 2. Run the extraction (assuming scraper and parser are integrated into a main.py, or run them sequentially)
# For now, let's assume you have a script that runs the daily scrape & DB insertion
# python backend/daily_job.py 

# 3. Export the local DB to the static JSON file
echo "Exporting database to docs/supply_chain_data.json..."
python backend/export.py

# 4. Git Automation: Push the static file to GitHub
echo "Pushing updates to GitHub..."
git add docs/supply_chain_data.json
git commit -m "Automated data pipeline update: $(date +'%Y-%m-%d')"

# Using '|| true' prevents the script from failing if there are no new changes to push
git push origin main || true

echo "Pipeline complete."
echo "======================================"
