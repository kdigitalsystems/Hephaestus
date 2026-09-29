# Review-page decisions

Each JSON file here is one review session from `docs/review.html`
(https://kdigitalsystems.github.io/Hephaestus/review.html). The page opens it as a
pull request; once merged, the next pipeline run applies it with
`backend/apply_human_review.py` as a human verdict and records it in
`data/edge_review_decisions.json`.

```json
{
  "reviewed_on": "2026-09-29",
  "decisions": [
    {"edge_id": 2981, "source_ticker": "SNX", "target_ticker": "AAPL", "type": "Supply Chain", "action": "reverse", "note": ""}
  ]
}
```

`action` is `approve`, `reject` or `reverse`. Applying a file twice changes nothing,
and files apply in name order, so a later session overrides an earlier one. CI checks
every file with `python3 backend/apply_human_review.py --check`.
