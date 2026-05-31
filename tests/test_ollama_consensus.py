import sys
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from review_edges_with_ollama import consensus_review, split_models


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
