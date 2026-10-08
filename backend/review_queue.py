"""The human review queue, published for docs/review.html.

Every pending edge is listed with why it is waiting (from its review note), a
suggested action read from its evidence excerpt, and the edges that run the other
way between the same two companies, so a reviewer can settle a two-way pair at once.

Largest companies first: a link to NVIDIA or Walmart is on pages people actually open,
so clearing it first does the most for the site. Size is the larger endpoint's market
cap; the page can re-sort by age.
"""
import json
import os
import re
from datetime import datetime, timezone

from audit_data_quality import HELD_NOTE_PREFIX
from customer_concentration import filer_documented_direction
from database import SessionLocal
from evidence_quality import evidence_direction, evidence_support, register_company_names, requires_source_evidence
from models import Edge, Node

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REVIEW_QUEUE_PATH = os.path.join(BASE_DIR, "docs", "review_queue.json")
REVIEW_QUEUE_MAX_ITEMS = int(os.environ.get("HEPHAESTUS_REVIEW_PAGE_LIMIT", "3000"))

# (key, label, pattern on the review note). First match wins, so the specific holds
# come before the generic "the models could not agree".
CATEGORIES = (
    ("backwards_evidence", "Excerpt says the direction is reversed",
     r"supply runs the other way|excerpt describes the opposite direction"),
    ("reopened", "Reopened after a rule fix", r"reopened after (?:an evidence-rule|a supplier-statement rule) fix"),
    ("two_way", "Published in both directions", r"opposite direction (?:is also published|is already approved)"),
    ("model_backwards", "Its approving model called it backwards",
     r"own rationale says the direction is backwards|rationale indicates the relationship direction"),
    ("wrong_company", "A company may be mismatched", r"place or generic word"),
    ("unnamed", "Excerpt does not name both companies", r"does not name both companies"),
    ("unclear", "Excerpt does not say who supplies whom",
     r"cut off inside the list|not who supplies whom|does not describe a supply"),
    ("disclosure", "Filing disclosure needs a check",
     r"revenue share|shares total|mega-cap|10% disclosure threshold|filing sentence no longer"),
    ("save_failed", "The models' verdict could not be saved", r"could not be saved"),
    ("split_vote", "The models disagreed", r"consensus was insufficient"),
    ("reversal_vote", "The models voted to reverse it", r"Consensus \d+/\d+ for reverse"),
)
AWAITING_MODELS = ("awaiting_models", "Not reviewed by the models yet")
OTHER = ("other", "Other")
LABELS = dict((key, label) for key, label, _pattern in CATEGORIES) | dict((AWAITING_MODELS, OTHER))


def categorize(note):
    text = str(note or "")
    for key, _label, pattern in CATEGORIES:
        if re.search(pattern, text, re.IGNORECASE):
            return key
    if not text.startswith(HELD_NOTE_PREFIX):
        return AWAITING_MODELS[0]
    return OTHER[0]


def ticker(node):
    return getattr(node, "ticker", None) or "?"


def suggestion(edge):
    """What the evidence rules would do, for the reviewer to accept or overrule."""
    if not requires_source_evidence(edge.source_url) or filer_documented_direction(edge):
        return None
    source, target = edge.source_node, edge.target_node
    verdict, reason = evidence_support(edge.evidence_excerpt, source, target)
    if verdict == "unsupported":
        return {"action": "reject", "reason": reason[:1].upper() + reason[1:]}
    direction, phrase = evidence_direction(edge.evidence_excerpt, source, target)
    if verdict == "backwards" or direction == "backward":
        return {
            "action": "reverse",
            "reason": f"The excerpt says {ticker(target)} supplies {ticker(source)}" + (f': "{phrase[:120]}"' if phrase else "."),
        }
    if verdict == "supported" or direction == "forward":
        return {
            "action": "approve",
            "reason": f"The excerpt says {ticker(source)} supplies {ticker(target)}" + (f': "{phrase[:120]}"' if phrase else "."),
        }
    return None


def market_cap(node):
    value = getattr(node, "market_cap", None)
    return float(value) if isinstance(value, (int, float)) and value > 0 else None


def reach(edge):
    """The larger endpoint's market cap: how much traffic the link's pages see."""
    return max(market_cap(edge.source_node) or 0.0, market_cap(edge.target_node) or 0.0)


def queue_item(edge, mirrors):
    category = categorize(edge.review_note)
    return {
        "edge_id": edge.id,
        "source_ticker": ticker(edge.source_node),
        "source_name": getattr(edge.source_node, "name", "") or "",
        "target_ticker": ticker(edge.target_node),
        "target_name": getattr(edge.target_node, "name", "") or "",
        "source_market_cap": market_cap(edge.source_node),
        "target_market_cap": market_cap(edge.target_node),
        "type": edge.dependency_type or "",
        "product": edge.product or "",
        "revenue_share": edge.revenue_share,
        "confidence": edge.confidence_score,
        "source_url": edge.source_url or "",
        "source_title": edge.source_title or "",
        "evidence_excerpt": edge.evidence_excerpt or "",
        "review_note": edge.review_note or "",
        "category": category,
        "category_label": LABELS[category],
        "suggestion": suggestion(edge),
        "mirror_edge_ids": mirrors,
    }


def build_review_queue(session, limit=REVIEW_QUEUE_MAX_ITEMS, now=None):
    register_company_names(session.query(Node.ticker, Node.name).filter(Node.ticker.is_not(None)).all())
    pending = sorted(
        session.query(Edge).filter(Edge.review_status == "pending").all(),
        key=lambda edge: (-reach(edge), edge.id),
    )[:limit]
    # Opposite-direction edges that are still live (published or waiting). One pass
    # over the live edges; an OR per pair overflows SQLite's expression depth.
    reverse_ids = {}
    for other_id, other_source, other_target in session.query(Edge.id, Edge.source_id, Edge.target_id).filter(
        Edge.review_status != "rejected"
    ):
        reverse_ids.setdefault((other_target, other_source), []).append(other_id)
    items = [queue_item(edge, sorted(reverse_ids.get((edge.source_id, edge.target_id), []))) for edge in pending]
    total = session.query(Edge).filter(Edge.review_status == "pending").count()
    return {
        "generated_at": (now or datetime.now(timezone.utc)).isoformat(),
        "pending_count": total,
        "truncated": total > len(items),
        "categories": {key: LABELS[key] for key in LABELS},
        "items": items,
    }


def write_review_queue(path=REVIEW_QUEUE_PATH, session=None):
    own_session = session is None
    session = session or SessionLocal()
    try:
        payload = build_review_queue(session)
    finally:
        if own_session:
            session.close()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temporary = f"{path}.tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, separators=(",", ":"), ensure_ascii=False)
        handle.write("\n")
    os.replace(temporary, path)
    print(f"Review queue: {len(payload['items'])} pending edge(s) -> {path}")
    return payload


if __name__ == "__main__":
    write_review_queue()
