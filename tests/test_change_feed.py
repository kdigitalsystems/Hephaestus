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


# --- a pair that stays published under a different type ---------------------------------

def flip_days():
    """2026-09-17 -> 09-18 on the real history: UMC -> NVDA stayed published while a second
    type overtook the first, which changed its relationship_key."""
    before = entry("2026-09-17", ["UMC->NVDA:MANUFACTURING SERVICES", "TSM->AMD:FOUNDRY", "MU->NVDA:MEMORY"])
    after = entry("2026-09-18", ["UMC->NVDA:SUPPLY RELATIONSHIP", "TSM->AMD:FOUNDRY", "ASML->TSM:EQUIPMENT"])
    after["links"]["UMC->NVDA:SUPPLY RELATIONSHIP"]["type"] = "Supply Relationship / Manufacturing Services"
    before["links"]["UMC->NVDA:MANUFACTURING SERVICES"]["type"] = "Manufacturing Services"
    return before, after


def test_a_retyped_pair_is_updated_not_removed_and_new():
    before, after = flip_days()

    day = feed.diff_snapshots(before, after)

    # MU->NVDA really left and ASML->TSM really arrived; UMC->NVDA never stopped being published.
    assert [item["relationship_key"] for item in day["new_links"]] == ["ASML->TSM:EQUIPMENT"]
    assert [item["relationship_key"] for item in day["removed_links"]] == ["MU->NVDA:MEMORY"]
    assert (day["new_count"], day["removed_count"], day["changed_count"]) == (1, 1, 1)
    updated = day["changed_links"][0]
    assert updated["relationship_key"] == "UMC->NVDA:SUPPLY RELATIONSHIP"
    assert updated["previous_type"] == "Manufacturing Services"
    assert day["net_change"] == 0


def test_the_homepage_change_summary_counts_a_retyped_pair_once():
    before, after = flip_days()

    summary = export.build_change_summary([before], after["links"], generated_on="2026-09-18")

    assert (summary["new_count"], summary["removed_count"], summary["changed_count"]) == (1, 1, 1)
    assert [item["relationship_key"] for item in summary["changed_links"]] == ["UMC->NVDA:SUPPLY RELATIONSHIP"]


def test_status_and_feed_carry_the_corrected_counts():
    from write_status import build_status

    before, after = flip_days()
    summary = export.build_change_summary([before], after["links"], generated_on="2026-09-18")
    status = build_status({"investor_metrics": {"change_summary": summary}})
    assert (status["new_links"], status["removed_links"]) == (1, 1)

    rss = feed.build_rss(feed.build_changes_payload([before, after]))
    root = ET.fromstring(rss)
    assert root.find("./channel/item/title").text == "2026-09-18: 1 new, 1 removed, 1 updated supply links (3 total)"
    assert "was Manufacturing Services" in root.find("./channel/item/description").text


def test_a_pair_that_really_goes_or_arrives_is_still_removed_or_new():
    gone = feed.diff_snapshots(entry("a", ["A->B:X", "C->D:Y"]), entry("b", ["C->D:Y"]))
    assert (gone["new_count"], gone["removed_count"], gone["changed_count"]) == (0, 1, 0)
    # The reverse direction is a different pair: B->A is new and A->B is removed.
    swapped = feed.diff_snapshots(entry("a", ["A->B:X"]), entry("b", ["B->A:X"]))
    assert (swapped["new_count"], swapped["removed_count"], swapped["changed_count"]) == (1, 1, 0)
    # Keys with no direction in them keep the old identity rule.
    legacy = feed.diff_snapshots({"links": {"41": {}, "42": {}}}, {"links": {"42": {}, "43": {}}})
    assert (legacy["new_count"], legacy["removed_count"], legacy["changed_count"]) == (1, 1, 0)


def test_replaying_the_real_history_loses_only_the_type_flips():
    history = [item for item in export.load_link_history(str(ROOT / "docs" / "link_history.json")) if isinstance(item.get("links"), dict)]
    assert len(history) > 1
    for previous, current in zip(history, history[1:]):
        old_new = set(current["links"]) - set(previous["links"])
        old_removed = set(previous["links"]) - set(current["links"])
        flips = {export.link_pair(key) for key in old_removed} & {export.link_pair(key) for key in old_new}
        day = feed.diff_snapshots(previous, current)

        new_pairs = {export.link_pair(item["relationship_key"]) for item in day["new_links"]}
        removed_pairs = {export.link_pair(item["relationship_key"]) for item in day["removed_links"]}
        # No pair is both added and dropped on one day, and every genuine add or drop survives.
        assert not new_pairs & removed_pairs
        assert day["new_count"] == len(old_new) - len(flips)
        assert day["removed_count"] == len(old_removed) - len(flips)
        assert day["net_change"] == len(current["links"]) - len(previous["links"])


# --- feed.xml must stay valid XML -------------------------------------------------------

def test_control_characters_in_a_product_do_not_break_the_feed():
    bad = entry("2026-09-02", ["ASML->TSM:EQUIPMENT"])
    bad["links"]["ASML->TSM:EQUIPMENT"]["product"] = "EUV\x0b lithography\x0c tools\x00￾"
    bad["links"]["ASML->TSM:EQUIPMENT"]["type"] = "Equip\x1fment"
    rss = feed.build_rss(feed.build_changes_payload([entry("2026-09-01", []), bad]))

    root = ET.fromstring(rss.encode("utf-8"))  # raised ParseError on the old escape()

    assert "EUV" in root.find("./channel/item/description").text
    assert "\x0b" not in rss and "\x0c" not in rss and "\x00" not in rss and "\x1f" not in rss
