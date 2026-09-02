import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import generate_static_pages as pages


def link(ticker, name, kind="Foundry", **extra):
    return {
        "edge_id": 1,
        "relationship_key": f"X->{ticker}:{kind.upper()}",
        "name": name,
        "ticker": ticker,
        "type": kind,
        "product": "wafers",
        "source": "AI Multi-Source Research",
        "source_type": "AI Research",
        "review_status": "approved",
        "review_summary": {"method": "consensus", "label": "Consensus panel 3/3 models"},
        "evidence_excerpt": "TSMC fabricates AMD's <processors> & sells them.",
        **extra,
    }


DASHBOARD = {
    "generated_at": "2026-09-02T05:00:00+00:00",
    "industries": {
        "Technology": [
            {"id": 1, "name": "Advanced Micro Devices, Inc. Common Stock", "ticker": "AMD", "industry": "Semiconductors", "market_cap": 2.5e11,
             "upstream": [link("TSM", "Taiwan Semiconductor <Manufacturing>", source="https://www.sec.gov/Archives/edgar/data/1/10k.htm", source_title="SEC EDGAR (10-K filed 2025-10-31)", revenue_share=12.5)],
             "downstream": []},
            {"id": 2, "name": "Taiwan Semiconductor <Manufacturing>", "ticker": "TSM", "industry": "Semiconductors", "market_cap": None,
             "upstream": [], "downstream": [link("AMD", "Advanced Micro Devices, Inc. Common Stock", revenue_share=12.5)]},
            {"id": 3, "name": "Lonely Corp", "ticker": "LNLY", "industry": "Software", "market_cap": 1e9, "upstream": [], "downstream": []},
        ]
    },
}


def test_static_pages_are_generated_for_linked_companies_only(tmp_path):
    dashboard_path = tmp_path / "dashboard_data.json"
    dashboard_path.write_text(json.dumps(DASHBOARD), encoding="utf-8")
    output = tmp_path / "company"
    output.mkdir()
    (output / "GONE.html").write_text("stale", encoding="utf-8")
    sitemap = tmp_path / "sitemap.xml"

    result = pages.generate_static_pages(dashboard_path, output, sitemap)

    assert result == {"pages": 2, "removed": 1, "generated_on": "2026-09-02"}
    assert sorted(path.name for path in output.iterdir()) == ["AMD.html", "TSM.html", "index.html"]

    amd = (output / "AMD.html").read_text(encoding="utf-8")
    assert "<title>Advanced Micro Devices, Inc. (AMD) suppliers and customers | Hephaestus</title>" in amd
    assert 'rel="canonical" href="https://kdigitalsystems.github.io/Hephaestus/company/AMD.html"' in amd
    assert '<a href="TSM.html">Taiwan Semiconductor &lt;Manufacturing&gt; (TSM)</a>' in amd
    assert "&lt;processors&gt; &amp; sells" in amd and "<processors>" not in amd
    assert "Consensus panel 3/3 models" in amd
    assert "12.5% of TSM revenue" in amd
    assert 'href="https://www.sec.gov/Archives/edgar/data/1/10k.htm"' in amd and "SEC EDGAR (10-K filed 2025-10-31)" in amd
    assert "Market cap: $250.0B" in amd
    assert "Data as of 2026-09-02" in amd
    assert '"@type": "Organization"' in amd

    tsm = (output / "TSM.html").read_text(encoding="utf-8")
    assert "12.5% of revenue" in tsm
    assert "Market cap" not in tsm
    assert "Customers include Advanced Micro Devices, Inc." in tsm and "Inc.." not in tsm

    assert pages.meaningful("Uncategorized") == "" and pages.meaningful("Linked Companies") == "" and pages.meaningful("Semiconductors") == "Semiconductors"

    index = (output / "index.html").read_text(encoding="utf-8")
    assert 'href="AMD.html"' in index and 'href="TSM.html"' in index and "LNLY" not in index

    root = ET.parse(sitemap).getroot()
    namespace = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    locations = [url.find("sm:loc", namespace).text for url in root.findall("sm:url", namespace)]
    assert locations == [
        "https://kdigitalsystems.github.io/Hephaestus/",
        "https://kdigitalsystems.github.io/Hephaestus/methodology.html",
        "https://kdigitalsystems.github.io/Hephaestus/company/index.html",
        "https://kdigitalsystems.github.io/Hephaestus/company/AMD.html",
        "https://kdigitalsystems.github.io/Hephaestus/company/TSM.html",
    ]


def test_static_pages_are_deterministic(tmp_path):
    dashboard_path = tmp_path / "dashboard_data.json"
    dashboard_path.write_text(json.dumps(DASHBOARD), encoding="utf-8")
    output = tmp_path / "company"
    sitemap = tmp_path / "sitemap.xml"

    pages.generate_static_pages(dashboard_path, output, sitemap)
    first = {path.name: path.read_bytes() for path in output.iterdir()} | {"sitemap": sitemap.read_bytes()}
    pages.generate_static_pages(dashboard_path, output, sitemap)
    second = {path.name: path.read_bytes() for path in output.iterdir()} | {"sitemap": sitemap.read_bytes()}

    assert first == second
    assert not [path for path in tmp_path.rglob("*.tmp")]


def test_page_filenames_are_safe():
    assert pages.page_filename("brk.b") == "BRK_B.html"
    assert pages.page_filename("BF-B") == "BF-B.html"
    assert pages.page_filename("../x") == "___X.html"
