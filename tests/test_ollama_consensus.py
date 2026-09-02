import sys
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from models import Base, Edge, Node
from review_edges_with_ollama import (
    apply_reverse,
    consensus_review,
    correct_review_for_reason,
    deterministic_review,
    split_models,
)


def args():
    return Namespace(
        min_approve=0.85,
        min_reject=0.85,
        min_reverse=0.85,
        consensus_min_votes=2,
        consensus_min_ratio=0.66,
    )


def edge():
    return SimpleNamespace(dependency_type="Foundry", product="Advanced node wafers")


def test_ai_edge_without_source_excerpt_is_rejected_before_model_review():
    candidate = SimpleNamespace(
        source_id=1,
        target_id=2,
        source_url="AI Multi-Source Research",
        source_node=SimpleNamespace(ticker="SUP", name="Supplier", sector="Technology"),
        target_node=SimpleNamespace(ticker="CUS", name="Customer", sector="Technology"),
        dependency_type="Foundry",
        product="Advanced node wafers",
        evidence_excerpt=None,
        confidence_score=0.95,
    )

    result = deterministic_review(candidate)

    assert result["action"] == "reject"
    assert result["confidence"] == 1.0
    assert "no usable excerpt" in result["reason"]


def test_manufacturing_partnership_in_filing_excerpt_is_not_hard_rejected():
    candidate = SimpleNamespace(
        source_id=1,
        target_id=2,
        source_url="https://www.sec.gov/Archives/edgar/data/1/10k.htm",
        source_node=SimpleNamespace(ticker="TSM", name="Taiwan Semiconductor", sector="Technology", industry="Semiconductors"),
        target_node=SimpleNamespace(ticker="NVDA", name="NVIDIA", sector="Technology", industry="Semiconductors"),
        dependency_type="Foundry Services",
        product="GPUs",
        evidence_excerpt="Under a long-term manufacturing partnership, TSMC produces substantially all of NVIDIA GPUs at its fabs.",
        confidence_score=0.9,
    )

    assert deterministic_review(candidate) is None


def test_supplied_by_rationale_keeps_a_correct_reverse_vote():
    candidate = SimpleNamespace(
        source_node=SimpleNamespace(ticker="AMD", name="Advanced Micro Devices, Inc."),
        target_node=SimpleNamespace(ticker="TSM", name="Taiwan Semiconductor Manufacturing"),
    )
    review = {
        "action": "reverse",
        "supplier_side": "target",
        "customer_side": "source",
        "reason": "AMD is supplied by TSM with advanced node wafers, so the edge direction is backwards.",
    }

    assert correct_review_for_reason(candidate, review)["action"] == "reverse"


def test_reverse_does_not_resurrect_a_rejected_opposite_edge():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    supplier = Node(name="Supplier", ticker="SUP")
    customer = Node(name="Customer", ticker="CUS")
    session.add_all([supplier, customer])
    session.commit()
    proposed = Edge(source_id=customer.id, target_id=supplier.id, dependency_type="Foundry", review_status="pending")
    rejected = Edge(source_id=supplier.id, target_id=customer.id, dependency_type="Foundry", review_status="rejected")
    session.add_all([proposed, rejected])
    session.commit()

    result = apply_reverse(session, proposed, {"relationship_type": "Foundry", "product": "", "confidence": 0.95, "reason": "direction is backwards"})
    session.commit()

    assert result is None
    assert session.get(Edge, rejected.id).review_status == "rejected"
    assert session.get(Edge, proposed.id).review_status == "pending"
    assert "previously rejected" in session.get(Edge, proposed.id).review_note


def review(model, action, confidence=0.9, supplier_side="source", customer_side="target"):
    return {
        "model": model,
        "action": action,
        "supplier_side": supplier_side,
        "customer_side": customer_side,
        "confidence": confidence,
        "relationship_type": "Foundry",
        "product": "Advanced node wafers",
        "reason": f"{model} voted {action}",
    }


def test_split_models_trims_empty_values():
    assert split_models(" qwen2.5:7b-instruct, ,llama3.1:8b ") == [
        "qwen2.5:7b-instruct",
        "llama3.1:8b",
    ]


def test_consensus_approves_when_two_models_agree_on_direction():
    result = consensus_review(
        edge(),
        [
            review("qwen2.5:7b-instruct", "approve", 0.91),
            review("llama3.1:8b", "approve", 0.88),
            review("mistral:7b-instruct", "reject", 0.86, "neither", "neither"),
        ],
        args(),
    )

    assert result["action"] == "approve"
    assert result["supplier_side"] == "source"
    assert result["customer_side"] == "target"
    assert "approve:2" in result["votes"]


def test_consensus_holds_split_or_low_confidence_votes_pending():
    split_result = consensus_review(
        edge(),
        [
            review("qwen2.5:7b-instruct", "approve", 0.92),
            review("llama3.1:8b", "reverse", 0.91, "target", "source"),
            review("mistral:7b-instruct", "reject", 0.9, "neither", "neither"),
        ],
        args(),
    )
    low_confidence_result = consensus_review(
        edge(),
        [
            review("qwen2.5:7b-instruct", "approve", 0.84),
            review("llama3.1:8b", "approve", 0.83),
            review("mistral:7b-instruct", "pending", 0.6, "unknown", "unknown"),
        ],
        args(),
    )

    assert split_result["action"] == "pending"
    assert "insufficient" in split_result["reason"]
    assert low_confidence_result["action"] == "pending"


def test_consensus_can_reverse_when_direction_votes_match():
    result = consensus_review(
        edge(),
        [
            review("qwen2.5:7b-instruct", "reverse", 0.92, "target", "source"),
            review("llama3.1:8b", "reverse", 0.9, "target", "source"),
            review("mistral:7b-instruct", "pending", 0.6, "unknown", "unknown"),
        ],
        args(),
    )

    assert result["action"] == "reverse"
    assert result["supplier_side"] == "target"
    assert result["customer_side"] == "source"
