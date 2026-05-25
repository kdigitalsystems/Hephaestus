# Hephaestus

Hephaestus is a local-first supply chain intelligence dashboard. It builds a SQLite graph of public companies, enriches those companies with market and profile data, discovers supplier/customer relationships, and exports a static JSON payload for the browser dashboard in `docs/`.

The project is designed around a simple split:

- Local Python jobs own ingestion, enrichment, LLM-assisted discovery, and export.
- SQLite stores the company and supply-chain graph.
- A static frontend reads `docs/dashboard_data.json` and renders the investor radar, watchlist, comparison view, sector pages, decision briefs, and Supply Chain X-Ray.

## Repository Layout

```text
backend/
  auto_discover_edges.py  LLM-assisted supply-chain discovery
  database.py             SQLAlchemy engine/session setup
  export.py               SQLite to docs/dashboard_data.json export
  main.py                 URL scrape -> LLM parse -> database workflow
  models.py               Node and Edge ORM models
  parser.py               Ollama structured extraction prompt/schema
  repair_dashboard_from_decisions.py
                          Restores approved links into the published dashboard export
  scraper.py              Web article text extraction
  seed_db.py              Alpaca equity universe seeding
  seed_edges.py           Manual starter relationships
  update_metrics.py       YahooQuery financial/profile enrichment
docs/
  index.html              Static dashboard shell
  app.js                  Dashboard behavior
  styles.css              Dashboard styling
  dashboard_data.json     Exported dashboard data
one_off_scripts/
  alpaca_fetch.py         Manual Alpaca/Yahoo diagnostic script
run_pipeline.sh           Seed, enrich, export, and optionally push dashboard data
```

## Requirements

- Python 3.10+
- Ollama running locally for LLM extraction, for example `ollama serve`
- Alpaca API credentials for seeding active US equities
- Network access for Alpaca, YahooQuery, Wikipedia, and article scraping

Install Python dependencies:

```bash
pip install -r requirements.txt
```

## Credentials

Do not hardcode Alpaca keys in source files. The scripts read credentials from environment variables first:

```bash
export ALPACA_API_KEY="your-api-key"
export ALPACA_SECRET_KEY="your-secret-key"
```

For local use, you can also create `~/.ssh/alpaca_paper_keys`:

```text
Key:YOUR_ALPACA_API_KEY
Secret_Key:YOUR_ALPACA_SECRET_KEY
```

If hardcoded keys were ever committed or pushed, rotate them in Alpaca before continuing.

## Initialize The Database

Create the SQLite schema:

```bash
python3 backend/database.py
```

Seed public companies from Alpaca:

```bash
python3 backend/seed_db.py
```

For a quick test run:

```bash
python3 backend/seed_db.py --limit 25
```

To rebuild the local database from scratch and refresh dashboard data:

```bash
./scripts/rebuild_db.sh
```

For a limited debug rebuild:

```bash
./scripts/rebuild_db.sh 25
```

## Update Market Data

Populate pricing, market cap, valuation, profile, and analyst fields:

```bash
python3 backend/update_metrics.py
```

For a quick test run:

```bash
python3 backend/update_metrics.py --limit 25
```

## Build Supply Chain Edges

Seed the curated starter hardware relationships:

```bash
python3 backend/seed_edges.py
```

Run LLM-assisted discovery for companies that do not yet have relationships:

```bash
python3 backend/auto_discover_edges.py --limit 5
```

Optional sector targeting:

```bash
python3 backend/auto_discover_edges.py --limit 10 --sectors Technology Industrials
```

Research already-connected companies:

```bash
python3 backend/auto_discover_edges.py --limit 10 --deep-dive
```

AI-discovered relationships are saved as pending by default. Review them before publishing:

```bash
python3 backend/review_edges.py list --status pending --limit 20
python3 backend/review_edges.py approve 123 --note "Verified source"
python3 backend/review_edges.py reject 123 --note "Wrong direction or not a supply-chain link"
python3 backend/review_edges.py edit 123 --type "Manufacturing" --product "Advanced node wafer fabrication"
```

You can also filter the review queue by company:

```bash
python3 backend/review_edges.py list --status pending --source AMD
python3 backend/review_edges.py list --status pending --target TSM
```

To batch-curate pending edges with a local Ollama model:

```bash
python3 backend/review_edges_with_ollama.py --model qwen2.5:14b-instruct --limit 25
python3 backend/review_edges_with_ollama.py --model qwen2.5:14b-instruct --limit 250 --apply
```

The Ollama reviewer can approve, reject, or reverse high-confidence edges. Ambiguous edges stay pending. Review reports are written to `reports/ollama_edge_review.csv` by default.

Long review runs can be resumed safely:

```bash
python3 backend/review_edges_with_ollama.py --model qwen2.5:14b-instruct --limit 1000 --apply --max-seconds 3300
python3 backend/apply_ollama_review_report.py reports/ollama_edge_review.csv --min-approve 0.85 --min-reverse 0.85
```

Persist reviewed decisions outside the local SQLite database:

```bash
python3 backend/edge_review_decisions.py export
python3 backend/edge_review_decisions.py apply
```

The exported decisions live in `data/edge_review_decisions.json` so future database rebuilds can reapply the reviewed approvals/rejections.

## Export Dashboard Data

Before publishing dashboard data, run the quality audit:

```bash
python3 backend/audit_data_quality.py
```

The audit checks for duplicate tickers, reversed role-label edges, non-supply relationships, and self-edges. To make it fail a pipeline when warnings are present:

```bash
python3 backend/audit_data_quality.py --fail-on-warnings
```

Write the static dashboard payload:

```bash
python3 backend/export.py
```

After export, repair the payload from persisted reviewed decisions and validate it:

```bash
python3 backend/repair_dashboard_from_decisions.py
python3 backend/validate_dashboard_data.py
```

This repair step is important. The broad stock screener still prefers companies with current market data, but approved/manual supply-chain relationships should not disappear just because Yahoo/market metrics are temporarily missing for one endpoint. The repair step uses `data/edge_review_decisions.json` as the durable source of reviewed links, adds missing approved endpoints under `Linked Companies`, and writes stable `relationship_key` values so the dashboard link count is not tied to volatile SQLite edge IDs.

By default, export is conservative: it includes reviewed/manual edges and hides unreviewed AI-discovered edges. To publish AI research edges anyway:

```bash
HEPHAESTUS_EXPORT_AI_RESEARCH=1 python3 backend/export.py
```

Use that mode only after reviewing the generated relationships; LLM extraction can create plausible but wrong links.

The exported dashboard also includes a review summary. Visit `#quality` in the static app, or click `Review Queue`, to see pending AI edges and the review commands for each one.

The dashboard payload also includes investor-facing derived metrics:

- `investor_metrics.unique_links`, `approved_links`, `pending_links`, and `sector_exposure` summarize the published graph.
- Each company has `investor_metrics` with upstream/downstream counts, approval counts, top counterparties, average confidence, concentration score, explainable risk scores, and last verified date.
- `docs/link_history.json` keeps a rolling daily snapshot of published relationship keys so the dashboard can show what changed between runs.
- The static UI uses those fields for the Investment Radar, richer search filters, watchlist cards, comparison views, trust badges, source/evidence modals, sector pages, and company Decision Briefs.

The risk scores are intentionally simple and explainable:

- `risk_score` combines concentration risk, incomplete review coverage, and lower confidence.
- `supplier_risk` and `customer_risk` show whether concentration is mostly upstream or downstream.
- `review_score`, `confidence_score`, and `freshness_score` expose the components instead of hiding them behind a black-box AI score.

The website's Supply Links number counts unique stable relationship keys. Relationship rows appear from both sides of a connection, so the raw number of upstream/downstream rows is usually about twice the unique link count.

The Watchlist is local to the browser via `localStorage`; it does not require accounts or a backend. The Compare view is routeable with hash parameters, for example `#compare?a=AMD&b=NVDA`.

The dashboard reads:

```text
docs/dashboard_data.json
```

Open `docs/index.html` through a static file server or GitHub Pages. Some browsers restrict `fetch()` for local `file://` pages, so a tiny local server is usually smoother:

```bash
python3 -m http.server 8000 -d docs
```

Then visit `http://localhost:8000`.

Useful local smoke checks:

```bash
node --check docs/app.js
node tests/check_app_link_count.js
node tests/check_app_behaviors.js
python3 backend/validate_dashboard_data.py
python3 -m pytest -q
```

The static site includes `docs/sitemap.xml` and `docs/robots.txt` for GitHub Pages discovery. The app uses hash routes such as `#company?ticker=AMD` internally, but the sitemap points search engines at the canonical dashboard entry point because URL fragments are not reliable sitemap targets.

## Full Pipeline

Run the standard daily pipeline:

```bash
./run_pipeline.sh
```

Run a limited debug pipeline:

```bash
./run_pipeline.sh 25
```

The pipeline now reapplies persisted edge decisions, reviews a bounded batch of pending AI edges with Ollama, exports the dashboard, repairs approved links from persisted decisions, validates the published JSON, fails fast if any step fails, and commits dashboard/review-decision changes.

Review behavior can be tuned with environment variables:

```bash
HEPHAESTUS_REVIEW_MODEL=qwen2.5:14b-instruct \
HEPHAESTUS_REVIEW_LIMIT=200 \
HEPHAESTUS_REVIEW_MAX_SECONDS=3300 \
HEPHAESTUS_REVIEW_MIN_CONFIDENCE=0.85 \
./run_pipeline.sh
```

To skip local AI review during a manual pipeline run:

```bash
HEPHAESTUS_RUN_OLLAMA_REVIEW=0 ./run_pipeline.sh
```

The scheduled GitHub workflow checks that Ollama is available on the self-hosted runner and pulls the configured review model if it is missing:

```bash
ollama pull qwen2.5:14b-instruct
```

The scheduled workflow and local scripts both run the same publishing safety sequence:

```bash
python3 backend/export.py
python3 backend/repair_dashboard_from_decisions.py
python3 backend/validate_dashboard_data.py
```

## Data Model

`Node` represents a company or tracked entity. Important fields include:

- `name`
- `ticker`
- `sector`
- `industry`
- market and valuation metrics
- profile fields such as CEO, employees, and business summary

`Edge` represents a supply-chain relationship:

- `source_id`: supplier/provider
- `target_id`: customer/receiver
- `dependency_type`
- `product`
- `confidence_score`
- `source_url`
- `source_title`
- `evidence_excerpt`
- `review_status`: `pending`, `approved`, or `rejected`
- `review_note`
- `reviewed_at`

Edges are unique by source, target, and dependency type.

Published relationship payloads also include `relationship_key`, a stable supplier/customer/type key used by the browser to count unique links across database rebuilds. Do not rely on SQLite `edge_id` values for long-term trend tracking because IDs can change after a rebuild.

## Troubleshooting

`ConnectionRefusedError` from Ollama:
Start Ollama with `ollama serve` and make sure the configured model is available.

Dashboard shows no companies:
Run `seed_db.py`, then `update_metrics.py`, then `export.py`. The broad screener prefers nodes with market cap and current price. Approved relationship endpoints without fresh market data are restored by `repair_dashboard_from_decisions.py` under `Linked Companies`.

Dashboard shows no Supply Chain X-Ray relationships:
Run `seed_edges.py` for starter edges or `auto_discover_edges.py` for LLM-assisted discovery, review/apply the discovered edges, then run `export.py`, `repair_dashboard_from_decisions.py`, and `validate_dashboard_data.py`.

Supply Links decreased after a daily run:
Check `data/edge_review_decisions.json` and the workflow logs. Approved links should accumulate, but rejected/pending edges are intentionally hidden. If the count drops unexpectedly, run `python3 backend/repair_dashboard_from_decisions.py` and `python3 backend/validate_dashboard_data.py`; the pipeline now runs both automatically after every export.

Alpaca authentication fails:
Check `ALPACA_API_KEY`, `ALPACA_SECRET_KEY`, or `~/.ssh/alpaca_paper_keys`.

Git refuses to run on the WSL path:
Add the repository as a safe directory from the environment where you run Git, or run Git directly inside WSL.

## Security Notes

- Keep API keys out of source files and generated artifacts.
- Treat LLM and scraped data as untrusted input.
- The dashboard renders dynamic JSON as text to avoid script injection from company names, sectors, or dependency labels.
- `backend/supply_chain.db` is local runtime data and is ignored for future commits.

## License

MIT. See `LICENSE`.
