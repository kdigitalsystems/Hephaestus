# Hephaestus Terminal

Hephaestus Terminal is a local-first supply chain intelligence dashboard. It builds a SQLite graph of public companies, enriches those companies with market and profile data, discovers supplier/customer relationships, and exports a static JSON payload for the browser dashboard in `docs/`.

The project is designed around a simple split:

- Local Python jobs own ingestion, enrichment, LLM-assisted discovery, and export.
- SQLite stores the company and supply-chain graph.
- A static frontend reads `docs/dashboard_data.json` and renders the screener and Supply Chain X-Ray.

## Repository Layout

```text
backend/
  auto_discover_edges.py  LLM-assisted supply-chain discovery
  database.py             SQLAlchemy engine/session setup
  export.py               SQLite to docs/dashboard_data.json export
  main.py                 URL scrape -> LLM parse -> database workflow
  models.py               Node and Edge ORM models
  parser.py               Ollama structured extraction prompt/schema
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

The dashboard reads:

```text
docs/dashboard_data.json
```

Open `docs/index.html` through a static file server or GitHub Pages. Some browsers restrict `fetch()` for local `file://` pages, so a tiny local server is usually smoother:

```bash
python3 -m http.server 8000 -d docs
```

Then visit `http://localhost:8000`.

## Full Pipeline

Run the standard daily pipeline:

```bash
./run_pipeline.sh
```

Run a limited debug pipeline:

```bash
./run_pipeline.sh 25
```

The pipeline now fails fast if any step fails. When `docs/dashboard_data.json` changes, it commits and pushes the dashboard export.

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

Edges are unique by source, target, and dependency type.

## Troubleshooting

`ConnectionRefusedError` from Ollama:
Start Ollama with `ollama serve` and make sure the configured model is available.

Dashboard shows no companies:
Run `seed_db.py`, then `update_metrics.py`, then `export.py`. The exporter skips nodes without market cap or current price.

Dashboard shows no Supply Chain X-Ray relationships:
Run `seed_edges.py` for starter edges or `auto_discover_edges.py` for LLM-assisted discovery, then run `export.py`.

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
