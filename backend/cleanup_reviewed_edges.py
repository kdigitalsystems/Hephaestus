import re
from datetime import datetime, timezone

from audit_data_quality import (
    has_non_supply_label,
    has_invalid_dependency_label,
    has_reversed_role_label,
    has_speculative_supply_label,
    has_wrong_direction_review,
    normalized_evidence,
    reciprocal_same_evidence_edges,
)
from evidence_quality import is_role_label
from customer_concentration import describe_share, disclosure_sentence, extract_disclosures, implausible_share
from database import SessionLocal
from evidence_quality import unsupported_ai_evidence
from models import Edge

CONCENTRATION_TYPE = "Revenue Concentration"
# Must match review_edges_with_ollama.HELD_NOTE_PREFIX; importing that module pulls in
# the Ollama client, which this cleanup step does not need.
HELD_NOTE_PREFIX = "Ollama consensus review"


def recheck_concentration_edges(session, counts):
    """Re-derive each stored concentration share from its own evidence sentence.

    The extractor is deterministic, so its rules can be tightened after the fact:
    a share paired wrongly in an earlier run (Howmet -> GE was published at 53%, the
    commercial-aerospace segment, when the sentence says GE is ~11%) is corrected
    here, and the decisions file re-applied at the start of the next run then
    carries the corrected value instead of restoring the old one. Evidence that no
    longer yields a share for the named customer sends the edge back to review.
    """
    from auto_discover_edges import clean_company_name, known_company_names  # heavy module; import lazily

    # A human's rejection stands; only live edges are rechecked.
    edges = session.query(Edge).filter(Edge.dependency_type == CONCENTRATION_TYPE, Edge.review_status != "rejected").all()
    if not edges:
        return
    # The whole universe, not just this edge's customer: discovery's rules (at most
    # three names per sentence, peer lists, rating agencies) must apply identically
    # here, or the recheck would confirm a share discovery would now reject.
    known = known_company_names(session)
    for edge in edges:
        filer, customer = edge.source_node, edge.target_node
        if not filer or not customer:
            continue
        sentence = disclosure_sentence(edge.evidence_excerpt)
        filer_names = (filer.name, clean_company_name(filer.name))
        # The stored excerpt is the disclosure sentence alone, but discovery may have
        # taken the customer cue from the sentence before it; requiring the cue again
        # here would demote a valid edge on every run.
        shares = [
            d.share_pct
            for d in extract_disclosures(sentence, known, filer_names, require_customer_cue=False)
            if d.customer_name == customer.name and d.share_pct is not None
        ]
        if not shares:
            if edge.review_status != "pending":
                edge.review_status = "pending"
                edge.review_note = "Automated recheck: the filing sentence no longer yields a revenue share for this customer."
                edge.reviewed_at = None
                counts["concentration_unsupported"] = counts.get("concentration_unsupported", 0) + 1
            continue
        share = max(shares)
        if edge.revenue_share != share:
            edge.revenue_share = share
            edge.product = describe_share(share, filer.ticker)
            counts["concentration_share_corrected"] = counts.get("concentration_share_corrected", 0) + 1

        reason = implausible_share(share, customer.market_cap)
        if reason and needs_human_confirmation(edge):
            # Held notes are skipped by the consensus review, so a human decides once
            # instead of the models re-approving what this step keeps holding.
            edge.review_status = "pending"
            edge.review_note = f"{HELD_NOTE_PREFIX} hold: {reason}; a human must confirm this disclosure."[:1000]
            edge.reviewed_at = None
            counts["concentration_held_implausible"] = counts.get("concentration_held_implausible", 0) + 1


def is_published(edge):
    return edge.review_status != "rejected" and (edge.review_status == "approved" or "Manual" in (edge.source_url or ""))


ROLE_WORDS = {"manufacturer", "customer", "customers", "supplier", "suppliers", "vendor", "buyer", "client", "provider", "partner", "distributor", "reseller", "receiver"}


def looks_like_role_label(label):
    """'Customer', 'manufacturer -> customer', 'supplier/customer': roles, not a dependency."""
    if is_role_label(label):
        return True
    text = str(label or "").lower()
    if "->" in text or "→" in text:
        return True
    words = [word for word in re.split(r"[^a-z]+", text) if word]
    return bool(words) and all(word in ROLE_WORDS or word in {"and", "of", "to", "the", "a"} for word in words)


def direction_rank(edge):
    """Higher keeps: a real dependency label over a role label, a human verdict over a
    model's, then the older edge."""
    note = str(edge.review_note or "")
    human = edge.review_status == "approved" and not note.startswith("Ollama consensus") and not note.startswith("Automated")
    return (0 if looks_like_role_label(edge.dependency_type) else 1, 1 if human else 0, -(edge.id or 0))


def resolve_reciprocal_duplicates(session, counts):
    """One fact published in both directions is always wrong; keep one, hold the other.

    Two runners' graphs merged through the decisions file can leave an approved edge
    and its approved mirror with identical evidence (Broadcom <-> Dell). The audit
    treats that as a warning and would block the whole publish; a human should
    decide the direction, so the weaker one goes back to review with a held note.
    """
    published = [edge for edge in session.query(Edge).all() if is_published(edge)]
    flagged = reciprocal_same_evidence_edges(published)
    by_key = {}
    for edge in flagged:
        if edge.source_node and edge.target_node:
            key = (edge.source_id, edge.target_id, normalized_evidence(edge.evidence_excerpt))
            by_key.setdefault(key, []).append(edge)
    handled = set()
    for (source_id, target_id, evidence), edges in by_key.items():
        mirrors = by_key.get((target_id, source_id, evidence), [])
        if not mirrors or (target_id, source_id, evidence) in handled:
            continue
        handled.add((source_id, target_id, evidence))
        keeper = max(edges + mirrors, key=direction_rank)
        for edge in edges + mirrors:
            if edge is keeper or edge.review_status == "pending":
                continue
            edge.review_status = "pending"
            edge.review_note = (
                f"{HELD_NOTE_PREFIX} hold: the opposite direction is published as edge #{keeper.id} "
                "with the same evidence; a human must decide which direction is correct."
            )[:1000]
            edge.reviewed_at = None
            counts["reciprocal_held"] = counts.get("reciprocal_held", 0) + 1


def needs_human_confirmation(edge):
    """Fresh or model-approved edges go to a human; a human's own verdict stands."""
    note = str(edge.review_note or "")
    if edge.review_status == "pending":
        return not note.startswith(HELD_NOTE_PREFIX)
    if edge.review_status == "approved":
        return note.startswith("Ollama consensus")
    return False


def cleanup_reviewed_edges():
    session = SessionLocal()
    counts = {"rejected_non_supply": 0, "rejected_unsupported_ai": 0, "pending_role_labels": 0}
    try:
        recheck_concentration_edges(session, counts)
        resolve_reciprocal_duplicates(session, counts)
        approved_edges = session.query(Edge).filter(Edge.review_status == "approved").all()
        for edge in approved_edges:
            if unsupported_ai_evidence(edge.source_url, edge.evidence_excerpt):
                edge.review_status = "rejected"
                edge.review_note = "Automated cleanup: AI-derived relationship has no usable source excerpt."
                edge.reviewed_at = datetime.now(timezone.utc)
                counts["rejected_unsupported_ai"] += 1
                continue

            if has_non_supply_label(
                edge.dependency_type,
                edge.product,
                edge.evidence_excerpt,
                edge.review_note,
            ) or has_invalid_dependency_label(edge.dependency_type):
                edge.review_status = "rejected"
                edge.review_note = (
                    f"Automated cleanup: '{edge.dependency_type or edge.product}' "
                    "is not an operational supply-chain relationship."
                )[:1000]
                edge.reviewed_at = datetime.now(timezone.utc)
                counts["rejected_non_supply"] += 1
                continue

            if has_reversed_role_label(edge.dependency_type):
                edge.review_status = "pending"
                edge.review_note = (
                    f"Automated cleanup: '{edge.dependency_type}' is a role label, "
                    "not a supply-chain dependency type."
                )[:1000]
                edge.reviewed_at = None
                counts["pending_role_labels"] += 1
                continue

            if has_speculative_supply_label(edge.evidence_excerpt, edge.review_note):
                edge.review_status = "rejected"
                edge.review_note = "Automated cleanup: relationship evidence is speculative, not verified."
                edge.reviewed_at = datetime.now(timezone.utc)
                counts.setdefault("rejected_speculative", 0)
                counts["rejected_speculative"] += 1
                continue

            if has_wrong_direction_review(edge.evidence_excerpt, edge.review_note):
                edge.review_status = "pending"
                edge.review_note = "Automated cleanup: review rationale indicates the relationship direction needs human review."
                edge.reviewed_at = None
                counts.setdefault("pending_wrong_direction", 0)
                counts["pending_wrong_direction"] += 1

        session.commit()
        print("Reviewed edge cleanup:", counts)
    finally:
        session.close()


if __name__ == "__main__":
    cleanup_reviewed_edges()
