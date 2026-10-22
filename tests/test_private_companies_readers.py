"""The filing readers and private companies (OpenAI, Anthropic, Stripe ... named in public filers' annual reports).

A private company is a node with `entity_type == "Private Company"`, a registry id as its ticker, no market
cap, and its aliases in `metadata_json` (docs: the private-companies contract). These tests build such nodes
by hand, so they do not depend on the registry or the seeding code.
"""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import auto_discover_edges
from customer_concentration import (
    SUPPLIER_TITLE_MARKER,
    KnownNames,
    extract_disclosures,
    filer_documented_direction,
    mentioned_names,
    share_statement_problem,
)
from evidence_quality import mentions_company, strong_aliases
from private_companies import node_aliases
from models import Base, Edge, Node
from supplier_dependence import NameIndex, extract_supplier_dependencies, supplier_statement_problem

PRIVATE = "Private Company"

# (id, name, aliases): the shape the registry gives seeding. The ids are 6-14 characters, the aliases the
# strings filings use.
REGISTRY = [
    ("OPENAI", "OpenAI", ["OpenAI, Inc.", "OpenAI Global, LLC", "OpenAI OpCo, LLC"]),
    ("ANTHROPIC", "Anthropic", ["Anthropic, PBC", "Anthropic PBC"]),
    ("XAICORP", "xAI", ["xAI Corp.", "X.AI Holdings Corp."]),
    ("DATABRICKS", "Databricks", ["Databricks, Inc."]),
    ("STRIPE", "Stripe", ["Stripe, Inc."]),
    ("SCALEAI", "Scale AI", ["Scale AI, Inc."]),
    ("TOGETHERAI", "Together AI", ["Together Computer, Inc."]),
    ("MISTRALAI", "Mistral AI", ["Mistral AI SAS"]),
    ("PERPLEXITY", "Perplexity", ["Perplexity AI, Inc."]),
    ("HUGGINGFACE", "Hugging Face", ["Hugging Face, Inc."]),
    ("ANDURIL", "Anduril", ["Anduril Industries, Inc."]),
    ("SHEINGROUP", "Shein", ["SHEIN"]),
    ("CREDCLUB", "CRED", ["Dreamplug Technologies"]),
    ("PREFERREDNET", "Preferred Networks", ["Preferred Networks, Inc."]),
    ("PLAID", "Plaid", ["Plaid Inc."]),
    ("RAMPBUSINESS", "Ramp", ["Ramp Business Corporation"]),
    ("CURSORAI", "Cursor", ["Anysphere, Inc."]),
]
PRIVATE_NAMES = {name for _, name, _ in REGISTRY}

UNIVERSE = [
    ("Amazon.com, Inc. Common Stock", "AMZN", 2.3e12),
    ("Microsoft Corporation Common Stock", "MSFT", 3.5e12),
    ("PayPal Holdings, Inc. Common Stock", "PYPL", 6e10),
    ("NVIDIA Corporation Common Stock", "NVDA", 4e12),
    ("DoorDash, Inc. Class A Common Stock", "DASH", 9e10),
    ("CoreWeave, Inc. Class A Common Stock", "CRWV", 6e10),
    ("Oracle Corporation Common Stock", "ORCL", 6e11),
    ("Snowflake Inc.", "SNOW", 6e10),
    ("Acme Cloud Inc.", "ACME", 3e9),
    ("IDT Corporation Class B", "IDT", 1e9),  # a short all-caps listed name: held back by the market-cap guard
    ("Apple Inc. Common Stock", "AAPL", 3e12),
]


def registry_node(ident, name, aliases, **extra):
    return Node(
        name=name, ticker=ident, entity_type=PRIVATE, sector="Technology", industry="Artificial Intelligence",
        market_cap=None, metadata_json=json.dumps({"aliases": aliases, "registry": True}), **extra,
    )


def make_session(registry=REGISTRY):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, autoflush=False)()  # like database.SessionLocal
    nodes = {ticker: Node(name=name, ticker=ticker, market_cap=cap, sector="Technology") for name, ticker, cap in UNIVERSE}
    nodes.update({ident: registry_node(ident, name, aliases) for ident, name, aliases in registry})
    session.add_all(nodes.values())
    session.commit()
    return session, nodes


def reader_inputs(session):
    known = auto_discover_edges.known_company_names(session)
    return known, auto_discover_edges.build_supplier_index(session, known)


SESSION, NODES = make_session()
KNOWN, INDEX = reader_inputs(SESSION)


def suppliers(sentence, filer=("Acme Cloud", "Acme Cloud Inc.", "ACME")):
    return [found.supplier_name for found in extract_supplier_dependencies(sentence, INDEX, filer)]


def customers(sentence, filer=("Acme Cloud", "Acme Cloud Inc.", "ACME")):
    return [(d.customer_name, d.share_pct) for d in extract_disclosures(sentence, KNOWN, filer, require_customer_cue=False)]


# --- how the readers' name list is built --------------------------------------------------------------


def test_private_nodes_join_the_name_list_with_their_aliases_and_no_market_cap():
    assert isinstance(KNOWN, KnownNames)
    assert KNOWN.private == PRIVATE_NAMES
    assert KNOWN["OpenAI"] == "OpenAI" and "OpenAI OpCo, LLC" in KNOWN.aliases["OpenAI"] and "OpenAI OpCo" in KNOWN.aliases["OpenAI"]
    caps = auto_discover_edges.known_company_caps(SESSION)
    assert caps["OpenAI"] == 0 and caps["Microsoft Corporation Common Stock"] == 3.5e12
    # A malformed or missing alias list is no alias list, never an error.
    for broken in (None, "", "not json", '{"aliases": "OpenAI"}', '{"aliases": [1, null]}', "[]"):
        assert node_aliases(SimpleNamespace(metadata_json=broken)) == []


# --- the supplier reader: names, casing, ambiguity ---------------------------------------------------------


@pytest.mark.parametrize("sentence, expected", [
    ("We rely on models provided by OpenAI and Anthropic to power our assistant.", ["OpenAI", "Anthropic"]),
    ("We rely on Databricks for our data platform.", ["Databricks"]),
    ("We primarily rely on a third-party payment processor, Stripe, to process payments.", ["Stripe"]),
    ("We rely on xAI to train our models.", ["xAI"]),
    ("We depend on Scale AI for data labeling and on Mistral AI for our language models.", ["Scale AI", "Mistral AI"]),
    ("We rely on Perplexity for search and on Hugging Face for model hosting.", ["Perplexity", "Hugging Face"]),
    ("We rely on Anduril for the production of our autonomous systems.", ["Anduril"]),
    # Aliases are other spellings of the same company, and a legal form is not part of the name.
    ("We rely on OpenAI OpCo, LLC for inference capacity.", ["OpenAI"]),
    ("We rely on third-party providers, such as Anthropic PBC, for our AI features.", ["Anthropic"]),
    ("We rely on Anysphere, Inc. for coding assistance.", ["Cursor"]),
    ("We rely on Hugging Face, Inc. for model hosting.", ["Hugging Face"]),
    # A list of names is a list, not a longer proper noun.
    ("We rely on third-party providers such as OpenAI, Anthropic and Perplexity for our AI features.", ["OpenAI", "Anthropic", "Perplexity"]),
    # The company's own wordmark in capitals, for a name long enough to be no acronym.
    ("We rely on OPENAI for our models.", ["OpenAI"]),
    ("We rely on SHEIN for fulfilment.", ["Shein"]),
])
def test_a_private_company_named_in_a_supplier_statement_is_read(sentence, expected):
    assert suppliers(sentence) == expected


@pytest.mark.parametrize("sentence", [
    # Ordinary words that are also company names: only the capitalised, registered spelling counts.
    "We rely on stripe and plaid patterns and on a mistral wind for the design of our ramp, which we need to scale.",
    "We rely on the anthropic principle to scale our ramp up.",
    "We rely on xai for nothing and on Xai models and XAI techniques.",
    "We rely on explainable AI (XAI) for our models.",
    # A product or a longer name is not the company.
    "We rely on third-party providers such as OpenAI-compatible APIs for inference.",
    "We rely on Scale AI-powered tools for data labeling.",
    "We rely on Stripe Connect and Plaid Link for payments.",
    "We rely on our Stripe account for payments.",
    # Two clauses are not one name.
    "Together, AI will improve our products and we rely on that.",
    "We rely on Together, AI models and tools for our product.",
    # A hypothetical replacement, a competitor, an investee and a partner are not suppliers.
    "We rely on a replacement for Stripe or PayPal for payments, which may not meet our expectations.",
    "Our competitors include OpenAI, Anthropic and Perplexity.",
    "We hold a minority investment in OpenAI and have a strategic partnership with Anthropic.",
    "We engaged Stripe to sell our common stock under an at-the-market equity program.",
    "Our compensation arrangements are informed by the third-party data provided by Stripe.",
    "Our ability to operate our business depends upon the consent of Databricks.",
    "Our gold is smelted into bars, which are delivered to Databricks for refining.",
    "Our reserve figures were estimated using costs provided by Databricks.",
    "Our coverage is limited to the insurance provided by Databricks.",
    "We face competition from large language models built by OpenAI, Anthropic and Perplexity.",
    "We sell through our web store at Stripe.com and rely on Databricks.com for hosting.",
    "We rely on Stripe for payments, and our own web store is Stripe.com.",
    "Our costs are dependent on the price of oil, and according to Databricks the price fell last year.",
    "All services provided by Databricks for our fiscal year were approved by our Board.",
    "We do not rely on Databricks for our data platform.",
    "We no longer rely on Stripe for payments.",
    # Customers are not suppliers, private or not.
    "We depend on Stripe for a significant portion of our revenue.",
    "We rely on Plaid, our largest customer, to carry our full product line.",
])
def test_a_private_name_that_is_something_else_is_not_read(sentence):
    assert suppliers(sentence) == []


def test_a_name_followed_by_a_bracketed_short_form_is_read_in_its_whole_sentence():
    text = ("The plasma comes from a vendor. Databricks, Inc. (\u201cDatabricks\u201d) is our single source supplier "
            "of the polymer mesh.")
    found = extract_supplier_dependencies(text, INDEX, ("Acme Cloud",))
    assert [(r.supplier_name, r.sentence.startswith("Databricks, Inc. (\u201c") and r.sentence.endswith("of the polymer mesh.")) for r in found] == [("Databricks", True)]


def test_a_product_of_a_listed_company_is_the_listed_company():
    # "Azure Databricks" is Microsoft's service: the label before the name makes it a product, not Databricks.
    assert suppliers("We rely on Azure Databricks for our data lake.") == ["Microsoft Corporation Common Stock"]
    assert suppliers("We rely on AWS Lambda for compute.") == ["Amazon.com, Inc. Common Stock"]


def test_a_very_short_private_name_in_capitals_is_an_acronym_unless_a_legal_form_follows():
    assert suppliers("We rely on CRED for payments.") == []
    assert suppliers("We rely on CRED Inc. for payments.") == ["CRED"]
    # In a customer disclosure the verb right after the name is the corporate context.
    assert customers("During 2025 CRED was a customer that represented 14% of our revenue.") == []
    assert customers("CRED accounted for 14% of our revenue.") == [("CRED", 14.0)]
    # Five letters in capitals is a brand: "SHEIN".
    assert suppliers("We rely on SHEIN for fulfilment.") == ["Shein"]


def test_a_private_company_is_not_judged_by_a_market_cap_it_does_not_have():
    # The listed acronym with no cap to speak of stays held back ...
    assert suppliers("We rely on IDT for our DNA.") == []
    # ... and a private company is not, merely because its market cap is None.
    assert ("openai",) in INDEX.private_forms and suppliers("We rely on OpenAI for compute.") == ["OpenAI"]
    # Its spelling is what counts: capitals of a mixed-case name only when the name is long.
    assert suppliers("We rely on RAMP for expense management.") == []
    assert suppliers("We rely on Ramp for expense management.") == ["Ramp"]
    assert suppliers("We rely on PLAID for bank connections.") == []
    assert suppliers("We rely on Plaid for bank connections.") == ["Plaid"]


def test_an_acronym_the_filing_defines_as_something_else_is_not_the_company():
    text = ("The Secure Telemetry Reporting and Intelligent Processing Engine (STRIPE) tracks our devices. "
            "We rely on STRIPE for telemetry from our fleet.")
    assert suppliers(text) == []
    # The same capitals, with no definition saying otherwise, are the company's wordmark.
    assert suppliers("We rely on STRIPE for payment processing.") == ["Stripe"]


def test_a_word_the_filing_also_uses_in_lower_case_is_not_the_company():
    text = "We rely on Ramp for expense management. We must ramp up production, and the ramp is steep."
    assert suppliers(text) == []


def test_a_private_name_never_takes_a_listed_companys_name_nor_makes_it_ambiguous():
    session, _ = make_session(REGISTRY + [
        # An alias a listed company also carries, and a name two private companies share.
        ("NVIDIAAI", "Nvidia AI Labs", ["NVIDIA"]),
        ("DUPLICATEA", "Duplicate Labs", ["Twin Labs"]),
        ("DUPLICATEB", "Twin Holdings", ["Twin Labs"]),
    ])
    known, index = reader_inputs(session)
    nvidia = [r.supplier_name for r in extract_supplier_dependencies("We rely on NVIDIA for GPUs.", index)]
    assert nvidia == ["NVIDIA Corporation Common Stock"]
    assert [r.supplier_name for r in extract_supplier_dependencies("We rely on Twin Labs for chips.", index)] == []
    # The customer reader keeps the listed company too, and reads the private company by its own name.
    found = {d.customer_name for d in extract_disclosures("NVIDIA accounted for 12% of our revenue.", known, ("Acme",), False)}
    assert "NVIDIA Corporation Common Stock" in found


# --- the customer reader ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("sentence, expected", [
    ("OpenAI accounted for 20% of our total revenue.", [("OpenAI", 20.0)]),
    ("Our largest customer, Anthropic, accounted for 35% of our revenue in 2025.", [("Anthropic", 35.0)]),
    ("xAI accounted for 18% of our net sales.", [("xAI", 18.0)]),
    ("Sales to Databricks represented 14% of total revenue.", [("Databricks", 14.0)]),
    ("Customers such as Shein and Anthropic accounted for 15% and 12% of revenue, respectively.", [("Shein", 15.0), ("Anthropic", 12.0)]),
    # A list that holds a name the capital-letter rule alone would not count ("xAI") still maps by position.
    ("OpenAI, xAI and Anthropic accounted for 20%, 15% and 12% of our revenue, respectively.",
     [("OpenAI", 20.0), ("xAI", 15.0), ("Anthropic", 12.0)]),
    # An alias is another spelling of the same customer.
    ("OpenAI OpCo, LLC accounted for 25% of our revenue.", [("OpenAI", 25.0)]),
    ("Revenue from Anthropic PBC accounted for 33% of total revenue from customers.", [("Anthropic", 33.0)]),
    ("Sales to Scale AI accounted for 11% of revenue.", [("Scale AI", 11.0)]),
])
def test_a_private_company_named_in_a_customer_disclosure_is_read(sentence, expected):
    assert customers(sentence) == expected


@pytest.mark.parametrize("sentence", [
    "The plaid shirts accounted for 14% of our revenue from customers.",
    "Revenue from stripe patterns accounted for 14% of total revenue from customers.",
    "Our ramp business accounted for 14% of revenue from customers.",
    "Revenue from Ramp business customers accounted for 14% of revenue.",
    "Revenue from Scale accounted for 11% of total revenue from customers.",
    # The filer's own web address (and brand) is not a customer.
    "Approximately 51% of sales were processed on our store (Plaid.com), 40% on Amazon and 1% of our net revenues were generated from Plaid app subscriptions.",
    "Revenue from our Plaid Link product accounted for 11% of total revenue from customers.",
    # Not a customer's share: a rating, a ceiling, a peer list.
    "No customer other than OpenAI accounted for more than 10% of revenue.",
])
def test_a_private_name_that_is_something_else_is_not_a_customer(sentence):
    assert customers(sentence) == []


def test_a_one_word_name_the_filing_also_uses_as_a_word_needs_corporate_context():
    """Whether "Ramp" is the company or a production ramp is the filing's own evidence: it writes "ramp up" too."""
    plain = "We depend on a small number of customers. Ramp, our largest customer, accounted for 15% of our revenue."
    assert [(d.customer_name, d.share_pct) for d in extract_disclosures(plain, KNOWN, ("Acme",))] == [("Ramp", 15.0)]
    ordinary_word = plain + " We expect to ramp up production."
    assert extract_disclosures(ordinary_word, KNOWN, ("Acme",)) == []
    # With corporate context (a verb right after the name) it is the company even so.
    assert [d.customer_name for d in extract_disclosures(
        "We depend on a small number of customers. Ramp accounted for 15% of our revenue. We expect to ramp up production.", KNOWN, ("Acme",)
    )] == ["Ramp"]
    # A list that goes on after the name counts as context, too: the first of a list has nothing before it.
    listed = ("We depend on a small number of customers. Ramp, xAI and Anthropic accounted for 20%, 15% and 12% of revenue, respectively. "
              "We expect to ramp up production.")
    assert [(d.customer_name, d.share_pct) for d in extract_disclosures(listed, KNOWN, ("Acme",))] == [("Ramp", 20.0), ("xAI", 15.0), ("Anthropic", 12.0)]


def test_the_matched_alias_sets_where_a_customer_name_ends():
    from customer_concentration import matched_length

    sentence = "OpenAI OpCo, LLC accounted for 25% of our revenue."
    assert matched_length(sentence, 0, "OpenAI", KNOWN) == len("OpenAI OpCo, LLC")
    assert matched_length("OpenAI accounted for 25%.", 0, "OpenAI", KNOWN) == len("OpenAI")
    assert matched_length("Walmart accounted", 0, "Walmart Inc.", {"Walmart Inc.": "Walmart"}) == len("Walmart")


def test_a_short_private_name_needs_its_own_casing():
    assert customers("xAI accounted for 18% of our net sales.") == [("xAI", 18.0)]
    assert customers("XAI accounted for 18% of our net sales.") == []
    assert customers("Xai accounted for 18% of our net sales.") == []
    # A listed three-letter name is still too short for the customer reader.
    assert mentioned_names("IDT accounted for 18% of net sales.", KNOWN) == []


# --- direction: the filer wrote the sentence ---------------------------------------------------------------------


def stored(session, nodes, supplier, customer, sentence, title, share=None, filer="ACME"):
    prefix = {"ACME": "Acme Cloud", "DASH": "DoorDash"}.get(filer, filer)
    edge = Edge(
        source_id=nodes[supplier].id, target_id=nodes[customer].id, dependency_type="Revenue Concentration" if share else "Supply Relationship",
        product=f"{share:g}% of {filer} revenue" if share else f"Supplier named in {filer}'s 10-K", revenue_share=share, confidence_score=0.85,
        review_status="pending", source_url="https://www.sec.gov/Archives/edgar/data/1/x.htm", source_title=title,
        evidence_excerpt=f"{prefix} ({filer}) 10-K filed 2026-02-25: {sentence}",
    )
    session.add(edge)
    session.commit()
    return edge


SUPPLIER_TITLE = f"SEC EDGAR (10-K filed 2026-02-25; {SUPPLIER_TITLE_MARKER})"
CUSTOMER_TITLE = "SEC EDGAR (10-K filed 2026-02-25; customer-concentration disclosure)"


def test_the_filing_fixes_the_direction_whichever_side_the_private_company_is_on():
    session, nodes = make_session()
    supplier_side = stored(session, nodes, "DATABRICKS", "ACME", "We rely on Databricks for our data platform.", SUPPLIER_TITLE)
    customer_side = stored(session, nodes, "ACME", "OPENAI", "OpenAI accounted for 20% of our revenue.", CUSTOMER_TITLE, share=20.0)
    assert filer_documented_direction(supplier_side) and filer_documented_direction(customer_side)
    # The private company is never the filer: a statement "written" by it fixes nothing.
    backwards = stored(session, nodes, "ACME", "DATABRICKS", "We rely on Databricks for our data platform.", SUPPLIER_TITLE)
    assert not filer_documented_direction(backwards)
    # A private id is longer than any listed ticker, so it cannot be read as the filer's prefix either.
    odd = SimpleNamespace(source_node=nodes["ACME"], target_node=nodes["ANTHROPIC"], source_title=CUSTOMER_TITLE,
                          evidence_excerpt="Anthropic (ANTHROPIC) 10-K filed 2026-02-25: x")
    assert not filer_documented_direction(odd)


# --- discovery: the sweep reads a filing and links private companies -----------------------------------------

FILING = {"form": "10-K", "filing_date": "2026-02-25", "url": "https://www.sec.gov/Archives/edgar/data/1/acme-10k.htm"}
ACME_TEXT = """
Item 1A. Risk Factors. We depend on a small number of vendors.
We rely on Databricks for our data platform and on models provided by Anthropic and Stripe.
We also rely on Preferred Networks for training capacity.
We also depend on a small number of customers.
OpenAI accounted for 20% of our total revenue in fiscal 2025.
Revenue from Snowflake Inc. accounted for 12% of our total revenue.
Revenue from Preferred Networks accounted for 11% of our total revenue.
We compete with Perplexity and Scale AI, and we do not rely on Plaid.
We need to scale our operations.
"""
CONSENSUS = "Ollama consensus review: Consensus 3/3 for approve (avg confidence 0.95; votes approve:3). Lead rationale from qwen2.5:7b-instruct: ok"


def discover(session, nodes, monkeypatch, filer="ACME", text=ACME_TEXT):
    monkeypatch.setattr(auto_discover_edges, "latest_annual_filing", lambda ticker: (FILING, text))
    known, index = reader_inputs(session)
    created = auto_discover_edges.discover_customer_concentration(session, nodes[filer], known, index)
    session.commit()
    return created


def edge_pairs(session):
    return {(e.source_node.ticker, e.target_node.ticker, e.dependency_type) for e in session.query(Edge).all()}


def test_discovery_links_private_companies_in_both_directions(monkeypatch):
    session, nodes = make_session()
    private_before = {t: (n.last_updated, n.concentration_checked_at, n.last_researched_at) for t, n in nodes.items() if t in {i for i, _, _ in REGISTRY}}

    created = discover(session, nodes, monkeypatch)

    assert edge_pairs(session) == {
        ("DATABRICKS", "ACME", "Supply Relationship"),
        ("ANTHROPIC", "ACME", "Supply Relationship"),
        ("STRIPE", "ACME", "Supply Relationship"),
        # "Preferred" in a registry company's name is not a preferred share.
        ("PREFERREDNET", "ACME", "Supply Relationship"),
        ("ACME", "OPENAI", "Revenue Concentration"),
        ("ACME", "SNOW", "Revenue Concentration"),
        ("ACME", "PREFERREDNET", "Revenue Concentration"),
    }
    assert created == 7
    by_pair = {(e.source_node.ticker, e.target_node.ticker): e for e in session.query(Edge).all()}
    customer = by_pair[("ACME", "OPENAI")]
    assert (customer.revenue_share, customer.product, customer.review_status) == (20.0, "20% of ACME revenue", "pending")
    supplier = by_pair[("DATABRICKS", "ACME")]
    assert supplier.product == "Supplier named in ACME's 10-K" and supplier.source_title.endswith("supplier-dependence disclosure)")
    # The filing fixes both directions, and the review step sees both companies named.
    for edge in by_pair.values():
        assert filer_documented_direction(edge)
        assert mentions_company(edge.evidence_excerpt, edge.source_node) and mentions_company(edge.evidence_excerpt, edge.target_node)
    # Nothing was written to a private node: its timestamp is the registry's, and a write would move it.
    for ticker, before in private_before.items():
        node = nodes[ticker]
        assert (node.last_updated, node.concentration_checked_at, node.last_researched_at) == before
    assert nodes["ACME"].concentration_checked_at is not None
    # Reading the same filing again adds nothing.
    assert discover(session, nodes, monkeypatch) == 0
    assert session.query(Edge).count() == 7


def test_a_private_node_is_never_read_as_a_filer(monkeypatch):
    session, nodes = make_session()
    calls = []
    monkeypatch.setattr(auto_discover_edges, "latest_annual_filing", lambda ticker: (calls.append(ticker), (FILING, ACME_TEXT))[1])
    known, index = reader_inputs(session)
    assert auto_discover_edges.discover_customer_concentration(session, nodes["OPENAI"], known, index) == 0
    assert calls == [] and nodes["OPENAI"].concentration_checked_at is None


def test_a_private_alias_reads_the_same_in_a_filing_and_in_its_stored_sentence(monkeypatch):
    """Cursor is Anysphere in the filing: the alias finds it, and the review step's name check sees it too."""
    session, nodes = make_session()
    text = "We rely on Anysphere, Inc. for coding assistance. Revenue from Anysphere accounted for 11% of our revenue in 2025."
    assert discover(session, nodes, monkeypatch, text=text) == 2
    for edge in session.query(Edge).all():
        assert mentions_company(edge.evidence_excerpt, edge.source_node if edge.source_node.ticker == "CURSORAI" else edge.target_node)
        assert filer_documented_direction(edge)


# --- cleanup and the audit gate: a stored statement survives the nightly re-read exactly as a listed one does -------


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


def seed_pipeline(Session, registry=REGISTRY):
    session = Session()
    nodes = {ticker: Node(name=name, ticker=ticker, market_cap=cap, sector="Technology") for name, ticker, cap in UNIVERSE}
    nodes.update({ident: registry_node(ident, name, aliases) for ident, name, aliases in registry})
    session.add_all(nodes.values())
    session.commit()
    return session, nodes


def test_cleanup_and_the_audit_gate_agree_with_discovery_about_private_companies(pipeline_db, monkeypatch, capsys):
    from audit_data_quality import audit_database
    from cleanup_reviewed_edges import SUPPLIER_REJECTION_NOTE, cleanup_reviewed_edges

    session, nodes = seed_pipeline(pipeline_db)
    discover(session, nodes, monkeypatch)
    for edge in session.query(Edge).all():  # the model panel approves what discovery found
        edge.review_status, edge.review_note = "approved", CONSENSUS
    # A statement an older reader accepted and the current one does not (a competitor list), naming a private company.
    stale = stored(session, nodes, "PERPLEXITY", "ACME", "We compete with Perplexity and Scale AI.", SUPPLIER_TITLE)
    stale.review_status, stale.review_note = "approved", CONSENSUS
    # Statements only a reader that knows the registry accepts: a name that starts in lower case, and an alias.
    lowercase_initial = stored(session, nodes, "XAICORP", "ACME", "We rely on xAI to train our models.", SUPPLIER_TITLE)
    by_alias = stored(session, nodes, "CURSORAI", "ACME", "We rely on Anysphere, Inc. for coding assistance.", SUPPLIER_TITLE)
    for edge in (lowercase_initial, by_alias):
        edge.review_status, edge.review_note = "approved", CONSENSUS
    # A person's approval of a statement the reader would not accept stands.
    human = stored(session, nodes, "MISTRALAI", "ACME", "We hold shares in Mistral AI.", SUPPLIER_TITLE)
    human.review_status, human.review_note = "approved", "Human review (2026-10-20): approved against the filing."
    session.commit()
    kept = [e.id for e in session.query(Edge).filter(Edge.id.notin_([stale.id, human.id])).all()]
    assert lowercase_initial.id in kept and by_alias.id in kept

    capsys.readouterr()
    with pytest.raises(SystemExit):
        audit_database(fail_on_warnings=True)  # the gate sees the stale private-company statement ...
    assert "Unsupported supplier-statement warnings: 1" in capsys.readouterr().out

    cleanup_reviewed_edges()
    session.expire_all()
    # ... cleanup rejects exactly that one, leaves what discovery found and what a person approved ...
    assert session.get(Edge, stale.id).review_status == "rejected" and session.get(Edge, stale.id).review_note.startswith(SUPPLIER_REJECTION_NOTE)
    assert session.get(Edge, human.id).review_status == "approved"
    assert {session.get(Edge, i).review_status for i in kept} == {"approved"}
    audit_database(fail_on_warnings=True)  # ... and the gate is clean.

    # A second run changes nothing.
    snapshot = sorted((e.id, e.review_status, e.review_note, e.revenue_share) for e in session.query(Edge).all())
    cleanup_reviewed_edges()
    session.expire_all()
    assert sorted((e.id, e.review_status, e.review_note, e.revenue_share) for e in session.query(Edge).all()) == snapshot
    audit_database(fail_on_warnings=True)


def test_the_reread_of_a_stored_customer_share_reads_private_names_like_discovery(pipeline_db, monkeypatch):
    from cleanup_reviewed_edges import cleanup_reviewed_edges

    session, nodes = seed_pipeline(pipeline_db)
    # List mapping with a lower-case-initial name: 20/15/12 belong to OpenAI/xAI/Anthropic. A wrong stored
    # share (the first figure for xAI) is corrected in place; a right one is left alone.
    sentence = "OpenAI, xAI and Anthropic accounted for 20%, 15% and 12% of our revenue, respectively."
    wrong = stored(session, nodes, "ACME", "XAICORP", sentence, CUSTOMER_TITLE, share=20.0)
    right = stored(session, nodes, "ACME", "ANTHROPIC", sentence, CUSTOMER_TITLE, share=12.0)
    for edge in (wrong, right):
        edge.review_status, edge.review_note = "approved", CONSENSUS
    session.commit()
    cleanup_reviewed_edges()
    session.expire_all()
    assert (session.get(Edge, wrong.id).revenue_share, session.get(Edge, wrong.id).review_status) == (15.0, "approved")
    assert (session.get(Edge, right.id).revenue_share, session.get(Edge, right.id).review_status) == (12.0, "approved")


def test_a_private_company_is_never_grouped_with_a_listed_one_of_the_same_name():
    session, nodes = make_session(REGISTRY)
    listed = Node(name="Ramp Common Stock", ticker="RMPC", market_cap=1e9, sector="Industrials")
    sibling = Node(name="Ramp Class A Common Stock", ticker="RMPA", market_cap=1e9, sector="Industrials")
    session.add_all([listed, sibling])
    session.commit()
    assert auto_discover_edges.company_node_ids(session, listed) == {listed.id, sibling.id}
    assert auto_discover_edges.company_node_ids(session, nodes["RAMPBUSINESS"]) == {nodes["RAMPBUSINESS"].id}


# --- the filing sweep: private nodes are no filers, and a registry change is a reason to read again ---------------


NOW = datetime(2026, 11, 3, 12, tzinfo=timezone.utc)


def sweep_world(monkeypatch, registry_updated, checked_days_ago):
    """Four listed filers read `checked_days_ago` days ago, the registry last changed `registry_updated` days ago."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, autoflush=False)()
    filers = [
        Node(name=f"Filer {name} Inc.", ticker=name, market_cap=cap, sector="Technology", concentration_checked_at=NOW - timedelta(days=checked_days_ago),
             last_updated=NOW - timedelta(hours=1))  # market data is refreshed daily: only a private node's timestamp is the registry's
        for name, cap in (("AAA", 4e11), ("BBB", 3e11), ("CCC", 2e11), ("DDD", 1e11))
    ]
    private = registry_node("OPENAI", "OpenAI", ["OpenAI, Inc."], last_updated=NOW - timedelta(days=registry_updated))
    # A private node must not be swept even if a bad row gave it a market cap.
    private.market_cap = 9e12
    session.add_all([*filers, private])
    session.commit()
    read = []
    monkeypatch.setattr(auto_discover_edges, "utc_now", lambda: NOW)
    monkeypatch.setattr(auto_discover_edges, "latest_annual_filing", lambda ticker: (read.append(ticker), (None, None))[1])
    monkeypatch.setattr(auto_discover_edges, "SWEEP_RULES_UPDATED", datetime(2026, 10, 1, tzinfo=timezone.utc))
    return session, private, read


def test_the_sweep_never_reads_a_private_node_as_a_filer(monkeypatch):
    session, private, read = sweep_world(monkeypatch, registry_updated=2, checked_days_ago=400)
    stamp = private.last_updated
    auto_discover_edges.sweep_customer_concentration(session, auto_discover_edges.known_company_names(session), limit=10, max_seconds=60)
    assert read == ["AAA", "BBB", "CCC", "DDD"]
    session.refresh(private)
    assert private.concentration_checked_at is None and private.last_updated == stamp


def test_a_registry_change_makes_every_filing_due_and_the_daily_limit_spreads_them(monkeypatch):
    session, private, read = sweep_world(monkeypatch, registry_updated=2, checked_days_ago=10)
    known = auto_discover_edges.known_company_names(session)
    # Read 10 days ago, registry changed 2 days ago: all four are due, two per day, largest first.
    auto_discover_edges.sweep_customer_concentration(session, known, limit=2, max_seconds=60)
    assert read == ["AAA", "BBB"]
    auto_discover_edges.sweep_customer_concentration(session, known, limit=2, max_seconds=60)
    assert read == ["AAA", "BBB", "CCC", "DDD"]
    # Every filing has now been read after the change: nothing is due until the 120-day cycle.
    auto_discover_edges.sweep_customer_concentration(session, known, limit=2, max_seconds=60)
    assert read == ["AAA", "BBB", "CCC", "DDD"]


def test_filings_read_after_the_registry_last_changed_are_not_due(monkeypatch):
    session, private, read = sweep_world(monkeypatch, registry_updated=20, checked_days_ago=10)
    auto_discover_edges.sweep_customer_concentration(session, auto_discover_edges.known_company_names(session), limit=10, max_seconds=60)
    assert read == []


def test_filing_due_weighs_the_interval_the_rules_marker_and_the_registry(monkeypatch):
    monkeypatch.setattr(auto_discover_edges, "SWEEP_RULES_UPDATED", datetime(2026, 10, 1, tzinfo=timezone.utc))
    monkeypatch.setattr(auto_discover_edges, "CONCENTRATION_RECHECK_DAYS", 120)
    due = auto_discover_edges.filing_due
    registry = NOW - timedelta(days=5)
    assert due(NOW - timedelta(days=10), NOW) is False  # no registry change: the plain cycle
    assert due(NOW - timedelta(days=10), NOW, registry_updated=registry) is True
    assert due(NOW - timedelta(days=4), NOW, registry_updated=registry) is False
    assert due((NOW - timedelta(days=10)).replace(tzinfo=None), NOW, registry_updated=registry.replace(tzinfo=None)) is True  # SQLite is naive
    assert due(None, NOW, registry_updated=registry) is True
    assert due(NOW - timedelta(days=121), NOW, registry_updated=NOW - timedelta(days=200)) is True  # the old cycle still applies
    # A timestamp from the future (clock skew) can never make a filing permanently due.
    assert due(NOW, NOW, registry_updated=NOW + timedelta(days=3)) is False


def test_the_rules_marker_postdates_the_reader_that_could_not_see_private_companies():
    assert auto_discover_edges.SWEEP_RULES_UPDATED >= datetime(2026, 10, 22, tzinfo=timezone.utc)


# --- LLM discovery: private companies are counterparties, never subjects ------------------------------------------


def test_private_nodes_are_never_researched_as_subjects(monkeypatch):
    session, nodes = make_session()
    for ident in ("OPENAI", "ANTHROPIC"):
        nodes[ident].market_cap = 5e12  # even a stray market cap does not make one a subject
    session.commit()
    for deep_dive in (False, True):
        queue = auto_discover_edges.select_research_queue(session, 50, deep_dive=deep_dive)
        assert queue and not any(node.entity_type == PRIVATE for node in queue)
    # The daily quota is the eligible universe over the cooldown: private nodes are not part of it.
    monkeypatch.setattr(auto_discover_edges, "MIN_RESEARCH_DAILY_QUOTA", 1)
    monkeypatch.setattr(auto_discover_edges, "RESEARCH_COOLDOWN_DAYS", 1.0)
    monkeypatch.setattr(auto_discover_edges, "RESEARCH_DAILY_QUOTA", 0)
    assert auto_discover_edges.research_quota(session) == len(UNIVERSE)


def test_a_private_company_resolves_by_exact_name_or_alias_and_never_by_fuzzy_match(monkeypatch):
    session, nodes = make_session()

    def no_network(*args, **kwargs):
        raise AssertionError("a name that is exactly a private company must not reach Yahoo")

    monkeypatch.setattr(auto_discover_edges, "yq_search", no_network)
    resolve = auto_discover_edges.resolve_counterparty
    for name, ident in [
        ("OpenAI", "OPENAI"), ("OpenAI, Inc.", "OPENAI"), ("openai", "OPENAI"), ("OpenAI OpCo, LLC", "OPENAI"), ("Anysphere", "CURSORAI"),
        ("Cursor", "CURSORAI"), ("Scale AI", "SCALEAI"), ("Hugging Face, Inc.", "HUGGINGFACE"), ("xAI", "XAICORP"), ("X.AI Holdings Corp.", "XAICORP"),
    ]:
        assert resolve(session, None, name) is nodes[ident], name
    # A ticker the model made up does not outrank an exact name; a listed company still resolves as before.
    assert resolve(session, "STRP", "Stripe") is nodes["STRIPE"]
    assert resolve(session, "NVDA", "NVIDIA Corporation") is nodes["NVDA"]
    assert resolve(session, None, "Microsoft Corporation") is nodes["MSFT"]
    assert auto_discover_edges.node_named(session, "Databricks") is nodes["DATABRICKS"]
    # Near misses are nothing: no fuzzy match turns a name into a private company or a private company into a public one.
    searched = []
    monkeypatch.setattr(auto_discover_edges, "yq_search", lambda value: (searched.append(value), {"quotes": [{"symbol": "OPENAI", "quoteType": "EQUITY"}]})[1])
    for name in ("Open AI", "OpenAI Labs", "Anthropic Claude Holdings", "Scale", "Mistral"):
        assert resolve(session, None, name) is None, name
    assert searched, "the near misses went to the listed-company search, which cannot return a private node"


def test_an_alias_a_listed_company_also_carries_resolves_to_the_listed_company():
    session, nodes = make_session(REGISTRY + [("NVIDIAAI", "Nvidia AI Labs", ["NVIDIA"])])
    assert auto_discover_edges.exact_private_node(session, "NVIDIA") is None
    assert auto_discover_edges.exact_private_node(session, "Nvidia AI Labs") is nodes["NVIDIAAI"]
    # Two private companies sharing a name is no exact match either.
    session2, _ = make_session(REGISTRY + [("TWINA", "Twin A", ["Twin Labs"]), ("TWINB", "Twin B", ["Twin Labs"])])
    assert auto_discover_edges.exact_private_node(session2, "Twin Labs") is None


# --- the review step sees the registry's aliases too ---------------------------------------------------------------


def test_the_review_steps_name_check_knows_a_private_companys_aliases():
    cursor = NODES["CURSORAI"]
    assert "anysphere" in strong_aliases(cursor)
    assert mentions_company("Acme Cloud (ACME) 10-K filed 2026-02-25: We rely on Anysphere for coding assistance.", cursor)


# --- the same rules, whoever is named: hand-labelled real sentences with the name swapped for a private one --------

SUPPLIER_LABELS = json.loads((ROOT / "tests" / "data" / "supplier_statement_labels.json").read_text(encoding="utf-8"))
CUSTOMER_LABELS = json.loads((ROOT / "tests" / "data" / "customer_share_labels.json").read_text(encoding="utf-8"))
SWAP_NAMES = [
    "OpenAI", "Anthropic", "Databricks", "Stripe", "Scale AI", "Hugging Face", "xAI", "Mistral AI", "Perplexity", "Anduril", "Plaid", "Canva", "SpaceX", "Cargill",
]
SWAP_ALIASES = {"OpenAI": ("OpenAI, Inc.",)}


def swapped_names(public_names):
    known = KnownNames(dict(public_names), aliases=SWAP_ALIASES, private=set(SWAP_NAMES))
    known.update({name: name for name in SWAP_NAMES})
    return known


def test_a_private_supplier_passes_through_the_same_rules_as_a_listed_one():
    """Each hand-labelled real supplier statement (532 right, 123 wrong), with the supplier's name replaced by a
    private company's wherever the supplier is named once. The private name must be read when the listed one was and
    not read when it was not: the sentence rules (customers, auditors, drug partners, negations, former suppliers ...)
    do not care who is named. A private name followed by more capitalised words ("Plaid Education") is a longer
    name, which the listed run never had to decide."""
    from supplier_dependence import company_key, is_non_common_security, same_company

    names = {item["name"]: auto_discover_edges.clean_company_name(item["name"]) for item in SUPPLIER_LABELS["universe"]}
    listed = NameIndex(names, {item["name"]: item["market_cap"] for item in SUPPLIER_LABELS["universe"]})
    private = NameIndex(swapped_names(names), {item["name"]: item["market_cap"] for item in SUPPLIER_LABELS["universe"]})
    alias_only = {"aws", "tsmc", "amd", "ibm", "azure", "foxconn", "supermicro", "onsemi", "amazon web services", "google cloud", "microsoft azure"}
    compared = 0
    differences, kept_wrong = [], []
    for link in SUPPLIER_LABELS["links"]:
        if is_non_common_security(link["filer"]) or is_non_common_security(link["supplier"]) or same_company(link["supplier"], link["filer"]):
            continue
        spans = [(s, e) for s, e, d in listed.find(link["sentence"], set(), None) if company_key(d) == company_key(link["supplier"])]
        if len(spans) != 1 or link["sentence"][spans[0][0]:spans[0][1]].lower() in alias_only:
            continue  # named twice, or by an acronym: the swap would not be like for like
        swap = SWAP_NAMES[compared % len(SWAP_NAMES)]
        compared += 1
        swapped = link["sentence"][:spans[0][0]] + swap + link["sentence"][spans[0][1]:]
        filer = (link["filer"], auto_discover_edges.clean_company_name(link["filer"]), link["filer_ticker"])
        kept_listed = company_key(link["supplier"]) in {company_key(r.supplier_name) for r in extract_supplier_dependencies(link["sentence"], listed, filer)}
        kept_private = swap in {r.supplier_name for r in extract_supplier_dependencies(swapped, private, filer)}
        if kept_listed != kept_private:
            differences.append((link["id"], link["label"], kept_listed, kept_private))
        if link["label"] == "W" and kept_private:
            kept_wrong.append(link["id"])
    assert compared >= 340, compared
    # The only differences are names that continue as a longer name ("Plaid Education", "Anduril Asia Marketplace").
    assert len(differences) <= 3 and all(d[2] and not d[3] for d in differences), differences  # never reads a name the listed run did not
    assert set(kept_wrong) <= {3673, 3063}, kept_wrong  # test_supplier_dependence.KNOWN_MISSES: FedEx, a customer in a supplier's words; Avista, a valuation firm


def test_a_private_customer_passes_through_the_same_rules_as_a_listed_one():
    """The 182 hand-labelled customer disclosures with the customer's name replaced by a private company's: the
    share the reader gives it is the share it gave the listed company, for lists, tables, appositives and ranges."""
    from customer_concentration import name_pattern

    names = {item["name"]: auto_discover_edges.clean_company_name(item["name"]) for item in CUSTOMER_LABELS["universe"]}
    private = swapped_names(names)
    compared, differences = 0, []
    for link in CUSTOMER_LABELS["links"]:
        cleaned = names.get(link["customer"])
        if link["label"] == "unconfirmed" or not cleaned:
            continue
        matches = list(name_pattern(cleaned).finditer(link["sentence"]))
        if len(matches) != 1:
            continue
        swap = SWAP_NAMES[compared % len(SWAP_NAMES)]
        compared += 1
        swapped = link["sentence"][:matches[0].start()] + swap + link["sentence"][matches[0].end():]
        filer = (link["filer"], auto_discover_edges.clean_company_name(link["filer"]))
        listed_share = [d.share_pct for d in extract_disclosures(link["sentence"], names, filer, require_customer_cue=False) if d.customer_name == link["customer"]]
        private_share = [d.share_pct for d in extract_disclosures(swapped, private, filer, require_customer_cue=False) if d.customer_name == swap]
        if (max(listed_share) if listed_share else None) != (max(private_share) if private_share else None):
            differences.append(link["id"])
    assert compared >= 150, compared
    # "OpenAI Trading, Inc" and "OpenAI Logistics" are other companies: the private name is right not to be read.
    assert set(differences) <= {"CNX>DTE", "GSIT>AVT"}, differences


# --- the LLM queue end to end: subjects are listed companies, counterparties may be private --------------------------


@pytest.mark.parametrize("registry_changed_days_ago, expected_reads", [(2, ["ALPH", "BETA"]), (20, [])])
def test_the_llm_queue_researches_listed_subjects_links_private_counterparties_and_honours_a_registry_change(
    tmp_path, monkeypatch, registry_changed_days_ago, expected_reads
):
    import auto_discover_edges as discovery

    engine = create_engine(f"sqlite:///{tmp_path / 'graph.db'}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False)
    now = datetime.now(timezone.utc)
    session = Session()
    checked = now - timedelta(days=10)
    session.add_all([
        Node(name="Alpha Inc.", ticker="ALPH", market_cap=5e9, sector="Technology", concentration_checked_at=checked),
        Node(name="Beta Inc.", ticker="BETA", market_cap=4e9, sector="Technology", concentration_checked_at=checked),
        registry_node("OPENAI", "OpenAI", ["OpenAI, Inc."], last_updated=now - timedelta(days=registry_changed_days_ago)),
    ])
    session.commit()
    private_stamp = session.query(Node).filter(Node.ticker == "OPENAI").one().last_updated
    session.close()

    excerpt = "Alpha Inc. licenses its language models from OpenAI under a multi-year agreement."
    blob = f"SOURCE: WIKIPEDIA (Page: Alpha; https://en.wikipedia.org/wiki/Alpha)\nDATA:\n{excerpt} " + "Alpha builds software. " * 30
    dependencies = [
        {"source_company": "OpenAI, Inc.", "target_company": "Alpha Inc.", "source_ticker": None, "target_ticker": "ALPH",
         "dependency_type": "AI Model Supply", "product": "language models", "evidence_excerpt": excerpt, "confidence_score": 0.9,
         "evidence_source_url": "https://en.wikipedia.org/wiki/Alpha"},
        {"source_company": "Zorgon Private LLC", "target_company": "Alpha Inc.", "source_ticker": None, "target_ticker": "ALPH",
         "dependency_type": "AI Model Supply", "product": "language models", "evidence_excerpt": excerpt, "confidence_score": 0.9,
         "evidence_source_url": "https://en.wikipedia.org/wiki/Alpha"},
    ]
    reads, researched = [], []
    monkeypatch.setattr(discovery, "SessionLocal", Session)
    monkeypatch.setattr(discovery, "USE_SEC_SOURCE", True)
    monkeypatch.setattr(discovery, "sweep_customer_concentration", lambda *a, **k: {"checked": 0, "created": 0})
    monkeypatch.setattr(discovery, "discover_customer_concentration", lambda session, company, known, index: (reads.append(company.ticker), 0)[1])
    monkeypatch.setattr(discovery, "SWEEP_RULES_UPDATED", datetime(2026, 1, 1, tzinfo=timezone.utc))
    monkeypatch.setattr(discovery.IntelGatherer, "get_wiki_data", staticmethod(lambda name, ticker: (researched.append(ticker), blob)[1]))
    for collector in ("get_sec_data", "get_sec_exhibits", "get_yahoo_news"):
        monkeypatch.setattr(discovery.IntelGatherer, collector, staticmethod(lambda *a, **k: ""))
    monkeypatch.setattr(discovery.IntelGatherer, "get_additional_sources", staticmethod(lambda *a, **k: ""))
    monkeypatch.setattr(discovery, "extract_dependencies", lambda *a, **k: {"dependencies": [dict(d) for d in dependencies]} if researched.count("ALPH") == 1 else {"dependencies": []})
    monkeypatch.setattr(discovery, "yq_search", lambda value: {"quotes": []})  # the listed-company search finds nothing
    monkeypatch.setattr(discovery.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(discovery, "write_discovery_summary", lambda summary, **k: None)

    discovery.auto_discover_supply_chain(limit=5)

    check = Session()
    # Only listed companies are researched, and each filing is read again only if the registry changed after it was.
    assert sorted(researched) == ["ALPH", "BETA"]
    assert sorted(reads) == expected_reads
    openai = check.query(Node).filter(Node.ticker == "OPENAI").one()
    assert openai.last_researched_at is None and openai.concentration_checked_at is None and openai.last_updated == private_stamp
    # The counterparty a model named by its exact registry alias is linked; an unregistered private name is still dropped.
    assert [(e.source_node.ticker, e.target_node.ticker, e.review_status) for e in check.query(Edge).all()] == [("OPENAI", "ALPH", "pending")]


# --- the real commands, twice: discovery's statement path, the review step, cleanup and the audit gate -----------------

OLLAMA_STUB = '''
import json


class Client:
    def __init__(self, *args, **kwargs):
        pass

    def chat(self, model=None, messages=None, format=None, options=None, **kwargs):
        verdict = {"supplier_side": "source", "customer_side": "target", "action": "approve", "confidence": 0.95,
                   "relationship_type": "", "product": "", "reason": "The excerpt describes a direct supply relationship in the stated direction."}
        return {"message": {"content": json.dumps(verdict)}}


def chat(**kwargs):
    return Client().chat(**kwargs)
'''

REHEARSAL_SEED = '''
import json, sys
sys.path.insert(0, "backend")
import database
database.init_db()
from database import SessionLocal
from models import Node
import auto_discover_edges as discovery

session = SessionLocal()
session.add_all([
    Node(name="Acme Cloud Inc.", ticker="ACME", sector="Technology", industry="Software", market_cap=3e9),
    Node(name="Snowflake Inc.", ticker="SNOW", sector="Technology", industry="Software", market_cap=6e10),
    Node(name="OpenAI", ticker="OPENAI", entity_type="Private Company", sector="Technology", industry="Artificial Intelligence",
         metadata_json=json.dumps({"aliases": ["OpenAI, Inc.", "OpenAI OpCo, LLC"], "registry": True})),
    Node(name="xAI", ticker="XAICORP", entity_type="Private Company", sector="Technology", industry="Artificial Intelligence",
         metadata_json=json.dumps({"aliases": ["X.AI Holdings Corp."], "registry": True})),
    Node(name="Cursor", ticker="CURSORAI", entity_type="Private Company", sector="Technology", industry="Artificial Intelligence",
         metadata_json=json.dumps({"aliases": ["Anysphere, Inc."], "registry": True})),
])
session.commit()

TEXT = """
We rely on xAI to train our models and on coding assistance from Anysphere, Inc. for our engineers.
We depend on a small number of customers.
OpenAI OpCo, LLC accounted for 21% of our total revenue in fiscal 2025.
"""
discovery.latest_annual_filing = lambda ticker: ({"form": "10-K", "filing_date": "2026-02-25", "url": "https://www.sec.gov/Archives/edgar/data/1/acme.htm"}, TEXT)
known = discovery.known_company_names(session)
index = discovery.build_supplier_index(session, known)
acme = session.query(Node).filter(Node.ticker == "ACME").one()
print("created", discovery.discover_customer_concentration(session, acme, known, index))
session.commit()
'''


def test_the_real_commands_run_twice_and_the_gate_stays_clean(tmp_path):
    import os
    import shutil
    import subprocess

    shutil.copytree(ROOT / "backend", tmp_path / "backend", ignore=shutil.ignore_patterns("__pycache__", "*.db", "*.db-*"))
    for name in ("docs", "data", "reports", "stubs"):
        (tmp_path / name).mkdir()
    (tmp_path / "data" / "edge_review_decisions.json").write_text(json.dumps({"decisions": []}))
    (tmp_path / "stubs" / "ollama.py").write_text(OLLAMA_STUB)
    (tmp_path / "seed.py").write_text(REHEARSAL_SEED)
    env = {**os.environ, "PYTHONPATH": str(tmp_path / "stubs"), "PYTHONDONTWRITEBYTECODE": "1", "HEPHAESTUS_USE_SEC_SOURCE": "1"}
    env.pop("HEPHAESTUS_DB_PATH", None)

    def run(*args):
        result = subprocess.run([sys.executable, *args], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=180)
        assert result.returncode == 0, f"{args} failed:\n{result.stdout[-2500:]}\n{result.stderr[-2500:]}"
        return result.stdout

    assert "created 3" in run("seed.py")
    review = run("backend/review_edges_with_ollama.py", "--models", "stub-a,stub-b,stub-c", "--status", "pending", "--limit", "50", "--apply",
                 "--max-seconds", "60", "--min-approve", "0.85", "--min-reverse", "0.85", "--min-reject", "0.85",
                 "--consensus-min-votes", "2", "--consensus-min-ratio", "0.66", "--report", "reports/review.csv")
    assert "'approve': 3" in review, review[-1500:]

    def snapshot():
        import sqlite3

        with sqlite3.connect(tmp_path / "backend" / "supply_chain.db") as connection:
            return connection.execute(
                "select s.ticker, t.ticker, e.dependency_type, e.review_status, e.review_note, e.revenue_share "
                "from edges e join nodes s on s.id = e.source_id join nodes t on t.id = e.target_id order by e.id"
            ).fetchall()

    before = snapshot()
    assert {(s, t, status) for s, t, _, status, _, _ in before} == {
        ("XAICORP", "ACME", "approved"), ("CURSORAI", "ACME", "approved"), ("ACME", "OPENAI", "approved"),
    }
    for _ in range(2):  # the nightly order: cleanup, then the gate
        run("backend/cleanup_reviewed_edges.py")
        run("backend/audit_data_quality.py", "--fail-on-warnings")
        assert snapshot() == before, "cleanup or the audit changed a statement discovery had just found"
