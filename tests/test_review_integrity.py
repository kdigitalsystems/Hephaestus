"""Direction, provenance and queue integrity of the review pipeline.

Each test pins a defect found on the published graph or in the nightly run logs.
"""
import sys
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from models import Base, Edge, Node  # noqa: E402

CONSENSUS = "Ollama consensus review: Consensus 3/3 for approve (avg confidence 0.95; votes approve:3). Lead rationale from qwen2.5:7b-instruct: "


def memory_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine, sessionmaker(bind=engine)


# --- votes -----------------------------------------------------------------------------

def test_a_reverse_vote_is_never_turned_into_an_approval():
    """The old heuristic approved "TSMC manufactures the processors Apple uses ... backwards"."""
    from review_edges_with_ollama import correct_review_for_reason, normalize_review

    apple_to_tsmc = SimpleNamespace(
        source_node=SimpleNamespace(ticker="AAPL", name="Apple Inc. Common Stock"),
        target_node=SimpleNamespace(ticker="TSM", name="Taiwan Semiconductor Manufacturing Company Ltd."),
    )
    reason = "TSMC manufactures the processors that Apple uses, so TSMC is the supplier and the edge is backwards."
    vote = normalize_review({"supplier_side": "target", "customer_side": "source", "action": "reverse",
                             "confidence": 0.95, "relationship_type": "Foundry", "product": "chips", "reason": reason})
    assert correct_review_for_reason(apple_to_tsmc, vote)["action"] == "reverse"


def test_a_vote_that_contradicts_itself_counts_as_no_vote():
    from review_edges_with_ollama import correct_review_for_reason, normalize_review

    edge = SimpleNamespace(source_node=SimpleNamespace(ticker="SNX", name="TD SYNNEX"),
                           target_node=SimpleNamespace(ticker="AAPL", name="Apple Inc."))
    # Side fields say approve, the stated action says reverse: this was recorded as approve.
    split = normalize_review({"supplier_side": "source", "customer_side": "target", "action": "reverse",
                              "confidence": 1.0, "relationship_type": "Supply Chain", "product": "", "reason": "x"})
    assert split["action"] == "pending"
    # An approval whose own rationale says the edge is backwards.
    backwards = normalize_review({"supplier_side": "source", "customer_side": "target", "action": "approve", "confidence": 1.0,
                                  "relationship_type": "Supply Chain", "product": "",
                                  "reason": "The relationship is real, but the direction is backwards."})
    assert correct_review_for_reason(edge, backwards)["action"] == "pending"


def test_a_filing_fixes_the_direction_of_its_own_customer_disclosure():
    """The panel calls P&G -> Walmart "backwards"; the 10-K says Walmart is P&G's customer."""
    from customer_concentration import filer_documented_direction
    from review_edges_with_ollama import correct_review_for_reason, normalize_review

    edge = SimpleNamespace(
        revenue_share=16.0,
        source_title="SEC EDGAR (10-K filed 2026-08-01; customer-concentration disclosure)",
        evidence_excerpt="Procter & Gamble (PG) 10-K filed 2026-08-01: Walmart accounted for 16% of net sales.",
        source_node=SimpleNamespace(ticker="PG", name="Procter & Gamble"),
        target_node=SimpleNamespace(ticker="WMT", name="Walmart Inc."),
    )
    assert filer_documented_direction(edge)
    vote = normalize_review({"supplier_side": "target", "customer_side": "source", "action": "reverse", "confidence": 0.95,
                             "relationship_type": "Revenue Concentration", "product": "",
                             "reason": "The proposed direction is backwards. P&G sells products to Walmart."}, fixed_direction=True)
    vote = correct_review_for_reason(edge, vote)
    assert (vote["action"], vote["supplier_side"], vote["customer_side"]) == ("approve", "source", "target")
    # Another company's filing does not fix this edge's direction.
    assert not filer_documented_direction(SimpleNamespace(**{**edge.__dict__, "source_node": SimpleNamespace(ticker="WMT")}))


def test_reversals_are_held_for_a_human_unless_a_run_opts_in():
    from review_edges_with_ollama import decision_allowed

    review = {"action": "reverse", "confidence": 0.99}
    assert not decision_allowed(review, Namespace(min_approve=0.85, min_reject=0.85, min_reverse=0.85))
    assert decision_allowed(review, Namespace(min_approve=0.85, min_reject=0.85, min_reverse=0.85, apply_reversals=True))


# --- saving a verdict ------------------------------------------------------------------

def test_a_verdict_is_saved_under_the_old_label_when_the_new_one_is_taken():
    """Renaming into a sibling's label violated uq_edge_dependency; 18 verdicts were lost nightly."""
    from review_edges_with_ollama import apply_rejection

    _, Session = memory_session()
    session = Session()
    fox, roku = Node(name="Fox Corporation", ticker="FOXA"), Node(name="Roku, Inc.", ticker="ROKU")
    session.add_all([fox, roku])
    session.commit()
    session.add(Edge(source_id=fox.id, target_id=roku.id, dependency_type="acquisition", review_status="rejected"))
    pending = Edge(source_id=fox.id, target_id=roku.id, dependency_type="Content Licensing", review_status="pending")
    session.add(pending)
    session.commit()

    apply_rejection(pending, {"relationship_type": "acquisition", "product": "", "confidence": 0.97, "reason": "an acquisition"})
    session.commit()  # raised IntegrityError before

    assert pending.review_status == "rejected" and pending.dependency_type == "Content Licensing"


def test_an_industry_name_is_never_stored_as_the_product():
    from review_edges_with_ollama import update_metadata

    lear = SimpleNamespace(ticker="LEA", name="Lear Corporation", industry="Auto Parts", sector="Consumer Cyclical")
    gm = SimpleNamespace(ticker="GM", name="General Motors", industry="Auto Manufacturers", sector="Consumer Cyclical")
    edge = SimpleNamespace(source_node=lear, target_node=gm, dependency_type="Seating", product="Seats",
                           confidence_score=0.5, review_note="", reviewed_at=None, revenue_share=None)
    update_metadata(edge, {"relationship_type": "Seating", "product": "Auto Parts", "confidence": 0.9, "reason": "ok"})
    assert edge.product == "Seats"
    # Both sides are compared without the trailing "products": this form survived the first fix.
    from evidence_quality import is_endpoint_label
    helen = SimpleNamespace(industry="Household & Personal Products", sector="Consumer Defensive")
    assert is_endpoint_label("Household & Personal Products", helen)
    assert is_endpoint_label("household & personal", helen)
    assert not is_endpoint_label("Hair dryers and styling tools", helen)

    disclosure = SimpleNamespace(
        source_node=SimpleNamespace(ticker="VTRS", industry="Drug Manufacturers - Specialty & Generic", sector="Healthcare"),
        target_node=SimpleNamespace(ticker="COR", industry="Medical Distribution", sector="Healthcare"),
        dependency_type="Revenue Concentration", product="11% of VTRS revenue", revenue_share=11.0,
        source_title="SEC EDGAR (10-K filed 2026-02-26; customer-concentration disclosure)",
        evidence_excerpt="Viatris (VTRS) 10-K filed 2026-02-26: Cencora, Inc. 11 %",
        confidence_score=0.5, review_note="", reviewed_at=None,
    )
    update_metadata(disclosure, {"relationship_type": "Revenue Concentration", "product": "Drug Manufacturers - Specialty & Generic",
                                 "confidence": 0.95, "reason": "ok"})
    assert disclosure.product == "11% of VTRS revenue"
    # A real description from the model is still welcome.
    update_metadata(disclosure, {"relationship_type": "Revenue Concentration", "product": "Generic drugs",
                                 "confidence": 0.95, "reason": "ok"})
    assert disclosure.product == "Generic drugs"


def test_a_single_model_review_is_not_a_human_verdict():
    from audit_data_quality import needs_human_confirmation

    def approved(note):
        return SimpleNamespace(review_status="approved", review_note=note)

    assert needs_human_confirmation(approved("Ollama review: Veeva supplies Lilly."))
    assert needs_human_confirmation(approved("Ollama report review: fine."))
    assert needs_human_confirmation(approved(CONSENSUS + "ok"))
    assert not needs_human_confirmation(approved("Confirmed from the 10-K by hand."))


# --- cleanup and the publish gate ------------------------------------------------------

@pytest.fixture
def pipeline_db(monkeypatch):
    import audit_data_quality
    import cleanup_reviewed_edges

    engine, Session = memory_session()
    monkeypatch.setattr(cleanup_reviewed_edges, "SessionLocal", Session)
    monkeypatch.setattr(audit_data_quality, "SessionLocal", Session)
    monkeypatch.setattr(audit_data_quality, "engine", engine)
    return Session


def test_cleanup_leaves_nothing_for_the_publish_gate_to_fail_on(pipeline_db):
    """Every new audit warning mirrors a cleanup rule; a mismatch would block publishing."""
    from audit_data_quality import audit_database
    from cleanup_reviewed_edges import HELD_NOTE_PREFIX, cleanup_reviewed_edges
    from review_edges_with_ollama import selected_edges

    session = pipeline_db()

    def company(ticker, name, industry="Other", cap=1e10):
        row = Node(name=name, ticker=ticker, industry=industry, sector="Technology", market_cap=cap)
        session.add(row)
        return row

    tsm = company("TSM", "Taiwan Semiconductor Manufacturing Company Ltd.", "Semiconductors")
    nvda = company("NVDA", "NVIDIA Corporation", "Semiconductors")
    asml = company("ASML", "ASML Holding N.V.", "Semiconductor Equipment & Materials")
    lng = company("LNG", "Cheniere Energy, Inc.", "Oil & Gas Midstream")
    msft, rng = company("MSFT", "Microsoft Corporation"), company("RNG", "RingCentral, Inc.")
    veev, lly = company("VEEV", "Veeva Systems Inc."), company("LLY", "Eli Lilly and Company")
    mdt, iart = company("MDT", "Medtronic plc"), company("IART", "Integra LifeSciences Holdings")
    lea, gm = company("LEA", "Lear Corporation", "Auto Parts"), company("GM", "General Motors Company", "Auto Manufacturers")
    session.commit()

    def approved(source, target, label, evidence, note=CONSENSUS + "ok", **extra):
        row = Edge(source_id=source.id, target_id=target.id, dependency_type=label, evidence_excerpt=evidence,
                   review_status="approved", review_note=note, source_url="AI Multi-Source Research", **extra)
        session.add(row)
        return row

    foundry = approved(nvda, tsm, "Foundry Services", "Most fabless companies such as NVIDIA are customers of TSMC.", product="chips")
    litho = approved(asml, tsm, "Lithography Systems", "ASML supplies EUV lithography systems to TSMC.", product="EUV lithography")
    place = approved(lng, tsm, "LNG Supplier", "Cheniere Energy signed an agreement with CPC Corporation, Taiwan to supply LNG.")
    backwards = approved(msft, rng, "Enterprise Software Integration", "RingCentral for Teams integrates with Microsoft Teams.",
                         note=CONSENSUS + "The proposed direction is backwards. RingCentral is the supplier.")
    pair_a = approved(veev, lly, "Cloud Computing Software", "Veeva provides cloud computing software for Eli Lilly.")
    pair_b = approved(lly, veev, "Software Subscription", "The company has 1,552 customers including Eli Lilly and Company.",
                      note="Ollama review: Veeva provides the platform.")
    deal = approved(mdt, iart, "equipment supplier", "In October 2014, Integra LifeSciences acquired instrumentation lines from Medtronic.")
    industry = approved(lea, gm, "Manufacturing Supplier", "Lear supplies seating to General Motors.", product="Auto Parts")
    session.commit()

    cleanup_reviewed_edges()
    audit_database(fail_on_warnings=True)  # raises SystemExit on any warning

    check = pipeline_db()
    get = lambda row: check.get(Edge, row.id)  # noqa: E731
    assert (get(foundry).source_id, get(foundry).target_id) == (tsm.id, nvda.id)
    assert (get(litho).source_id, get(litho).target_id) == (asml.id, tsm.id)
    for row in (place, backwards, pair_a, pair_b):
        assert get(row).review_status == "pending" and get(row).review_note.startswith(HELD_NOTE_PREFIX), get(row).dependency_type
    assert get(deal).review_status == "rejected"
    assert get(industry).product == "Manufacturing Supplier"

    # Held edges leave the nightly review queue: no approve-then-demote loop.
    args = Namespace(status="pending", include_held=False, source=None, target=None, edge_id=None, limit=100)
    assert {edge.id for edge in selected_edges(check, args)} & {place.id, backwards.id, pair_a.id, pair_b.id} == set()


def test_an_industry_product_is_replaced_by_the_filings_figure(pipeline_db):
    from cleanup_reviewed_edges import repair_endpoint_products

    session = pipeline_db()
    vtrs = Node(name="Viatris Inc.", ticker="VTRS", industry="Drug Manufacturers - Specialty & Generic", market_cap=1e10)
    cor = Node(name="Cencora, Inc.", ticker="COR", industry="Medical Distribution", market_cap=5e10)
    session.add_all([vtrs, cor])
    session.commit()
    disclosure = Edge(source_id=vtrs.id, target_id=cor.id, dependency_type="Revenue Concentration", revenue_share=11.0,
                      product="Drug Manufacturers - Specialty & Generic", review_status="approved")
    described = Edge(source_id=cor.id, target_id=vtrs.id, dependency_type="Distribution", product="Generic drug distribution",
                     review_status="approved")
    session.add_all([disclosure, described])
    session.commit()

    repair_endpoint_products(session, counts := {})

    assert disclosure.product == "11% of VTRS revenue" and described.product == "Generic drug distribution"
    assert counts == {"industry_products_cleared": 1}


def test_a_vendor_table_is_not_a_customer_disclosure():
    from auto_discover_edges import clean_company_name
    from customer_concentration import disclosure_sentence, extract_disclosures

    known = {name: clean_company_name(name) for name in ("Apple Inc. Common Stock", "HP Inc. Common Stock")}
    evidence = ("TD SYNNEX (SNX) 10-K filed 2026-01-27: The following table provides revenue generated from products "
                "purchased from vendors that exceeded 10% of our consolidated revenue for the periods indicated: "
                "Apple, Inc. 12 % 12 % 11 % HP Inc. 10 % N/A (1) (1) Revenue generated from products purchased from "
                "this vendor was less than 10% of consolidated revenue during the period presented.")
    assert extract_disclosures(disclosure_sentence(evidence), known, ("TD SYNNEX Corporation",), require_customer_cue=False) == []


# --- discovery ---------------------------------------------------------------------------

def test_the_researched_company_resolves_with_its_ticker_suffix(monkeypatch):
    import auto_discover_edges

    _, Session = memory_session()
    session = Session()
    session.add(Node(name="TD SYNNEX Corporation Common Stock", ticker="SNX", market_cap=10e9))
    session.commit()
    monkeypatch.setattr(auto_discover_edges, "yq_search", lambda *_a, **_k: {"quotes": []})

    node = auto_discover_edges.resolve_counterparty(session, None, "TD SYNNEX Corporation (SNX)")
    assert node is not None and node.ticker == "SNX"
    assert auto_discover_edges.split_trailing_ticker("Apple Inc.") == ("Apple Inc.", None)


def test_a_dead_news_endpoint_is_asked_once_per_run(monkeypatch, capsys):
    import auto_discover_edges

    calls = []

    class DeadTicker:
        def __init__(self, ticker):
            calls.append(ticker)

        def news(self, count=5):
            return ["error"]

    monkeypatch.setattr(auto_discover_edges, "Ticker", DeadTicker)
    monkeypatch.setattr(auto_discover_edges.IntelGatherer, "yahoo_news_disabled", False)

    assert auto_discover_edges.IntelGatherer.get_yahoo_news("SNX") == ""
    assert auto_discover_edges.IntelGatherer.get_yahoo_news("AAPL") == ""
    assert calls == ["SNX"]
    assert "skipping it for the rest of this run" in capsys.readouterr().out


def test_research_is_paced_across_the_cooldown(monkeypatch):
    import auto_discover_edges

    _, Session = memory_session()
    session = Session()
    session.add_all(Node(name=f"Co {index}", ticker=f"C{index}", market_cap=1e9, sector="Technology") for index in range(900))
    session.add(Node(name="Tiny", ticker="TNY", market_cap=1e6, sector="Technology"))
    session.commit()
    monkeypatch.setattr(auto_discover_edges, "RESEARCH_COOLDOWN_DAYS", 30)
    monkeypatch.setattr(auto_discover_edges, "RESEARCH_DAILY_QUOTA", 0)

    # 900 eligible companies over a 30-day cooldown: 30 a day, not 500 then weeks of nothing.
    assert auto_discover_edges.research_quota(session) == 30
    monkeypatch.setattr(auto_discover_edges, "RESEARCH_DAILY_QUOTA", 120)
    assert auto_discover_edges.research_quota(session) == 120


# --- does an excerpt that names one company support the link? ---------------------------

def company(ticker, name):
    return SimpleNamespace(ticker=ticker, name=name)


SITIME, MSFT = company("SITM", "SiTime Corporation Common Stock"), company("MSFT", "Microsoft Corporation")


@pytest.mark.parametrize("source, target, evidence, verdict", [
    # The unnamed company is the excerpt's subject and the verb puts it on the right side.
    (SITIME, MSFT, "It also reported its top customers as Apple, Fitbit, Garmin, Samsung, Google, Microsoft, Dell.", "supported"),
    (company("NE", "Noble Corporation plc"), company("SHEL", "Shell plc"), "In 2020, 21.7% of revenues were from Shell.", "supported"),
    (company("CIEN", "Ciena Corporation"), company("KT", "KT Corp."), "Customers include AT&T, Deutsche Telekom, KT Corporation and Verizon Communications.", "supported"),
    (company("MSFT", "Microsoft Corporation"), company("SNOW", "Snowflake Inc."),
     "The platform allows organizations to unify data warehousing into a single service on public cloud infrastructure such as Amazon Web Services (AWS), Microsoft Azure, and Google Cloud Platform (GCP).", "supported"),
    # Trade names and trademarks identify the company.
    (company("NVDA", "NVIDIA Corporation"), company("SMCI", "Super Micro Computer, Inc."),
     "In June 2023, Supermicro saw increased demand for its large language model optimized AI systems, featuring NVIDIA chips.", "named"),
    # Right relationship, published the wrong way round.
    (company("F", "Ford Motor Company"), company("TKR", "The Timken Company"),
     "the company's wheel bearings were used on Ford Motor Company's F-150 Lightning", "backwards"),
    (company("TM", "Toyota Motor Corporation"), company("MGA", "Magna International"),
     "It produces automotive systems, assemblies, modules, and components, which are supplied to Toyota...", "backwards"),
    # Cut off inside the list that would have named the company.
    (company("TSM", "Taiwan Semiconductor Manufacturing Company Ltd."), company("NVDA", "NVIDIA Corporation"),
     "Most fabless semiconductor companies such as AMD... are customers of TSMC,", "unclear"),
    # Junk: never refers to a company, a past event, a rival, an integration.
    (company("VALE", "VALE S.A."), company("BHP", "BHP Group Limited"), "The company's iron ore mines are primarily in Brazil.", "unsupported"),
    (company("IBM", "International Business Machines"), company("CHRW", "C.H. Robinson Worldwide, Inc."), "The company adopted IBM mainframes in 1979.", "unsupported"),
    (company("CMC", "Commercial Metals Company"), company("NUE", "Nucor Corporation"),
     "Along with Commercial Metals Company, it is one of two primary suppliers of rebar used to reinforce concrete.", "unsupported"),
    (company("CRM", "Salesforce, Inc."), company("ZM", "Zoom Communications, Inc."),
     "Over the course of 2015 and 2016, the company integrated its software with Slack, Salesforce, and Skype for Business.", "unsupported"),
    (company("KEX", "Kirby Corporation"), company("FDXF", "FedEx Freight Holding Company"),
     "Effective October 30, 2012, Kirby Corp. replaced Overseas Shipholding Group, Inc.", "unsupported"),
])
def test_evidence_support(source, target, evidence, verdict):
    from evidence_quality import evidence_support

    assert evidence_support(evidence, source, target)[0] == verdict


def test_cleanup_rejects_junk_and_holds_the_doubtful(pipeline_db):
    from audit_data_quality import audit_database
    from cleanup_reviewed_edges import HELD_NOTE_PREFIX, cleanup_reviewed_edges

    session = pipeline_db()
    nodes = {ticker: Node(name=name, ticker=ticker, market_cap=1e10) for ticker, name in (
        ("SITM", "SiTime Corporation Common Stock"), ("MSFT", "Microsoft Corporation"),
        ("VALE", "VALE S.A."), ("BHP", "BHP Group Limited"),
        ("F", "Ford Motor Company"), ("TKR", "The Timken Company"))}
    session.add_all(nodes.values())
    session.commit()

    def approved(source, target, evidence, note=CONSENSUS + "ok"):
        row = Edge(source_id=nodes[source].id, target_id=nodes[target].id, dependency_type="Components",
                   evidence_excerpt=evidence, review_status="approved", review_note=note, source_url="AI Multi-Source Research")
        session.add(row)
        return row

    valid = approved("SITM", "MSFT", "It also reported its top customers as Apple, Google, Microsoft and Dell.")
    junk = approved("VALE", "BHP", "The company's iron ore mines are primarily in Brazil.")
    backwards = approved("F", "TKR", "the company's wheel bearings were used on Ford Motor Company's F-150 Lightning")
    human = approved("VALE", "MSFT", "The company's iron ore mines are primarily in Brazil.", note="Confirmed by hand.")
    session.commit()

    cleanup_reviewed_edges()
    audit_database(fail_on_warnings=True)

    check = pipeline_db()
    assert check.get(Edge, valid.id).review_status == "approved"
    assert check.get(Edge, junk.id).review_status == "rejected"
    assert "does not support this link" in check.get(Edge, junk.id).review_note
    held = check.get(Edge, backwards.id)
    assert held.review_status == "pending" and held.review_note.startswith(HELD_NOTE_PREFIX)
    assert check.get(Edge, human.id).review_status == "approved", "a human's verdict is never overruled"


# --- companies merely listed side by side --------------------------------------------------

@pytest.mark.parametrize("source, target, evidence", [
    (company("META", "Meta Platforms, Inc."), company("NVDA", "NVIDIA Corporation"),
     "Big Tech, which refers to the largest six tech companies in the United States, Alphabet (Google), Amazon, Apple, Meta (Facebook), Microsoft, and Nvidia..."),
    (company("PSX", "Phillips 66"), company("KMI", "Kinder Morgan, Inc."),
     "Phillips 66, Kinder Morgan and HF Sinclair Announce Final Investment Decision for Western Gateway Pipeline"),
    (company("UMC", "United Microelectronics Corporation"), company("AMD", "Advanced Micro Devices, Inc."),
     "examples of pure play foundries are GlobalFoundries, TSMC, and UMC, and examples of fabless companies are AMD, Nvidia, and Qualcomm."),
    (company("PFE", "Pfizer Inc."), company("ARVN", "Arvinas, Inc."), "Vepdegestrant was developed by Arvinas and Pfizer."),
    (company("ORCL", "Oracle Corporation"), company("BL", "BlackLine, Inc."),
     "BlackLine integrates with over 30 leading ERP systems, including SAP SE, Oracle Corporation..."),
    (company("GE", "GE Aerospace"), company("BETA", "BETA Technologies"),
     "In collaboration with NASA, BETA Technologies, and Boeing, GE Aerospace conducted the first hybrid electric flight above 30,000 feet."),
    (company("LMT", "Lockheed Martin Corp."), company("FEIM", "Frequency Electronics, Inc."),
     "Air Force Army Navy Space Force Lockheed Martin Northrop Grumman +Approved Primes NASDAQ: FEIM LIVE $69.74 +1.01%"),
])
def test_companies_merely_listed_together_are_not_a_relationship(source, target, evidence):
    from evidence_quality import evidence_support

    assert evidence_support(evidence, source, target)[0] == "unsupported"


@pytest.mark.parametrize("source, target, evidence", [
    # One company outside the list, and a verb relating the list to it.
    (company("TSM", "Taiwan Semiconductor Manufacturing Company Ltd."), company("INTC", "Intel Corporation"),
     "Some integrated device manufacturers that have their own fabrication facilities, such as Intel, NXP, STMicroelectronics, and Texas Instruments, outsource some of their production to TSMC."),
    (company("LPL", "LG Display Co., Ltd."), company("DELL", "Dell Technologies Inc."),
     "Some examples of products that use LCD panels from LG display are Apple's 2009 27-inch iMac, Apple's Thunderbolt Display, and Dell's U2711 LCD Monitor."),
    (company("TMUS", "T-Mobile US, Inc."), company("BBY", "Best Buy Co., Inc."),
     "Best Buy sells cellular phones from Verizon Wireless, AT&T Mobility, T-Mobile, Boost Mobile and Ting Mobile in the United States."),
    # Both in one coordination, but one is also the subject elsewhere and supply is stated.
    (company("BLDP", "Ballard Power Systems Inc."), company("PLUG", "Plug Power Inc."),
     "Plug Power's GenDrive system integrates fuel cells manufactured by both Plug Power and Ballard Power Systems."),
    # A leading date is not a list item.
    (company("MGNI", "Magnite, Inc."), company("NFLX", "Netflix, Inc."),
     "In May 2024, Netflix announced its first expansion of its advertising partnerships to include Magnite, The Trade Desk and Google DV360."),
])
def test_list_sentences_that_relate_the_companies_are_kept(source, target, evidence):
    from evidence_quality import evidence_support

    assert evidence_support(evidence, source, target)[0] == "named"
