"""Split the published dashboard into a light homepage file and detail shards.

docs/dashboard_data.json stays the complete record for downloads, static pages
and validators. The homepage loads docs/dashboard_lite.json, which is the same
structure minus the two fields that made up two thirds of the bytes (business
summaries and per-company investor metrics); those live in
docs/company-data/<first letter>.json and are fetched when a brief opens.
"""

import json
import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS_DIR = os.path.join(BASE_DIR, "docs")
DEFAULT_DASHBOARD_PATH = os.path.join(DOCS_DIR, "dashboard_data.json")
DEFAULT_LITE_PATH = os.path.join(DOCS_DIR, "dashboard_lite.json")
DEFAULT_SHARD_DIR = os.path.join(DOCS_DIR, "company-data")
DETAIL_FIELDS = ("summary", "investor_metrics")


def has_links(company):
    return bool(company.get("upstream") or company.get("downstream"))


def detail_fields_for(company):
    """Summaries always move; investor metrics stay for linked companies because the
    sector and exposure views order by their risk scores, and are all zeros otherwise."""
    return DETAIL_FIELDS[:1] if has_links(company) else DETAIL_FIELDS


def shard_key(ticker):
    first = str(ticker or "").strip().upper()[:1]
    return first if first.isalnum() else "_"


def split_dashboard(dashboard):
    """Return (lite dashboard, {shard: {ticker: detail fields}})."""
    lite = {key: value for key, value in dashboard.items() if key != "industries"}
    lite["industries"] = {}
    shards = {}
    for sector, companies in (dashboard.get("industries") or {}).items():
        slim_companies = []
        for company in companies:
            moving = detail_fields_for(company)
            slim = {key: value for key, value in company.items() if key not in moving}
            detail = {key: company[key] for key in moving if key in company}
            ticker = company.get("ticker")
            if ticker and detail:
                shards.setdefault(shard_key(ticker), {})[ticker] = detail
            slim_companies.append(slim)
        lite["industries"][sector] = slim_companies
    return lite, shards


def write_json(path, payload):
    tmp_path = f"{path}.tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, separators=(",", ":"), allow_nan=False)
        os.replace(tmp_path, path)
    except BaseException:
        # A killed or failed write must not leave a .tmp file for `git add -A`.
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


def write_split(dashboard_path=DEFAULT_DASHBOARD_PATH, lite_path=DEFAULT_LITE_PATH, shard_dir=DEFAULT_SHARD_DIR):
    with open(dashboard_path, encoding="utf-8") as handle:
        dashboard = json.load(handle)
    lite, shards = split_dashboard(dashboard)
    os.makedirs(shard_dir, exist_ok=True)
    write_json(lite_path, lite)
    written = set()
    for key, payload in sorted(shards.items()):
        name = f"{key}.json"
        write_json(os.path.join(shard_dir, name), payload)
        written.add(name)
    removed = 0
    for name in os.listdir(shard_dir):
        if name.endswith(".tmp"):
            os.remove(os.path.join(shard_dir, name))
            removed += 1
        elif name.endswith(".json") and name not in written:
            os.remove(os.path.join(shard_dir, name))
            removed += 1
    lite_size = os.path.getsize(lite_path)
    full_size = os.path.getsize(dashboard_path)
    print(
        f"Dashboard split: lite {lite_size / 1e6:.1f}MB from {full_size / 1e6:.1f}MB, "
        f"{len(written)} detail shard(s) written, {removed} stale removed -> {lite_path}, {shard_dir}"
    )
    return {"lite_bytes": lite_size, "full_bytes": full_size, "shards": len(written), "removed": removed}


if __name__ == "__main__":
    try:
        write_split()
    except (OSError, ValueError) as exc:
        print(f"Dashboard split failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
