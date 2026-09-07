"""Publish a small run-status file for the site.

The homepage shows "Updated <date> - N companies researched - M new links", which
turns the pipeline log into a trust signal for visitors and an alarm for the
operator when a day is missed. Inputs are the discovery summary written by
auto_discover_edges.py and the published dashboard data.
"""

import json
import os
import sys
from datetime import datetime, timezone

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DASHBOARD_PATH = os.path.join(BASE_DIR, "docs", "dashboard_data.json")
DEFAULT_SUMMARY_PATH = os.path.join(BASE_DIR, "reports", "discovery_summary.json")
DEFAULT_STATUS_PATH = os.path.join(BASE_DIR, "docs", "status.json")


def load_json(path):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


# The job's own timeout is four hours, so anything older than this belongs to an
# earlier run: a 24-hour window would still accept yesterday's summary.
MAX_SUMMARY_AGE_HOURS = 6


def fresh_discovery(discovery, now):
    """Only a summary written in this run's window counts; an older one is a crashed
    discovery step and must not publish yesterday's numbers as today's."""
    if not discovery:
        return {}
    stamp = discovery.get("generated_at")
    try:
        written = datetime.fromisoformat(str(stamp))
    except (TypeError, ValueError):
        return {}
    if written.tzinfo is None:
        written = written.replace(tzinfo=timezone.utc)
    age_hours = (now - written).total_seconds() / 3600
    return discovery if 0 <= age_hours <= MAX_SUMMARY_AGE_HOURS else {}


def build_status(dashboard, discovery=None, now=None):
    dashboard = dashboard or {}
    now = now or datetime.now(timezone.utc)
    metrics = dashboard.get("investor_metrics") or {}
    change = metrics.get("change_summary") or {}
    discovery = fresh_discovery(discovery, now)
    sweep = discovery.get("concentration_sweep") or {}
    return {
        "generated_at": now.isoformat(timespec="seconds"),
        "data_as_of": dashboard.get("generated_at"),
        "companies": metrics.get("company_count"),
        "unique_links": metrics.get("unique_links"),
        "linked_companies": metrics.get("linked_companies"),
        "new_links": change.get("new_count"),
        "removed_links": change.get("removed_count"),
        "companies_researched": discovery.get("companies_analyzed"),
        "companies_deferred": discovery.get("deferred"),
        "extraction_failures": discovery.get("extraction_failures"),
        "filings_swept": sweep.get("checked"),
        "disclosures_found": sweep.get("created"),
    }


def write_status(dashboard_path=DEFAULT_DASHBOARD_PATH, summary_path=DEFAULT_SUMMARY_PATH, status_path=DEFAULT_STATUS_PATH):
    dashboard = load_json(dashboard_path)
    if not dashboard:
        print(f"Dashboard data not found at {dashboard_path}; status not written.", file=sys.stderr)
        return None
    status = build_status(dashboard, load_json(summary_path))
    tmp_path = f"{status_path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as handle:
        json.dump(status, handle, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(tmp_path, status_path)
    print(f"Run status written to {status_path}: {status['companies_researched']} researched, {status['new_links']} new links.")
    return status


if __name__ == "__main__":
    raise SystemExit(0 if write_status() else 1)
