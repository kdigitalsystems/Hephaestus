# Testing and CI

Hephaestus uses a small set of deterministic checks so source expansion and dashboard work can be reviewed without live API calls or a local Ollama model.

## Local Smoke Checks

Run these before committing frontend, source, or data-quality changes:

```bash
python -m pytest tests/test_additional_sources.py tests/test_sec_sources.py -q
python -m compileall -q backend tests
node --check docs/app.js
node tests/check_app_behaviors.js
node tests/check_app_link_count.js
node tests/check_sanitize_review_decisions.js
node scripts/sanitize_review_decisions.js --check data/edge_review_decisions.json
python backend/validate_dashboard_data.py
python backend/validate_predictions.py
git diff --check
```

Run the full Python unit suite when the local environment has all dependencies:

```bash
python -m pytest -q
```

If the host Python is missing packages such as SQLAlchemy, Ollama, or BeautifulSoup, install `requirements.txt` into a virtual environment first.

## CI Jobs

`.github/workflows/ci.yml` is split into four jobs:

- `python-tests`: installs Python dependencies, runs `ruff`, compiles backend/tests, and runs `pytest` on Python 3.10 and 3.12.
- `dashboard-checks`: runs the JavaScript syntax and dashboard behavior checks, confirms the committed review decisions are already sanitized, then validates `docs/dashboard_data.json`.
- `prediction-checks`: validates the bounded prediction export and the deterministic graph-learning tests. It does not invoke Ollama or make market-data requests.
- `quality-gates`: checks shell script syntax and committed whitespace.

CI disables live source fetches with:

```text
HEPHAESTUS_USE_SEC_SOURCE=0
HEPHAESTUS_USE_SEC_EXHIBITS=0
HEPHAESTUS_USE_ADDITIONAL_SOURCES=0
```

Unit tests mock external source responses, so pull requests do not depend on SEC, openFDA, FCC, SAM.gov, Yahoo, or company websites being reachable.

## Scheduled Pipeline

`.github/workflows/gpu_pipeline.yml` is the self-hosted discovery workflow. It keeps live network, SQLite runtime data, and Ollama work out of standard CI, then performs the publish safety sequence:

```bash
python backend/audit_data_quality.py --fail-on-warnings
python backend/export.py
python backend/repair_dashboard_from_decisions.py
python backend/validate_dashboard_data.py
```

The scheduled pipeline requires the self-hosted GPU runner, Alpaca credentials, and local Ollama review models.

## Research Signal Workflow

`.github/workflows/predictions.yml` is intentionally separate from the daily graph-discovery workflow. It runs on the existing self-hosted GPU runner, verifies that Ollama is available, and generates a research-only top-50-company export. Both publishing workflows share a GitHub Actions concurrency group, so they cannot race while writing to `main`:

```bash
python backend/generate_predictions.py --limit 50 --use-ollama --require-ollama --ollama-model qwen2.5:7b-instruct
python backend/validate_predictions.py
```

The deterministic scorer sets direction and confidence from published market inputs and approved supply-chain relationships. Ollama may only narrate the structured bull/bear scenarios; it cannot change scores, directions, or add unsupported evidence. Invalid prose, including trading instructions or price-target language, is rejected. The scheduled job fails rather than silently publishing fallback scenarios when Ollama produces no valid output. Each run appends a snapshot to `docs/prediction_history.json`. When a 30-day horizon matures, the next run evaluates it against the first available Yahoo daily close on or after the target date, records the outcome, and lightly recalibrates relationship-type transfer weights from resolved history. If the historical lookup is unavailable, the record explicitly falls back to the current exported price. Scenario narration receives a bounded retrieval of relevant resolved outcomes, selected locally by ticker, sector, and relationship type.
