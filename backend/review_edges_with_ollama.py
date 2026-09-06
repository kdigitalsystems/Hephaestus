import argparse
import csv
import json
import os
import re
import time
from datetime import datetime, timezone

import ollama
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError

from database import SessionLocal
from evidence_quality import (
    has_non_supply_relationship,
    is_role_label,
    requires_source_evidence,
    unsupported_ai_evidence,
)
from models import Edge, Node


DEFAULT_MODEL = os.environ.get("HEPHAESTUS_REVIEW_MODEL", "qwen2.5:7b-instruct")
DEFAULT_MODELS = os.environ.get("HEPHAESTUS_REVIEW_MODELS")
VALID_ACTIONS = {"approve", "reject", "reverse", "pending"}
NON_OPERATING_SECTORS = {"financial services", "real estate", "shell companies"}
NON_OPERATING_NAME_MARKERS = [
    " fund",
    " etf",
    " trust",
    " tax-free",
    " municipal",
    " income",
    " treasury",
    " bond",
    " note",
    " acquisition corp",
    " spac",
]
UNCERTAIN_REASON_MARKERS = [
    "not clear",
    "unclear",
    "not enough",
    "insufficient",
    "likely",
    "may be",
    "might",
    "no evidence",
    "unknown",
    "does not",
    "no direct",
    "not a supply",
    "not explicitly stated",
    "suggesting",
    "would be",
    "would use",
]

REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "supplier_side": {"type": "string", "enum": ["source", "target", "neither", "unknown"]},
        "customer_side": {"type": "string", "enum": ["source", "target", "neither", "unknown"]},
        "action": {"type": "string", "enum": sorted(VALID_ACTIONS)},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "relationship_type": {"type": "string"},
        "product": {"type": "string"},
        "reason": {"type": "string"},
    },
    "required": ["supplier_side", "customer_side", "action", "confidence", "relationship_type", "product", "reason"],
}


SYSTEM_PROMPT = """
You are reviewing a public-company supply chain graph.

The graph direction must always be:
supplier/provider/manufacturer/logistics provider/material provider -> customer/receiver/buyer.

Decide whether the proposed edge is correct.

Return JSON only.

First identify which side is the supplier/provider and which side is the customer/receiver:
- supplier_side must be "source", "target", "neither", or "unknown".
- customer_side must be "source", "target", "neither", or "unknown".

Actions:
- approve: the direction is correct and the relationship is a real operational supply-chain dependency.
- reverse: the relationship is real, but the direction is backwards.
- reject: this is not a supply-chain relationship, is only a competitor/partner/investor/acquisition/news relationship, or is too speculative.
- pending: the available context is insufficient and you cannot make a reliable decision.

Important rules:
- TSMC/TSM manufactures chips for AMD, NVIDIA, Apple, Broadcom, Marvell, and many fabless semiconductor companies, so TSM is upstream of those firms.
- ASML supplies lithography equipment to chip manufacturers such as TSM, Samsung, Intel, and Micron.
- Micron supplies memory products to computing/device/platform companies.
- Vertiv supplies power/cooling infrastructure for data centers and AI infrastructure customers.
- A company using another company's product or service is downstream of that supplier.
- A collaboration, partnership, investment, acquisition, patent dispute, competitor mention, index membership, or analyst comparison is not enough.
- If the product field is generic, replace it with a more specific product/service when you know it.
- If you are not sure, choose pending, not approve.
- If supplier_side is "source" and customer_side is "target", action must be approve.
- If supplier_side is "target" and customer_side is "source", action must be reverse.
- If there is no operational supplier/customer relationship, action must be reject.
"""


def node_label(node):
    if not node:
        return "Unknown"
    parts = [node.name or "Unknown"]
    if node.ticker:
        parts.append(f"ticker {node.ticker}")
    if node.sector:
        parts.append(f"sector {node.sector}")
    if node.industry:
        parts.append(f"industry {node.industry}")
    return " | ".join(parts)


def node_aliases(node):
    if not node:
        return []
    aliases = []
    if node.ticker:
        aliases.append(node.ticker.lower())
    if node.name:
        cleaned = re.sub(r"\b(common stock|class a|class b|inc\.?|corporation|corp\.?|company|co\.?|ltd\.?|plc|n\.v\.)\b", "", node.name, flags=re.I)
        cleaned = re.sub(r"[^a-z0-9 ]+", " ", cleaned.lower())
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        if cleaned:
            aliases.append(cleaned)
            words = cleaned.split()
            if words and len(words[0]) >= 4:
                aliases.append(words[0])
            if len(words) >= 2:
                aliases.append(" ".join(words[:2]))
    return [alias for alias in dict.fromkeys(aliases) if len(alias) >= 2]


def has_alias(text, aliases):
    return any(alias in text for alias in aliases)


# Trade names that never appear in the listed company name.
KNOWN_ALIASES = {
    "TSM": ("tsmc",),
    "GOOG": ("google",),
    "GOOGL": ("google",),
    "META": ("facebook",),
    "MMM": ("3m",),
    "HNHPF": ("foxconn",),
}
HELD_NOTE_PREFIX = "Ollama consensus review"


def strong_aliases(node):
    """Aliases that identify a company unambiguously in prose.

    A ticker of three or more letters, the cleaned full name, its first two words,
    and known trade names. Single first words ("Boston", "Taiwan", "Alaska") are
    deliberately excluded: they are how place names get matched to companies.
    """
    if not node:
        return []
    aliases = []
    ticker = str(node.ticker or "").strip().lower()
    if len(ticker) >= 3:
        aliases.append(ticker)
    cleaned = re.sub(
        r"\b(common stock|ordinary shares|american depositary shares|class a|class b|incorporated|inc\.?|corporation|corp\.?|company|co\.?|limited|ltd\.?|plc|holdings?|group|n\.?v\.?|s\.?a\.?|a\.?g\.?|s\.?e\.?|the)\b",
        "",
        node.name or "",
        flags=re.I,
    )
    cleaned = re.sub(r"[^a-z0-9& ]+", " ", cleaned.lower())
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if cleaned:
        aliases.append(cleaned)
        words = cleaned.split()
        if len(words) >= 2:
            aliases.append(" ".join(words[:2]))
    aliases.extend(KNOWN_ALIASES.get(str(node.ticker or "").upper(), ()))
    return [alias for alias in dict.fromkeys(aliases) if len(alias) >= 2]


def mentions_company(text, node):
    lowered = " ".join(str(text or "").lower().split())
    return any(
        re.search(r"(?<![a-z0-9])" + re.escape(alias) + r"(?![a-z0-9])", lowered)
        for alias in strong_aliases(node)
    )


def approved_mirror_edge(edge):
    """An approved edge in the opposite direction between the same two companies."""
    target = getattr(edge, "target_node", None)
    for candidate in getattr(target, "supplies_to", None) or []:
        if (
            getattr(candidate, "id", None) != getattr(edge, "id", None)
            and getattr(candidate, "target_id", None) == edge.source_id
            and getattr(candidate, "review_status", None) == "approved"
        ):
            return candidate
    return None


def held_review(edge, reason):
    return {
        "action": "pending",
        "supplier_side": "unknown",
        "customer_side": "unknown",
        "confidence": 0.0,
        "relationship_type": edge.dependency_type or "",
        "product": edge.product or "",
        "reason": reason,
    }


def alias_before_terms(text, aliases, terms):
    positions = [text.find(alias) for alias in aliases if alias in text]
    if not positions:
        return False
    first_alias = min(positions)
    window = text[first_alias:first_alias + 180]
    return any(term in window for term in terms)


def correct_review_for_reason(edge, review):
    reason = review["reason"].lower()
    source_aliases = node_aliases(edge.source_node)
    target_aliases = node_aliases(edge.target_node)
    # "X is supplied by Y" makes X the customer, so "supplied by" is a customer cue only.
    supplier_terms = [" supplies ", " supplies", " provides ", " provides", " manufactures ", " produces ", " supplier "]
    customer_terms = [" uses ", " purchases ", " buys ", " customer of ", " supplied by "]

    if any(marker in reason for marker in ["do not have a direct", "does not have a direct", "not a direct", "no direct"]):
        review["action"] = "reject"
        review["supplier_side"] = "neither"
        review["customer_side"] = "neither"
        return review

    if review["action"] == "approve":
        source_is_customer = alias_before_terms(reason, source_aliases, customer_terms)
        target_is_supplier = alias_before_terms(reason, target_aliases, supplier_terms)
        target_then_source = has_alias(reason, target_aliases) and has_alias(reason, source_aliases) and (
            target_is_supplier or "not the other way around" in reason
        )
        if source_is_customer and target_then_source:
            review["action"] = "reverse"
            review["supplier_side"] = "target"
            review["customer_side"] = "source"
            return review

    if review["action"] == "reverse":
        source_is_supplier = alias_before_terms(reason, source_aliases, supplier_terms + [" upstream of "])
        target_is_customer = alias_before_terms(reason, target_aliases, [" customer ", " customer of ", " uses ", " buys ", " purchases "])
        if source_is_supplier and (target_is_customer or has_alias(reason, target_aliases)):
            review["action"] = "approve"
            review["supplier_side"] = "source"
            review["customer_side"] = "target"

    return review


def split_models(value):
    return [model.strip() for model in str(value or "").split(",") if model.strip()]


def build_user_prompt(edge):
    return f"""
Review this proposed edge:

source/supplier candidate:
{node_label(edge.source_node)}

target/customer candidate:
{node_label(edge.target_node)}

current_dependency_type: {edge.dependency_type or ""}
current_product: {edge.product or ""}
source_title: {edge.source_title or ""}
source_url: {edge.source_url or ""}
evidence_excerpt: {edge.evidence_excerpt or ""}

Question:
Should this edge be approved as source -> target, rejected, reversed to target -> source, or left pending?
"""


def is_non_operating_vehicle(node):
    if not node:
        return False
    sector = (node.sector or "").strip().lower()
    industry = (node.industry or "").strip().lower()
    name = f" {node.name or ''} ".lower()
    if sector in NON_OPERATING_SECTORS:
        return True
    if any(marker in industry for marker in ("asset management", "closed-end fund", "reit", "shell compan")):
        return True
    return any(marker in name for marker in NON_OPERATING_NAME_MARKERS)


def deterministic_review(edge):
    if edge.source_id == edge.target_id:
        return {
            "action": "reject",
            "supplier_side": "neither",
            "customer_side": "neither",
            "confidence": 1.0,
            "relationship_type": edge.dependency_type or "Invalid self-edge",
            "product": edge.product or "",
            "reason": "Self-edges are not valid supply-chain relationships.",
        }

    if unsupported_ai_evidence(edge.source_url, edge.evidence_excerpt):
        return {
            "action": "reject",
            "supplier_side": "neither",
            "customer_side": "neither",
            "confidence": 1.0,
            "relationship_type": edge.dependency_type or "Unsupported AI relationship",
            "product": edge.product or "",
            "reason": "AI-derived relationship has no usable excerpt from the collected source text.",
        }

    mirror = approved_mirror_edge(edge)
    if mirror:
        # Publishing both directions of one fact is always wrong, and a model vote
        # cannot tell which of the two directions a human already approved is right.
        return held_review(
            edge,
            f"The opposite direction is already approved as edge #{mirror.id}; a human must decide which direction is correct.",
        )

    if requires_source_evidence(edge.source_url) and not (
        mentions_company(edge.evidence_excerpt, edge.source_node)
        and mentions_company(edge.evidence_excerpt, edge.target_node)
    ):
        # Entity resolution can bind "Boston" to Boston Scientific or "MSA" storage to
        # Mine Safety; an excerpt that does not name both companies cannot support the edge.
        return held_review(edge, "Evidence excerpt does not name both companies; held for human review.")

    bad_nodes = [node for node in (edge.source_node, edge.target_node) if is_non_operating_vehicle(node)]
    if bad_nodes:
        names = ", ".join(f"{node.ticker} {node.name}" for node in bad_nodes)
        return {
            "action": "reject",
            "supplier_side": "neither",
            "customer_side": "neither",
            "confidence": 0.96,
            "relationship_type": edge.dependency_type or "Non-operating financial vehicle",
            "product": edge.product or "",
            "reason": f"Non-operating financial vehicle or fund is not a useful operating supply-chain node: {names}.",
        }

    if has_non_supply_relationship(edge.dependency_type, edge.product, edge.evidence_excerpt):
        return {
            "action": "reject",
            "supplier_side": "neither",
            "customer_side": "neither",
            "confidence": max(edge.confidence_score or 0.0, 0.95),
            "relationship_type": edge.dependency_type or "Non-supply relationship",
            "product": edge.product or "",
            "reason": "Relationship label or evidence describes a non-operational relationship, not a supply-chain dependency.",
        }

    return None


def parse_json_response(content):
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", content, re.DOTALL)
        if not match:
            raise
        return json.loads(match.group(0))


def normalize_review(raw):
    supplier_side = str(raw.get("supplier_side", "unknown")).strip().lower()
    customer_side = str(raw.get("customer_side", "unknown")).strip().lower()
    if supplier_side not in {"source", "target", "neither", "unknown"}:
        supplier_side = "unknown"
    if customer_side not in {"source", "target", "neither", "unknown"}:
        customer_side = "unknown"

    if supplier_side == "source" and customer_side == "target":
        action = "approve"
    elif supplier_side == "target" and customer_side == "source":
        action = "reverse"
    elif supplier_side == "neither" or customer_side == "neither":
        action = "reject"
    else:
        action = str(raw.get("action", "pending")).strip().lower()
        if action not in VALID_ACTIONS:
            action = "pending"

    reason = str(raw.get("reason") or "").strip()
    reason_lower = reason.lower()
    relationship_type = str(raw.get("relationship_type") or "").strip()
    product = str(raw.get("product") or "").strip()
    if has_non_supply_relationship(relationship_type, product, note=reason):
        action = "reject"
        supplier_side = "neither"
        customer_side = "neither"
    if action in {"approve", "reverse"} and any(marker in reason_lower for marker in UNCERTAIN_REASON_MARKERS):
        action = "pending"
    try:
        confidence = float(raw.get("confidence", 0))
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))

    return {
        "action": action,
        "supplier_side": supplier_side,
        "customer_side": customer_side,
        "confidence": confidence,
        "relationship_type": relationship_type,
        "product": product,
        "reason": reason,
    }


# A verdict is a few dozen tokens; a grammar-constrained response that keeps
# going, or a wedged server, must fail the edge rather than stall the run.
REVIEW_MAX_TOKENS = int(os.environ.get("HEPHAESTUS_REVIEW_MAX_TOKENS", "512"))
REVIEW_TIMEOUT_SECONDS = float(os.environ.get("HEPHAESTUS_REVIEW_TIMEOUT_SECONDS", "180"))


def review_edge_with_model(edge, model):
    response = ollama.Client(timeout=REVIEW_TIMEOUT_SECONDS).chat(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": build_user_prompt(edge)},
        ],
        format=REVIEW_SCHEMA,
        options={"temperature": 0, "num_predict": REVIEW_MAX_TOKENS},
    )
    review = normalize_review(parse_json_response(response["message"]["content"]))
    review = correct_review_for_reason(edge, review)
    review["model"] = model
    return review


def action_threshold(action, args):
    if action == "approve":
        return args.min_approve
    if action == "reject":
        return args.min_reject
    if action == "reverse":
        return args.min_reverse
    return 1.0


def review_vote_key(review):
    action = review["action"]
    if action in {"approve", "reverse"}:
        return (action, review["supplier_side"], review["customer_side"])
    return (action, "neither", "neither") if action == "reject" else ("pending", "unknown", "unknown")


def consensus_review(edge, reviews, args):
    if not reviews:
        return {
            "action": "pending",
            "supplier_side": "unknown",
            "customer_side": "unknown",
            "confidence": 0.0,
            "relationship_type": edge.dependency_type or "",
            "product": edge.product or "",
            "reason": "No model reviews were available.",
            "votes": "none",
            "model_reviews": [],
        }

    groups = {}
    for review in reviews:
        groups.setdefault(review_vote_key(review), []).append(review)

    winning_key, winning_reviews = max(
        groups.items(),
        key=lambda item: (len(item[1]), sum(review["confidence"] for review in item[1]) / len(item[1])),
    )
    action, supplier_side, customer_side = winning_key
    vote_count = len(winning_reviews)
    vote_ratio = vote_count / len(reviews)
    avg_confidence = sum(review["confidence"] for review in winning_reviews) / vote_count
    min_confidence = min(review["confidence"] for review in winning_reviews)
    threshold = action_threshold(action, args)
    strong_enough = (
        action != "pending"
        and vote_count >= min(args.consensus_min_votes, len(reviews))
        and vote_ratio >= args.consensus_min_ratio
        and avg_confidence >= threshold
        and min_confidence >= max(0.5, threshold - 0.15)
    )

    best_review = max(winning_reviews, key=lambda review: review["confidence"])
    vote_summary = ", ".join(
        f"{key[0]}:{len(value)}" for key, value in sorted(groups.items(), key=lambda item: (-len(item[1]), item[0]))
    )
    reason = (
        f"Consensus {vote_count}/{len(reviews)} for {action} "
        f"(avg confidence {avg_confidence:.2f}; votes {vote_summary}). "
        f"Lead rationale from {best_review.get('model', 'model')}: {best_review['reason']}"
    )

    if not strong_enough:
        action = "pending"
        supplier_side = "unknown"
        customer_side = "unknown"
        reason = (
            f"Held for review because model consensus was insufficient. "
            f"{reason}"
        )

    return {
        "action": action,
        "supplier_side": supplier_side,
        "customer_side": customer_side,
        "confidence": round(avg_confidence, 4),
        "relationship_type": best_review["relationship_type"] or edge.dependency_type or "",
        "product": best_review["product"] or edge.product or "",
        "reason": reason,
        "votes": vote_summary,
        "model_reviews": reviews,
    }


def review_edge(edge, models, args):
    deterministic = deterministic_review(edge)
    if deterministic:
        deterministic["model"] = "deterministic"
        deterministic["votes"] = "deterministic:1"
        deterministic["model_reviews"] = [dict(deterministic)]
        return deterministic

    reviews = [review_edge_with_model(edge, model) for model in models]
    if len(reviews) == 1:
        review = reviews[0]
        review["votes"] = f"{review['action']}:1"
        review["model_reviews"] = reviews
        return review
    return consensus_review(edge, reviews, args)


def selected_edges(session, args):
    query = session.query(Edge).filter(Edge.review_status == args.status)
    if args.status == "pending" and not getattr(args, "include_held", False):
        # Edges the panel already held keep their confidence, so without this they
        # sit at the head of the queue and consume the nightly budget forever.
        query = query.filter(or_(Edge.review_note.is_(None), ~Edge.review_note.like(f"{HELD_NOTE_PREFIX}%")))
    if args.source:
        query = query.filter(Edge.source_node.has(Node.ticker == args.source.upper()))
    if args.target:
        query = query.filter(Edge.target_node.has(Node.ticker == args.target.upper()))
    if args.edge_id:
        query = query.filter(Edge.id.in_(args.edge_id))
    return query.order_by(Edge.confidence_score.desc().nullslast(), Edge.id.asc()).limit(args.limit).all()


def decision_allowed(review, args):
    action = review["action"]
    confidence = review["confidence"]
    if action == "approve":
        return confidence >= args.min_approve
    if action == "reject":
        return confidence >= args.min_reject
    if action == "reverse":
        return confidence >= args.min_reverse
    return False


def update_metadata(edge, review):
    relationship_type = review["relationship_type"]
    if relationship_type and not is_role_label(relationship_type):
        edge.dependency_type = relationship_type[:255]
    elif is_role_label(edge.dependency_type):
        # Never leave a bare role label ("Supplier") as the published dependency
        # type, or the next cleanup demotes the edge and the panel re-reviews it.
        edge.dependency_type = "Supply Relationship"
    if review["product"]:
        edge.product = review["product"][:255]
    edge.confidence_score = review["confidence"]
    edge.review_note = f"Ollama consensus review: {review['reason']}"[:1000]
    edge.reviewed_at = datetime.now(timezone.utc)


def apply_approval(edge, review):
    update_metadata(edge, review)
    edge.review_status = "approved"


def apply_rejection(edge, review):
    update_metadata(edge, review)
    edge.review_status = "rejected"


def apply_reverse(session, edge, review):
    existing = (
        session.query(Edge)
        .filter(
            Edge.source_id == edge.target_id,
            Edge.target_id == edge.source_id,
            Edge.dependency_type == (review["relationship_type"] or edge.dependency_type),
            Edge.id != edge.id,
        )
        .first()
    )

    if existing and existing.review_status == "rejected":
        # The reversed direction was already reviewed and rejected; a model vote must
        # not silently resurrect that decision. Hold the edge for a human instead.
        edge.review_status = "pending"
        edge.review_note = (
            f"Ollama consensus review held: reversed direction was previously rejected as edge #{existing.id}. "
            f"{review['reason']}"
        )[:1000]
        edge.reviewed_at = None
        return None

    if existing:
        apply_approval(existing, review)
        edge.review_status = "rejected"
        edge.review_note = f"Ollama consensus review: duplicate reversed edge approved as #{existing.id}. {review['reason']}"[:1000]
        edge.reviewed_at = datetime.now(timezone.utc)
        return existing.id

    edge.source_id, edge.target_id = edge.target_id, edge.source_id
    update_metadata(edge, review)
    edge.review_status = "approved"
    return edge.id


def apply_review(session, edge, review):
    action = review["action"]
    if action == "approve":
        apply_approval(edge, review)
        return "approved"
    if action == "reject":
        apply_rejection(edge, review)
        return "rejected"
    if action == "reverse":
        target_id = apply_reverse(session, edge, review)
        if target_id is None:
            return "held_reverse_previously_rejected"
        return f"reversed_to_edge_{target_id}"

    edge.review_status = "pending"
    edge.review_note = f"Ollama consensus review left pending: {review['reason']}"[:1000]
    return "pending"


def write_report(path, rows):
    if not path:
        return
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "edge_id",
                "source",
                "target",
                "model_action",
                "consensus_votes",
                "supplier_side",
                "customer_side",
                "model_confidence",
                "applied",
                "result",
                "relationship_type",
                "product",
                "reason",
                "model_reviews",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(description="Use local Ollama model consensus to batch-review supply-chain edges.")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--models", default=DEFAULT_MODELS, help="Comma-separated Ollama models. Overrides --model when set.")
    parser.add_argument("--status", default="pending", choices=["pending", "approved", "rejected"])
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--source")
    parser.add_argument("--target")
    parser.add_argument("--edge-id", type=int, action="append")
    parser.add_argument("--apply", action="store_true", help="Apply high-confidence decisions to the database.")
    parser.add_argument("--min-approve", type=float, default=0.82)
    parser.add_argument("--min-reject", type=float, default=0.86)
    parser.add_argument("--min-reverse", type=float, default=0.88)
    parser.add_argument("--consensus-min-votes", type=int, default=2)
    parser.add_argument("--consensus-min-ratio", type=float, default=0.66)
    parser.add_argument("--sleep", type=float, default=0.0)
    parser.add_argument("--max-seconds", type=float, default=0.0, help="Stop gracefully after this many seconds.")
    parser.add_argument("--include-held", action="store_true", help="Re-review pending edges the panel previously held.")
    parser.add_argument("--report", default="reports/ollama_edge_review.csv")
    args = parser.parse_args()
    models = split_models(args.models) if args.models else [args.model]
    if len(models) == 1:
        args.consensus_min_votes = 1

    session = SessionLocal()
    report_rows = []
    counts = {"approve": 0, "reject": 0, "reverse": 0, "pending": 0, "applied": 0, "held": 0, "errors": 0}
    started_at = time.monotonic()

    try:
        edges = selected_edges(session, args)
        print(
            f"Reviewing {len(edges)} {args.status} edge(s) with {', '.join(models)}. "
            f"consensus_min_votes={args.consensus_min_votes} consensus_min_ratio={args.consensus_min_ratio} apply={args.apply}"
        )

        for index, edge in enumerate(edges, start=1):
            if args.max_seconds and time.monotonic() - started_at >= args.max_seconds:
                print(f"Reached --max-seconds={args.max_seconds}; stopping gracefully.")
                break

            source = edge.source_node.ticker if edge.source_node else str(edge.source_id)
            target = edge.target_node.ticker if edge.target_node else str(edge.target_id)
            try:
                review = review_edge(edge, models, args)
                counts[review["action"]] += 1
                allowed = decision_allowed(review, args)
                result = "held"

                if args.apply and allowed:
                    result = apply_review(session, edge, review)
                    try:
                        session.commit()
                    except IntegrityError:
                        session.rollback()
                        review["action"] = "pending"
                        result = "held_integrity_conflict"
                    if result.startswith("held"):
                        counts["held"] += 1
                    else:
                        counts["applied"] += 1
                else:
                    counts["held"] += 1
                    if args.apply and (edge.review_status or "pending") == "pending":
                        # Record why the panel held the edge so it leaves the nightly
                        # queue instead of being re-reviewed every run.
                        edge.review_note = f"{HELD_NOTE_PREFIX} left pending: {review['reason']}"[:1000]
                        session.commit()

                print(
                    f"[{index}/{len(edges)}] #{edge.id} {source}->{target} "
                    f"{review['action']} {review['confidence']:.2f} applied={args.apply and allowed} {review['reason'][:140]}"
                )

                report_rows.append({
                    "edge_id": edge.id,
                    "source": source,
                    "target": target,
                    "model_action": review["action"],
                    "consensus_votes": review.get("votes", ""),
                    "supplier_side": review["supplier_side"],
                    "customer_side": review["customer_side"],
                    "model_confidence": review["confidence"],
                    "applied": bool(args.apply and allowed and not result.startswith("held")),
                    "result": result,
                    "relationship_type": review["relationship_type"],
                    "product": review["product"],
                    "reason": review["reason"],
                    "model_reviews": json.dumps(review.get("model_reviews", []), sort_keys=True),
                })

                if args.sleep:
                    time.sleep(args.sleep)
            except Exception as exc:
                session.rollback()
                counts["errors"] += 1
                print(f"[{index}/{len(edges)}] #{edge.id} {source}->{target} ERROR {exc}")

        write_report(args.report, report_rows)
        print("Summary:", counts)
        if args.report:
            print(f"Report written to {args.report}")
    finally:
        session.close()


if __name__ == "__main__":
    main()
