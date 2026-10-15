import argparse
import csv
import json
import math
import os
import re
import time
from datetime import datetime, timezone

import ollama
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import object_session

from database import SessionLocal
from evidence_quality import (  # noqa: F401  (mentions_company is re-exported for callers)
    KNOWN_ALIASES,
    evidence_direction,
    register_company_names,
    evidence_support,
    has_non_supply_relationship,
    is_endpoint_label,
    mentions_company,
    normalize_for_alias_match,
    strong_aliases,
    is_role_label,
    requires_source_evidence,
    unsupported_ai_evidence,
)
from customer_concentration import filer_documented_direction
from models import Edge, Node


DEFAULT_MODEL = os.environ.get("HEPHAESTUS_REVIEW_MODEL", "qwen2.5:7b-instruct")
DEFAULT_MODELS = os.environ.get("HEPHAESTUS_REVIEW_MODELS")
VALID_ACTIONS = {"approve", "reject", "reverse", "pending"}
NON_OPERATING_SECTORS = {"financial services", "real estate", "shell companies"}
# Whole words only: the old substring test (" spac" in " AST SpaceMobile ") rejected AST
# SpaceMobile, SpaceX and MDA Space as funds, and " note"/" income"/" treasury" had the same
# problem. These are the words a fund, ETF, trust, bond issue or blank-check company puts in
# its name; "bond" is plural-only because "Our Bond, Inc." is a software company.
NON_OPERATING_NAME_PATTERN = re.compile(
    r"\b(?:funds?|etfs?|etns?|trusts?|bonds|notes|tax-free|spac)\b|\bblank check\b|\bacquisition corp",
    re.IGNORECASE,
)
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
    "not a supply",
    "not explicitly stated",
    "suggesting",
    "would be",
    "would use",
]

# The rationale denies the relationship itself ("Ford and Mercury Systems do not have a
# direct supply relationship"). "TSMC is the sole foundry; no direct alternative exists"
# says the opposite and must not match.
DENIES_RELATIONSHIP = re.compile(
    r"\b(?:do|does|did) not have a direct (?:supply|supplier|customer|business|commercial|operational|relationship|connection|link)"
    r"|\bnot a direct (?:supplier|customer|supply|relationship)"
    r"|\bno direct (?:supply|supplier|customer|business|commercial|operational|relationship|connection|link|evidence|mention|indication|documentation)",
    re.IGNORECASE,
)

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


HELD_NOTE_PREFIX = "Ollama consensus review"


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


# Phrases in which a model says the proposed direction is wrong, and ones in which it
# says the direction is right. A vote whose own words disagree with it is not a vote.
BACKWARDS_REASON = re.compile(
    r"backwards|should be reversed|direction (?:is|was) (?:reversed|wrong|incorrect|inverted)"
    r"|direction should be reversed|reverse the (?:edge|direction)|wrong direction|opposite direction"
    r"|not the other way around",
    re.IGNORECASE,
)
FORWARDS_REASON = re.compile(r"direction is (?:correct|right)|correct direction|edge is correct as proposed", re.IGNORECASE)


def correct_review_for_reason(edge, review):
    """Screen one model vote against its own rationale.

    This used to *flip* votes: a "reverse" became "approve" whenever a supply verb
    appeared within 180 characters of the source's name, which is almost always.
    "TSMC manufactures the processors Apple uses ... the edge is backwards" came out as
    approve, and 65 of 344 consensus-approved links carried a rationale calling them
    backwards. A vote that contradicts itself now counts as no vote (pending); nothing
    here ever changes a vote's direction. (A rationale containing "no direct alternative"
    also used to turn a pending or approving vote into a rejection at the same confidence;
    a rationale that denies the relationship is now handled in normalize_review, as no vote.)
    """
    reason = review["reason"].lower()

    if review.get("direction_fixed"):
        return review
    if review["action"] == "approve" and BACKWARDS_REASON.search(reason):
        review["action"] = "pending"
    elif review["action"] == "reverse" and FORWARDS_REASON.search(reason):
        review["action"] = "pending"
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
    if sector in NON_OPERATING_SECTORS:
        return True
    if any(marker in industry for marker in ("asset management", "closed-end fund", "reit", "shell compan")):
        return True
    return bool(NON_OPERATING_NAME_PATTERN.search(node.name or ""))


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

    if requires_source_evidence(edge.source_url) and not filer_documented_direction(edge):
        verdict, reason = evidence_support(edge.evidence_excerpt, edge.source_node, edge.target_node)
        if verdict == "unsupported":
            # Junk - an excerpt about neither company, a past event, an asset sale, two
            # names in a list - is rejected here rather than queued for a person.
            return {
                "action": "reject",
                "supplier_side": "neither",
                "customer_side": "neither",
                "confidence": 1.0,
                "relationship_type": edge.dependency_type or "Unsupported relationship",
                "product": edge.product or "",
                "reason": f"The evidence excerpt does not support this link: {reason}.",
            }

    if requires_source_evidence(edge.source_url) and not (
        mentions_company(edge.evidence_excerpt, edge.source_node)
        and mentions_company(edge.evidence_excerpt, edge.target_node)
    ):
        # Entity resolution can bind "Boston" to Boston Scientific or "MSA" storage to
        # Mine Safety; an excerpt that does not name both companies cannot support the edge.
        return held_review(edge, "Evidence excerpt does not name both companies; held for human review.")

    if requires_source_evidence(edge.source_url) and not filer_documented_direction(edge):
        direction, phrase = evidence_direction(edge.evidence_excerpt, edge.source_node, edge.target_node)
        if direction == "backward":
            # The models approve these with consistent reasoning; the wording says otherwise.
            return held_review(edge, f'The excerpt describes the opposite direction ("{phrase[:150]}"); held for a human to reverse.')

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


def parse_confidence(value):
    """A confidence on the 0-1 scale the schema asks for, or the 0-100 scale models drift to.

    A value above 1 up to 100 is a percentage (95 -> 0.95); anything unusable (NaN, 250,
    text) counts as no confidence, where the old clamp turned both 95 and 250 into 1.0.
    """
    try:
        number = float(str(value).strip().rstrip("%")) if isinstance(value, str) else float(value)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(number) or number < 0 or number > 100:
        return 0.0
    return number / 100 if number > 1 else number


def normalize_review(raw, fixed_direction=False):
    """One model's vote, reconciled from its side fields and its stated action.

    The side fields used to override the action outright, so a model answering
    action "reverse" with sides (source, target) was recorded as an approval. When the
    two disagree the vote now counts as "pending". With `fixed_direction` (the supplier's
    own filing named the customer) only "is this relationship real" is being asked, so
    a direction vote of either kind counts as confirming it.
    """
    supplier_side = str(raw.get("supplier_side", "unknown")).strip().lower()
    customer_side = str(raw.get("customer_side", "unknown")).strip().lower()
    if supplier_side not in {"source", "target", "neither", "unknown"}:
        supplier_side = "unknown"
    if customer_side not in {"source", "target", "neither", "unknown"}:
        customer_side = "unknown"

    if supplier_side == "source" and customer_side == "target":
        side_action = "approve"
    elif supplier_side == "target" and customer_side == "source":
        side_action = "reverse"
    elif supplier_side == "neither" or customer_side == "neither":
        side_action = "reject"
    else:
        side_action = None
    stated_action = str(raw.get("action", "")).strip().lower()
    if stated_action not in VALID_ACTIONS:
        stated_action = None

    if fixed_direction:
        votes = {side_action, stated_action} - {None, "pending"}
        if votes and votes <= {"approve", "reverse"}:
            action = "approve"
            supplier_side, customer_side = "source", "target"
        elif votes == {"reject"}:
            action = "reject"
        else:
            action = "pending"
    elif side_action and stated_action and stated_action != "pending" and side_action != stated_action:
        action = "pending"
    else:
        action = side_action or stated_action or "pending"

    reason = str(raw.get("reason") or "").strip()
    reason_lower = reason.lower()
    relationship_type = str(raw.get("relationship_type") or "").strip()
    product = str(raw.get("product") or "").strip()
    if has_non_supply_relationship(relationship_type, product, note=reason):
        action = "reject"
        supplier_side = "neither"
        customer_side = "neither"
    if action in {"approve", "reverse"} and (
        any(marker in reason_lower for marker in UNCERTAIN_REASON_MARKERS) or DENIES_RELATIONSHIP.search(reason)
    ):
        # An approval whose own rationale denies the relationship is no vote; it is never
        # promoted to a rejection either.
        action = "pending"
    confidence = parse_confidence(raw.get("confidence"))

    return {
        "action": action,
        "supplier_side": supplier_side,
        "customer_side": customer_side,
        "confidence": confidence,
        "relationship_type": relationship_type,
        "product": product,
        "reason": reason,
        "direction_fixed": bool(fixed_direction),
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
    review = normalize_review(parse_json_response(response["message"]["content"]), filer_documented_direction(edge))
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
    # One model saying "reverse" against two saying "approve" (or the reverse) is a
    # disagreement about the one thing the panel is least reliable at; the published
    # method promises such links stay pending, where 2/3 used to be enough.
    opposite = {"approve": "reverse", "reverse": "approve"}.get(action)
    direction_dissent = [review for review in reviews if opposite and review["action"] == opposite]
    strong_enough = (
        action != "pending"
        and not direction_dissent
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
        why = "the models disagree on direction" if direction_dissent else "votes, direction or confidence fell short"
        reason = (
            f"Held for review because model consensus was insufficient ({why}). "
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

    reviews = []
    for model in models:
        try:
            reviews.append(review_edge_with_model(edge, model))
        except Exception as exc:  # a timeout, a dropped connection or unparsable JSON
            reviews.append(abstention(model, exc))
    if all(review.get("failed") for review in reviews):
        review = held_review(edge, "; ".join(review["reason"] for review in reviews))
        review.update(failed=True, votes="none", model_reviews=reviews)
        return review
    if len(reviews) == 1:
        review = reviews[0]
        review["votes"] = f"{review['action']}:1"
        review["model_reviews"] = reviews
        return review
    return consensus_review(edge, reviews, args)


def abstention(model, exc):
    """A model that timed out or garbled its answer casts no vote.

    One bad answer used to abort the whole edge with no note, so the edge sat at the head
    of the confidence-ordered queue and cost up to three 180-second timeouts every night.
    """
    return {
        "action": "pending",
        "supplier_side": "unknown",
        "customer_side": "unknown",
        "confidence": 0.0,
        "relationship_type": "",
        "product": "",
        "reason": f"{model} gave no usable answer ({type(exc).__name__}: {str(exc)[:120]})",
        "direction_fixed": False,
        "model": model,
        "failed": True,
    }


MAX_FAILED_ATTEMPTS = 3
MAX_CONSECUTIVE_FAILURES = 3
FAILED_NOTE = re.compile(r"^Ollama review attempt (\d+) of ")


def record_failed_attempt(edge, reason):
    """Count a run on which no model answered; after the third the edge is held, not retried forever.

    The note does not start with HELD_NOTE_PREFIX, so the first attempts keep the edge in
    the queue; the last one does, which takes it out until a person or --include-held asks.
    """
    match = FAILED_NOTE.match(str(edge.review_note or ""))
    attempt = int(match.group(1)) + 1 if match else 1
    if attempt >= MAX_FAILED_ATTEMPTS:
        edge.review_note = f"{HELD_NOTE_PREFIX} hold: no model gave a usable answer on {attempt} runs. {reason}"[:1000]
    else:
        edge.review_note = f"Ollama review attempt {attempt} of {MAX_FAILED_ATTEMPTS} found no usable model answer. {reason}"[:1000]
    return attempt


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
        # The panel's direction calls are the least reliable thing it produces (it calls
        # P&G -> Walmart backwards as readily as TSM -> ASML), so a reversal is held for a
        # human unless a run opts in explicitly.
        return getattr(args, "apply_reversals", False) and confidence >= args.min_reverse
    return False


def label_taken(edge, label):
    """Another edge between the same two companies already uses this label.

    Renaming into it violates uq_edge_dependency: the commit failed, was rolled back
    without a note, and the same 18 verdicts were re-run and lost every night.
    """
    try:
        session = object_session(edge)
    except Exception:  # a plain object in tests, or an edge not attached to a session
        return False
    if session is None or edge.source_id is None or edge.target_id is None:
        return False
    with session.no_autoflush:
        return session.query(Edge.id).filter(
            Edge.source_id == edge.source_id,
            Edge.target_id == edge.target_id,
            Edge.dependency_type == label,
            Edge.id != edge.id,
        ).first() is not None


def echoes_endpoint_label(edge, product):
    """The prompt shows each company's industry and sector; models copy them back as the product."""
    return is_endpoint_label(product, getattr(edge, "source_node", None), getattr(edge, "target_node", None))


def update_metadata(edge, review):
    relationship_type = review["relationship_type"]
    if relationship_type and not is_role_label(relationship_type):
        if relationship_type[:255] != edge.dependency_type and not label_taken(edge, relationship_type[:255]):
            edge.dependency_type = relationship_type[:255]
    elif is_role_label(edge.dependency_type) and not label_taken(edge, "Supply Relationship"):
        # Never leave a bare role label ("Supplier") as the published dependency
        # type, or the next cleanup demotes the edge and the panel re-reviews it.
        edge.dependency_type = "Supply Relationship"
    # "11% of VTRS revenue" became "Drug Manufacturers - Specialty & Generic": the prompt
    # shows each company's industry, and a model copying it back is not describing a product.
    if review["product"] and not echoes_endpoint_label(edge, review["product"]):
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
    parser.add_argument("--apply-reversals", action="store_true",
                        help="Apply reverse verdicts. Off by default: reversals are held for a human.")
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
    consecutive_failures = 0

    try:
        register_company_names(session.query(Node.ticker, Node.name).filter(Node.ticker.is_not(None)).all())
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
                if review.get("failed"):
                    counts["errors"] += 1
                    consecutive_failures += 1
                    result = "held_no_model_answer"
                else:
                    consecutive_failures = 0

                if args.apply and allowed:
                    result = apply_review(session, edge, review)
                    try:
                        session.commit()
                    except IntegrityError:
                        session.rollback()
                        review["action"] = "pending"
                        result = "held_integrity_conflict"
                        # Without a held note the edge stays at the head of the queue and
                        # the same verdict is recomputed and lost on every run.
                        edge.review_note = (
                            f"{HELD_NOTE_PREFIX} hold: the verdict could not be saved (it conflicts with "
                            f"another edge between these companies). {review['reason']}"
                        )[:1000]
                        session.commit()
                    if result.startswith("held"):
                        counts["held"] += 1
                    else:
                        counts["applied"] += 1
                else:
                    counts["held"] += 1
                    if args.apply and (edge.review_status or "pending") == "pending":
                        # Record why the panel held the edge so it leaves the nightly
                        # queue instead of being re-reviewed every run.
                        if review.get("failed"):
                            record_failed_attempt(edge, review["reason"])
                        else:
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

                if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    # Every model failed on several edges in a row: the server is down or
                    # wedged, and each further edge would cost three timeouts for nothing.
                    print(f"No model answered {consecutive_failures} edges in a row; stopping the run.")
                    break
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
