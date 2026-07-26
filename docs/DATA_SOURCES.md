# Data Sources

Hephaestus treats source text as evidence for proposed supplier/customer links. Discovery gathers text from primary filings, official public datasets, and configured high-signal pages, then the local extraction model emits pending relationships for review.

## Source Families

| Source | Module | Default | Notes |
| --- | --- | --- | --- |
| SEC annual/quarterly filings | `backend/sec_sources.py` | On | Primary-source company disclosures. |
| SEC material-contract exhibits | `backend/sec_sources.py` | On | Searches exhibit indexes for contracts, supply agreements, manufacturing, purchase, distribution, logistics, and service agreements. |
| Company IR / website pages | `backend/additional_sources.py` | On | Uses Yahoo profile website data and common IR/news paths. |
| Configured source URLs | `backend/additional_sources.py` | On | Curated URLs in `data/source_urls.json`; use this for investor decks, transcript pages, shipment-data exports, paid datasets, and partner pages. |
| USAspending awards | `backend/additional_sources.py` | On | Government award summaries by recipient. |
| SAM.gov opportunities | `backend/additional_sources.py` | Key-gated | Requires `HEPHAESTUS_SAM_API_KEY`; focused on procurement-heavy sectors. |
| openFDA device/drug datasets | `backend/additional_sources.py` | Sector-gated | Used for healthcare, medical device, pharma, biotech, and life-sciences companies. |
| FCC equipment authorization | `backend/additional_sources.py` | Sector-gated | Used for technology, electronics, telecom, wireless, and hardware companies. |
| NHTSA manufacturer records | `backend/additional_sources.py` | Sector-gated | Used for vehicle-related companies. |
| Press/news article bodies | `backend/additional_sources.py` | Off | Enable only when needed because publisher terms and article quality vary. |

## Toggles

Disable broad source collection:

```bash
HEPHAESTUS_USE_ADDITIONAL_SOURCES=0 python backend/auto_discover_edges.py --limit 5
```

Disable individual source groups:

```bash
HEPHAESTUS_USE_SEC_SOURCE=0 python backend/auto_discover_edges.py --limit 5
HEPHAESTUS_USE_SEC_EXHIBITS=0 python backend/auto_discover_edges.py --limit 5
HEPHAESTUS_USE_IR_SOURCES=0 python backend/auto_discover_edges.py --limit 5
HEPHAESTUS_USE_PROCUREMENT_SOURCE=0 python backend/auto_discover_edges.py --limit 5
HEPHAESTUS_USE_REGULATORY_SOURCE=0 python backend/auto_discover_edges.py --limit 5
```

Enable optional article fetching:

```bash
HEPHAESTUS_FETCH_NEWS_ARTICLES=1 python backend/auto_discover_edges.py --limit 5
```

Use SAM.gov opportunities:

```bash
export HEPHAESTUS_SAM_API_KEY="your-sam-api-key"
python backend/auto_discover_edges.py --limit 5 --sectors Industrials Technology
```

Adjust the source context sent to the extractor:

```bash
HEPHAESTUS_CONTEXT_MAX_CHARS=18000 python backend/auto_discover_edges.py --limit 5
```

## Curated Source File

Copy the example file:

```bash
cp data/source_urls.example.json data/source_urls.json
```

Add ticker-specific or global entries:

```json
{
  "sources": {
    "AMD": [
      {
        "url": "https://ir.amd.com/",
        "title": "AMD Investor Relations",
        "source_type": "Company IR"
      }
    ],
    "*": [
      {
        "url": "https://www.usaspending.gov/",
        "title": "USAspending public procurement reference",
        "source_type": "Government Procurement"
      }
    ]
  }
}
```

Set `HEPHAESTUS_SOURCE_CONFIG=/path/to/source_urls.json` to use a different file.

## Evidence Quality

All AI-discovered edges remain `pending` until reviewed. Keep these rules when adding sources:

- Prefer official APIs, issuer documents, regulatory records, and curated source URLs.
- Treat news, blogs, and inferred job-posting evidence as supporting context, not approval-grade evidence by itself.
- Do not add sources that require scraping private pages, bypassing paywalls, or violating dataset terms.
- Add mocked unit tests for every new source parser or API payload formatter.
