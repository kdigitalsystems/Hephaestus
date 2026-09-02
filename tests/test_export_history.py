import json
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import export


def snapshot(keys):
    return {
        key: {"relationship_key": key, "review_status": "approved", "confidence": 0.9}
        for key in keys
    }


def relationship(key, ticker, name):
    return {
        "edge_id": 1,
        "relationship_key": key,
        "name": name,
        "ticker": ticker,
        "type": "Foundry",
        "product": "wafers",
        "confidence": 0.9,
        "source": "Manual System Jumpstart",
        "source_title": "Manual System Jumpstart",
        "source_type": "Manual",
        "review_status": "approved",
        "evidence_excerpt": "",
        "last_verified": "2026-08-28",
    }


def company(company_id, ticker, name, upstream=(), downstream=()):
    return {
        "id": company_id,
        "name": name,
        "ticker": ticker,
        "industry": "Semiconductors",
        "price": 100,
        "change": 0,
        "market_cap": 1_000_000_000,
        "upstream": list(upstream),
        "downstream": list(downstream),
    }


def test_change_summary_compares_against_the_previous_day_not_the_same_run():
    history = [
        {"generated_on": "2026-08-28", "links": snapshot(["A->B:X", "C->D:Y"])},
        # Export already wrote today's intermediate snapshot before repair ran.
        {"generated_on": "2026-08-29", "links": snapshot(["A->B:X"])},
    ]

    summary = export.build_change_summary(history, snapshot(["A->B:X", "C->D:Y"]), generated_on="2026-08-29")

    assert summary["previous_unique_links"] == 2
    assert summary["new_count"] == 0
    assert summary["removed_count"] == 0
    assert summary["net_change"] == 0


def test_change_summary_without_a_date_uses_the_latest_entry():
    history = [{"generated_on": "2026-08-28", "links": snapshot(["A->B:X"])}]

    summary = export.build_change_summary(history, snapshot(["A->B:X", "C->D:Y"]))

    assert summary["previous_unique_links"] == 1
    assert summary["new_count"] == 1


def test_annotated_history_series_ends_with_the_current_run(tmp_path):
    history_path = tmp_path / "link_history.json"
    history_path.write_text(json.dumps([
        {
            "generated_at": "2026-08-28T05:00:00+00:00",
            "generated_on": "2026-08-28",
            "unique_links": 1,
            "approved_links": 1,
            "pending_links": 0,
            "linked_companies": 2,
            "links": snapshot(["TSM->AMD:FOUNDRY"]),
        },
        {
            "generated_at": f"{datetime.now(timezone.utc).date().isoformat()}T03:00:00+00:00",
            "generated_on": datetime.now(timezone.utc).date().isoformat(),
            "unique_links": 0,
            "approved_links": 0,
            "pending_links": 0,
            "linked_companies": 0,
            "links": {},
        },
    ]), encoding="utf-8")
    dashboard = {
        "industries": {
            "Technology": [
                company(1, "TSM", "TSMC", downstream=[relationship("TSM->AMD:FOUNDRY", "AMD", "AMD")]),
                company(2, "AMD", "AMD", upstream=[relationship("TSM->AMD:FOUNDRY", "TSM", "TSMC")]),
            ]
        }
    }

    export.annotate_dashboard_data(dashboard, str(history_path))

    metrics = dashboard["investor_metrics"]
    generated_on = dashboard["generated_at"][:10]
    assert metrics["unique_links"] == 1
    assert metrics["change_summary"]["previous_unique_links"] == 1
    assert metrics["change_summary"]["net_change"] == 0
    assert [entry["generated_on"] for entry in metrics["history"]] == ["2026-08-28", generated_on]
    assert metrics["history"][-1]["unique_links"] == 1
    assert metrics["history"][-1]["linked_companies"] == 2
    assert metrics["history"][-1]["rejected_links"] == 0


def test_publish_dashboard_writes_dashboard_then_history_without_temp_files(tmp_path):
    dashboard_path = tmp_path / "dashboard_data.json"
    history_path = tmp_path / "link_history.json"
    dashboard = {
        "industries": {
            "Technology": [
                company(1, "TSM", "TSMC", downstream=[relationship("TSM->AMD:FOUNDRY", "AMD", "AMD")]),
                company(2, "AMD", "AMD", upstream=[relationship("TSM->AMD:FOUNDRY", "TSM", "TSMC")]),
            ]
        },
        "quality": {"pending_count": 0, "approved_count": 1, "rejected_count": 0, "review_queue": []},
    }
    export.annotate_dashboard_data(dashboard, str(history_path))

    published = export.publish_dashboard(dashboard, str(dashboard_path), str(history_path))

    written = json.loads(dashboard_path.read_text(encoding="utf-8"))
    history = json.loads(history_path.read_text(encoding="utf-8"))
    assert "relationship_snapshot" not in written["investor_metrics"]
    assert "relationship_snapshot" not in published["investor_metrics"]
    assert history[-1]["unique_links"] == 1
    assert set(history[-1]["links"]) == {"TSM->AMD:FOUNDRY"}
    assert not [path for path in tmp_path.iterdir() if path.suffix == ".tmp"]


def test_write_json_atomic_leaves_existing_file_intact_on_failure(tmp_path):
    target = tmp_path / "data.json"
    target.write_text('{"ok": true}\n', encoding="utf-8")

    try:
        export.write_json_atomic(str(target), {"bad": float("nan")})
    except ValueError:
        pass
    else:
        raise AssertionError("NaN must be rejected")

    assert json.loads(target.read_text(encoding="utf-8")) == {"ok": True}
    assert not [path for path in tmp_path.iterdir() if path.suffix == ".tmp"]


def test_merged_rows_keep_key_and_edge_id_from_the_same_member_and_fold_case_duplicates():
    manual = {"edge_id": 10, "ticker": "TSM", "name": "TSMC", "type": "Advanced Silicon Fabrication", "product": "chips",
              "confidence": 1.0, "source_type": "Manual", "review_status": "approved", "evidence_excerpt": "",
              "relationship_key": "TSM->AMD:ADVANCED SILICON FABRICATION"}
    ai = {"edge_id": 3, "ticker": "TSM", "name": "TSMC", "type": "foundry services", "product": "wafers",
          "confidence": 0.9, "source_type": "AI Research", "review_status": "approved", "evidence_excerpt": "x",
          "relationship_key": "TSM->AMD:FOUNDRY SERVICES"}
    duplicate_case = dict(ai, edge_id=4, type="Foundry Services", relationship_key="TSM->AMD:FOUNDRY SERVICES")

    merged = export.merge_relationships([ai, manual, duplicate_case])[0]

    assert merged["edge_id"] == 10
    assert merged["relationship_key"] == "TSM->AMD:ADVANCED SILICON FABRICATION"
    assert merged["type"] == "Advanced Silicon Fabrication / foundry services"


def test_rejected_members_never_contribute_to_a_merged_row():
    approved = {"edge_id": 1, "ticker": "TSM", "name": "TSMC", "type": "Foundry", "product": "wafers", "confidence": 0.9,
                "source_type": "AI Research", "review_status": "approved", "evidence_excerpt": "", "relationship_key": "TSM->AMD:FOUNDRY"}
    rejected = {"edge_id": 2, "ticker": "TSM", "name": "TSMC", "type": "Competitor", "product": "x", "confidence": 1.0,
                "source_type": "Manual", "review_status": "rejected", "evidence_excerpt": "", "relationship_key": "TSM->AMD:COMPETITOR"}

    merged = export.merge_relationships([approved, rejected])[0]

    assert merged["review_status"] == "approved"
    assert merged["type"] == "Foundry"


def test_placeholders_are_never_published_as_values():
    assert export.displayable("None") == "N/A"
    assert export.displayable("none") == "N/A"
    assert export.displayable("Uncategorized") == "N/A"
    assert export.displayable(None) == "N/A"
    assert export.displayable("Buy") == "Buy"


def test_merge_relationship_group_tolerates_missing_edge_ids():
    merged = export.merge_relationships([
        {
            "ticker": "TSM",
            "name": "TSMC",
            "type": "Foundry",
            "product": "wafers",
            "confidence": 0.9,
            "source_type": "AI Research",
            "evidence_excerpt": "",
        }
    ])

    assert merged[0]["edge_id"] is None
    assert merged[0]["ticker"] == "TSM"
