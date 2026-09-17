"""Publishing defects found by auditing the pipeline end to end."""

import json
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


def test_a_killed_run_does_not_wedge_every_later_run(tmp_path):
    """pathlib's "*" matches dotfiles, so the orphan temp was listed twice and the second
    unlink raised, killing the step before the sitemap and failing every run after it."""
    from generate_static_pages import generate_static_pages

    dashboard = tmp_path / "dashboard_data.json"
    dashboard.write_text(json.dumps({
        "generated_at": "2026-09-17T08:00:00+00:00",
        "industries": {"Technology": [
            {"ticker": "AMD", "name": "Advanced Micro Devices, Inc.", "market_cap": 2.5e11, "industry": "Semiconductors",
             "upstream": [{"ticker": "TSM", "name": "Taiwan Semiconductor", "type": "Foundry", "product": "wafers",
                           "review_status": "approved", "source": "https://www.sec.gov/x.htm", "source_title": "SEC EDGAR",
                           "evidence_excerpt": "TSMC fabricates processors for AMD."}],
             "downstream": []},
        ]},
    }))
    pages = tmp_path / "company"
    pages.mkdir()
    (pages / ".AMD.html.k3x9.tmp").write_text("half-written")  # what a killed run leaves behind
    (pages / "GONE.html").write_text("a company that lost its last relationship")

    result = generate_static_pages(dashboard, pages, tmp_path / "sitemap.xml")

    assert (tmp_path / "sitemap.xml").exists()
    assert not list(pages.glob("*.tmp")) and not list(pages.glob(".*.tmp"))
    assert not (pages / "GONE.html").exists()
    assert result["pages"] == 1


def test_the_status_banner_keeps_the_last_numbers_when_a_run_has_no_discovery():
    """run_pipeline.sh has no discovery step, and publishing nulls blanked the banner."""
    from write_status import build_status

    dashboard = {"generated_at": "2026-09-17T08:00:00+00:00", "investor_metrics": {"company_count": 3938, "unique_links": 509, "linked_companies": 501, "change_summary": {"new_count": 4, "removed_count": 0}}}
    previous = {"companies_researched": 132, "companies_deferred": 0, "extraction_failures": 5, "filings_swept": 55, "disclosures_found": 7}

    status = build_status(dashboard, discovery=None, previous=previous)

    assert status["companies_researched"] == 132 and status["filings_swept"] == 55
    assert status["unique_links"] == 509  # this run's own numbers still win
    fresh = build_status(dashboard, discovery={"generated_at": status["generated_at"], "companies_analyzed": 7, "deferred": 0, "concentration_sweep": {"checked": 2, "created": 1}}, previous=previous)
    assert fresh["companies_researched"] == 7 and fresh["filings_swept"] == 2


def test_tied_companies_are_ordered_by_ticker_not_by_row_order(tmp_path):
    """16 companies tied at the last slot; the cut was decided by SQLite row order."""
    from export import annotate_dashboard_data

    def company(ticker):
        return {"ticker": ticker, "name": f"{ticker} Inc.", "market_cap": 1e10, "price": 10, "upstream": [
            {"ticker": "SUP", "name": "Supplier Inc.", "type": "Components", "product": "parts", "review_status": "approved",
             "source": "https://www.sec.gov/x.htm", "relationship_key": f"SUP->{ticker}:COMPONENTS", "edge_id": hash(ticker) % 1000,
             "evidence_excerpt": f"Supplier Inc. ships parts to {ticker} Inc."}], "downstream": []}

    dashboard = {"industries": {"Technology": [company(t) for t in ("ZZZ", "AAA", "MMM")]}, "quality": {"pending_count": 0, "approved_count": 3, "rejected_count": 0, "review_queue": []}}
    annotate_dashboard_data(dashboard, history_path=str(tmp_path / "history.json"))

    tied = [row["ticker"] for row in dashboard["investor_metrics"]["most_connected"] if row["total_links"] == 1]
    assert tied == sorted(tied)


def test_the_source_placeholder_is_not_published_as_a_product():
    from export import publishable_product

    assert publishable_product("AI Multi-Source Research") == ""
    assert publishable_product("Cabin pressure sensors") == "Cabin pressure sensors"


def test_a_merged_relationship_keeps_its_product_and_share_together():
    """CENT -> HD read "16% of CENT revenue" beside a 37% badge, because the badge took
    the largest share from a different member of the merge."""
    from export import merge_relationship_group

    winner = {"edge_id": 2, "ticker": "HD", "type": "Revenue Concentration", "product": "16% of CENT revenue",
              "revenue_share": 16.0, "review_status": "approved", "confidence": 0.95, "source": "https://www.sec.gov/a.htm"}
    other = {"edge_id": 9, "ticker": "HD", "type": "Supply Relationship", "product": "garden products",
             "revenue_share": 37.0, "review_status": "pending", "confidence": 0.4, "source": "AI Multi-Source Research"}

    merged = merge_relationship_group([winner, other])

    assert merged["revenue_share"] == 16.0
    assert "16% of CENT revenue" in merged["product"]


def test_the_feed_survives_a_company_name_with_an_ampersand_or_quote():
    from generate_change_feed import company_url, link_html

    from xml.etree import ElementTree

    html = link_html({"source_ticker": 'A&B "X"', "target_ticker": "TSM", "type": "Foundry", "product": "wafers"})
    # escape() leaves quotes intact, so the old anchor broke the markup for every reader.
    ElementTree.fromstring(f"<div>{html}</div>")
    assert company_url("BRK.B").endswith("ticker=BRK.B")
    assert "%26" in company_url("A&B")


def test_the_split_check_catches_a_stale_lite_file(tmp_path):
    """Nothing validated the file the site actually loads first."""
    from split_dashboard import check_split, write_split

    dashboard = tmp_path / "dashboard_data.json"
    dashboard.write_text(json.dumps({"generated_at": "2026-09-17T08:00:00+00:00", "industries": {"Tech": [
        {"ticker": "AMD", "name": "AMD", "summary": "Designs processors.", "investor_metrics": {"total_links": 1},
         "upstream": [{"ticker": "TSM"}], "downstream": []}]}}))
    lite, shards = tmp_path / "dashboard_lite.json", tmp_path / "company-data"
    write_split(dashboard, lite, shards)
    assert check_split(dashboard, lite, shards) == []

    lite.write_text(json.dumps({"industries": {}}))
    problems = check_split(dashboard, lite, shards)
    assert problems and "does not match" in problems[0]


@pytest.mark.parametrize("script", ["published_today.sh", "publish_with_retry.sh"])
def test_publishing_scripts_stay_syntactically_valid(script):
    subprocess.run(["bash", "-n", str(ROOT / "scripts" / script)], check=True, capture_output=True)


def test_a_collector_outage_leaves_the_queue_researchable(tmp_path, monkeypatch):
    """Every collector returns "" during an SEC or network outage. Stamping the 30-day
    cooldown then silenced the whole queue for a month, invisibly."""
    import auto_discover_edges as discovery
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from models import Base, Node

    engine = create_engine(f"sqlite:///{tmp_path / 'graph.db'}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    session.add_all([Node(name="Alpha Inc.", ticker="ALPH", market_cap=5e9, sector="Technology"),
                     Node(name="Beta Inc.", ticker="BETA", market_cap=4e9, sector="Technology")])
    session.commit()
    session.close()

    monkeypatch.setattr(discovery, "SessionLocal", Session)
    monkeypatch.setattr(discovery, "sweep_customer_concentration", lambda *a, **k: {"checked": 0, "created": 0})
    monkeypatch.setattr(discovery, "known_company_names", lambda session: {})
    for collector in ("get_wiki_data", "get_sec_data", "get_sec_exhibits", "get_yahoo_news"):
        monkeypatch.setattr(discovery.IntelGatherer, collector, staticmethod(lambda *a, **k: ""))
    monkeypatch.setattr(discovery.IntelGatherer, "get_additional_sources", staticmethod(lambda *a, **k: ""))
    summaries = []
    monkeypatch.setattr(discovery, "write_discovery_summary", lambda summary, **k: summaries.append(summary))

    discovery.auto_discover_supply_chain(limit=2)

    checked = Session()
    assert [node.last_researched_at for node in checked.query(Node).all()] == [None, None]
    assert summaries[-1]["no_source_companies"] == 2
    assert summaries[-1]["companies_analyzed"] == 0
