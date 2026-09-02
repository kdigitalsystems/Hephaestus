import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import export
import generate_change_feed as feed


def link(key, status="approved", confidence=0.9):
    source, rest = key.split("->", 1)
    target, kind = rest.split(":", 1)
    return {
        "relationship_key": key,
        "source_ticker": source,
        "target_ticker": target,
        "type": kind.title(),
        "product": "wafers" if kind == "FOUNDRY" else kind.title(),
        "review_status": status,
        "confidence": confidence,
    }


def entry(day, keys, **overrides):
    links = {key: link(key) for key in keys}
    for key, value in overrides.items():
        links[key] = value
    return {
        "generated_at": f"{day}T05:00:00+00:00",
        "generated_on": day,
        "unique_links": len(links),
        "approved_links": len(links),
        "pending_links": 0,
        "linked_companies": len(links) + 1,
        "links": links,
    }


HISTORY = [
    entry("2026-08-30", ["TSM->AMD:FOUNDRY", "MU->NVDA:MEMORY"]),
    entry("2026-08-31", ["TSM->AMD:FOUNDRY", "MU->NVDA:MEMORY"]),
    entry("2026-09-01", ["TSM->AMD:FOUNDRY", "MU->NVDA:MEMORY", "ASML->TSM:EQUIPMENT"], **{"MU->NVDA:MEMORY": link("MU->NVDA:MEMORY", confidence=0.95)}),
    entry("2026-09-02", ["TSM->AMD:FOUNDRY", "ASML->TSM:EQUIPMENT"], **{"MU->NVDA:MEMORY": link("MU->NVDA:MEMORY", confidence=0.95)}),
]
# 09-02 dropped nothing (MU->NVDA re-added via override); make 09-02 remove TSM->AMD instead
HISTORY[-1]["links"].pop("TSM->AMD:FOUNDRY")
HISTORY[-1]["unique_links"] = len(HISTORY[-1]["links"])


def test_daily_changes_are_newest_first_with_correct_diffs():
    days = feed.daily_changes(HISTORY)

    assert [day["date"] for day in days] == ["2026-09-02", "2026-09-01", "2026-08-31"]
    latest = days[0]
    assert latest["previous_date"] == "2026-09-01"
    assert latest["removed_count"] == 1 and latest["removed_links"][0]["relationship_key"] == "TSM->AMD:FOUNDRY"
    assert latest["new_count"] == 0 and latest["net_change"] == -1
    added = days[1]
    assert added["new_count"] == 1 and added["new_links"][0]["relationship_key"] == "ASML->TSM:EQUIPMENT"
    assert added["changed_count"] == 1 and added["changed_links"][0]["relationship_key"] == "MU->NVDA:MEMORY"
    quiet = days[2]
    assert not (quiet["new_count"] or quiet["removed_count"] or quiet["changed_count"])


def test_rss_is_well_formed_and_skips_quiet_days():
    payload = feed.build_changes_payload(HISTORY)
    rss = feed.build_rss(payload)

    root = ET.fromstring(rss)
    items = root.findall("./channel/item")
    assert [item.findtext("title")[:10] for item in items] == ["2026-09-02", "2026-09-01"]
    assert all(item.findtext("pubDate").endswith("+0000") for item in items)
    guids = [item.findtext("guid") for item in items]
    assert len(guids) == len(set(guids))
    description = items[1].findtext("description")
    assert "ASML" in description and "#company?ticker=TSM" in description
    assert "&lt;li&gt;" not in description  # description is HTML, escaped once, not twice


def test_generate_change_feed_writes_deterministic_files(tmp_path):
    history_path = tmp_path / "link_history.json"
    history_path.write_text(json.dumps(HISTORY), encoding="utf-8")
    changes_path = tmp_path / "changes.json"
    feed_path = tmp_path / "feed.xml"

    feed.generate_change_feed(history_path, changes_path, feed_path)
    first = (changes_path.read_bytes(), feed_path.read_bytes())
    feed.generate_change_feed(history_path, changes_path, feed_path)

    assert (changes_path.read_bytes(), feed_path.read_bytes()) == first
    payload = json.loads(changes_path.read_text(encoding="utf-8"))
    assert payload["generated_at"] == "2026-09-02T05:00:00+00:00"
    assert payload["unique_links"] == 2
    assert payload["days"][0]["date"] == "2026-09-02"
    assert not [path for path in tmp_path.iterdir() if path.suffix == ".tmp"]


def test_change_summary_records_the_dates_it_compares():
    history = [{"generated_on": "2026-09-01", "links": {"A->B:X": {"review_status": "approved"}}}]

    summary = export.build_change_summary(history, {"A->B:X": {"review_status": "approved"}}, generated_on="2026-09-02")

    assert summary["previous_generated_on"] == "2026-09-01"
    assert summary["current_generated_on"] == "2026-09-02"
