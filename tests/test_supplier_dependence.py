"""Supplier statements read from a filer's own annual report (backend/supplier_dependence.py).

Every sentence below is quoted from a real 10-K, including the ones that must not
produce a link.
"""
import json
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
from supplier_dependence import (
    NameIndex,
    company_key,
    extract_supplier_dependencies,
    is_non_common_security,
    same_company,
    supplier_statement_problem,
)


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


INDEX = NameIndex(known_names(), {name: cap for name, _ticker, cap in UNIVERSE})


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


# --- the hand-labelled production links -----------------------------------------------------

LABELS = json.loads((ROOT / "tests" / "data" / "supplier_statement_labels.json").read_text(encoding="utf-8"))


def labelled_index():
    names = {item["name"]: auto_discover_edges.clean_company_name(item["name"]) for item in LABELS["universe"]}
    return NameIndex(names, {item["name"]: item["market_cap"] for item in LABELS["universe"]})


def handled_before_the_reader(link):
    """Warrant and preferred lines and a company's own other listing never reach the reader
    (discovery skips them; cleanup rejects any already published)."""
    return is_non_common_security(link["filer"]) or is_non_common_security(link["supplier"]) or same_company(link["supplier"], link["filer"])


# Wrong links the reader still produces, each for a reason no sentence-level rule can see.
KNOWN_MISSES = {
    3673: "Air T 'depends on a contractual relationship with FedEx': FedEx is its customer, in the same words a supplier would be",
    3063: "Avista, a valuation firm, shares its name with the utility Avista Corporation",
}


def test_the_reader_on_every_hand_labelled_link():
    """674 links the sweep produced or a cold re-read of 574 filings found, read one by one: 123 were
    wrong (drug partners, sales channels, auditors, court cases and tax rulings, a lender, "DoW"
    read as Dow ...). The reader must drop them without losing a real supplier."""
    index = labelled_index()
    kept_wrong, lost_correct, counts = {}, [], {"wrong": 0, "correct": 0}
    for link in LABELS["links"]:
        if handled_before_the_reader(link):
            continue
        filer_names = (link["filer"], auto_discover_edges.clean_company_name(link["filer"]), link["filer_ticker"])
        found = {company_key(r.supplier_name) for r in extract_supplier_dependencies(link["sentence"], index, filer_names)}
        kept = company_key(link["supplier"]) in found
        counts["wrong"] += link["label"] == "W"
        counts["correct"] += link["label"] == "ok"
        if link["label"] == "W" and kept:
            kept_wrong[link["id"]] = link["why"]
        if link["label"] == "ok" and not kept:
            lost_correct.append((link["id"], link["sentence"][:80]))
    assert not lost_correct, lost_correct
    assert set(kept_wrong) <= set(KNOWN_MISSES), kept_wrong
    assert counts["wrong"] >= 100 and counts["correct"] >= 500, counts


def test_the_tighter_rules_keep_the_statements_they_could_have_cost():
    kept = [
        # An in-licence says what the licence is for; that is not a partner selling the filer's drug.
        ("we obtained a license from Novartis Pharma AG, or Novartis, to develop and commercialize an antibody.", ["Novartis AG"]),
        # A bottler or a hauler depends on its relationship with the company that supplies it.
        ("We are dependent upon our relationships with Waste Management and Republic Services for the operation of landfills.",
         ["Waste Management, Inc."]),
        # The passive is about the filer's goods, said before it or just after the name.
        ("Our mainline aircraft were all manufactured by Airbus or Boeing.", ["The Boeing Company Common Stock"]),
        ("Products manufactured by Microsoft Corporation, HP Inc., and Dell Inc. represented 15%, 12%, and 12% of our total product purchases.",
         ["Microsoft Corporation Common Stock", "HP Inc. Common Stock"]),
        # A list whose names follow "for" inside the clause is still a list of suppliers.
        ("The key suppliers of equipment and software for our existing networks are Huawei, Ericsson, Nokia and Juniper.", ["Nokia Oyj"]),
        # "does not source from anyone other than" is a dependence, not a negation.
        ("We do not currently source these wafers from anyone other than GLOBALFOUNDRIES, which could stop making them.",
         ["GlobalFoundries Inc. Ordinary Shares"]),
        # Large companies keep their acronyms; a legacy double name keeps its first half.
        ("We rely on wireless carriers, mainly AT&T and Verizon, to provide access to wireless networks.", ["AT&T Inc."]),
        ("We rely on contract manufacturing services provided by Flex, and Sanmina-SCI Israel Medical Systems Ltd.", ["Flex Ltd."]),
        # Camel-case brands: the listing says "Sportradar", the filing "SportRadar".
        ("We rely on third-party sports data providers, such as SportRadar and Genius Sports, to obtain accurate information.",
         ["Sportradar Group AG"]),
    ]
    index = NameIndex(
        {
            "Novartis AG": "Novartis", "Waste Management, Inc.": "Waste Management", "The Boeing Company Common Stock": "Boeing",
            "Microsoft Corporation Common Stock": "Microsoft", "HP Inc. Common Stock": "HP Inc.", "Nokia Oyj": "Nokia",
            "GlobalFoundries Inc. Ordinary Shares": "GlobalFoundries", "AT&T Inc.": "AT&T", "Flex Ltd.": "Flex",
            "Sportradar Group AG": "Sportradar", "Verizon Communications Inc.": "Verizon", "Huawei": "Huawei",
        },
        {"AT&T Inc.": 1.5e11, "Flex Ltd.": 2e10, "Sportradar Group AG": 7e9, "Nokia Oyj": 3e10},
    )
    for sentence, expected in kept:
        found = [r.supplier_name for r in extract_supplier_dependencies(sentence, index)]
        for name in expected:
            assert name in found, (sentence, found)


def test_names_that_are_something_else_in_the_sentence_are_not_companies():
    index = NameIndex(
        {
            "Dow Inc.": "Dow", "Flex Ltd.": "Flex", "IDT Corporation Class B": "IDT", "CSP Inc.": "CSP", "Eve Holding, Inc.": "Eve",
            "Group 1 Automotive, Inc.": "Group 1 Automotive", "Lucid Group, Inc.": "Lucid", "ATI Inc.": "ATI", "APi Group Corporation": "APi",
            "NOV Inc.": "NOV", "V.F. Corporation": "V.F.",
        },
        {"Dow Inc.": 3e10, "Flex Ltd.": 2e10, "IDT Corporation Class B": 1e9, "CSP Inc.": 1e8, "Eve Holding, Inc.": 1e9,
         "Group 1 Automotive, Inc.": 5e9, "Lucid Group, Inc.": 5e9, "ATI Inc.": 1e10, "APi Group Corporation": 9e9,
         "NOV Inc.": 6e9, "V.F. Corporation": 6e9},
    )
    collisions = [
        "We are heavily reliant upon the continued availability of funding provided by the DoW (including its ability to secure funding).",  # the Department of War
        "We rely on third-party manufacturers to produce certain products, including MiniMed Flex pumps and infusion sets.",  # a product line
        "We can purchase products already manufactured by IDT pursuant to its own specifications.",  # a private DNA company
        "The technology we license from these companies includes solder bumping, ultra CSP assembly and wafer-level packaging.",  # chip-scale packaging
        "We were dependent on a single spaceflight system consisting of a spaceship, VSS Unity, and launch vehicle, VMS Eve.",  # a launch vehicle
        "Our business model relies on us maintaining a relationship with one or more Tier 1 automotive suppliers.",  # a grade, not a company
        "We rely on the UHN License to use certain patents and other intellectual property rights associated with Lucid-MS.",  # a drug
        "We exclusively license rights to ATI-052 from Biosion.",  # a drug code
        "We expect to rely on third parties for the manufacture of the raw materials, API and finished drug product.",  # an acronym
        "We depend on various technology, software and services from third parties, including for our data centers and API technology.",  # a noun, not a legal form
        "We entered into an exclusive license agreement from the Stanford Office of Technology Licensing, filed Nov. 3, 2006.",  # November
        "The main supplier of fuel is World Fuel Services Mexico, S. de R. L. de C. V. F-36 Table of Contents.",  # a company form and a page number
    ]
    for sentence in collisions:
        assert [r.supplier_name for r in extract_supplier_dependencies(sentence, index)] == [], sentence
    # The same names, as companies, still match.
    assert [r.supplier_name for r in extract_supplier_dependencies("We purchase resin from Dow Inc. and from Flex Ltd. each year.", index)] == [
        "Dow Inc.", "Flex Ltd.",
    ]
    assert [r.supplier_name for r in extract_supplier_dependencies("We rely on IDT Corporation for our DNA.", index)] == ["IDT Corporation Class B"]


def test_company_identity_across_listings():
    assert same_company("Alphabet Inc. Class A Common Stock", "Alphabet Inc. Class C Capital Stock")
    assert same_company("Webull Corporation Class A Ordinary Shares", "Webull Corporation Warrants")
    assert same_company("LifeMD, Inc.", "LifeMD, Inc. 8.875% Series A Cumulative Perpetual Preferred Stock")
    # Different companies that share a first word stay different: the legal form is part of the key.
    assert not same_company("Toro Company (The)", "Toro Corp. Common Stock")
    assert not same_company("Graham Corporation", "GRAHAM HOLDINGS COMPANY")
    assert not same_company("", "")
    assert is_non_common_security("Cingulate Inc. Warrants") and is_non_common_security("Gen Digital Inc. Contingent Value Rights")
    assert is_non_common_security("Microchip Technology Incorporated Depositary Shares Each Representing a 1/20th Interest in a Share of 7.50% Series A Mandatory Convertible Preferred Stock")
    # Units of an operating partnership are its common equity; "(each representing the right to ...)" is a description.
    assert not is_non_common_security("Plains GP Holdings, L.P. Class A Units representing Limited Partner Interests")
    assert not is_non_common_security("America Movil S.A.B de C.V American Depositary Shares (each representing the right to receive twenty (20) Series B Shares)")


# --- discovery: what a statement does to the links already there --------------------------------

HELD = "Ollama consensus review hold: the opposite direction is published as edge #989; a human must decide which direction is correct."


def add_node(session, name, ticker, cap=1e10):
    node = Node(name=name, ticker=ticker, market_cap=cap, sector="Technology")
    session.add(node)
    session.commit()
    return node


def test_a_filing_statement_replaces_the_weaker_links_waiting_for_the_pair(monkeypatch):
    """NVIDIA showed one supplier because queued Wikipedia and AI-research links for TSMC, Micron
    and SK hynix counted as "already there" while waiting for a person."""
    session, nodes = make_session()
    monkeypatch.setattr(auto_discover_edges, "latest_annual_filing", lambda ticker: (FILING, NVDA_TEXT))

    def waiting(source, label, url, title, note, excerpt):
        row = Edge(source_id=nodes[source].id, target_id=nodes["NVDA"].id, dependency_type=label, source_url=url, source_title=title,
                   review_status="pending", review_note=note, evidence_excerpt=excerpt, confidence_score=0.8)
        session.add(row)
        return row

    same_label = waiting("TSM", "Foundry Services", "AI Multi-Source Research", "AI Multi-Source Research", HELD,
                         "Most fabless semiconductor companies such as Nvidia are customers of TSMC.")
    seed = waiting("TSM", "Advanced Silicon Fabrication", "Manual System Jumpstart", "Manual System Jumpstart",
                   "Ollama consensus review hold: published without an evidence excerpt; a human must confirm the source.", "")
    wiki = waiting("HXSCL", "Advanced Silicon Fabrication", "https://en.wikipedia.org/wiki/SK_Hynix", "WIKIPEDIA (Page: SK Hynix)",
                   "Ollama consensus review left pending: Evidence excerpt does not name both companies.", "The company also supplies the HBM3E to Nvidia.")
    published = Edge(source_id=nodes["MU"].id, target_id=nodes["NVDA"].id, dependency_type="HBM", review_status="approved",
                     review_note="Ollama consensus review: ok", source_url="AI Multi-Source Research", evidence_excerpt="Micron supplies HBM to Nvidia.")
    session.add_all([published])
    session.commit()
    known = auto_discover_edges.known_company_names(session)

    created = auto_discover_edges.discover_customer_concentration(session, nodes["NVDA"], known, NameIndex(known))
    session.commit()

    # TSMC: the same label, so the filing's sentence replaces the AI-research excerpt in place and the panel reviews it again.
    assert same_label.source_title.endswith("supplier-dependence disclosure)") and "We utilize foundries" in same_label.evidence_excerpt
    assert same_label.review_status == "pending" and same_label.review_note is None
    assert filer_documented_direction(same_label)
    # A curated seed waiting for a person is neither replaced nor rejected.
    assert seed.review_status == "pending" and seed.source_title == "Manual System Jumpstart"
    # SK hynix: a new link, and the weaker Wikipedia link it replaces is retired with a pointer.
    new = session.query(Edge).filter(Edge.source_id == nodes["HXSCL"].id, Edge.dependency_type == "Supply Relationship").one()
    assert new.review_status == "pending" and filer_documented_direction(new)
    assert wiki.review_status == "rejected" and wiki.review_note.startswith(auto_discover_edges.SUPERSEDED_NOTE) and f"#{new.id}" in wiki.review_note
    # Micron is already published (under another label): nothing is added.
    assert session.query(Edge).filter(Edge.source_id == nodes["MU"].id).count() == 1
    assert created == 1  # only SK hynix is a new edge; TSMC's was upgraded in place


def test_a_link_published_through_the_other_listing_is_already_known(monkeypatch):
    session, nodes = make_session()
    goog = add_node(session, "Alphabet Inc. Class C Capital Stock", "GOOG", 2.4e12)
    session.add(Edge(source_id=goog.id, target_id=nodes["SNOW"].id, dependency_type="Cloud Infrastructure Provider",
                     review_status="approved", review_note="Ollama consensus review: ok", source_url="AI Multi-Source Research",
                     evidence_excerpt="Alphabet provides cloud services to Snowflake."))
    session.commit()
    text = ("In addition, our platform currently operates on public cloud infrastructure provided by Amazon Web Services (AWS), "
            "Microsoft Azure (Azure), and Google Cloud Platform (GCP).")
    monkeypatch.setattr(auto_discover_edges, "latest_annual_filing", lambda ticker: ({**FILING, "form": "10-K"}, text))
    known = auto_discover_edges.known_company_names(session)

    auto_discover_edges.discover_customer_concentration(session, nodes["SNOW"], known, NameIndex(known))
    session.commit()

    suppliers = sorted(edge.source_node.ticker for edge in session.query(Edge).filter(Edge.target_id == nodes["SNOW"].id))
    assert suppliers == ["AMZN", "GOOG", "MSFT"], suppliers  # Alphabet once, not GOOG and GOOGL


def test_discovery_ignores_warrants_and_a_companys_own_other_listing(monkeypatch):
    session, nodes = make_session()
    iqv = add_node(session, "IQVIA Holdings Inc.", "IQV", 3e10)
    cing = add_node(session, "Cingulate Inc. Common Stock", "CING", 5e7)
    cingw = add_node(session, "Cingulate Inc. Warrants", "CINGW", 1e6)
    lifemd = add_node(session, "LifeMD, Inc.", "LFMD", 3e8)
    lifemdp = add_node(session, "LifeMD, Inc. 8.875% Series A Cumulative Perpetual Preferred Stock", "LFMDP", 3e7)
    reads = []

    def filing(ticker):
        reads.append(ticker)
        text = {"CING": "The parties will negotiate in good faith any changes to the services provided by IQVIA due to changes in priorities established by us.",
                "LFMDP": "We depend on LifeMD to deliver healthcare consultations through our platform."}.get(ticker, "")
        return ({**FILING, "form": "10-K"}, text)

    monkeypatch.setattr(auto_discover_edges, "latest_annual_filing", filing)
    known = auto_discover_edges.known_company_names(session)
    index = NameIndex(known, auto_discover_edges.known_company_caps(session))

    assert auto_discover_edges.discover_customer_concentration(session, cingw, known, index) == 0
    assert cingw.concentration_checked_at is not None and reads == [], "a warrant has no annual report: stamped, never fetched"
    assert auto_discover_edges.discover_customer_concentration(session, cing, known, index) == 1
    assert auto_discover_edges.discover_customer_concentration(session, lifemdp, known, index) == 0  # LifeMD is its own preferred shares' company
    session.commit()
    assert [(e.source_node.ticker, e.target_node.ticker) for e in session.query(Edge).all()] == [("IQV", "CING")]

    # The sweep does not pick warrant or preferred lines up at all.
    for node in (cing, cingw, lifemd, lifemdp):
        node.concentration_checked_at = None
    session.commit()
    reads.clear()
    auto_discover_edges.sweep_customer_concentration(session, known, limit=100, max_seconds=60, supplier_index=index)
    assert "CINGW" not in reads and "LFMDP" not in reads and "CING" in reads and "LFMD" in reads, reads
