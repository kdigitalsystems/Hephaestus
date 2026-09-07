import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from split_dashboard import shard_key, split_dashboard, write_split


DASHBOARD = {
    "generated_at": "2026-09-07T10:08:18+00:00",
    "quality": {"pending_count": 0},
    "investor_metrics": {"unique_links": 2},
    "industries": {
        "Technology": [
            {"ticker": "AAPL", "name": "Apple", "price": 1.0, "summary": "Designs phones.", "investor_metrics": {"total_links": 2}, "upstream": [{"ticker": "TSM"}]},
            {"ticker": "9988", "name": "Alibaba", "summary": "Commerce."},
            {"ticker": "ZZZ", "name": "Zed", "summary": "Unlinked.", "investor_metrics": {"total_links": 0}},
        ],
        "Energy": [{"ticker": "XOM", "name": "Exxon"}],
    },
}


def test_split_moves_only_detail_fields_and_keeps_structure():
    lite, shards = split_dashboard(DASHBOARD)

    assert lite["generated_at"] == DASHBOARD["generated_at"] and lite["investor_metrics"] == {"unique_links": 2}
    apple, _alibaba, zed = lite["industries"]["Technology"]
    # A linked company keeps its investor metrics (risk ordering in sector/exposure views).
    assert "summary" not in apple and apple["investor_metrics"] == {"total_links": 2}
    assert apple["upstream"] == [{"ticker": "TSM"}] and apple["price"] == 1.0
    # An unlinked company's all-zero metrics move with its summary.
    assert zed == {"ticker": "ZZZ", "name": "Zed"}
    assert lite["industries"]["Energy"] == [{"ticker": "XOM", "name": "Exxon"}]
    assert shards == {
        "A": {"AAPL": {"summary": "Designs phones."}},
        "9": {"9988": {"summary": "Commerce."}},
        "Z": {"ZZZ": {"summary": "Unlinked.", "investor_metrics": {"total_links": 0}}},
    }
    assert shard_key("$weird") == "_" and shard_key(None) == "_"


def test_write_split_writes_lite_and_shards_and_removes_stale_shards(tmp_path):
    dashboard_path = tmp_path / "dashboard_data.json"
    dashboard_path.write_text(json.dumps(DASHBOARD))
    shard_dir = tmp_path / "company-data"
    shard_dir.mkdir()
    (shard_dir / "Q.json").write_text("{}")

    result = write_split(dashboard_path, tmp_path / "dashboard_lite.json", shard_dir)

    assert result["shards"] == 3 and result["removed"] == 1
    assert sorted(p.name for p in shard_dir.iterdir()) == ["9.json", "A.json", "Z.json"]
    assert json.loads((shard_dir / "A.json").read_text())["AAPL"]["summary"] == "Designs phones."
    lite = json.loads((tmp_path / "dashboard_lite.json").read_text())
    assert "summary" not in lite["industries"]["Technology"][0]
    assert result["lite_bytes"] < result["full_bytes"]
    assert not (tmp_path / "dashboard_lite.json.tmp").exists()
