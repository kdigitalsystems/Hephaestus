# Hephaestus

Hephaestus is a local-first supply chain intelligence dashboard. It builds a SQLite graph of public companies, enriches those companies with market and profile data, discovers supplier/customer relationships, and exports a static JSON payload for the browser dashboard in `docs/`.

The project is designed around a simple split:

- Local Python jobs own ingestion, enrichment, LLM-assisted discovery, and export.
- SQLite stores the company and supply-chain graph.
- A static frontend reads `docs/dashboard_data.json` and renders the market overview, screener, watchlist, comparison view, sector pages, company briefs, and Supply Chain X-Ray.

## Repository Layout

```text
backend/
  additional_sources.py   Company IR, procurement, regulatory, and configured source collectors
  auto_discover_edges.py  LLM-assisted supply-chain discovery
  database.py             SQLAlchemy engine/session setup
  db_health.py            SQLite readiness check for local and scheduled runs
  export.py               SQLite to docs/dashboard_data.json export
  generate_predictions.py Top-50 graph-aware research signal exporter
  main.py                 URL scrape -> LLM parse -> database workflow
  models.py               Node and Edge ORM models
  parser.py               Ollama structured extraction prompt/schema
  repair_dashboard_from_decisions.py
                          Restores approved links into the published dashboard export
  scraper.py              Web article text extraction
  sec_sources.py          SEC filing and material-contract exhibit collectors
  seed_db.py              Alpaca equity universe seeding
  seed_edges.py           Manual starter relationships
  update_metrics.py       YahooQuery financial/profile enrichment
docs/
  DATA_SOURCES.md         Source inventory and source-quality guidance
  TESTING.md              Local checks, CI jobs, and scheduled pipeline notes
  index.html              Static dashboard shell
  methodology.html        Public explanation of sources, review rules, labels, and limits
  company/                Pre-rendered, indexable page per linked company plus an index
  app.js                  Dashboard behavior
  styles.css              Dashboard styling
  dashboard_data.json     Exported dashboard data
  link_history.json       Daily published link-count history
  changes.json            Day-over-day graph changes, newest first (from link history)
  feed.xml                RSS feed of the same changes
  predictions.json        Published, research-only top-50 signal export
  prediction_history.json Outcome history used for conservative recalibration
one_off_scripts/
  alpaca_fetch.py         Manual Alpaca/Yahoo diagnostic script
run_pipeline.sh           Seed, enrich, export, and optionally push dashboard data
```

## Requirements

- Python 3.10+
- Ollama running locally for LLM extraction, for example `ollama serve`
- Alpaca API credentials for seeding active US equities
- Network access for Alpaca, YahooQuery, Wikipedia, SEC EDGAR, and article scraping

Install Python dependencies:

```bash
pip install -r requirements.txt
```

For testing and CI details, see `docs/TESTING.md`. For source coverage and source-quality guidance, see `docs/DATA_SOURCES.md`.

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

Check whether the local database file is usable:

```bash
python3 backend/db_health.py
```

Require a seeded company universe before a scheduled run:

```bash
python3 backend/db_health.py --require-nodes
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

Discovery combines Wikipedia operations/product sections, recent YahooQuery headlines, recent SEC EDGAR annual filings, SEC material-contract exhibits, company IR/web pages, configured source URLs, government procurement summaries, and sector-aware regulatory datasets.

The current source mix includes:

- SEC EDGAR annual/quarterly filings and material-contract exhibits.
- Company IR, newsroom, and website pages discovered from market profile data.
- Custom configured URLs for investor presentations, earnings transcripts, shipment-data exports, paid datasets, partner pages, and other curated evidence.
- USAspending award summaries.
- SAM.gov opportunities when `HEPHAESTUS_SAM_API_KEY` is set.
- openFDA device 510(k), device recall, and drug enforcement records for healthcare, medical device, pharma, and biotech companies.
- FCC equipment authorization data for technology, electronics, telecom, wireless, and hardware companies.
- NHTSA manufacturer records for vehicle-related companies.

Annual reports are also mined deterministically for **customer-concentration disclosures** — the customers an issuer must name because they account for 10% or more of its revenue ("Apple accounted for approximately 24% of our net sales"). Each match becomes a pending `Revenue Concentration` edge from the filer to the named customer, cites the filing, and stores the disclosed share in `revenue_share`, which the dashboard shows as "24% of revenue" on the relationship card and the research signals use to scale how much of a customer's signal transfers to its supplier. Disable it with `HEPHAESTUS_USE_CUSTOMER_CONCENTRATION=0`.

SEC annual filings and exhibits are primary-source evidence and are enabled by default; they can be disabled for faster local experiments:

```bash
HEPHAESTUS_USE_SEC_SOURCE=0 python3 backend/auto_discover_edges.py --limit 5
HEPHAESTUS_USE_SEC_EXHIBITS=0 python3 backend/auto_discover_edges.py --limit 5
```

SEC requests use `HEPHAESTUS_SEC_USER_AGENT` when set, falling back to the project default user agent.

Broader source collection is also enabled by default. Use these toggles when you want a narrower or faster run:

```bash
HEPHAESTUS_USE_ADDITIONAL_SOURCES=0 python3 backend/auto_discover_edges.py --limit 5
HEPHAESTUS_USE_IR_SOURCES=0 python3 backend/auto_discover_edges.py --limit 5
HEPHAESTUS_USE_PROCUREMENT_SOURCE=0 python3 backend/auto_discover_edges.py --limit 5
HEPHAESTUS_USE_REGULATORY_SOURCE=0 python3 backend/auto_discover_edges.py --limit 5
```

SAM.gov opportunities require a public API key from SAM.gov:

```bash
export HEPHAESTUS_SAM_API_KEY="your-sam-api-key"
python3 backend/auto_discover_edges.py --limit 5 --sectors Industrials Technology
```

To add company-specific source URLs such as investor presentations, annual-report PDFs converted to web pages, shipment-data pages, earnings-call transcripts, partner pages, or paid dataset exports, copy `data/source_urls.example.json` to `data/source_urls.json` and add ticker-keyed entries:

```json
{
  "sources": {
    "AMD": [
      {
        "url": "https://ir.amd.com/",
        "title": "AMD Investor Relations",
        "source_type": "Company IR"
      }
    ]
  }
}
```

Set `HEPHAESTUS_SOURCE_CONFIG=/path/to/source_urls.json` to use another file. Press/news article body fetching is off by default because it is noisier and publisher terms vary; enable it with `HEPHAESTUS_FETCH_NEWS_ARTICLES=1`. Increase or reduce the source context budget with `HEPHAESTUS_CONTEXT_MAX_CHARS`.

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

The exported dashboard includes review metadata for CI and maintainer workflows. Each published relationship also carries a compact `review_summary` (curated seed, consensus panel vote count, single-model review, or human review, plus a short rationale) and a `source_title` citation derived from the collector that supplied the evidence, so the dashboard can show how a link was verified and where it came from; `docs/methodology.html` documents the sources, review rules, and limitations for readers. The pending review queue itself is not part of the public experience.

The dashboard payload also includes investor-facing derived metrics:

- `investor_metrics.unique_links`, `approved_links`, `pending_links`, and `sector_exposure` summarize the published graph.
- Each company has `investor_metrics` with upstream/downstream counts, approval counts, top counterparties, average confidence, concentration score, explainable risk scores, and last verified date.
- `docs/link_history.json` keeps one rolling snapshot per UTC date of published relationship keys so the dashboard can show what changed between daily runs without adding duplicate same-day snapshots. The overview renders that comparison as a "What changed in the graph" panel, and `python3 backend/generate_change_feed.py` derives `docs/changes.json` and an RSS feed (`docs/feed.xml`) from the same snapshots; both pipelines run it after validation.
- `update_metrics.py` refreshes companies that have supply-chain links before the rest of the universe, so a throttled market-data crawl degrades the long tail rather than the companies people open.
- The static UI uses those fields for search filters, watchlist cards, comparison views, source/evidence modals, sector pages, and company Decision Briefs.

The risk scores are intentionally simple and explainable:

- `risk_score` combines concentration risk, incomplete review coverage, and lower confidence.
- `supplier_risk` and `customer_risk` show whether concentration is mostly upstream or downstream.
- `review_score`, `confidence_score`, and `freshness_score` expose the components instead of hiding them behind a black-box AI score.

The website's Supply Links number counts unique stable relationship keys. Relationship rows appear from both sides of a connection, so the raw number of upstream/downstream rows is usually about twice the unique link count.

## Graph-Aware Research Signals

Hephaestus also publishes a bounded research-signal view for the 50 largest exported companies by market capitalization. It is research tooling, not investment advice, a price target, or a recommendation to trade.

Each signal combines a small set of explainable direct inputs with one-hop supply-chain propagation:

- Direct inputs: recent published price change, analyst target gap, and the exported analyst recommendation.
- Customer-to-supplier propagation: a positive customer signal can contribute a dampened positive demand signal to a tracked supplier.
- Supplier-to-customer propagation: supplier-side signals use a smaller weight because their effect on the customer is more ambiguous.
- Relationship confidence, approval state, the disclosed revenue share when the supplier reported one, and later measured relationship-type performance constrain every transfer.

Every published prediction contains its direct and network scores, structured inputs, all contributing relationship paths with source evidence (each counterparty is counted once, and the network score is the sum of the published path contributions), confidence, bull/bear scenarios, model version, and generation time. Scores remain deterministic and auditable. A local Ollama model may generate concise scenario prose from this already-selected evidence, but it cannot choose a direction, change confidence, set a price target, or introduce new facts.

Run a local deterministic export:

```bash
python3 backend/generate_predictions.py --limit 50
python3 backend/validate_predictions.py
```

Use local Ollama only for scenario narration:

```bash
python3 backend/generate_predictions.py --limit 50 --use-ollama --require-ollama --ollama-model qwen2.5:7b-instruct
```

The export also publishes a `track_record` block — resolved count, hit rate, the hit rate an "always up" guess would have achieved over the same signals, per-direction rates, and how many matured signals are still awaiting price data — and the Predictions view shows it above the signals, stating plainly when the signals have not beaten that naive baseline. Below 30 resolved signals the view is labelled experimental.

`docs/prediction_history.json` retains prediction snapshots; unresolved predictions are always kept until their horizon can be evaluated, and only resolved entries are pruned to bound the file. On later runs, signals whose 30-day horizon has matured are evaluated against the first available Yahoo daily close on or after the target date, then recorded as correct or incorrect and used to conservatively recalibrate relationship-type transfer weights. If that history lookup is unavailable, the result falls back to the current exported price and labels the fallback in `outcome_price_source`. Early results are shrunk toward neutral weights so a few lucky predictions do not distort the model. The Ollama pass also retrieves a small, local set of resolved historical outcomes with matching tickers, sectors, or relationship types for scenario context. This retrieval layer is deliberately bounded and auditable; it can evolve to an embedding store later without changing the published prediction contract.

The Watchlist is local to the browser via `localStorage`; it does not require accounts or a backend. The Compare view is routeable with hash parameters, for example `#compare?a=AMD&b=NVDA`.

The Exposure view (`#exposure?ticker=TSM`, or the Exposure button on any company brief) answers "who is affected if this company is disrupted": it walks the published graph two hops in each direction, listing direct dependents, second-order dependents (companies whose suppliers depend on the target), and the target's own suppliers, with the path shown for every hit. Rows are flagged when the evidence uses single-source language ("sole source", "substantially all") and show disclosed revenue shares; the sector breakdown of exposed companies links to each sector page. Everything is computed in the browser from `dashboard_data.json`.

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
python3 -m pytest tests/test_additional_sources.py tests/test_sec_sources.py -q
python3 -m compileall -q backend tests
node --check docs/app.js
node tests/check_app_link_count.js
node tests/check_app_behaviors.js
python3 backend/validate_dashboard_data.py
git diff --check
```

Full local test suite:

```bash
python3 -m pytest -q
```

GitHub CI runs Python tests on Python 3.10 and 3.12, frontend/dashboard checks on Node 24, shell syntax checks, committed whitespace checks, and dashboard validation. Standard CI disables live source fetching and relies on mocked unit tests for external API collectors. The scheduled GPU pipeline runs the SQLite-backed relationship audit after live discovery and review.

`main` only accepts changes through pull requests. The two scheduled publishing workflows are the exception: they authenticate as the **Hephaestus Pipeline Bot** GitHub App (secrets `HEPHAESTUS_APP_ID` and `HEPHAESTUS_APP_PRIVATE_KEY`, minted per run with `actions/create-github-app-token`), and that app is the only actor on the ruleset's bypass list.

Research-signal generation is isolated in `.github/workflows/predictions.yml`. It uses the self-hosted GPU runner and Ollama, writes only `docs/predictions.json` and `docs/prediction_history.json`, and does not alter the daily discovery/update workflow. The prediction and daily graph workflows share a concurrency group before their publishing steps, preventing simultaneous pushes to `main`. Standard CI includes a separate prediction-export check that runs without Ollama. Scheduled prediction generation fails when Ollama cannot produce any valid, evidence-bound scenario rather than quietly publishing a model-fallback run.

The app uses hash routes such as `#company?ticker=AMD` internally, which search engines do not index, so `python3 backend/generate_static_pages.py` pre-renders one static page per company with published relationships under `docs/company/<TICKER>.html` (suppliers, customers, verification labels, evidence, citations, and links back to the interactive brief and exposure map), plus `docs/company/index.html` and `docs/sitemap.xml` listing them all. Both pipelines run it after the feeds; pages for companies that lose their last relationship are removed. `docs/robots.txt` points crawlers at the sitemap.

## Full Pipeline

Run the standard daily pipeline:

```bash
./run_pipeline.sh
```

Run a limited debug pipeline:

```bash
./run_pipeline.sh 25
```

The pipeline checks local database readiness, initializes the SQLite schema if needed, reapplies persisted edge decisions, reviews a bounded batch of pending AI edges with an Ollama consensus panel, exports the dashboard, repairs approved links from persisted decisions, validates the published JSON, fails fast if any step fails, and commits dashboard, link-history, and review-decision changes. It refuses to run on any branch other than `main`, and it stops with an error when `ollama` is installed but the daemon is not responding, so an unreviewed batch can never be published as if it had been reviewed.

Review behavior can be tuned with environment variables:

```bash
HEPHAESTUS_REVIEW_MODELS=qwen2.5:7b-instruct,llama3.1:8b,mistral:7b-instruct \
HEPHAESTUS_REVIEW_LIMIT=200 \
HEPHAESTUS_REVIEW_MAX_SECONDS=3300 \
HEPHAESTUS_REVIEW_MIN_CONFIDENCE=0.85 \
HEPHAESTUS_REVIEW_CONSENSUS_MIN_VOTES=2 \
HEPHAESTUS_REVIEW_CONSENSUS_MIN_RATIO=0.66 \
./run_pipeline.sh
```

By default the reviewer runs three 7B/8B-class models one at a time so they fit on a 12GB GPU. A pending edge is auto-applied only when the configured consensus threshold agrees on the same action and direction. Split votes, low confidence, or direction disagreement stay `pending` instead of being published as approved links.

To skip local AI review during a manual pipeline run:

```bash
HEPHAESTUS_RUN_OLLAMA_REVIEW=0 ./run_pipeline.sh
```

The scheduled GitHub workflow checks that Ollama is available on the self-hosted runner and pulls the configured review models if they are missing:

```bash
ollama pull qwen2.5:7b-instruct
ollama pull llama3.1:8b
ollama pull mistral:7b-instruct
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
- `revenue_share`: percentage of the supplier's revenue attributed to the customer when the supplier disclosed it (10% customers); otherwise null
- `review_note`

AI-discovered relationships require a substantive excerpt from the collected source text. The discovery, review, cleanup, repair, and published-data validation stages all reject missing or placeholder evidence; explicitly curated manual seeds remain supported. Discovery also checks that the excerpt actually appears in the text the collectors gathered (minor rewording is tolerated), trusts a model-supplied ticker only when it names the extracted company, and keeps `evidence_source_url` only when it is one of the URLs Hephaestus itself fetched.

Non-supply keywords such as "acquisition", "partnership", or "collaboration" only disqualify a relationship when they appear in the relationship label itself; product names and verbatim filing excerpts are screened only for explicit non-supply phrases, so a "Collaboration software" product or "we acquired components from" excerpt is not rejected automatically.
- `reviewed_at`

Edges are unique by source, target, and dependency type.

Published relationship payloads also include `relationship_key`, a stable supplier/customer/type key used by the browser to count unique links across database rebuilds. Do not rely on SQLite `edge_id` values for long-term trend tracking because IDs can change after a rebuild.

## Troubleshooting

`ConnectionRefusedError` from Ollama:
Start Ollama with `ollama serve` and make sure the configured model is available.

Dashboard shows no companies:
Run `python3 backend/db_health.py --require-nodes` first. If it reports a missing, empty, unreadable, or unseeded database, run `python3 backend/database.py`, `python3 backend/seed_db.py`, then `python3 backend/update_metrics.py`. The broad screener prefers nodes with market cap and current price. Approved relationship endpoints without fresh market data are restored by `repair_dashboard_from_decisions.py` under `Linked Companies`.

Dashboard shows no Supply Chain X-Ray relationships:
Run `seed_edges.py` for starter edges or `auto_discover_edges.py` for LLM-assisted discovery, review/apply the discovered edges, then run `export.py`, `repair_dashboard_from_decisions.py`, and `validate_dashboard_data.py`.

Supply Links decreased after a daily run:
Check `data/edge_review_decisions.json` and the workflow logs. Approved links should accumulate, but rejected/pending edges are intentionally hidden. If the count drops unexpectedly, run `python3 backend/repair_dashboard_from_decisions.py` and `python3 backend/validate_dashboard_data.py`; the pipeline now runs both automatically after every export.

Database is locked:
The local SQLite database now waits briefly for a competing reader or writer to finish. Avoid running two ingestion, audit, or rebuild commands against the same database at once. When the repository is opened through a `\\wsl.localhost` path, run database commands from WSL rather than using a Windows Python interpreter against the network share; SQLite locking is not reliable across that boundary.

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
