import json
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from write_status import build_status, write_status


DASHBOARD = {
    "generated_at": "2026-09-07T10:08:18+00:00",
    "investor_metrics": {
        "company_count": 3923,
        "unique_links": 313,
        "linked_companies": 319,
        "change_summary": {"new_count": 14, "removed_count": 0, "net_change": 14},
    },
}
DISCOVERY = {"generated_at": "2026-09-07T10:00:00+00:00", "companies_analyzed": 158, "deferred": 342, "extraction_failures": 7, "concentration_sweep": {"checked": 300, "created": 21}}


def test_build_status_combines_dashboard_and_discovery_summary():
    status = build_status(DASHBOARD, DISCOVERY, now=datetime(2026, 9, 7, 10, 9, tzinfo=timezone.utc))
    assert status["data_as_of"] == "2026-09-07T10:08:18+00:00"
    assert (status["unique_links"], status["new_links"], status["companies_researched"]) == (313, 14, 158)
    assert (status["filings_swept"], status["disclosures_found"], status["companies_deferred"]) == (300, 21, 342)
    assert status["generated_at"] == "2026-09-07T10:09:00+00:00"


def test_status_survives_a_missing_discovery_summary(tmp_path):
    dashboard_path = tmp_path / "dashboard_data.json"
    dashboard_path.write_text(json.dumps(DASHBOARD))
    status_path = tmp_path / "status.json"

    written = write_status(dashboard_path, tmp_path / "missing.json", status_path)

    assert written["companies_researched"] is None and written["unique_links"] == 313
    assert json.loads(status_path.read_text())["new_links"] == 14
    assert not (tmp_path / "status.json.tmp").exists()


def test_status_is_not_written_without_dashboard_data(tmp_path):
    assert write_status(tmp_path / "nope.json", tmp_path / "nope2.json", tmp_path / "status.json") is None
    assert not (tmp_path / "status.json").exists()


def test_a_stale_discovery_summary_is_ignored():
    stale = {**DISCOVERY, "generated_at": "2026-09-05T10:00:00+00:00"}
    status = build_status(DASHBOARD, stale, now=datetime(2026, 9, 7, 10, 9, tzinfo=timezone.utc))
    assert status["companies_researched"] is None and status["filings_swept"] is None
    unstamped = {k: v for k, v in DISCOVERY.items() if k != "generated_at"}
    assert build_status(DASHBOARD, unstamped, now=datetime(2026, 9, 7, 10, 9, tzinfo=timezone.utc))["companies_researched"] is None


# Yesterday's published status, as write_status() always reads it back from docs/status.json.
YESTERDAY = {
    "companies_researched": 89, "companies_deferred": 4, "extraction_failures": 2, "filings_swept": 1000,
    "disclosures_found": 12, "new_links": 3, "discovery": "ran", "discovery_at": "2026-09-06T10:00:00+00:00",
}
NOW = datetime(2026, 9, 7, 10, 9, tzinfo=timezone.utc)
DISCOVERY_NUMBERS = ("companies_researched", "companies_deferred", "extraction_failures", "filings_swept", "disclosures_found")


def test_a_stale_discovery_does_not_publish_yesterdays_numbers_as_todays():
    """A crashed discovery leaves the previous run's summary behind; with `previous` supplied the
    old code republished yesterday's counts under today's data_as_of."""
    stale = {**DISCOVERY, "generated_at": "2026-09-06T10:00:00+00:00"}

    status = build_status(DASHBOARD, stale, previous=YESTERDAY, now=NOW)

    assert all(status[key] is None for key in DISCOVERY_NUMBERS)
    assert status["discovery"] == "stale"
    # When discovery last finished, so the site can say so instead of staying silent.
    assert status["discovery_at"] == "2026-09-06T10:00:00+00:00"
    assert status["data_as_of"] == "2026-09-07T10:08:18+00:00" and status["new_links"] == 14


def test_a_run_without_a_discovery_step_keeps_the_banner_and_says_where_it_is_from():
    status = build_status(DASHBOARD, None, previous=YESTERDAY, now=NOW)

    assert (status["companies_researched"], status["filings_swept"], status["disclosures_found"]) == (89, 1000, 12)
    assert status["discovery"] == "not_run" and status["discovery_at"] == "2026-09-06T10:00:00+00:00"
    # new_links comes from today's dashboard, never from yesterday's status.
    assert status["new_links"] == 14

    # A second run without discovery does not make the numbers look newer, and the first
    # run after a stale day has nothing left to carry.
    again = build_status(DASHBOARD, None, previous=status, now=NOW)
    assert again["discovery_at"] == "2026-09-06T10:00:00+00:00" and again["companies_researched"] == 89
    blank = build_status(DASHBOARD, None, previous=build_status(DASHBOARD, {**DISCOVERY, "generated_at": "2026-09-01T00:00:00+00:00"}, previous=YESTERDAY, now=NOW), now=NOW)
    assert blank["companies_researched"] is None and blank["discovery"] == "not_run"


def test_a_fresh_summary_never_borrows_what_today_did_not_produce():
    # The filing sweep was switched off today: yesterday's 1,000 filings are not today's.
    no_sweep = {k: v for k, v in DISCOVERY.items() if k != "concentration_sweep"}

    status = build_status(DASHBOARD, no_sweep, previous=YESTERDAY, now=NOW)

    assert status["companies_researched"] == 158 and status["filings_swept"] is None and status["disclosures_found"] is None
    assert status["discovery"] == "ran" and status["discovery_at"] == "2026-09-07T10:00:00+00:00"


def test_write_status_reads_the_previous_status_but_not_a_stale_summary(tmp_path):
    dashboard_path = tmp_path / "dashboard_data.json"
    dashboard_path.write_text(json.dumps(DASHBOARD))
    status_path = tmp_path / "status.json"
    status_path.write_text(json.dumps(YESTERDAY))
    summary_path = tmp_path / "discovery_summary.json"
    summary_path.write_text(json.dumps({**DISCOVERY, "generated_at": "2026-09-06T10:00:00+00:00"}))

    written = write_status(dashboard_path, summary_path, status_path)

    assert written["companies_researched"] is None and written["discovery"] == "stale"
    assert json.loads(status_path.read_text())["filings_swept"] is None

    summary_path.unlink()
    status_path.write_text(json.dumps(YESTERDAY))
    assert write_status(dashboard_path, summary_path, status_path)["companies_researched"] == 89
