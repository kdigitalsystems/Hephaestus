import sys
import json
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import export


def node(node_id, ticker):
    return SimpleNamespace(id=node_id, ticker=ticker)


def edge(source, target, dependency_type, product="", review_note=""):
    return SimpleNamespace(
        source_node=source,
        target_node=target,
        dependency_type=dependency_type,
        product=product,
        evidence_excerpt="",
        review_note=review_note,
    )


def test_tsm_foundry_edges_are_supplier_to_nvidia_and_amd():
    tsm = node(1, "TSM")
    nvda = node(2, "NVDA")
    amd = node(3, "AMD")

    reversed_nvda_edge = edge(
        nvda,
        tsm,
        "Advanced Silicon Fabrication",
        "semiconductor chips",
        "TSM is the manufacturer that produces semiconductor chips for NVIDIA.",
    )
    reversed_amd_edge = edge(
        amd,
        tsm,
        "Advanced Silicon Fabrication",
        "advanced process node chip fabrication",
        "TSMC manufactures chips for AMD.",
    )

    assert export.canonical_edge_nodes(reversed_nvda_edge) == (tsm, nvda)
    assert export.canonical_edge_nodes(reversed_amd_edge) == (tsm, amd)


def test_tsm_outsourced_production_edges_are_supplier_to_customer():
    tsm = node(1, "TSM")
    intel = node(2, "INTC")
    reversed_edge = edge(intel, tsm, "outsourced production", "advanced manufacturing services")

    assert export.canonical_edge_nodes(reversed_edge) == (tsm, intel)


def test_non_foundry_edges_keep_original_direction():
    tsm = node(1, "TSM")
    amd = node(2, "AMD")
    partnership_edge = edge(amd, tsm, "Technology Partnership", "joint development")

    assert export.canonical_edge_nodes(partnership_edge) == (amd, tsm)


def test_dashboard_data_shows_tsm_as_supplier_to_nvidia_and_amd():
    data = json.loads((ROOT / "docs/dashboard_data.json").read_text(encoding="utf-8"))
    companies = [company for sector in data["industries"].values() for company in sector]
    by_ticker = {company["ticker"]: company for company in companies}

    tsm = by_ticker["TSM"]
    amd = by_ticker["AMD"]
    nvda = by_ticker["NVDA"]

    assert any(edge["ticker"] == "AMD" for edge in tsm["downstream"])
    assert any(edge["ticker"] == "NVDA" for edge in tsm["downstream"])
    assert not any(edge["ticker"] in {"AMD", "NVDA"} for edge in tsm["upstream"])
    assert any(edge["ticker"] == "TSM" for edge in amd["upstream"])
    assert any(edge["ticker"] == "TSM" for edge in nvda["upstream"])
    assert not any(edge["ticker"] == "TSM" for edge in amd["downstream"])
    assert not any(edge["ticker"] == "TSM" for edge in nvda["downstream"])


def test_relationship_dedupe_keeps_one_entry_per_connected_ticker():
    duplicate_ai = {
        "edge_id": 20,
        "ticker": "TSM",
        "name": "Taiwan Semiconductor Manufacturing Company Ltd.",
        "type": "Foundry Services",
        "product": "foundry services",
        "confidence": 0.9,
        "source_type": "AI Research",
        "evidence_excerpt": "",
    }
    manual_seed = {
        "edge_id": 10,
        "ticker": "TSM",
        "name": "Taiwan Semiconductor Manufacturing Company Ltd.",
        "type": "Advanced Silicon Fabrication",
        "product": "Advanced process node chip fabrication",
        "confidence": 1.0,
        "source_type": "Manual",
        "evidence_excerpt": "",
    }

    deduped = export.dedupe_relationships([duplicate_ai, manual_seed])

    assert deduped == [manual_seed]


def test_dashboard_data_has_no_duplicate_supplier_or_buyer_tickers():
    data = json.loads((ROOT / "docs/dashboard_data.json").read_text(encoding="utf-8"))
    companies = [company for sector in data["industries"].values() for company in sector]

    for company in companies:
        for side in ("upstream", "downstream"):
            tickers = [edge["ticker"] for edge in company.get(side, [])]
            assert len(tickers) == len(set(tickers)), f"{company['ticker']} has duplicate {side} tickers"
