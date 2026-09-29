"""Supplier statements read from a filer's own annual report (backend/supplier_dependence.py).

Every sentence below is quoted from a real 10-K, including the ones that must not
produce a link.
"""
import sys
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import auto_discover_edges
from customer_concentration import filer_documented_direction
from evidence_quality import mentions_company
from models import Base, Edge, Node
from supplier_dependence import NameIndex, extract_supplier_dependencies


UNIVERSE = [
    ("NVIDIA Corporation Common Stock", "NVDA", 4e12),
    ("Taiwan Semiconductor Manufacturing Company Ltd.", "TSM", 1.2e12),
    ("Micron Technology, Inc. Common Stock", "MU", 1.5e11),
    ("SK hynix Inc. American Depositary Shares", "HXSCL", 1e11),
    ("Fabrinet", "FN", 8e9),
    ("GlobalFoundries Inc. Ordinary Shares", "GFS", 2.5e10),
    ("Amazon.com, Inc. Common Stock", "AMZN", 2.3e12),
    ("Microsoft Corporation Common Stock", "MSFT", 3.5e12),
    ("Alphabet Inc. Class A Common Stock", "GOOGL", 2.4e12),
    ("Advanced Micro Devices, Inc. Common Stock", "AMD", 2.5e11),
    ("Intel Corporation Common Stock", "INTC", 1e11),
    ("The Boeing Company Common Stock", "BA", 1.5e11),
    ("Embraer S.A. American Depositary Shares (Each representing Four Common Shares)", "ERJ", 8e9),
    ("Snowflake Inc.", "SNOW", 6e10),
    ("Netflix, Inc. Common Stock", "NFLX", 4e11),
    ("American Airlines Group Inc. Common Stock", "AAL", 9e9),
    ("HP Inc. Common Stock", "HPQ", 3e10),
    ("Walmart Inc. Common Stock", "WMT", 7e11),
    ("111, Inc. American Depositary Shares", "YI", 5e7),
    ("Novartis AG", "NVS", 2.5e11),
    ("AstraZeneca PLC", "AZN", 2.3e11),
    ("GSK plc American Depositary Shares", "GSK", 8e10),
    ("Benchmark Electronics, Inc.", "BHE", 1.4e9),
    ("Avantor, Inc.", "AVTR", 9e9),
    ("CoreWeave, Inc. Class A Common Stock", "CRWV", 6e10),
    ("Westlake Corporation", "WLK", 1.2e10),
    ("Sony Group Corporation American Depositary Shares", "SONY", 1.3e11),
    ("ATI Inc.", "ATI", 1e10),
    ("ABBVIE INC.", "ABBV", 3.5e11),
    # Names that are also ordinary words or places, and must stay that way.
    ("Korea Electric Power Corp", "KEP", 1e10),
    ("Founder Group Limited Class A Ordinary Shares", "FGL", 3e7),
    ("The Joint Corp. Common Stock", "JYNT", 2e8),
    ("APi Group Corporation", "APG", 9e9),
    ("Nasdaq, Inc. Common Stock", "NDAQ", 5e10),
    ("Dick's Sporting Goods, Inc.", "DKS", 1.8e10),
    ("Johnson & Johnson", "JNJ", 4e11),
]


def known_names():
    return {name: auto_discover_edges.clean_company_name(name) for name, _ticker, _cap in UNIVERSE}


INDEX = NameIndex(known_names())


def suppliers(text, filer=()):
    return [found.supplier_name for found in extract_supplier_dependencies(text, INDEX, filer)]


def test_real_supplier_statements_name_the_supplier():
    assert suppliers("We purchase memory from SK Hynix Inc., Micron Technology, Inc., and Samsung.") == [
        "SK hynix Inc. American Depositary Shares", "Micron Technology, Inc. Common Stock",
    ]
    assert suppliers(
        "We utilize foundries, such as Taiwan Semiconductor Manufacturing Company Limited, or TSMC, and Samsung "
        "Electronics Co., Ltd., or Samsung, to produce our semiconductor wafers."
    ) == ["Taiwan Semiconductor Manufacturing Company Ltd."]
    assert suppliers(
        "We engage with independent subcontractors and contract manufacturers such as Hon Hai Precision Industry Co., "
        "Ltd., Wistron Corporation, and Fabrinet to perform assembly, testing and packaging of our final products."
    ) == ["Fabrinet"]
    assert suppliers(
        "We rely on Taiwan Semiconductor Manufacturing Company Limited (TSMC) for the production of all wafers for "
        "microprocessor and GPU products at 7 nanometer (nm) or smaller nodes, and we rely primarily on GLOBALFOUNDRIES Inc."
    ) == ["Taiwan Semiconductor Manufacturing Company Ltd.", "GlobalFoundries Inc. Ordinary Shares"]
    # "Embraer S.A." once its ADR wording and legal form are set aside.
    assert suppliers(
        "For example, all of our mainline aircraft were manufactured by either Airbus or Boeing and all of our "
        "regional aircraft were manufactured by either Bombardier or Embraer."
    ) == ["The Boeing Company Common Stock", "Embraer S.A. American Depositary Shares (Each representing Four Common Shares)"]


def test_small_company_supplier_statements():
    # From a risk-factor summary whose bullets arrive as one sentence.
    assert suppliers(
        "If we are unable to successfully commercialize our products, our business will be adversely affected. "
        "• We rely on a single contract manufacturer, Benchmark Electronics, Inc."
    ) == ["Benchmark Electronics, Inc."]
    assert suppliers("In particular, we obtain NuSil brand medical-grade silicone from Avantor, Inc.") == ["Avantor, Inc."]
    # An in-licence: the licensor supplies the intellectual property.
    assert suppliers(
        "Under the terms of the Original Agreements, we obtained from AstraZeneca an exclusive worldwide, sub-licensable "
        "license under AstraZeneca's intellectual property rights relating to alvelestat."
    ) == ["AstraZeneca PLC"]


def test_a_planned_supplier_is_not_one_yet():
    assert suppliers(
        "Grid and Off-Site Power: We plan to source a portion of our initial capacity from nearby merchant power "
        "facilities, which include the nearby Micron Technology, Inc. plants."
    ) == []
    assert suppliers("We will continue to rely on Micron Technology, Inc. for memory.") == ["Micron Technology, Inc. Common Stock"]
    assert suppliers(
        "We currently rely on one major supplier, Nvidia, for the GPU chips we offer, and plan to rely on another major "
        "supplier, AMD."
    ) == ["NVIDIA Corporation Common Stock"]


def test_cloud_services_resolve_to_their_companies():
    found = extract_supplier_dependencies(
        "In addition, our platform currently operates on public cloud infrastructure provided by Amazon Web Services "
        "(AWS), Microsoft Azure (Azure), and Google Cloud Platform (GCP), and our costs and gross margins are "
        "significantly influenced by the prices we are able to negotiate with these public cloud providers.",
        INDEX,
    )
    assert [(f.supplier_name, f.dependency_type) for f in found] == [
        ("Amazon.com, Inc. Common Stock", "Cloud Infrastructure Provider"),
        ("Microsoft Corporation Common Stock", "Cloud Infrastructure Provider"),
        ("Alphabet Inc. Class A Common Stock", "Cloud Infrastructure Provider"),
    ]
    assert suppliers("We rely upon Amazon Web Services to operate certain aspects of our service.") == [
        "Amazon.com, Inc. Common Stock"
    ]
    # An acronym for a company whose own name is spelled out.
    assert suppliers(
        "We also rely on Intel, AMD, and NVIDIA, or other suppliers to provide us with a sufficient supply of processors."
    ) == ["Intel Corporation Common Stock", "Advanced Micro Devices, Inc. Common Stock", "NVIDIA Corporation Common Stock"]


def test_statements_that_are_not_supplier_dependence():
    rejected = [
        # Retailers are a sales channel.
        "Our products are also available to purchase from a number of third-party retailers, including Amazon, "
        "Dick's Sporting Goods, Johnson Fitness & Wellness, John Lewis & Partners, Fitshop, and MediaMarkt.",
        # A place, a person's title, an ordinary word used in the filing, an acronym.
        "Finally, our business depends on our ability to receive consistent and reliable supply from our overseas "
        "partners, especially in Taiwan and South Korea.",
        "We are highly dependent on the services and reputation of Robert J. Scaringe, our Founder and CEO.",
        "As a result of the Joint Venture, we are dependent on the Joint Venture to develop certain software.",
        # "joint" is an ordinary word in this filing (see the sentence appended below).
        "We rely on Joint development agreements with universities for early research.",
        "As such, we expect to continue to rely on third parties for the manufacture of commercial supplies of the "
        "raw materials, API and finished drug product.",
        # Listing rules and partnerships.
        "With respect to the corporate governance requirements of Nasdaq that we do follow, we may in the future rely on "
        "available Nasdaq exemptions that would allow us to follow our home country practice.",
        "Our ability to realize the value of tebipenem HBr, currently our only product candidate, depends on the potential "
        "FDA approval and the commercialization of tebipenem HBr through our partnership with GSK.",
        # Storefronts sell the filer's products.
        "We rely upon third-party digital delivery platforms, such as Microsoft's Xbox Live, PlayStation Network, Steam, "
        "and other third-party service providers, to provide connectivity to our digital products.",
        # Good Clinical Practice is not Google Cloud Platform.
        "We rely exclusively on CROs and clinical trial sites, which need to comply with GCP, to ensure the proper and "
        "timely conduct of our clinical trials.",
        # Customers and parents the filer depends on.
        "Our success in the Colocation segment is highly dependent on the success of CoreWeave and the fulfillment by it "
        "of its obligations under our existing contractual arrangements.",
        "We are substantially dependent on Westlake for our cash flows.",
        "We derive a significant portion of our revenue from the sale of products made for video game platforms "
        "manufactured by third parties, such as Sony's PlayStation consoles and Microsoft's Xbox consoles.",
        # A drug code, and licensees that develop or sell the filer's products.
        "We exclusively license global rights (excluding Greater China) to ATI-052 from Biosion.",
        "For example, we depend on AbbVie for the manufacture and commercialization of ORILISSA and ORIAHNN.",
        "We have entered into License Agreements with Novartis, and pursuant to the terms of that agreement, are dependent "
        "on Novartis for certain development and commercialization activities.",
        # An asset bought once is not a supplier.
        "In 2015, when we purchased acumapimod, leflutrozole and setrusumab from Novartis, we agreed to pay Novartis if "
        "certain events occurred in relation to these compounds.",
        # The named company does its own work; it does not supply the filer.
        "We are dependent on Novartis, AstraZeneca and Mereo BioPharma 5 having conducted their research and development "
        "in accordance with the applicable protocols.",
        "Our ability to innovate beyond the x86 instruction set depends partially on Microsoft designing and "
        "developing its operating systems to run on or support our x86-based microprocessor products.",
        "With respect to our graphics products, we depend in part on Microsoft to design and develop its operating "
        "system to run on or support our graphics products.",
        # Numbers are not companies ("111, Inc."), and a cash-flow table is not a supplier.
        "Net cash provided by operating activities $ 162,623 $ 58,170 Net cash provided by investing activities 271,111 70,788.",
        "We rely on 111 contract manufacturers across Asia for our apparel.",
        # The stock-performance graph.
        "The historical stock performance presented below is not indicative of future stock performance. "
        "(1) Underlying data provided by Nasdaq Global Indexes.",
        # Negations and former suppliers.
        "We do not rely on Micron Technology for any of our memory.",
        "We no longer purchase wafers from GlobalFoundries Inc.",
        # Customers, not suppliers.
        "We depend on a small number of customers, such as Amazon Web Services and Microsoft, for most of our revenue.",
        "We depend on Walmart for a significant portion of our net sales.",
        "We rely on Walmart, our largest customer, to carry our full product line.",
        "We rely on Amazon.com to sell our products in the United States.",
        # Not the filer speaking.
        "Intel relies on Taiwan Semiconductor Manufacturing Company Limited for some of its products.",
    ]
    for sentence in rejected:
        text = sentence + " Our joint development work continues."
        assert suppliers(text) == [], sentence


def test_the_filer_is_never_its_own_supplier():
    text = "We rely on NVIDIA Corporation and Micron Technology, Inc. for components."
    assert suppliers(text, ("NVIDIA Corporation Common Stock", "NVIDIA", "NVDA")) == ["Micron Technology, Inc. Common Stock"]


def make_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, autoflush=False)()
    nodes = {ticker: Node(name=name, ticker=ticker, market_cap=cap, sector="Technology") for name, ticker, cap in UNIVERSE}
    session.add_all(nodes.values())
    session.commit()
    return session, nodes


FILING = {"form": "10-K", "filing_date": "2026-02-25", "url": "https://www.sec.gov/Archives/edgar/data/1045810/nvda-10k.htm"}
NVDA_TEXT = """
Item 1A. Risk Factors. We depend on a limited number of suppliers.
We utilize foundries, such as Taiwan Semiconductor Manufacturing Company Limited, or TSMC, and Samsung Electronics Co., Ltd., or Samsung, to produce our semiconductor wafers.
We purchase memory from SK Hynix Inc., Micron Technology, Inc., and Samsung.
Our competitors include Advanced Micro Devices, Inc. and Intel Corporation.
"""


def test_discovery_creates_supplier_edges_the_filing_directs(monkeypatch):
    session, nodes = make_session()
    monkeypatch.setattr(auto_discover_edges, "latest_annual_filing", lambda ticker: (FILING, NVDA_TEXT))
    known = auto_discover_edges.known_company_names(session)
    index = NameIndex(known)

    created = auto_discover_edges.discover_customer_concentration(session, nodes["NVDA"], known, index)
    session.commit()

    edges = {(e.source_node.ticker, e.target_node.ticker): e for e in session.query(Edge).all()}
    assert created == 3
    assert set(edges) == {("TSM", "NVDA"), ("HXSCL", "NVDA"), ("MU", "NVDA")}
    tsmc = edges[("TSM", "NVDA")]
    assert tsmc.review_status == "pending"
    assert tsmc.dependency_type == "Foundry Services"
    assert tsmc.product == "Supplier named in NVDA's 10-K"
    assert tsmc.revenue_share is None
    assert tsmc.source_url == FILING["url"]
    assert tsmc.source_title == "SEC EDGAR (10-K filed 2026-02-25; supplier-dependence disclosure)"
    assert tsmc.evidence_excerpt.startswith("NVIDIA (NVDA) 10-K filed 2026-02-25: We utilize foundries")
    # The filing fixes the direction, and the review step sees both companies named.
    assert all(filer_documented_direction(edge) for edge in edges.values())
    assert mentions_company(tsmc.evidence_excerpt, nodes["TSM"]) and mentions_company(tsmc.evidence_excerpt, nodes["NVDA"])

    # Re-reading the same filing adds nothing.
    assert auto_discover_edges.discover_customer_concentration(session, nodes["NVDA"], known, index) == 0
    assert session.query(Edge).count() == 3
    # Without the index only customer disclosures are read (there are none here).
    session.query(Edge).delete()
    session.commit()
    assert auto_discover_edges.discover_customer_concentration(session, nodes["NVDA"], known) == 0


def test_discovery_skips_suppliers_the_review_step_would_not_see_named(monkeypatch):
    session, nodes = make_session()
    text = ("For example, all of our mainline aircraft were manufactured by either Airbus or Boeing and all of our "
            "regional aircraft were manufactured by either Bombardier or Embraer.")
    monkeypatch.setattr(auto_discover_edges, "latest_annual_filing", lambda ticker: (FILING, text))
    known = auto_discover_edges.known_company_names(session)

    created = auto_discover_edges.discover_customer_concentration(session, nodes["AAL"], known, NameIndex(known))
    session.commit()

    # The reader finds Embraer, but its listing name hides it from the review step's
    # name check, which would hold the link for a person; Boeing is created.
    assert created == 1
    edge = session.query(Edge).one()
    assert (edge.source_node.ticker, edge.target_node.ticker) == ("BA", "AAL")
    assert mentions_company(edge.evidence_excerpt, nodes["BA"]) and mentions_company(edge.evidence_excerpt, nodes["AAL"])


def test_discovery_leaves_links_that_are_already_known_or_decided(monkeypatch):
    session, nodes = make_session()
    monkeypatch.setattr(auto_discover_edges, "latest_annual_filing", lambda ticker: (FILING, NVDA_TEXT))
    known = auto_discover_edges.known_company_names(session)
    session.add_all([
        # Already published under another label.
        Edge(source_id=nodes["TSM"].id, target_id=nodes["NVDA"].id, dependency_type="Contract Manufacturing", review_status="approved"),
        # A person rejected this pair.
        Edge(source_id=nodes["MU"].id, target_id=nodes["NVDA"].id, dependency_type="Memory",
             review_status="rejected", review_note="Human review (2026-09-20): rejected."),
        # An automated rejection does not block a filing statement.
        Edge(source_id=nodes["HXSCL"].id, target_id=nodes["NVDA"].id, dependency_type="HBM",
             review_status="rejected", review_note="Automated cleanup: the evidence excerpt does not support this link."),
    ])
    session.commit()

    created = auto_discover_edges.discover_customer_concentration(session, nodes["NVDA"], known, NameIndex(known))
    session.commit()

    assert created == 1
    new = session.query(Edge).filter(Edge.review_status == "pending").one()
    assert (new.source_node.ticker, new.target_node.ticker, new.dependency_type) == ("HXSCL", "NVDA", "Supply Relationship")


def test_filer_documented_direction_reads_who_wrote_the_statement():
    nvda, tsm = SimpleNamespace(ticker="NVDA"), SimpleNamespace(ticker="TSM")
    edge = SimpleNamespace(
        source_node=tsm, target_node=nvda, revenue_share=None,
        source_title="SEC EDGAR (10-K filed 2026-02-25; supplier-dependence disclosure)",
        evidence_excerpt="NVIDIA (NVDA) 10-K filed 2026-02-25: We utilize foundries, such as TSMC.",
    )
    assert filer_documented_direction(edge)
    # Written by the supplier's filing instead: the supplier-dependence reading does not apply.
    assert not filer_documented_direction(SimpleNamespace(**{**edge.__dict__, "source_node": nvda, "target_node": tsm}))
    # A model's excerpt with the same wording but no filing title.
    assert not filer_documented_direction(SimpleNamespace(**{**edge.__dict__, "source_title": "Reuters"}))
