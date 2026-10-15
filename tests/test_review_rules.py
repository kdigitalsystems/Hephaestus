"""Review-panel rules: which companies the vehicle rule rejects, how votes are read,
how a failing model is handled. Each test pins a defect found in the nightly output.
"""
import json
import sys
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import review_edges_with_ollama as reviewer  # noqa: E402
from models import Base, Edge, Node  # noqa: E402


def memory_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False)


def node(name, ticker="X", sector="Technology", industry="Software - Application"):
    return SimpleNamespace(name=name, ticker=ticker, sector=sector, industry=industry)


# --- the non-operating vehicle rule -----------------------------------------------------

# Real names from the rejected links: every one is a fund, ETF, trust, bond issue or
# blank-check company and must keep matching.
TRUE_VEHICLES = [
    "Vanguard S&P 500 ETF",
    "iShares MSCI Italy ETF",
    "Tema Space Innovators ETF",  # "Space" is not the reason, "ETF" is
    "The iShares Bitcoin Premium Income ETF",
    "NUVEEN SELECT TAX-FREE INC",  # only "tax-free" says what it is
    "NEW GERMANY FUND Inc",
    "Taiwan Fund, Inc.",
    "United States Oil Fund, LP",
    "Global X Funds Global X Robotics & Artificial Intelligence ETF",
    "BlackRock Income Trust Inc.",
    "Sprott Physical Silver Trust",
    "Permian Basin Royalty Trust",
    "Entergy Arkansas, LLC First Mortgage Bonds, 4.875% Series due September 1, 2066",
    "Tennessee Valley Authority Power Bonds 1998 Series D due June 1, 2028",
    "Brookfield Infrastructure Finance ULC 5.000% Subordinated Notes due 2081",
    "T-Mobile US, Inc. 5.500% Senior Notes due June 2070",
    "Armada Acquisition Corp. III Warrant",
    "Globa Terra Acquisition Corporation Class A Ordinary Shares",
    "Space Asset Acquisition Corp. Warrants",
    "Atlas Blank Check Holdings",
    "Orion SPAC Holdings Inc.",
]

# Operating companies a substring test rejected, or would reject: "Space" is not " spac",
# "Our Bond" is a software company, "Notable"/"Fundamental"/"Trustmark" start with a marker.
OPERATING_COMPANIES = [
    "AST SpaceMobile, Inc. Class A Common Stock",
    "Space Exploration Technologies Corp. Class A Common Stock",
    "MDA Space Ltd.",
    "Sidus Space, Inc. Class A Common Stock",
    "York Space Systems Inc.",
    "Our Bond, Inc. Common Stock",
    "Notable Labs, Ltd. Common Stock",
    "Fundamental Global Inc.",
    "Funding Circle Holdings",
    "Trustmark Industries",
    "Incomedia Technologies",
    "Treasury Wine Estates",
    "Bondholder Software Corp.",
    "Taiwan Semiconductor Manufacturing Company Ltd.",
    "Applied Materials, Inc. Common Stock",
]


@pytest.mark.parametrize("name", TRUE_VEHICLES)
def test_funds_etfs_trusts_bonds_and_blank_check_companies_are_vehicles(name):
    assert reviewer.is_non_operating_vehicle(node(name, sector=None, industry=None)), name


@pytest.mark.parametrize("name", OPERATING_COMPANIES)
def test_operating_companies_are_not_vehicles_because_of_a_substring(name):
    assert not reviewer.is_non_operating_vehicle(node(name, sector="Industrials", industry="Aerospace & Defense")), name


def test_the_sector_and_industry_rules_are_unchanged():
    for sector in ("Financial Services", "Real Estate", "Shell Companies"):
        assert reviewer.is_non_operating_vehicle(node("Perfectly Ordinary Name", sector=sector))
    for industry in ("Asset Management", "REIT - Industrial", "Closed-End Fund - Debt", "Shell Companies"):
        assert reviewer.is_non_operating_vehicle(node("Perfectly Ordinary Name", industry=industry))
    assert not reviewer.is_non_operating_vehicle(None)


def link(source, target):
    return SimpleNamespace(
        id=1, source_id=1, target_id=2, source_url="Manual research", source_node=source, target_node=target,
        dependency_type="Satellite Communications", product="Direct-to-cell service", evidence_excerpt="x" * 40,
        confidence_score=0.9,
    )


def test_a_space_company_is_reviewed_by_the_panel_not_rejected_as_a_fund():
    nokia = node("Nokia Corporation", "NOK", "Technology", "Communication Equipment")
    asts = node("AST SpaceMobile, Inc. Class A Common Stock", "ASTS", "Communication Services", "Communication Equipment")
    assert reviewer.deterministic_review(link(nokia, asts)) is None

    etf = node("Tema Space Innovators ETF", "NASA", None, None)
    rejected = reviewer.deterministic_review(link(nokia, etf))
    assert rejected["action"] == "reject" and "Non-operating financial vehicle" in rejected["reason"]


# --- reopening the links the old rule rejected ------------------------------------------

VEHICLE_NOTE = (
    "Ollama consensus review: Non-operating financial vehicle or fund is not a useful operating "
    "supply-chain node: ASTS AST SpaceMobile, Inc. Class A Common Stock."
)


def test_wrong_vehicle_rejections_go_back_to_the_panel_and_nothing_else_does():
    from cleanup_reviewed_edges import reopen_wrong_vehicle_rejections

    Session = memory_session()
    session = Session()
    nodes = {
        "NOK": Node(name="Nokia Corporation", ticker="NOK", sector="Technology", industry="Communication Equipment"),
        "ASTS": Node(name="AST SpaceMobile, Inc. Class A Common Stock", ticker="ASTS", sector="Communication Services", industry="Communication Equipment"),
        "SPCX": Node(name="Space Exploration Technologies Corp. Class A Common Stock", ticker="SPCX", sector="Industrials", industry="Aerospace & Defense"),
        "VOO": Node(name="Vanguard S&P 500 ETF", ticker="VOO", sector=None, industry=None),
        "GS": Node(name="Goldman Sachs Group, Inc.", ticker="GS", sector="Financial Services", industry="Capital Markets"),
    }
    session.add_all(nodes.values())
    session.commit()

    def edge(source, target, label, note, status="rejected", **extra):
        row = Edge(source_id=nodes[source].id, target_id=nodes[target].id, dependency_type=label, review_status=status,
                   review_note=note, reviewed_at=extra.pop("reviewed_at", None), **extra)
        session.add(row)
        return row

    wrong = edge("NOK", "ASTS", "Satellite Communications Technology", VEHICLE_NOTE)
    older_note = edge("SPCX", "ASTS", "Launch Services", VEHICLE_NOTE.replace("consensus ", ""))
    real_fund = edge("NOK", "VOO", "Index membership", VEHICLE_NOTE)
    real_sector = edge("NOK", "GS", "Banking", VEHICLE_NOTE)
    by_a_person = edge("SPCX", "NOK", "Hardware", "Human review (2026-09-29): rejected. Not a real customer.")
    # The vehicle sentence quoted inside a model's rationale is not the vehicle rule at work.
    other_reason = edge("NOK", "SPCX", "Chips", "Ollama consensus review: Consensus 3/3 for reject (avg confidence 0.95; votes reject:3). "
                        "Lead rationale from qwen2.5:7b-instruct: Non-operating financial vehicle or fund is not what this is.")
    approved = edge("ASTS", "NOK", "Antennas", "Ollama consensus review: Consensus 3/3 for approve.", status="approved")
    session.commit()

    counts = {}
    reopen_wrong_vehicle_rejections(session, counts)
    session.commit()

    assert counts == {"reopened_vehicle_rule_fix": 2}
    for row in (wrong, older_note):
        assert row.review_status == "pending" and row.reviewed_at is None
        # A note the panel does not skip, so it reviews the link; never a hold, never approved.
        assert row.review_note.startswith("Automated cleanup:")
        assert not row.review_note.startswith(reviewer.HELD_NOTE_PREFIX)
    for row in (real_fund, real_sector, by_a_person, other_reason):
        assert row.review_status == "rejected"
    assert approved.review_status == "approved"

    again = {}
    reopen_wrong_vehicle_rejections(session, again)
    assert again == {}

    queue = Namespace(status="pending", include_held=False, source=None, target=None, edge_id=None, limit=100)
    assert {row.id for row in reviewer.selected_edges(session, queue)} == {wrong.id, older_note.id}


# --- reading one model's rationale -------------------------------------------------------

def vote(action, reason, sides=("source", "target"), confidence=0.95):
    return reviewer.normalize_review({
        "supplier_side": sides[0], "customer_side": sides[1], "action": action, "confidence": confidence,
        "relationship_type": "Foundry", "product": "wafers", "reason": reason,
    })


def read(action, reason, **kwargs):
    candidate = SimpleNamespace(
        source_node=SimpleNamespace(ticker="TSM", name="Taiwan Semiconductor Manufacturing Company Ltd."),
        target_node=SimpleNamespace(ticker="NVDA", name="NVIDIA Corporation"),
    )
    return reviewer.correct_review_for_reason(candidate, vote(action, reason, **kwargs))


@pytest.mark.parametrize("reason", [
    "TSMC is the sole foundry for NVIDIA's data-center GPUs; no direct alternative exists.",
    "Samsung is the main supplier of HBM memory to Apple and there is no direct substitute for it.",
    "The 10-K names TSMC as NVIDIA's foundry. Other suppliers are not a direct replacement.",
])
def test_no_direct_alternative_is_not_a_rejection(reason):
    # It used to become a reject at the model's own confidence, whatever the vote was.
    assert read("approve", reason)["action"] == "approve"
    assert read("pending", reason, sides=("unknown", "unknown"))["action"] == "pending"


@pytest.mark.parametrize("reason", [
    "Ford Motor Company and Mercury Systems do not have a direct supply relationship.",
    "T-Mobile US is not a direct supplier of Apple; it resells the phones.",
    "There is no direct evidence of a supply relationship between the two.",
])
def test_a_rationale_that_denies_the_relationship_is_no_vote_never_a_flip_to_reject(reason):
    assert read("approve", reason)["action"] == "pending"
    assert read("reverse", reason, sides=("target", "source"))["action"] == "pending"
    assert read("pending", reason, sides=("unknown", "unknown"))["action"] == "pending"
    # A model that said reject keeps its own vote.
    rejected = read("reject", reason, sides=("neither", "neither"))
    assert rejected["action"] == "reject"


@pytest.mark.parametrize("raw, expected", [
    (0.95, 0.95), (1, 1.0), (1.0, 1.0), (0, 0.0), ("0.9", 0.9),
    (95, 0.95), (87.5, 0.875), ("95%", 0.95), ("95", 0.95), (100, 1.0),
    (250, 0.0), (-3, 0.0), (float("nan"), 0.0), (float("inf"), 0.0), ("high", 0.0), (None, 0.0),
])
def test_confidence_is_read_on_either_scale(raw, expected):
    assert reviewer.parse_confidence(raw) == pytest.approx(expected)
    assert vote("approve", "TSMC fabricates NVIDIA GPUs.", confidence=raw)["confidence"] == pytest.approx(expected)


def panel_args():
    return Namespace(min_approve=0.85, min_reject=0.85, min_reverse=0.85, consensus_min_votes=2, consensus_min_ratio=0.66)


def review_of(action, sides, model, confidence=0.95):
    return {"action": action, "supplier_side": sides[0], "customer_side": sides[1], "confidence": confidence,
            "relationship_type": "Foundry", "product": "wafers", "reason": f"{model} says {action}", "model": model}


APPROVE = ("source", "target")
REVERSE = ("target", "source")
NEITHER = ("neither", "neither")
UNKNOWN = ("unknown", "unknown")
EDGE = SimpleNamespace(dependency_type="Foundry", product="wafers")


def panel(*votes):
    reviews = [review_of(action, sides, f"model{index}") for index, (action, sides) in enumerate(votes)]
    return reviewer.consensus_review(EDGE, reviews, panel_args())


def test_a_dissenting_direction_keeps_the_link_pending():
    """Two approvals and a reverse were approved 2/3: 60 of 839 live panel approvals."""
    held = panel(("approve", APPROVE), ("approve", APPROVE), ("reverse", REVERSE))
    assert held["action"] == "pending"
    assert "disagree on direction" in held["reason"] and "consensus was insufficient" in held["reason"]
    assert panel(("reverse", REVERSE), ("reverse", REVERSE), ("approve", APPROVE))["action"] == "pending"
    assert panel(("approve", APPROVE), ("reverse", REVERSE), ("approve", APPROVE))["action"] == "pending"


def test_other_two_of_three_panels_still_decide():
    assert panel(("approve", APPROVE), ("approve", APPROVE), ("approve", APPROVE))["action"] == "approve"
    assert panel(("approve", APPROVE), ("approve", APPROVE), ("pending", UNKNOWN))["action"] == "approve"
    # A reject dissent is a disagreement about whether the link exists, not about direction.
    assert panel(("approve", APPROVE), ("approve", APPROVE), ("reject", NEITHER))["action"] == "approve"
    assert panel(("reverse", REVERSE), ("reverse", REVERSE), ("reject", NEITHER))["action"] == "reverse"
    assert panel(("reject", NEITHER), ("reject", NEITHER), ("approve", APPROVE))["action"] == "reject"


# --- a model that fails ------------------------------------------------------------------

GOOD = json.dumps({"supplier_side": "source", "customer_side": "target", "action": "approve", "confidence": 95,
                   "relationship_type": "Foundry Services", "product": "GPU wafers",
                   "reason": "The excerpt says TSMC fabricates NVIDIA's GPUs."})


class FakeOllama:
    """Stands in for ollama.Client: one canned answer (or exception) per model."""

    def __init__(self, answers):
        self.answers = answers
        self.calls = []

    def __call__(self, timeout=None):
        return self

    def chat(self, model, messages, format, options):
        self.calls.append(model)
        answer = self.answers[model]
        if isinstance(answer, Exception):
            raise answer
        return {"message": {"content": answer}}


def pending_edge(session, ticker, name="Customer Co"):
    source = session.query(Node).filter_by(ticker="TSM").first()
    if source is None:
        source = Node(name="Taiwan Semiconductor Manufacturing Company Ltd.", ticker="TSM", sector="Technology", industry="Semiconductors")
        session.add(source)
    target = Node(name=name, ticker=ticker, sector="Technology", industry="Semiconductors")
    session.add(target)
    session.flush()
    row = Edge(source_id=source.id, target_id=target.id, dependency_type="Foundry", review_status="pending",
               source_url="Manual research", evidence_excerpt="TSMC fabricates the GPUs.", confidence_score=0.9)
    session.add(row)
    session.commit()
    return row


def run_reviewer(monkeypatch, Session, fake, models="a,b,c", *extra):
    monkeypatch.setattr(reviewer, "SessionLocal", Session)
    monkeypatch.setattr(reviewer.ollama, "Client", fake)
    monkeypatch.setattr(sys, "argv", ["review", "--models", models, "--apply", "--report", "", *extra])
    reviewer.main()


def test_one_unreachable_or_garbled_model_is_an_abstention(monkeypatch, tmp_path):
    Session = memory_session()
    session = Session()
    edge = pending_edge(session, "NVDA")
    fake = FakeOllama({"a": GOOD, "b": TimeoutError("timed out after 180 s"), "c": GOOD})

    run_reviewer(monkeypatch, Session, fake)

    session.expire_all()
    row = session.get(Edge, edge.id)
    # Two models agree and the third cast no vote: decided, instead of aborting the edge.
    assert row.review_status == "approved"
    assert "Consensus 2/3" in row.review_note and "pending:1" in row.review_note

    # Unparsable JSON is the same thing.
    other = pending_edge(session, "AMD", "Advanced Micro Devices, Inc.")
    run_reviewer(monkeypatch, Session, FakeOllama({"a": GOOD, "b": "I think this is fine!", "c": GOOD}))
    session.expire_all()
    assert session.get(Edge, other.id).review_status == "approved"


def test_a_garbled_model_cannot_manufacture_an_approval():
    first = review_of("approve", APPROVE, "a")
    broken = reviewer.abstention("b", TimeoutError("timed out"))
    third = review_of("reject", NEITHER, "c")
    result = reviewer.consensus_review(EDGE, [first, broken, third], panel_args())
    assert result["action"] == "pending"
    assert "no usable answer" in broken["reason"]


def test_an_edge_no_model_can_review_is_retried_a_bounded_number_of_times(monkeypatch):
    Session = memory_session()
    session = Session()
    edges = [pending_edge(session, f"T{index}", f"Customer {index}") for index in range(5)]
    down = FakeOllama({model: ConnectionError("connection refused") for model in "abc"})

    def note(row):
        session.expire_all()
        return session.get(Edge, row.id).review_note or ""

    run_reviewer(monkeypatch, Session, down)
    # Three edges in a row with no answer from any model: the run stops instead of
    # spending three timeouts on each of the rest.
    assert len(down.calls) == 9
    assert [note(row).startswith("Ollama review attempt 1 of 3") for row in edges] == [True, True, True, False, False]
    assert not any(note(row).startswith(reviewer.HELD_NOTE_PREFIX) for row in edges)

    run_reviewer(monkeypatch, Session, down)
    assert [note(row).startswith("Ollama review attempt 2 of 3") for row in edges[:3]] == [True, True, True]

    run_reviewer(monkeypatch, Session, down)
    # The third failure is a hold: the edges leave the queue instead of heading it every night.
    assert all(note(row).startswith(reviewer.HELD_NOTE_PREFIX) and "no model gave a usable answer" in note(row) for row in edges[:3])

    healthy = FakeOllama({model: GOOD for model in "abc"})
    run_reviewer(monkeypatch, Session, healthy)
    session.expire_all()
    # The queue has moved on to the edges that were starved behind them.
    assert [session.get(Edge, row.id).review_status for row in edges] == ["pending"] * 3 + ["approved"] * 2


def test_a_model_that_recovers_clears_the_retry_count(monkeypatch):
    Session = memory_session()
    session = Session()
    edge = pending_edge(session, "NVDA")
    run_reviewer(monkeypatch, Session, FakeOllama({model: TimeoutError("slow") for model in "abc"}))
    session.expire_all()
    assert session.get(Edge, edge.id).review_note.startswith("Ollama review attempt 1 of 3")

    run_reviewer(monkeypatch, Session, FakeOllama({model: GOOD for model in "abc"}))
    session.expire_all()
    row = session.get(Edge, edge.id)
    assert row.review_status == "approved" and row.review_note.startswith("Ollama consensus review: Consensus 3/3")
