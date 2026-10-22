"""Seeding the private-company registry and its curated edges into the graph."""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import seed_private_companies as seeder
from models import Base, Edge, Node
from private_companies import PRIVATE_ENTITY_TYPE, RegistryError

LONG_AGO = datetime(2020, 1, 1)
ORACLE_ON_OPENAI = "Oracle provides cloud infrastructure capacity to OpenAI, Inc. under a multi-year agreement."


def company(**overrides):
    entry = {
        "id": "OPENAI", "name": "OpenAI", "aliases": ["OpenAI, Inc.", "OpenAI Global, LLC"],
        "sector": "Technology", "industry": "Artificial Intelligence",
        "description": "AI research and products company; maker of ChatGPT and the GPT model family.",
        "website": "https://openai.com", "hq": "San Francisco, California, USA", "founded": 2015,
        "sources": ["https://openai.com/about"], "checked": "2026-10-22", "listed_as": None,
    }
    entry.update(overrides)
    return entry


def anthropic(**overrides):
    return company(
        id="ANTHROPIC", name="Anthropic", aliases=["Anthropic, PBC"], website="https://www.anthropic.com", founded=2021,
        description="AI safety and research company; maker of the Claude family of AI models.", **overrides,
    )


def seed_edge(**overrides):
    entry = {
        "source": "ORCL", "target": "OPENAI", "dependency_type": "Cloud Infrastructure Provider", "product": "AI compute capacity",
        "source_url": "https://www.sec.gov/Archives/edgar/data/1341439/orcl-20250531.htm", "source_title": "Oracle 10-K FY2025",
        "evidence_excerpt": ORACLE_ON_OPENAI, "checked": "2026-10-22",
    }
    entry.update(overrides)
    return entry


class Graph:
    """A temporary SQLite graph with the production session settings (no autoflush, foreign keys enforced)."""

    def __init__(self, tmp_path):
        self.tmp_path = tmp_path
        self.engine = create_engine("sqlite:///:memory:")
        event.listen(self.engine, "connect", lambda connection, _record: connection.execute("PRAGMA foreign_keys = ON"))
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, autoflush=False)
        self.registry_path = tmp_path / "private_companies.json"
        self.edges_path = tmp_path / "private_seed_edges.json"
        self.decisions_path = tmp_path / "edge_review_decisions.json"
        self.write_registry()
        self.write_edges()
        session = self.Session()
        for name, ticker, sector, cap in (
            ("Oracle Corporation Common Stock", "ORCL", "Technology", 7e11),
            ("NVIDIA Corporation Common Stock", "NVDA", "Technology", 4.5e12),
            ("Microsoft Corporation Common Stock", "MSFT", "Technology", 3.8e12),
        ):
            session.add(Node(name=name, ticker=ticker, sector=sector, industry="Software", market_cap=cap, current_price=100.0))
        session.commit()
        session.close()

    def write_registry(self, *companies):
        self.registry_path.write_text(json.dumps({"version": 1, "companies": list(companies)}), encoding="utf-8")

    def write_edges(self, *edges):
        self.edges_path.write_text(json.dumps({"version": 1, "edges": list(edges)}), encoding="utf-8")

    def run(self, prune=False):
        return seeder.run(self.registry_path, self.edges_path, self.decisions_path, prune=prune, session_factory=self.Session)

    def add_public(self, ticker, name, **fields):
        session = self.Session()
        session.add(Node(name=name, ticker=ticker, sector="Technology", industry="Software", market_cap=1e10, current_price=10.0, **fields))
        session.commit()
        session.close()

    def node(self, ticker):
        session = self.Session()
        try:
            return session.query(Node).filter(Node.ticker == ticker).first()
        finally:
            session.close()

    def edges(self):
        session = self.Session()
        try:
            return sorted(
                (edge.source_node.ticker, edge.target_node.ticker, edge.dependency_type, edge.review_status)
                for edge in session.query(Edge).all()
            )
        finally:
            session.close()

    def add_edge(self, source, target, dependency_type="Cloud Infrastructure Provider", status="pending", confidence=0.5, **fields):
        session = self.Session()
        source_node = session.query(Node).filter(Node.ticker == source).one()
        target_node = session.query(Node).filter(Node.ticker == target).one()
        edge = Edge(
            source_id=source_node.id, target_id=target_node.id, dependency_type=dependency_type, review_status=status,
            confidence_score=confidence, source_url="https://www.sec.gov/x.htm", source_title="SEC", evidence_excerpt=ORACLE_ON_OPENAI, **fields,
        )
        session.add(edge)
        session.commit()
        edge_id = edge.id
        session.close()
        return edge_id

    def backdate(self):
        session = self.Session()
        for row in session.query(Node).all():
            session.execute(Node.__table__.update().where(Node.__table__.c.id == row.id).values(last_updated=LONG_AGO))
        session.commit()
        session.close()


@pytest.fixture
def graph(tmp_path):
    return Graph(tmp_path)


# --- companies --------------------------------------------------------------------------------

def test_a_registry_company_becomes_a_private_node_without_market_data(graph):
    graph.write_registry(company(), anthropic())

    report = graph.run()

    assert report.created == ["OPENAI", "ANTHROPIC"]
    node = graph.node("OPENAI")
    assert (node.name, node.ticker, node.entity_type) == ("OpenAI", "OPENAI", PRIVATE_ENTITY_TYPE)
    assert (node.sector, node.industry) == ("Technology", "Artificial Intelligence")
    assert node.business_summary == "AI research and products company; maker of ChatGPT and the GPT model family."
    assert json.loads(node.metadata_json) == {
        "website": "https://openai.com", "hq": "San Francisco, California, USA", "founded": 2015,
        "aliases": ["OpenAI, Inc.", "OpenAI Global, LLC"], "registry": True,
    }
    for market_field in ("current_price", "percent_change", "market_cap", "enterprise_value", "trailing_pe", "forward_pe",
                         "price_to_book", "dividend_yield", "fifty_two_week_high", "fifty_two_week_low", "total_revenue",
                         "gross_margin", "target_price", "recommendation", "ceo_name", "employees"):
        assert getattr(node, market_field) is None, market_field


def test_a_run_that_changes_nothing_writes_nothing(graph):
    """The discovery step reads the newest private `last_updated` as "the registry changed"."""
    graph.write_registry(company(), anthropic())
    graph.write_edges(seed_edge())
    graph.run()
    graph.backdate()

    report = graph.run()

    assert (report.created, report.updated, report.unchanged) == ([], {}, 2)
    assert report.edges_created == [] and report.edges_existing == 1
    assert graph.node("OPENAI").last_updated == LONG_AGO
    assert graph.node("ANTHROPIC").last_updated == LONG_AGO
    assert len(graph.edges()) == 1


def test_only_the_fields_that_changed_are_updated(graph):
    graph.write_registry(company())
    graph.run()
    graph.backdate()

    graph.write_registry(company(description="AI research company; maker of ChatGPT."))
    report = graph.run()
    assert report.updated == {"OPENAI": ["business_summary"]}
    assert graph.node("OPENAI").business_summary == "AI research company; maker of ChatGPT."
    assert graph.node("OPENAI").last_updated != LONG_AGO

    graph.backdate()
    graph.write_registry(company(description="AI research company; maker of ChatGPT.", website="https://openai.com/about", aliases=["OpenAI, Inc."]))
    report = graph.run()
    assert report.updated == {"OPENAI": ["metadata"]}
    assert json.loads(graph.node("OPENAI").metadata_json)["aliases"] == ["OpenAI, Inc."]

    graph.write_registry(company(description="AI research company; maker of ChatGPT.", website="https://openai.com/about", aliases=["OpenAI Labs, Inc."], sector="Industrials", industry="Research", name="OpenAI Labs", hq=None))
    report = graph.run()
    assert report.updated == {"OPENAI": ["name", "sector", "industry", "metadata"]}


def test_market_fields_are_never_touched_by_seeding(graph):
    graph.write_registry(company())
    graph.run()
    session = graph.Session()
    node = session.query(Node).filter(Node.ticker == "OPENAI").one()
    node.market_cap, node.current_price = 123.0, 4.5
    session.commit()
    session.close()

    graph.write_registry(company(description="A different description of the same lab."))
    graph.run()

    node = graph.node("OPENAI")
    assert (node.market_cap, node.current_price) == (123.0, 4.5)
    assert node.business_summary == "A different description of the same lab."


def test_an_entry_that_is_already_a_public_company_is_refused_not_duplicated(graph, capsys, monkeypatch):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    graph.add_public("OPAI", "OpenAI Group, Inc. Common Stock")
    graph.write_registry(company(), anthropic())

    report = graph.run()

    assert report.created == ["ANTHROPIC"]
    assert list(report.refused) == ["OPENAI"]
    assert graph.node("OPENAI") is None
    out = capsys.readouterr().out
    assert "REFUSED" not in out and "Private company refused" in out
    assert "::warning title=Private company refused::OPENAI:" in out
    assert "same company as public OPAI" in out and "listed_as" in out


def test_an_alias_that_is_a_public_company_is_refused(graph):
    graph.add_public("ANTH", "Anthropic Labs, PBC Common Stock")
    graph.write_registry(company(aliases=["OpenAI, Inc.", "Anthropic Labs"]))
    report = graph.run()
    assert list(report.refused) == ["OPENAI"] and "alias 'Anthropic Labs' is the same company as public ANTH" in report.refused["OPENAI"][0]


def test_an_id_that_is_a_public_ticker_is_refused(graph):
    graph.add_public("OPENAI", "Some Other Company Common Stock")
    graph.write_registry(company())

    report = graph.run()

    assert report.refused["OPENAI"] == ["id OPENAI is already the ticker of public company 'Some Other Company Common Stock'"]
    assert graph.node("OPENAI").entity_type == "Company"  # the public node is untouched


def test_an_existing_private_node_stays_untouched_when_a_lookalike_goes_public(graph):
    graph.write_registry(company())
    graph.write_edges(seed_edge())
    graph.run()
    graph.backdate()
    graph.add_public("OPAI", "OpenAI Group, Inc. Common Stock")
    graph.write_registry(company(description="A changed description that must not be written."))

    report = graph.run()

    assert list(report.refused) == ["OPENAI"]
    assert graph.node("OPENAI").last_updated == LONG_AGO
    assert graph.node("OPENAI").business_summary != "A changed description that must not be written."
    assert len(graph.edges()) == 1


def test_a_node_missing_from_the_registry_is_reported_and_kept_until_pruned(graph, capsys, monkeypatch):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    graph.write_registry(company(), anthropic())
    graph.write_edges(seed_edge())
    graph.run()
    graph.write_registry(anthropic())

    report = graph.run()
    assert report.orphans == ["OPENAI"] and graph.node("OPENAI") is not None and len(graph.edges()) == 1
    out = capsys.readouterr().out
    assert "::warning title=Private company not in the registry::OPENAI" in out and "--prune-orphans" in out

    report = graph.run(prune=True)
    assert report.pruned == ["OPENAI"] and graph.node("OPENAI") is None
    assert graph.edges() == []
    assert graph.node("ANTHROPIC") is not None and graph.node("ORCL") is not None


def test_pruning_never_touches_public_companies(graph):
    graph.write_registry()
    report = graph.run(prune=True)
    assert report.pruned == [] and graph.node("ORCL").entity_type == "Company"


# --- seed edges -------------------------------------------------------------------------------

def test_a_seed_edge_becomes_a_pending_edge_with_its_real_source(graph):
    graph.write_registry(company())
    graph.write_edges(seed_edge(evidence_excerpt="Oracle   provides cloud infrastructure capacity\nto OpenAI, Inc. under a multi-year agreement."))

    report = graph.run()

    assert report.edges_created == ["ORCL -> OPENAI (Cloud Infrastructure Provider)"]
    session = graph.Session()
    edge = session.query(Edge).one()
    assert (edge.source_node.ticker, edge.target_node.ticker) == ("ORCL", "OPENAI")
    assert edge.review_status == "pending" and edge.review_note is None and edge.reviewed_at is None
    assert edge.source_url == "https://www.sec.gov/Archives/edgar/data/1341439/orcl-20250531.htm"
    assert edge.source_title == "Oracle 10-K FY2025"
    assert edge.evidence_excerpt == ORACLE_ON_OPENAI
    assert (edge.product, edge.confidence_score, edge.revenue_share) == ("AI compute capacity", seeder.SEED_EDGE_CONFIDENCE, None)
    session.close()


def test_a_private_company_can_be_the_supplier_too(graph):
    graph.write_registry(company())
    graph.write_edges(seed_edge(
        source="OPENAI", target="MSFT", dependency_type="AI Model Licensing", product="GPT models",
        evidence_excerpt="OpenAI licenses its GPT models to Microsoft for use in Azure OpenAI Service.",
    ))
    assert graph.run().edges_created == ["OPENAI -> MSFT (AI Model Licensing)"]


def test_existing_edges_are_left_alone(graph):
    graph.write_registry(company())
    graph.run()
    edge_id = graph.add_edge("ORCL", "OPENAI", status="rejected", confidence=0.2, review_note="Human review (2026-10-01): not supply.")
    graph.write_edges(seed_edge())

    report = graph.run()

    assert report.edges_created == [] and report.edges_existing == 1
    session = graph.Session()
    edge = session.get(Edge, edge_id)
    assert (edge.review_status, edge.confidence_score, edge.review_note) == ("rejected", 0.2, "Human review (2026-10-01): not supply.")
    assert edge.source_url == "https://www.sec.gov/x.htm"
    session.close()


def test_running_twice_creates_each_edge_once(graph):
    graph.write_registry(company(), anthropic())
    graph.write_edges(seed_edge(), seed_edge(source="NVDA", target="ANTHROPIC", dependency_type="AI Accelerator Chips", product="GPUs",
                                              evidence_excerpt="NVIDIA sells data center GPUs to Anthropic for training its Claude models."))
    graph.run()
    graph.run()
    assert len(graph.edges()) == 2


def test_an_edge_with_a_missing_end_is_skipped_and_the_rest_still_run(graph, capsys, monkeypatch):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    graph.write_registry(company())
    graph.write_edges(
        seed_edge(source="WMT", target="STRIPE", dependency_type="Payment Processing", evidence_excerpt="Stripe processes payments for Walmart marketplace sellers."),
        seed_edge(),
    )

    report = graph.run()

    assert report.edges_created == ["ORCL -> OPENAI (Cloud Infrastructure Provider)"]
    assert len(report.edges_skipped) == 1
    label, reason = report.edges_skipped[0]
    assert label == "edge 1 (WMT -> STRIPE)" and "skipped: WMT, STRIPE is not in the database" in reason
    assert "::warning title=Private seed edge not seeded::edge 1 (WMT -> STRIPE)" in capsys.readouterr().out


def test_an_edge_to_a_refused_company_is_skipped(graph):
    graph.add_public("OPAI", "OpenAI Group, Inc. Common Stock")
    graph.write_registry(company())
    graph.write_edges(seed_edge())
    report = graph.run()
    assert report.edges_created == [] and "OPENAI is not in the database" in report.edges_skipped[0][1]


@pytest.mark.parametrize("url", ["Manual System Jumpstart", "manual", "AI Multi-Source Research", "", "ftp://sec.gov/x"])
def test_an_edge_citing_a_label_instead_of_a_page_is_rejected(graph, url):
    graph.write_registry(company())
    graph.write_edges(seed_edge(source_url=url))
    report = graph.run()
    assert report.edges_created == [] and graph.edges() == []
    assert report.edges_skipped[0][1].startswith("rejected: source_url")


def test_an_edge_whose_excerpt_does_not_name_both_companies_is_rejected(graph):
    graph.write_registry(company())
    graph.write_edges(
        seed_edge(evidence_excerpt="Oracle provides cloud infrastructure capacity to a large AI customer under a multi-year agreement."),
        seed_edge(source="MSFT", evidence_excerpt="Cloud infrastructure capacity is provided to OpenAI, Inc. under a multi-year agreement."),
    )
    report = graph.run()
    assert graph.edges() == []
    assert "does not name target OPENAI" in report.edges_skipped[0][1]
    assert "does not name source MSFT" in report.edges_skipped[1][1]


def test_an_alias_is_enough_to_name_the_private_company(graph):
    graph.write_registry(company())
    graph.write_edges(seed_edge(evidence_excerpt="Oracle supplies cloud infrastructure capacity to OpenAI Global, LLC for AI training."))
    assert graph.run().edges_created == ["ORCL -> OPENAI (Cloud Infrastructure Provider)"]


def test_an_alias_that_is_not_in_the_registry_names_nobody(graph):
    graph.write_registry(company(aliases=["OpenAI, Inc."]))
    graph.write_edges(seed_edge(evidence_excerpt="Oracle supplies cloud infrastructure capacity to ChatGPT Holdings for AI training."))
    assert graph.run().edges_created == []


def test_a_non_supply_edge_is_rejected(graph):
    graph.write_registry(company())
    graph.write_edges(seed_edge(dependency_type="Strategic Investment", evidence_excerpt="Oracle announced a strategic investment in OpenAI, Inc. last quarter."))
    report = graph.run()
    assert graph.edges() == [] and "dependency_type" in report.edges_skipped[0][1]


def test_a_malformed_edge_entry_does_not_stop_the_others(graph):
    graph.write_registry(company())
    graph.write_edges({"source": "ORCL"}, "not an object", seed_edge())
    report = graph.run()
    assert report.edges_created == ["ORCL -> OPENAI (Cloud Infrastructure Provider)"]
    assert len(report.edges_skipped) == 2


def test_an_edge_the_pipeline_would_reject_is_not_seeded(graph):
    graph.write_registry(company())
    graph.write_edges(seed_edge(evidence_excerpt="Oracle formerly provided cloud infrastructure capacity to OpenAI until 2019."))
    report = graph.run()
    assert graph.edges() == [] and "pipeline would reject" in report.edges_skipped[0][1]


def test_a_seed_edge_is_judged_by_the_review_panels_own_rules(graph):
    """What passes here is not held or rejected by deterministic_review the same night."""
    from review_edges_with_ollama import deterministic_review

    graph.write_registry(company())
    graph.write_edges(seed_edge())
    graph.run()
    session = graph.Session()
    edge = session.query(Edge).one()
    from evidence_quality import registered_company_names

    with registered_company_names(session.query(Node.ticker, Node.name).all()):
        assert deterministic_review(edge) is None
    session.close()


# --- graduation -------------------------------------------------------------------------------

def graduating_graph(graph):
    """OpenAI seeded with edges, then listed as OPAI, which already holds some edges of its own."""
    graph.write_registry(company(), anthropic())
    graph.run()
    graph.add_public("OPAI", "OpenAI Group, Inc. Class A Common Stock")
    graph.add_edge("ORCL", "OPENAI", status="approved", confidence=0.9)                               # beats the twin below
    graph.add_edge("NVDA", "OPENAI", "AI Accelerator Chips", status="pending", confidence=0.9)        # loses to the twin below
    graph.add_edge("OPENAI", "MSFT", "AI Model Licensing", status="approved", confidence=0.8)        # moves as the supplier
    graph.add_edge("OPENAI", "OPAI", "Self Dealing", status="pending")                               # a link to its own listing
    graph.add_edge("ORCL", "OPAI", status="pending", confidence=0.95)                                # twin of the first
    graph.add_edge("NVDA", "OPAI", "AI Accelerator Chips", status="approved", confidence=0.5)        # twin of the second
    graph.add_edge("ORCL", "ANTHROPIC", status="approved")                                           # unrelated, untouched
    graph.write_registry(company(listed_as="OPAI"), anthropic())


def test_graduation_moves_the_edges_and_deletes_the_private_node(graph):
    graduating_graph(graph)

    report = graph.run()

    assert graph.node("OPENAI") is None
    assert graph.node("OPAI").entity_type == "Company"
    assert graph.edges() == [
        ("NVDA", "OPAI", "AI Accelerator Chips", "approved"),
        ("OPAI", "MSFT", "AI Model Licensing", "approved"),
        ("ORCL", "ANTHROPIC", "Cloud Infrastructure Provider", "approved"),
        ("ORCL", "OPAI", "Cloud Infrastructure Provider", "approved"),
    ]
    assert len(report.graduated) == 1
    line = report.graduated[0]
    assert line.startswith("OPENAI -> OPAI: moved 2 edge(s), dropped 2 duplicate(s)") and "1 link(s) to its own listing" in line
    assert "deleted the private node" in line and "Remove the entry" in line


def test_a_moved_edge_is_not_deleted_with_the_node_it_left(graph):
    """Edges moved by id still sit in the old node's relationship collections; a cascade over them would delete them."""
    graph.write_registry(company())
    graph.run()
    graph.add_public("OPAI", "OpenAI Group, Inc. Common Stock")
    graph.add_edge("ORCL", "OPENAI", status="approved")
    graph.add_edge("OPENAI", "MSFT", "AI Model Licensing", status="approved")
    graph.write_registry(company(listed_as="OPAI"))

    graph.run()

    assert graph.edges() == [("OPAI", "MSFT", "AI Model Licensing", "approved"), ("ORCL", "OPAI", "Cloud Infrastructure Provider", "approved")]


def test_the_better_reviewed_edge_is_kept_whichever_side_it_was_on(graph):
    graph.write_registry(company())
    graph.run()
    graph.add_public("OPAI", "OpenAI Group, Inc. Common Stock")
    graph.add_edge("ORCL", "OPENAI", status="pending", confidence=0.99)
    graph.add_edge("ORCL", "OPAI", status="rejected", confidence=0.1, review_note="Human review (2026-10-01): rejected.")
    graph.write_registry(company(listed_as="OPAI"))

    graph.run()

    # Reviewed beats unreviewed whatever the confidence, as in the duplicate-edge cleanup.
    assert graph.edges() == [("ORCL", "OPAI", "Cloud Infrastructure Provider", "rejected")]
    session = graph.Session()
    assert session.query(Edge).one().review_note == "Human review (2026-10-01): rejected."
    session.close()


def test_graduation_waits_until_the_public_ticker_exists(graph):
    graph.write_registry(company())
    graph.write_edges(seed_edge())
    graph.run()
    graph.write_registry(company(listed_as="OPAI"))

    report = graph.run()

    assert report.graduated == [] and "OPAI is not in the database yet" in report.graduating_later[0]
    assert graph.node("OPENAI") is not None and len(graph.edges()) == 1


def test_graduation_is_idempotent(graph):
    graduating_graph(graph)
    graph.run()
    before = graph.edges()

    report = graph.run()

    assert graph.edges() == before and graph.node("OPENAI") is None
    assert report.graduated == ["OPENAI -> OPAI: already folded into OPAI. Remove the entry from data/private_companies.json."]
    assert report.created == [] and graph.node("OPAI").entity_type == "Company"


def test_a_graduated_companys_seed_edge_resolves_to_its_public_ticker(graph):
    graduating_graph(graph)
    graph.write_edges(seed_edge())  # still names OPENAI
    graph.run()
    before = graph.edges()

    report = graph.run()

    assert graph.edges() == before
    assert report.edges_existing == 1 and report.edges_skipped == []


def test_graduation_carries_the_review_decisions_to_the_public_ticker(graph):
    graduating_graph(graph)
    base = {"source_name": "Oracle Corporation Common Stock", "dependency_type": "Cloud Infrastructure Provider", "product": "p",
            "confidence_score": 0.9, "revenue_share": None, "source_url": "https://www.sec.gov/x.htm", "source_title": "SEC",
            "evidence_excerpt": ORACLE_ON_OPENAI}
    graph.decisions_path.write_text(json.dumps({"exported_at": "2026-10-21T00:00:00+00:00", "decisions": [
        {**base, "edge_id": 1, "source_ticker": "ORCL", "target_ticker": "OPENAI", "target_name": "OpenAI", "review_status": "approved",
         "review_note": "Human review (2026-10-02): approved.", "reviewed_at": "2026-10-02T10:00:00"},
        {**base, "edge_id": 2, "source_ticker": "ORCL", "target_ticker": "OPAI", "target_name": "OpenAI Group", "review_status": "rejected",
         "review_note": "Automated cleanup: x", "reviewed_at": "2026-10-05T10:00:00"},
        {**base, "edge_id": 3, "source_ticker": "ORCL", "target_ticker": "ANTHROPIC", "target_name": "Anthropic", "review_status": "approved",
         "review_note": None, "reviewed_at": "2026-09-01T10:00:00"},
        {**base, "edge_id": 4, "source_ticker": "OPENAI", "target_ticker": "OPAI", "source_name": "OpenAI", "target_name": "OpenAI Group", "review_status": "rejected",
         "review_note": None, "reviewed_at": "2026-09-01T10:00:00"},
    ]}), encoding="utf-8")

    graph.run()

    saved = json.loads(graph.decisions_path.read_text(encoding="utf-8"))
    assert saved["exported_at"] == "2026-10-21T00:00:00+00:00"
    pairs = [(d["source_ticker"], d["target_ticker"], d["review_status"], d["target_name"]) for d in saved["decisions"]]
    # The human approval (rank 2) beats the automated rejection of the same link; the self-link decision is gone.
    assert pairs == [
        ("ORCL", "OPAI", "approved", "OpenAI Group, Inc. Class A Common Stock"),
        ("ORCL", "ANTHROPIC", "approved", "Anthropic"),
    ]
    assert saved["decisions"][0]["review_note"] == "Human review (2026-10-02): approved."


def test_the_decisions_file_is_only_rewritten_when_something_changed(graph):
    graph.decisions_path.write_text('{"decisions": [{"source_ticker": "ORCL", "target_ticker": "NVDA"}]}', encoding="utf-8")
    before = graph.decisions_path.read_bytes()
    assert seeder.retarget_decisions_file(graph.decisions_path, "OPENAI", "OPAI", "OpenAI Group") is False
    assert graph.decisions_path.read_bytes() == before
    assert seeder.retarget_decisions_file(graph.tmp_path / "absent.json", "A", "B", "B Inc") is False
    # A night with nothing to graduate never opens the file either.
    graph.write_registry(company())
    graph.run()
    assert graph.decisions_path.read_bytes() == before
    assert seeder.retarget_decisions_file(graph.decisions_path, "NVDA", "NVDB", "NVIDIA B") is True
    assert json.loads(graph.decisions_path.read_text(encoding="utf-8"))["decisions"] == [{"source_ticker": "ORCL", "target_ticker": "NVDB", "target_name": "NVIDIA B"}]


def test_retarget_decisions_leaves_unrelated_decisions_exactly_as_they_were():
    untouched = {"source_ticker": "AAPL", "target_ticker": "AAPL", "dependency_type": "x", "review_status": "approved"}
    result, changed = seeder.retarget_decisions([untouched], "OPENAI", "OPAI", "OpenAI Group")
    assert result == [untouched] and not changed


# --- empty and broken files -------------------------------------------------------------------

def test_empty_files_are_a_no_op(graph, capsys):
    report = graph.run()
    assert (report.created, report.updated, report.unchanged, report.refused, report.graduated) == ([], {}, 0, {}, [])
    assert graph.edges() == [] and graph.node("OPENAI") is None
    assert "0 registry entries, 0 seed edge(s)" in capsys.readouterr().out
    assert graph.node("ORCL").entity_type == "Company"


def test_missing_files_are_a_no_op(graph):
    report = seeder.run(graph.tmp_path / "none.json", graph.tmp_path / "none2.json", graph.decisions_path, session_factory=graph.Session)
    assert report.created == [] and report.edges_created == []


def test_main_succeeds_on_empty_files(graph, capsys, monkeypatch):
    monkeypatch.setattr(seeder, "SessionLocal", graph.Session)
    assert seeder.main(["--registry", str(graph.registry_path), "--edges", str(graph.edges_path)]) == 0
    assert "Private companies: 0 created" in capsys.readouterr().out


def test_an_invalid_registry_stops_the_run_before_anything_is_written(graph, capsys, monkeypatch):
    monkeypatch.setattr(seeder, "SessionLocal", graph.Session)
    graph.write_registry(anthropic(), company(sector="Real Estate"))
    graph.write_edges(seed_edge())

    assert seeder.main(["--registry", str(graph.registry_path), "--edges", str(graph.edges_path)]) == 1

    err = capsys.readouterr().err
    assert "nothing was changed" in err and "OPENAI: sector 'Real Estate'" in err
    assert graph.node("ANTHROPIC") is None and graph.node("OPENAI") is None and graph.edges() == []
    with pytest.raises(RegistryError):
        graph.run()


def test_a_registry_that_is_not_json_stops_the_run(graph, capsys, monkeypatch):
    monkeypatch.setattr(seeder, "SessionLocal", graph.Session)
    graph.registry_path.write_text("{oops", encoding="utf-8")
    assert seeder.main(["--registry", str(graph.registry_path), "--edges", str(graph.edges_path)]) == 1
    assert "could not be read" in capsys.readouterr().err


def test_a_failure_while_seeding_rolls_everything_back(graph, monkeypatch):
    graph.write_registry(company())
    graph.write_edges(seed_edge())

    def boom(*args, **kwargs):
        raise RuntimeError("database went away")

    monkeypatch.setattr(seeder, "seed_edges", boom)
    with pytest.raises(RuntimeError):
        graph.run()
    assert graph.node("OPENAI") is None


def test_the_summary_line_counts_everything(graph, capsys):
    graph.write_registry(company(), anthropic())
    graph.write_edges(seed_edge())
    graph.run()
    out = capsys.readouterr().out
    assert "[+] created OPENAI" in out and "[+] pending edge ORCL -> OPENAI" in out
    assert "Private companies: 2 created, 0 updated, 0 unchanged, 0 refused, 0 graduated, 0 orphaned. Seed edges: 1 created, 0 already present, 0 not seeded." in out


def test_the_seeded_nodes_use_the_production_session_settings(graph):
    """autoflush must be off like database.SessionLocal, or lookups would hide the bugs that matter in production."""
    assert graph.Session().autoflush is False
    from database import SessionLocal

    assert SessionLocal().autoflush is False
    assert datetime.now(timezone.utc).year >= 2026
