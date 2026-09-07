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
