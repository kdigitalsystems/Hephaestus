"""The rest of the pipeline accepts a private company as an endpoint and never mistakes it for something else.

Each test sits where a rule reads a node's name, sector, ticker or market cap: the audit and the cleanup that mirrors it,
the review panel's vehicle test and prompt, the evidence checks, the market-data crawl and the review-decision replay.
"""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import private_companies as private
from evidence_quality import (
    bound_by_generic_word,
    evidence_support,
    mention_pattern,
    mentions_company,
    names_company,
    registered_company_names,
    strong_aliases,
)
from models import Base, Edge, Node

CONSENSUS = "Ollama consensus review: Consensus 3/3 for approve (avg confidence 0.95; votes approve:3). Lead rationale from qwen2.5:7b-instruct: the excerpt states it."
URL = "https://www.sec.gov/Archives/edgar/data/1/10k.htm"


def registry_entry(**overrides):
    entry = {
        "id": "OPENAI", "name": "OpenAI", "aliases": ["OpenAI, Inc.", "OpenAI Global, LLC"], "sector": "Technology",
        "industry": "Artificial Intelligence", "description": "AI research and products company.", "website": "https://openai.com",
        "hq": "San Francisco, California, USA", "founded": 2015, "sources": ["https://openai.com/about"], "checked": "2026-10-22", "listed_as": None,
    }
    entry.update(overrides)
    return entry


def private_node(**overrides):
    entry = registry_entry(**overrides)
    return Node(
        name=entry["name"], ticker=entry["id"], entity_type=private.PRIVATE_ENTITY_TYPE, sector=entry["sector"], industry=entry["industry"],
        business_summary=entry["description"], metadata_json=private.metadata_text(entry),
    )


def public_node(name, ticker, cap=1e11, sector="Technology", industry="Software"):
    return Node(name=name, ticker=ticker, sector=sector, industry=industry, market_cap=cap, current_price=10.0)


def approved_edge(supplier, customer, kind, excerpt, **fields):
    return Edge(
        source_id=supplier.id, target_id=customer.id, dependency_type=kind, product=fields.pop("product", "AI compute capacity"),
        confidence_score=0.9, source_url=URL, source_title="Annual report", evidence_excerpt=excerpt, review_status="approved",
        review_note=CONSENSUS, **fields,
    )


@pytest.fixture
def pipeline_db(monkeypatch):
    import audit_data_quality
    import cleanup_reviewed_edges

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False)  # like database.SessionLocal
    monkeypatch.setattr(cleanup_reviewed_edges, "SessionLocal", Session)
    monkeypatch.setattr(audit_data_quality, "SessionLocal", Session)
    monkeypatch.setattr(audit_data_quality, "engine", engine)
    return Session


# --- the audit and the cleanup that mirrors it ------------------------------------------------

def test_links_with_a_private_endpoint_pass_cleanup_and_the_audit_gate(pipeline_db):
    from audit_data_quality import audit_database
    from cleanup_reviewed_edges import cleanup_reviewed_edges

    session = pipeline_db()
    oracle, nvidia, msft = public_node("Oracle Corporation Common Stock", "ORCL", 7e11), public_node("NVIDIA Corporation Common Stock", "NVDA", 4.5e12), public_node("Microsoft Corporation Common Stock", "MSFT", 3.8e12)
    openai, anthropic = private_node(), private_node(id="ANTHROPIC", name="Anthropic", aliases=["Anthropic, PBC"])
    session.add_all([oracle, nvidia, msft, openai, anthropic])
    session.flush()
    on_alias = approved_edge(oracle, openai, "Cloud Infrastructure Provider", "Oracle provides cloud infrastructure capacity to OpenAI Global, LLC under a multi-year agreement.")
    supplier = approved_edge(openai, msft, "AI Model Licensing", "OpenAI licenses its GPT models to Microsoft for use in Azure OpenAI Service.", product="GPT models")
    chips = approved_edge(nvidia, anthropic, "AI Accelerator Chips", "NVIDIA sells data center GPUs to Anthropic, PBC for training its Claude models.", product="GPUs")
    session.add_all([on_alias, supplier, chips])
    session.commit()
    ids = [on_alias.id, supplier.id, chips.id]
    session.close()

    audit_database(fail_on_warnings=True)
    cleanup_reviewed_edges()
    audit_database(fail_on_warnings=True)

    check = pipeline_db()
    assert [check.get(Edge, edge_id).review_status for edge_id in ids] == ["approved"] * 3
    check.close()


def test_a_customer_disclosure_naming_a_private_customer_survives_cleanup_and_the_audit(pipeline_db):
    """The customer has no market cap: the megacap plausibility test must read that as "unknown", not crash."""
    from audit_data_quality import audit_database
    from cleanup_reviewed_edges import cleanup_reviewed_edges

    session = pipeline_db()
    supplier, labs = public_node("Acme Compute Corp Common Stock", "ACME", 2e9), private_node()
    session.add_all([supplier, labs])
    session.flush()
    edge = approved_edge(
        supplier, labs, "Revenue Concentration",
        "Acme Compute (ACME) 10-K filed 2026-02-25: OpenAI accounted for 38% of our revenue for the year ended December 31, 2025.",
        product="38% of ACME revenue", revenue_share=38.0,
    )
    edge.source_title = "SEC EDGAR (10-K filed 2026-02-25; customer-concentration disclosure)"
    session.add(edge)
    session.commit()
    edge_id = edge.id
    session.close()

    cleanup_reviewed_edges()
    audit_database(fail_on_warnings=True)

    check = pipeline_db()
    kept = check.get(Edge, edge_id)
    assert (kept.review_status, kept.revenue_share) == ("approved", 38.0)
    check.close()


def test_a_private_name_that_reads_like_a_security_is_still_a_company(pipeline_db):
    """"Preferred Networks" is an AI company, not a preferred share; the same name on a listed row is a security."""
    from audit_data_quality import audit_database, non_company_reason
    from cleanup_reviewed_edges import NON_COMPANY_NOTE, cleanup_reviewed_edges

    session = pipeline_db()
    oracle = public_node("Oracle Corporation Common Stock", "ORCL", 7e11)
    networks = private_node(id="PREFERREDNET", name="Preferred Networks", aliases=["Preferred Networks, Inc."])
    listed_lookalike = public_node("Preferred Networks Rights", "PNRT", 1e6)
    session.add_all([oracle, networks, listed_lookalike])
    session.flush()
    kept = approved_edge(oracle, networks, "Cloud Infrastructure Provider", "Oracle provides cloud infrastructure capacity to Preferred Networks, Inc. for AI training.")
    dropped = approved_edge(oracle, listed_lookalike, "Cloud Infrastructure Provider", "Oracle provides cloud infrastructure capacity to Preferred Networks Rights holders.")
    session.add_all([kept, dropped])
    session.commit()
    kept_id, dropped_id = kept.id, dropped.id

    assert non_company_reason(session.get(Edge, kept_id)) is None
    assert "not a company" in non_company_reason(session.get(Edge, dropped_id))
    session.close()

    cleanup_reviewed_edges()
    audit_database(fail_on_warnings=True)

    check = pipeline_db()
    assert check.get(Edge, kept_id).review_status == "approved"
    assert check.get(Edge, dropped_id).review_status == "rejected" and check.get(Edge, dropped_id).review_note.startswith(NON_COMPANY_NOTE)
    check.close()


def test_a_private_company_is_not_a_duplicate_listing_of_another_company(pipeline_db):
    from audit_data_quality import share_class_duplicates

    session = pipeline_db()
    oracle = public_node("Oracle Corporation Common Stock", "ORCL", 7e11)
    labs, group, other = private_node(), public_node("OpenAI Group, Inc. Class A Common Stock", "OPAI"), private_node(id="OPENAIFUND", name="OpenAI Research Fund Labs", aliases=[])
    session.add_all([oracle, labs, group, other])
    session.flush()
    edges = [
        approved_edge(oracle, labs, "Cloud Infrastructure Provider", "Oracle provides cloud infrastructure capacity to OpenAI."),
        approved_edge(oracle, other, "Cloud Infrastructure Provider", "Oracle provides cloud infrastructure capacity to OpenAI Research Fund Labs."),
    ]
    session.add_all(edges)
    session.commit()
    assert share_class_duplicates(session.query(Edge).all()) == []
    session.close()


def test_two_listings_of_one_company_are_still_found_when_one_end_is_private(pipeline_db):
    """A lookalike that slipped into the universe after the registry entry was written is the same company twice."""
    from audit_data_quality import non_company_reason

    session = pipeline_db()
    labs = private_node(id="STRIPEPAY", name="Stripe Inc.", aliases=["Stripe Payments, Inc."])
    listed = public_node("Stripe Inc. Class A Common Stock", "STRP", 1e10)
    session.add_all([labs, listed])
    session.flush()
    edge = approved_edge(labs, listed, "Payment Processing", "Stripe processes payments for Stripe Inc.")
    session.add(edge)
    session.commit()
    assert "two listings of one company" in non_company_reason(session.get(Edge, edge.id))
    session.close()


# --- the review panel's vehicle test and prompt -----------------------------------------------

def test_the_vehicle_test_does_not_read_a_private_name_as_a_fund_or_trust():
    from review_edges_with_ollama import is_non_operating_vehicle

    for name in ("Acme Trust Systems", "Notes & Funds Labs", "Spac Robotics", "Blank Check Robotics"):
        listed = SimpleNamespace(name=name, sector="Technology", industry="Software", entity_type="Company")
        registered = SimpleNamespace(name=name, sector="Technology", industry="Software", entity_type=private.PRIVATE_ENTITY_TYPE)
        assert is_non_operating_vehicle(listed), name
        assert not is_non_operating_vehicle(registered), name


def test_the_vehicle_test_still_judges_a_private_companys_sector_and_industry():
    from review_edges_with_ollama import is_non_operating_vehicle

    for sector, industry in (("Financial Services", "Banks"), ("Technology", "Asset Management"), ("Real Estate", "REIT - Office")):
        node = SimpleNamespace(name="Some Lab", sector=sector, industry=industry, entity_type=private.PRIVATE_ENTITY_TYPE)
        assert is_non_operating_vehicle(node), (sector, industry)
    assert not is_non_operating_vehicle(SimpleNamespace(name="OpenAI", sector="Technology", industry="Artificial Intelligence", entity_type=private.PRIVATE_ENTITY_TYPE))


def test_the_review_prompt_describes_a_private_company_as_private_not_by_its_registry_id():
    from review_edges_with_ollama import node_label

    assert node_label(private_node()) == "OpenAI | private company (no stock listing) | sector Technology | industry Artificial Intelligence"
    assert node_label(public_node("Oracle Corporation", "ORCL")) == "Oracle Corporation | ticker ORCL | sector Technology | industry Software"


def test_the_panels_deterministic_rules_accept_a_curated_edge_that_names_its_ends(pipeline_db):
    from review_edges_with_ollama import deterministic_review

    session = pipeline_db()
    oracle, labs = public_node("Oracle Corporation Common Stock", "ORCL", 7e11), private_node()
    session.add_all([oracle, labs])
    session.flush()
    edge = approved_edge(oracle, labs, "Cloud Infrastructure Provider", "Oracle provides cloud infrastructure capacity to OpenAI Global, LLC under a multi-year agreement.")
    edge.review_status, edge.review_note = "pending", None
    session.add(edge)
    session.commit()
    with registered_company_names(session.query(Node.ticker, Node.name).all()):
        assert deterministic_review(session.get(Edge, edge.id)) is None
    session.close()


# --- the evidence checks ----------------------------------------------------------------------

def test_a_private_companys_registry_aliases_count_as_naming_it():
    labs = private_node()
    for text in ("capacity for OpenAI, Inc.", "capacity for OpenAI Global, LLC.", "capacity for OpenAI", "capacity for openai global llc"):
        assert mentions_company(text, labs), text
    assert not mentions_company("capacity for Open Source AI", labs)
    assert not mentions_company("capacity for ChatGPT users", labs)
    assert names_company("capacity for OpenAI, Inc.", labs)
    assert mention_pattern(labs) and "openai\\ global\\ llc".replace("\\ ", "[\\W_]+") in mention_pattern(labs)


def test_the_registry_id_is_not_treated_as_a_ticker_symbol_in_prose():
    labs = private_node(id="SCALEAI1", name="Scale AI", aliases=["Scale AI, Inc."])
    assert "scaleai1" not in strong_aliases(labs)
    assert "scale ai" in strong_aliases(labs) and "scale ai inc" in strong_aliases(labs)
    assert "msft" in strong_aliases(public_node("Microsoft Corporation", "MSFT"))


def test_a_public_nodes_aliases_are_unchanged():
    node = public_node("Alphabet Inc. Class A Common Stock", "GOOGL")
    assert strong_aliases(node) == ["googl", "alphabet", "google"]


def test_an_alias_stops_a_generic_first_word_from_being_read_as_a_wrong_match():
    labs = private_node(id="GLOBALMIND", name="Global Mind", aliases=["Global Mind Labs, Inc."])
    unrelated = "Global shipping capacity is provided to customers in Asia."
    named = "Oracle provides capacity to Global Mind Labs, Inc. for training."
    assert bound_by_generic_word(unrelated, labs)
    assert not bound_by_generic_word(named, labs)


def test_an_excerpt_naming_a_private_company_by_alias_supports_the_edge():
    oracle, labs = public_node("Oracle Corporation Common Stock", "ORCL", 7e11), private_node()
    with registered_company_names([("ORCL", oracle.name), ("OPENAI", labs.name)]):
        verdict, _ = evidence_support("Oracle provides cloud infrastructure capacity to OpenAI Global, LLC under a multi-year agreement.", oracle, labs)
    assert verdict == "named"


# --- the market-data crawl --------------------------------------------------------------------

def test_the_replay_of_review_decisions_needs_the_private_node_to_exist():
    import edge_review_decisions

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, autoflush=False)()
    oracle = public_node("Oracle Corporation Common Stock", "ORCL", 7e11)
    session.add(oracle)
    session.commit()
    decision = {
        "source_ticker": "ORCL", "target_ticker": "OPENAI", "dependency_type": "Cloud Infrastructure Provider", "product": "AI compute capacity",
        "confidence_score": 0.9, "revenue_share": None, "source_url": URL, "source_title": "Oracle 10-K", "evidence_excerpt": "Oracle provides capacity to OpenAI.",
        "review_status": "approved", "review_note": CONSENSUS, "reviewed_at": "2026-10-20T00:00:00",
    }
    assert edge_review_decisions.apply_decision(session, decision) == "missing"
    session.add(private_node())
    session.commit()
    assert edge_review_decisions.apply_decision(session, decision) == "applied"
    session.commit()
    edge = session.query(Edge).one()
    assert (edge.source_node.ticker, edge.target_node.ticker, edge.review_status) == ("ORCL", "OPENAI", "approved")
    session.close()
