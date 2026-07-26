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
python backend/validate_dashboard_data.py
git diff --check
```

Run the full Python unit suite when the local environment has all dependencies:

```bash
python -m pytest -q
```

If the host Python is missing packages such as SQLAlchemy, Ollama, or BeautifulSoup, install `requirements.txt` into a virtual environment first.

## CI Jobs

`.github/workflows/ci.yml` is split into three jobs:

- `python-tests`: installs Python dependencies, compiles backend/tests, and runs `pytest` on Python 3.10 and 3.12.
- `dashboard-checks`: runs the JavaScript syntax and dashboard behavior checks, then validates `docs/dashboard_data.json`.
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
