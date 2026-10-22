"""Private companies from the database to the site: export, split, validation, status, repair, signals and pages."""
import copy
import json
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import export
import generate_static_pages as pages
import private_companies as private
import split_dashboard
from models import Base, Edge, Node
from validate_dashboard_data import private_company_problems, validate_dashboard_data

PUBLIC_KEYS = {
    "id", "name", "ticker", "industry", "price", "change", "market_cap", "enterprise_value", "trailing_pe", "forward_pe",
    "price_to_book", "dividend", "high_52w", "low_52w", "revenue", "margin", "target_price", "recommendation", "ceo",
    "employees", "summary", "last_updated", "upstream", "downstream", "sector", "investor_metrics",
}
REGISTRY_ENTRY = {
    "id": "OPENAI", "name": "OpenAI", "aliases": ["OpenAI, Inc.", "OpenAI Global, LLC"], "sector": "Technology",
    "industry": "Artificial Intelligence", "description": "AI research and products company; maker of ChatGPT and the GPT model family.",
    "website": "https://openai.com", "hq": "San Francisco, California, USA", "founded": 2015,
    "sources": ["https://openai.com/about"], "checked": "2026-10-22", "listed_as": None,
}
EXCERPT = "Oracle provides cloud infrastructure capacity to OpenAI, Inc. under a multi-year agreement."
CONSENSUS = "Ollama consensus review: Consensus 3/3 for approve (avg confidence 0.95; votes approve:3). Lead rationale from qwen2.5:7b-instruct: ok."


def private_node(entry=None, **overrides):
    entry = {**REGISTRY_ENTRY, **(entry or {})}
    return Node(
        name=entry["name"], ticker=entry["id"], entity_type=private.PRIVATE_ENTITY_TYPE, sector=entry["sector"],
        industry=entry["industry"], business_summary=entry["description"], metadata_json=private.metadata_text(entry), **overrides,
    )


def public_node(name, ticker, cap=1e11, price=100.0, sector="Technology"):
    return Node(name=name, ticker=ticker, sector=sector, industry="Software", market_cap=cap, current_price=price, percent_change=1.0, recommendation="Buy")


def link(session, supplier, customer, kind="Cloud Infrastructure Provider", status="approved", url="https://www.sec.gov/x.htm", excerpt=EXCERPT, note=CONSENSUS):
    session.add(Edge(
        source_id=supplier.id, target_id=customer.id, dependency_type=kind, product="AI compute capacity", confidence_score=0.9,
        source_url=url, source_title="Oracle 10-K", evidence_excerpt=excerpt, review_status=status, review_note=note,
    ))


@pytest.fixture
def exported(tmp_path, monkeypatch):
    """A graph with private companies, exported to a temporary dashboard file."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False)
    session = Session()
    oracle, nvidia = public_node("Oracle Corporation Common Stock", "ORCL", 7e11), public_node("NVIDIA Corporation Common Stock", "NVDA", 4.5e12)
    ghost = public_node("Unpriced Corp", "GHST", cap=None, price=None)
    openai, anthropic = private_node(), private_node({"id": "ANTHROPIC", "name": "Anthropic", "aliases": [], "website": None, "hq": None, "founded": None})
    bank = private_node({"id": "PRIVBANK", "name": "Private Bank", "sector": "Financial Services"})
    session.add_all([oracle, nvidia, ghost, openai, anthropic, bank])
    session.flush()
    link(session, oracle, openai)                                                    # approved: published on both pages
    link(session, nvidia, openai, "AI Accelerator Chips", status="pending")           # pending with a real source: not published
    link(session, oracle, anthropic, status="rejected")                              # rejected: not published
    link(session, ghost, openai, "Data Licensing", excerpt="Unpriced Corp licenses data to OpenAI, Inc. for model training.")
    link(session, oracle, bank)                                                      # an ignored sector's node is not published
    session.commit()
    session.close()

    path, history = tmp_path / "dashboard_data.json", tmp_path / "link_history.json"
    monkeypatch.setattr(export, "SessionLocal", Session)
    monkeypatch.setattr(export, "EXPORT_PATH", str(path))
    monkeypatch.setattr(export, "HISTORY_PATH", str(history))
    export.export_to_json()
    dashboard = json.loads(path.read_text(encoding="utf-8"))
    by_ticker = {company["ticker"]: company for rows in dashboard["industries"].values() for company in rows}
    return SimpleExport(dashboard, by_ticker, path, Session)


class SimpleExport:
    def __init__(self, dashboard, by_ticker, path, Session):
        self.dashboard, self.by_ticker, self.path, self.Session = dashboard, by_ticker, path, Session


# --- export -----------------------------------------------------------------------------------

def test_a_private_company_is_exported_with_its_registry_profile_and_no_market_data(exported):
    openai = exported.by_ticker["OPENAI"]
    assert openai["listing"] == "private"
    assert (openai["website"], openai["hq"], openai["founded"]) == ("https://openai.com", "San Francisco, California, USA", 2015)
    assert openai["aliases"] == ["OpenAI, Inc.", "OpenAI Global, LLC"]
    assert openai["summary"] == REGISTRY_ENTRY["description"]
    assert (openai["name"], openai["industry"], openai["sector"]) == ("OpenAI", "Artificial Intelligence", "Technology")
    for field in private.PRIVATE_MARKET_FIELDS:
        assert openai[field] is None, field
    for field in private.PRIVATE_PLACEHOLDER_FIELDS:
        assert openai[field] == "N/A", field
    assert PUBLIC_KEYS <= set(openai)
    assert set(openai) - PUBLIC_KEYS == set(private.PRIVATE_ONLY_KEYS)


def test_a_public_company_gets_no_new_keys(exported):
    oracle = exported.by_ticker["ORCL"]
    assert set(oracle) == PUBLIC_KEYS
    assert "listing" not in oracle


def test_aliases_are_published_only_when_there_are_some(exported):
    anthropic = exported.by_ticker["ANTHROPIC"]
    assert "aliases" not in anthropic
    assert (anthropic["listing"], anthropic["website"], anthropic["hq"], anthropic["founded"]) == ("private", None, None, None)


def test_a_private_company_is_exported_even_with_no_published_link(exported):
    anthropic = exported.by_ticker["ANTHROPIC"]
    assert anthropic["upstream"] == [] and anthropic["downstream"] == []
    assert exported.by_ticker["ORCL"]["downstream"][0]["ticker"] == "OPENAI"


def test_relationships_with_a_private_company_follow_the_usual_rules(exported):
    openai, oracle = exported.by_ticker["OPENAI"], exported.by_ticker["ORCL"]
    assert sorted(l["ticker"] for l in openai["upstream"]) == ["GHST", "ORCL"]        # approved ones only
    assert [l["ticker"] for l in oracle["downstream"]] == ["OPENAI"]                    # rejected, pending and ignored-sector links are not published
    assert exported.by_ticker["NVDA"]["downstream"] == []
    assert "PRIVBANK" not in exported.by_ticker, "an ignored sector is never exported, private or not"
    link = oracle["downstream"][0]
    assert link["relationship_key"] == "ORCL->OPENAI:CLOUD INFRASTRUCTURE PROVIDER" and link["review_status"] == "approved"
    assert "GHST" in exported.by_ticker, "an unpriced public company is published when an approved link reaches it"


def test_the_export_passes_the_dashboard_validator(exported):
    summary = validate_dashboard_data(exported.dashboard)
    assert summary["private_companies"] == 2 and summary["companies"] == len(exported.by_ticker)


def test_a_private_row_that_somehow_holds_market_data_is_still_published_without_it(exported, monkeypatch, tmp_path):
    session = exported.Session()
    node = session.query(Node).filter(Node.ticker == "OPENAI").one()
    node.market_cap, node.current_price, node.recommendation, node.ceo_name, node.employees, node.dividend_yield = 1e12, 99.0, "Buy", "A. Person", 5, "1%"
    session.commit()
    session.close()
    path = tmp_path / "again.json"
    monkeypatch.setattr(export, "EXPORT_PATH", str(path))
    monkeypatch.setattr(export, "HISTORY_PATH", str(tmp_path / "history2.json"))

    export.export_to_json()

    again = {c["ticker"]: c for rows in json.loads(path.read_text())["industries"].values() for c in rows}["OPENAI"]
    assert all(again[field] is None for field in private.PRIVATE_MARKET_FIELDS)
    assert all(again[field] == "N/A" for field in private.PRIVATE_PLACEHOLDER_FIELDS)
    assert private_company_problems(again) == []


def test_should_export_node_publishes_private_companies_without_market_data():
    assert export.should_export_node(private_node())
    assert export.should_export_node(private_node(), require_market_data=False)
    assert not export.should_export_node(private_node({"sector": "Real Estate"}))
    assert not export.should_export_node(private_node({"sector": "Shell Companies"}), require_market_data=False)
    # Public companies are unchanged: no market data, no export (unless a link needs them).
    unpriced = public_node("Unpriced Corp", "GHST", cap=None, price=None)
    assert not export.should_export_node(unpriced)
    assert export.should_export_node(unpriced, require_market_data=False)
    assert export.should_export_node(public_node("Priced", "PRCD"))


def test_the_private_fields_survive_odd_metadata():
    for garbage in (None, "", "{", "[]", json.dumps({"founded": "2015", "website": "", "hq": None, "aliases": [1, "", " Real Alias Inc "]})):
        node = Node(name="X Labs", ticker="XLABSINC", entity_type=private.PRIVATE_ENTITY_TYPE, metadata_json=garbage)
        fields = export.private_company_fields(node)
        assert fields["listing"] == "private" and fields["website"] is None and fields["hq"] is None and fields["founded"] is None
    assert export.private_company_fields(node)["aliases"] == ["Real Alias Inc"]


# --- split ------------------------------------------------------------------------------------

def test_the_split_carries_the_private_keys_into_the_lite_file_and_the_shards(exported, tmp_path):
    lite, shards = tmp_path / "dashboard_lite.json", tmp_path / "company-data"
    split_dashboard.write_split(exported.path, lite, shards)
    lite_company = {c["ticker"]: c for rows in json.loads(lite.read_text())["industries"].values() for c in rows}

    openai = lite_company["OPENAI"]
    assert openai["listing"] == "private" and openai["website"] == "https://openai.com" and openai["founded"] == 2015
    assert openai["aliases"] == ["OpenAI, Inc.", "OpenAI Global, LLC"] and openai["hq"].startswith("San Francisco")
    assert "summary" not in openai and "investor_metrics" in openai  # linked: only the summary moves
    assert "aliases" not in lite_company["ANTHROPIC"] and lite_company["ANTHROPIC"]["listing"] == "private"

    shard = json.loads((shards / "O.json").read_text())["OPENAI"]
    assert shard["summary"] == REGISTRY_ENTRY["description"]
    assert (shard["listing"], shard["website"], shard["hq"], shard["founded"], shard["aliases"]) == ("private", "https://openai.com", REGISTRY_ENTRY["hq"], 2015, REGISTRY_ENTRY["aliases"])
    unlinked = json.loads((shards / "A.json").read_text())["ANTHROPIC"]
    assert {"summary", "investor_metrics", "listing"} <= set(unlinked) and "aliases" not in unlinked
    # A public company's shard entry holds only what it did before.
    assert set(json.loads((shards / "O.json").read_text())["ORCL"]) == {"summary"}
    assert split_dashboard.check_split(exported.path, lite, shards) == []


def test_the_split_leaves_public_entries_exactly_as_before():
    company = {"ticker": "AMD", "name": "AMD", "summary": "Designs processors.", "investor_metrics": {"total_links": 1}, "upstream": [{"ticker": "TSM"}], "downstream": []}
    lite, shards = split_dashboard.split_dashboard({"industries": {"Tech": [company]}})
    assert lite["industries"]["Tech"][0] == {"ticker": "AMD", "name": "AMD", "investor_metrics": {"total_links": 1}, "upstream": [{"ticker": "TSM"}], "downstream": []}
    assert shards == {"A": {"AMD": {"summary": "Designs processors."}}}


# --- validation -------------------------------------------------------------------------------

def valid_private_entry():
    return {
        "id": 9, "name": "OpenAI", "ticker": "OPENAI", "industry": "Artificial Intelligence", "listing": "private",
        "website": "https://openai.com", "hq": "San Francisco, California, USA", "founded": 2015, "aliases": ["OpenAI, Inc."],
        **{field: None for field in private.PRIVATE_MARKET_FIELDS}, **{field: "N/A" for field in private.PRIVATE_PLACEHOLDER_FIELDS},
    }


def test_a_valid_private_entry_has_no_problems():
    assert private_company_problems(valid_private_entry()) == []
    assert private_company_problems({"ticker": "AMD", "market_cap": 1e9}) == []


@pytest.mark.parametrize("field, value", [
    ("price", 10.0), ("change", 0.0), ("market_cap", 1e9), ("enterprise_value", 5), ("trailing_pe", 12.0), ("forward_pe", 11.0),
    ("price_to_book", 3.0), ("high_52w", 20.0), ("low_52w", 5.0), ("revenue", 1e8), ("margin", 0.5), ("target_price", 99.0), ("employees", 100),
])
def test_a_private_entry_with_market_data_fails(field, value):
    entry = valid_private_entry()
    entry[field] = value
    assert any("market data" in problem and field in problem for problem in private_company_problems(entry))


@pytest.mark.parametrize("field, value", [("dividend", "1.20%"), ("recommendation", "Buy"), ("ceo", "A. Person")])
def test_a_private_entry_with_profile_placeholders_filled_in_fails(field, value):
    entry = valid_private_entry()
    entry[field] = value
    assert any(field in problem for problem in private_company_problems(entry))


def test_a_zero_is_market_data_too():
    entry = valid_private_entry()
    entry["market_cap"] = 0
    assert private_company_problems(entry)


@pytest.mark.parametrize("key, value", [("website", "https://x.example"), ("hq", "Austin"), ("founded", 2000), ("aliases", ["X Corp"])])
def test_a_public_entry_with_a_private_only_key_fails(key, value):
    assert any("public company" in problem for problem in private_company_problems({"ticker": "AMD", key: value}))


def test_an_unknown_listing_value_counts_as_a_public_entry_carrying_the_key():
    assert private_company_problems({"ticker": "AMD", "listing": "public"})
    assert private_company_problems({"ticker": "AMD", "listing": ""})


@pytest.mark.parametrize("ticker", ["AMD", "OPEN", "openai", "OPEN-AI", None, ""])
def test_a_private_entry_needs_a_registry_id_as_its_ticker(ticker):
    entry = valid_private_entry()
    entry["ticker"] = ticker
    assert any("ticker" in problem for problem in private_company_problems(entry))


@pytest.mark.parametrize("field, value", [
    ("website", "http://openai.com"), ("website", "javascript:alert(1)"), ("website", 5), ("hq", 5), ("founded", "2015"), ("founded", 1799), ("founded", True),
    ("aliases", []), ("aliases", "OpenAI"), ("aliases", [""]), ("aliases", [3]),
])
def test_malformed_private_profile_fields_fail(field, value):
    entry = valid_private_entry()
    entry[field] = value
    assert private_company_problems(entry)


def test_the_dashboard_validator_reports_these_with_the_company_named(exported):
    dashboard = copy.deepcopy(exported.dashboard)
    next(c for rows in dashboard["industries"].values() for c in rows if c["ticker"] == "OPENAI")["price"] = 12.5
    next(c for rows in dashboard["industries"].values() for c in rows if c["ticker"] == "ORCL")["listing"] = "private-ish"
    with pytest.raises(AssertionError) as raised:
        validate_dashboard_data(dashboard)
    message = str(raised.value)
    assert "Technology/OPENAI is a private company but carries market data" in message
    assert "Technology/ORCL is a public company but carries private-company key(s) ['listing']" in message


def test_private_companies_pass_without_prices_and_count_as_companies(exported):
    dashboard = copy.deepcopy(exported.dashboard)
    assert validate_dashboard_data(dashboard)["private_companies"] == 2


# --- status -----------------------------------------------------------------------------------

def test_the_status_counts_private_companies_and_keeps_the_company_count(exported):
    from write_status import build_status

    status = build_status(exported.dashboard)
    assert status["private_companies"] == 2
    assert status["companies"] == exported.dashboard["investor_metrics"]["company_count"] == len(exported.by_ticker)


def test_the_status_of_a_graph_without_private_companies_says_zero():
    from write_status import build_status

    status = build_status({"generated_at": "2026-10-22T00:00:00+00:00", "investor_metrics": {"company_count": 3}, "industries": {"T": [{"ticker": "AMD"}], "U": None}})
    assert status["private_companies"] == 0 and status["companies"] == 3
    assert build_status({})["private_companies"] == 0


# --- repair -----------------------------------------------------------------------------------

def test_repair_from_decisions_keeps_the_private_profile_and_attaches_its_links(exported, tmp_path):
    from repair_dashboard_from_decisions import repair_dashboard_from_decisions

    decisions = tmp_path / "decisions.json"
    decisions.write_text(json.dumps({"decisions": [
        {"edge_id": 1, "source_ticker": "ORCL", "target_ticker": "OPENAI", "dependency_type": "Cloud Infrastructure Provider", "product": "AI compute capacity",
         "confidence_score": 0.9, "source_url": "https://www.sec.gov/x.htm", "source_title": "Oracle 10-K", "evidence_excerpt": EXCERPT,
         "review_status": "approved", "review_note": CONSENSUS, "reviewed_at": "2026-10-20T00:00:00"},
        {"edge_id": 2, "source_ticker": "ORCL", "target_ticker": "NOTSEEDED", "dependency_type": "Cloud", "product": "x", "confidence_score": 0.9,
         "source_url": "https://www.sec.gov/x.htm", "evidence_excerpt": EXCERPT, "review_status": "approved", "reviewed_at": "2026-10-20T00:00:00"},
    ]}))

    repaired = repair_dashboard_from_decisions(exported.path, decisions)

    by_ticker = {c["ticker"]: c for rows in repaired["industries"].values() for c in rows}
    openai = by_ticker["OPENAI"]
    assert [l["ticker"] for l in openai["upstream"]] == ["ORCL"]
    assert (openai["listing"], openai["website"], openai["founded"]) == ("private", "https://openai.com", 2015)
    assert "ANTHROPIC" in by_ticker and by_ticker["ANTHROPIC"]["listing"] == "private", "an unlinked private company survives the repair"
    assert validate_dashboard_data(repaired)["private_companies"] == 2


# --- research signals -------------------------------------------------------------------------

def signal_company(ticker, cap, **extra):
    return {"ticker": ticker, "name": ticker, "sector": "Technology", "market_cap": cap, "price": 100, "change": 0, "recommendation": "Hold",
            "target_price": None, "upstream": [], "downstream": [], **extra}


def approved(ticker, kind="Cloud Infrastructure Provider"):
    return {"ticker": ticker, "type": kind, "confidence": 0.9, "review_status": "approved", "source_title": "Filed"}


NOW = datetime(2026, 10, 22, tzinfo=timezone.utc)


def test_private_companies_never_get_a_research_signal():
    from predictions import generate_predictions, select_top_companies

    labs = signal_company("OPENAI", None, price=None, change=None, listing="private", upstream=[approved("ORCL")])
    oracle = signal_company("ORCL", 7e11, downstream=[approved("OPENAI")])
    payload, history = generate_predictions({"industries": {"Technology": [labs, oracle]}}, now=NOW)
    assert [p["ticker"] for p in payload["predictions"]] == ["ORCL"]
    assert all(entry["ticker"] != "OPENAI" for entry in history)
    # Not even an entry that wrongly carries a market cap can be ranked.
    wrong = signal_company("OPENAI", 5e12, listing="private")
    assert [c["ticker"] for c in select_top_companies([wrong, oracle])] == ["ORCL"]


def test_a_private_counterparty_does_not_change_a_public_companys_signal():
    from predictions import generate_predictions

    base = signal_company("BASE", 5e11, change=6, recommendation="Strong Buy", target_price=130, downstream=[approved("SUP")])
    supplier = signal_company("SUP", 1e11, upstream=[approved("BASE")])
    labs = signal_company("OPENAI", None, price=None, listing="private", upstream=[approved("SUP")])
    alone, _ = generate_predictions({"industries": {"T": [copy.deepcopy(supplier), copy.deepcopy(base)]}}, now=NOW)
    supplier_with_private = copy.deepcopy(supplier)
    supplier_with_private["downstream"] = [approved("OPENAI")]
    together, _ = generate_predictions({"industries": {"T": [supplier_with_private, copy.deepcopy(base), labs]}}, now=NOW)

    def row(payload, ticker):
        return next(p for p in payload["predictions"] if p["ticker"] == ticker)

    for key in ("score", "confidence", "network_signal", "direct_signal", "direction", "connection_paths"):
        assert row(alone, "SUP")[key] == row(together, "SUP")[key], key


def test_private_companies_are_not_reported_as_missing_a_market_cap():
    from predictions import generate_predictions

    labs = signal_company("OPENAI", None, price=None, listing="private")
    unpriced = signal_company("NOCAP", None, price=None)
    payload, _ = generate_predictions({"industries": {"T": [labs, unpriced, signal_company("BIG", 1e11)]}}, now=NOW)
    assert payload["universe_notes"]["companies_without_market_cap"] == 1


# --- the review queue -------------------------------------------------------------------------

def test_the_review_queue_handles_a_private_company_with_no_market_cap():
    from review_queue import build_review_queue, reach

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, autoflush=False)()
    oracle, labs, other = public_node("Oracle Corporation Common Stock", "ORCL", 7e11), private_node(), private_node({"id": "ANTHROPIC", "name": "Anthropic", "aliases": []})
    session.add_all([oracle, labs, other])
    session.flush()
    link(session, oracle, labs, status="pending", note=None)
    link(session, labs, other, "Model Licensing", status="pending", note=None, excerpt="OpenAI licenses models to Anthropic for evaluation work.")
    session.commit()

    queue = build_review_queue(session)

    items = {(item["source_ticker"], item["target_ticker"]): item for item in queue["items"]}
    assert items[("ORCL", "OPENAI")]["source_market_cap"] == 7e11 and items[("ORCL", "OPENAI")]["target_market_cap"] is None
    assert items[("OPENAI", "ANTHROPIC")]["source_market_cap"] is None and items[("OPENAI", "ANTHROPIC")]["target_market_cap"] is None
    assert [item["target_ticker"] for item in queue["items"]] == ["OPENAI", "ANTHROPIC"], "the link to the large company is reviewed first"
    assert reach(session.query(Edge).filter(Edge.dependency_type == "Model Licensing").one()) == 0.0
    session.close()


# --- static pages -----------------------------------------------------------------------------

def render_site(tmp_path, dashboard):
    path = tmp_path / "dashboard_data.json"
    path.write_text(json.dumps(dashboard), encoding="utf-8")
    out = tmp_path / "company"
    out.mkdir(exist_ok=True)
    result = pages.generate_static_pages(path, out, tmp_path / "sitemap.xml")
    return result, out, tmp_path / "sitemap.xml"


def test_a_private_company_page_says_it_is_private_and_shows_no_market_data(exported, tmp_path):
    result, out, sitemap = render_site(tmp_path, exported.dashboard)

    html = (out / "OPENAI.html").read_text(encoding="utf-8")
    assert "<title>OpenAI suppliers and customers | Hephaestus</title>" in html
    assert "<h1>OpenAI</h1>" in html and "(OPENAI)" not in html
    assert '<p class="eyebrow">Private company · Sector: Technology · Industry: Artificial Intelligence</p>' in html
    assert "OpenAI is a private company with 2 tracked suppliers and 0 tracked customers" in html
    assert "<h2>About OpenAI</h2>" in html and REGISTRY_ENTRY["description"] in html
    assert "<li>Headquarters: San Francisco, California, USA</li>" in html and "<li>Founded: 2015</li>" in html
    assert '<a href="https://openai.com" rel="noopener noreferrer nofollow">openai.com</a>' in html
    assert "no stock price or market capitalisation" in html
    assert "Market cap" not in html and "market data, chart" not in html and "tickerSymbol" not in html
    assert "Open the interactive brief for OpenAI" in html and "../#company?ticker=OPENAI" in html


def test_a_private_page_shows_no_market_cap_even_if_the_data_wrongly_has_one(exported, tmp_path):
    """The validator rejects such an export; the page must not publish the figure if one ever got through."""
    dashboard = copy.deepcopy(exported.dashboard)
    next(c for rows in dashboard["industries"].values() for c in rows if c["ticker"] == "OPENAI")["market_cap"] = 5e12
    _, out, _ = render_site(tmp_path, dashboard)
    html = (out / "OPENAI.html").read_text(encoding="utf-8")
    assert "Market cap" not in html and "$5.0T" not in html


def test_a_private_company_page_uses_organization_markup_without_a_listing(exported, tmp_path):
    _, out, _ = render_site(tmp_path, exported.dashboard)
    html = (out / "OPENAI.html").read_text(encoding="utf-8")
    markup = json.loads(html.split('<script type="application/ld+json">')[1].split("</script>")[0])
    assert markup["@type"] == "Organization" and markup["name"] == "OpenAI"
    assert "tickerSymbol" not in markup and "exchange" not in json.dumps(markup).lower()
    assert (markup["foundingDate"], markup["location"], markup["sameAs"]) == ("2015", "San Francisco, California, USA", "https://openai.com")
    assert markup["url"].endswith("/company/OPENAI.html")


def test_the_public_page_of_a_company_with_a_private_counterparty_labels_it_private(exported, tmp_path):
    _, out, _ = render_site(tmp_path, exported.dashboard)
    html = (out / "ORCL.html").read_text(encoding="utf-8")
    assert '<a href="OPENAI.html">OpenAI (private)</a>' in html and "OpenAI (OPENAI)" not in html
    assert "Oracle Corporation (ORCL)" in html and "Market cap: $700.0B" in html


def test_private_companies_are_in_the_index_and_the_sitemap_with_a_private_label(exported, tmp_path):
    result, out, sitemap = render_site(tmp_path, exported.dashboard)
    index = (out / "index.html").read_text(encoding="utf-8")
    assert '<li><a href="OPENAI.html">OpenAI (private)</a> — 2 suppliers, 0 customers</li>' in index
    assert '<li><a href="ORCL.html">Oracle Corporation (ORCL)</a>' in index
    assert "companies (" in index and "public," in index and "private) with reviewed supplier" in index
    locations = [loc.text for loc in ET.fromstring(sitemap.read_text(encoding="utf-8")).iter("{http://www.sitemaps.org/schemas/sitemap/0.9}loc")]
    assert any(location.endswith("/company/OPENAI.html") for location in locations)
    # An unlinked private company has no relationships to describe, so no page.
    assert not (out / "ANTHROPIC.html").exists() and not any(location.endswith("ANTHROPIC.html") for location in locations)


def test_private_profile_text_is_escaped_and_only_http_urls_become_links(tmp_path):
    hostile = {
        "generated_at": "2026-10-22T00:00:00+00:00",
        "industries": {"Technology": [
            {"ticker": "EVILCORP", "name": "Evil <script>alert(1)</script> & Co", "listing": "private", "sector": "Technology", "industry": "AI <b>",
             "summary": "Does <img src=x onerror=alert(1)> things.", "hq": "Paris\"><svg onload=alert(1)>", "founded": 2001,
             "website": "javascript:alert(document.cookie)", "market_cap": None,
             "upstream": [{"ticker": "ORCL", "name": "Oracle", "type": "Cloud", "product": "x", "review_status": "approved", "source": "https://s.example/x", "evidence_excerpt": "Oracle sells to <Evil>."}],
             "downstream": []},
            {"ticker": "ORCL", "name": "Oracle", "market_cap": 1e11, "upstream": [], "downstream": [
                {"ticker": "EVILCORP", "name": "Evil <script>alert(1)</script> & Co", "type": "Cloud", "product": "x", "review_status": "approved"}]},
        ]},
    }
    _, out, _ = render_site(tmp_path, hostile)
    html = (out / "EVILCORP.html").read_text(encoding="utf-8")
    assert "<script>alert" not in html and "<img" not in html and "<svg" not in html and "<b>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt; &amp; Co" in html
    assert 'href="javascript' not in html and "Website:" not in html
    assert "<li>Founded: 2001</li>" in html and "Headquarters: Paris&quot;&gt;&lt;svg" in html
    markup = json.loads(html.split('<script type="application/ld+json">')[1].split("</script>")[0])
    assert "sameAs" not in markup
    assert "<" not in html.split('<script type="application/ld+json">')[1].split("</script>")[0]
    assert "Evil &lt;script&gt;alert(1)&lt;/script&gt; &amp; Co (private)" in (out / "ORCL.html").read_text(encoding="utf-8")


@pytest.mark.parametrize("website", ["data:text/html,<script>1</script>", "ftp://x.example", "//evil.example", "https://", "  https://x.example", "https://a b.example", 42, None])
def test_a_website_that_is_not_a_plain_http_url_is_not_linked(tmp_path, website):
    dashboard = {"generated_at": "2026-10-22T00:00:00+00:00", "industries": {"T": [
        {"ticker": "LABSINC", "name": "Labs", "listing": "private", "website": website, "upstream": [{"ticker": "ORCL", "name": "Oracle", "type": "x", "review_status": "approved"}], "downstream": []},
        {"ticker": "ORCL", "name": "Oracle", "upstream": [], "downstream": [{"ticker": "LABSINC", "name": "Labs", "type": "x", "review_status": "approved"}]},
    ]}}
    _, out, _ = render_site(tmp_path, dashboard)
    html = (out / "LABSINC.html").read_text(encoding="utf-8")
    assert "Website:" not in html and "sameAs" not in html


def test_a_private_page_without_a_profile_still_renders(tmp_path):
    dashboard = {"generated_at": "2026-10-22T00:00:00+00:00", "industries": {"T": [
        {"ticker": "BAREPRIV", "name": "Bare Private", "listing": "private", "summary": "No summary available.", "upstream": [{"ticker": "ORCL", "name": "Oracle", "type": "x", "review_status": "approved"}], "downstream": []},
        {"ticker": "ORCL", "name": "Oracle", "upstream": [], "downstream": [{"ticker": "BAREPRIV", "name": "Bare Private", "type": "x", "review_status": "approved"}]},
    ]}}
    _, out, _ = render_site(tmp_path, dashboard)
    html = (out / "BAREPRIV.html").read_text(encoding="utf-8")
    assert "<h2>About Bare Private</h2>" in html and "No summary available" not in html and "Headquarters" not in html and "<ul><li>" not in html


GOLDEN = ROOT / "tests" / "data" / "static_pages_public_golden"


def unversioned(data):
    """The cache-busting `?v=` of the stylesheet and scripts changes with every front-end release (and the golden
    files carry the one they were rendered with); everything else must stay byte for byte."""
    return re.sub(rb"(\?v=)[0-9A-Za-z.\-]+", rb"\1VERSION", data)


def test_public_pages_are_byte_identical_to_what_they_were_before_private_companies(tmp_path):
    """The golden files were rendered by the generator as it was before this feature, from a graph with no private company."""
    _, out, sitemap = render_site_from(tmp_path, ROOT / "tests" / "data" / "static_pages_public_dashboard.json")
    expected = sorted(path.name for path in GOLDEN.iterdir() if path.name != "sitemap.xml")
    assert sorted(path.name for path in out.iterdir()) == expected
    for name in expected:
        assert unversioned((out / name).read_bytes()) == unversioned((GOLDEN / name).read_bytes()), name
    assert sitemap.read_bytes() == (GOLDEN / "sitemap.xml").read_bytes()
    assert b"private" not in (out / "index.html").read_bytes().lower() and b"public companies" in (out / "index.html").read_bytes()


def render_site_from(tmp_path, source):
    out = tmp_path / "company"
    out.mkdir(exist_ok=True)
    result = pages.generate_static_pages(source, out, tmp_path / "sitemap.xml")
    return result, out, tmp_path / "sitemap.xml"


def test_adding_a_private_company_to_the_graph_changes_only_the_pages_it_touches(tmp_path):
    dashboard = json.loads((ROOT / "tests" / "data" / "static_pages_public_dashboard.json").read_text(encoding="utf-8"))
    labs = {"ticker": "LABSINC", "name": "Labs", "listing": "private", "sector": "Technology", "industry": "AI", "summary": "A lab.",
            "website": None, "hq": None, "founded": None, "upstream": [], "downstream": []}
    tsm = next(c for c in dashboard["industries"]["Technology"] if c["ticker"] == "AAPL")
    tsm["downstream"] = [{"edge_id": 9, "relationship_key": "AAPL->LABSINC:X", "name": "Labs", "ticker": "LABSINC", "type": "Devices", "product": "Devices",
                          "source": "https://s.example/x", "review_status": "approved", "evidence_excerpt": "Apple sells devices to Labs."}]
    labs["upstream"] = [{"edge_id": 9, "relationship_key": "AAPL->LABSINC:X", "name": "Apple Inc. Common Stock", "ticker": "AAPL", "type": "Devices", "product": "Devices",
                         "source": "https://s.example/x", "review_status": "approved", "evidence_excerpt": "Apple sells devices to Labs."}]
    dashboard["industries"]["Technology"].append(labs)
    _, out, _ = render_site(tmp_path, dashboard)
    changed = [path.name for path in GOLDEN.iterdir() if path.name not in ("sitemap.xml",) and unversioned((out / path.name).read_bytes()) != unversioned(path.read_bytes())]
    assert sorted(changed) == ["AAPL.html", "index.html"], "pages of companies with no private counterparty must not move"
    assert (out / "LABSINC.html").exists()


def test_the_sitemap_lists_private_pages_in_the_usual_order(exported, tmp_path):
    _, _, sitemap = render_site(tmp_path, exported.dashboard)
    locations = [loc.text for loc in ET.fromstring(sitemap.read_text(encoding="utf-8")).iter("{http://www.sitemaps.org/schemas/sitemap/0.9}loc")]
    company_pages = [location.rsplit("/", 1)[1] for location in locations if "/company/" in location and not location.endswith("index.html")]
    assert company_pages == sorted(company_pages) and "OPENAI.html" in company_pages


def test_a_graph_of_only_private_companies_still_renders_an_honest_index(tmp_path):
    dashboard = {"generated_at": "2026-10-22T00:00:00+00:00", "industries": {"T": [
        {"ticker": "LABSONE", "name": "Labs One", "listing": "private", "upstream": [{"ticker": "LABSTWO", "name": "Labs Two", "type": "x", "review_status": "approved"}], "downstream": []},
        {"ticker": "LABSTWO", "name": "Labs Two", "listing": "private", "upstream": [], "downstream": [{"ticker": "LABSONE", "name": "Labs One", "type": "x", "review_status": "approved"}]},
    ]}}
    _, out, _ = render_site(tmp_path, dashboard)
    index = (out / "index.html").read_text(encoding="utf-8")
    assert "2 companies (0 public, 2 private)" in index
    assert datetime.now(timezone.utc).year >= 2026
