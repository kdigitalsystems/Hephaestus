from datetime import datetime, timezone

from audit_data_quality import (
    has_non_supply_label,
    has_invalid_dependency_label,
    has_reversed_role_label,
    has_speculative_supply_label,
    has_wrong_direction_review,
)
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
    from auto_discover_edges import clean_company_name  # heavy module; import lazily

    edges = session.query(Edge).filter(Edge.dependency_type == CONCENTRATION_TYPE).all()
    for edge in edges:
        filer, customer = edge.source_node, edge.target_node
        if not filer or not customer:
            continue
        sentence = disclosure_sentence(edge.evidence_excerpt)
        known = {customer.name: clean_company_name(customer.name)}
        filer_names = (filer.name, clean_company_name(filer.name))
        shares = [d.share_pct for d in extract_disclosures(sentence, known, filer_names) if d.customer_name == customer.name and d.share_pct is not None]
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
