from collections import defaultdict
from datetime import datetime, timezone

from audit_data_quality import (  # noqa: F401  (HELD_NOTE_PREFIX is re-exported for callers)
    HELD_NOTE_PREFIX,
    backwards_foundry_edge,
    contested_reciprocal_edges,
    direction_contested,
    endpoint_label_as_product,
    evidence_problem,
    generic_word_entity_edge,
    has_invalid_dependency_label,
    has_non_supply_label,
    has_reversed_role_label,
    has_speculative_supply_label,
    is_published,
    model_verdict,
    needs_human_confirmation,
)
from customer_concentration import describe_share, disclosure_sentence, extract_disclosures, implausible_share
from sqlalchemy import or_

from database import SessionLocal
from customer_concentration import filer_documented_direction
from evidence_quality import (
    evidence_direction,
    evidence_support,
    has_usable_evidence,
    register_company_names,
    requires_source_evidence,
    unsupported_ai_evidence,
)
from models import Edge, Node

CONCENTRATION_TYPE = "Revenue Concentration"


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

    # Any edge carrying a disclosed share, whatever the panel relabelled it to: ROKU's
    # 81% survived for days as "operational supply chain dependency".
    edges = session.query(Edge).filter(
        or_(Edge.dependency_type == CONCENTRATION_TYPE, Edge.revenue_share.is_not(None)),
        Edge.review_status != "rejected",
    ).all()
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
            if needs_human_confirmation(edge):
                hold_for_human(edge, counts, "concentration_unsupported",
                               "the filing sentence no longer yields a revenue share for this customer")
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
            hold_for_human(edge, counts, "concentration_held_implausible", f"{reason}; a human must confirm this disclosure")


def hold_for_human(edge, counts, key, reason):
    """Send an edge back for review with a note the consensus panel skips."""
    edge.review_status = "pending"
    edge.review_note = f"{HELD_NOTE_PREFIX} hold: {reason}."[:1000]
    edge.reviewed_at = None
    counts[key] = counts.get(key, 0) + 1


def hold_impossible_share_totals(session, counts):
    """A supplier cannot sell more than all of its revenue.

    ROKU published 81% to both Best Buy and Walmart, HLIT 175% across two customers: a
    combined figure ("Amazon, Best Buy and Walmart in total accounted for 81%") copied
    onto each named customer.
    """
    totals = defaultdict(list)
    for edge in session.query(Edge).filter(Edge.revenue_share.is_not(None)).all():
        if is_published(edge):
            totals[edge.source_id].append(edge)
    for group in totals.values():
        total = sum(edge.revenue_share or 0 for edge in group)
        if len(group) < 2 or total <= 100:
            continue
        for edge in group:
            if needs_human_confirmation(edge):
                hold_for_human(
                    edge, counts, "concentration_totals_over_100",
                    f"this supplier's disclosed shares total {total:.0f}% of its revenue, "
                    "which usually means a combined figure was copied onto each customer",
                )


def correct_foundry_direction(session, counts):
    """Store TSMC's foundry work as TSM -> customer, in the database itself.

    The export used to flip these while publishing, which hid the fix from the audit,
    produced INTC <-> TSM in both directions, and also flipped ASML's lithography
    machines into TSM -> ASML. Correcting the stored edge lets every later check see it.
    """
    for edge in session.query(Edge).filter(Edge.review_status != "rejected").all():
        if not backwards_foundry_edge(edge):
            continue
        mirror = (
            session.query(Edge)
            .filter(
                Edge.source_id == edge.target_id,
                Edge.target_id == edge.source_id,
                Edge.dependency_type == edge.dependency_type,
                Edge.id != edge.id,
            )
            .first()
        )
        if mirror is not None:
            # TSM -> X under this label already exists (and a human may have rejected
            # it); the backwards copy adds nothing either way.
            edge.review_status = "rejected"
            edge.review_note = f"Automated cleanup: backwards duplicate of TSMC foundry edge #{mirror.id}."
            edge.reviewed_at = datetime.now(timezone.utc)
            counts["foundry_duplicates_rejected"] = counts.get("foundry_duplicates_rejected", 0) + 1
            continue
        edge.source_id, edge.target_id = edge.target_id, edge.source_id
        counts["foundry_direction_corrected"] = counts.get("foundry_direction_corrected", 0) + 1
    session.flush()
    # The relationships still point at the old nodes until they are reloaded.
    session.expire_all()


def repair_endpoint_products(session, counts):
    """An endpoint's industry is not a product (36 links read "Auto Parts", "Semiconductors")."""
    for edge in session.query(Edge).filter(Edge.review_status != "rejected").all():
        if not endpoint_label_as_product(edge):
            continue
        if edge.revenue_share is not None and edge.source_node is not None:
            edge.product = describe_share(edge.revenue_share, edge.source_node.ticker)
        else:
            # The frontend hides a product equal to the relationship type, so the
            # misleading line disappears instead of being replaced by a guess.
            edge.product = edge.dependency_type
        counts["industry_products_cleared"] = counts.get("industry_products_cleared", 0) + 1


def resolve_reciprocal_duplicates(session, counts):
    """One relationship published in both directions goes to a human.

    See audit_data_quality.contested_reciprocal_edges for which side is kept.
    """
    published = [edge for edge in session.query(Edge).all() if is_published(edge)]
    for edge in contested_reciprocal_edges(published):
        if edge.review_status != "approved":
            continue
        mirror = next(
            (other for other in published if other.source_id == edge.target_id and other.target_id == edge.source_id),
            None,
        )
        hold_for_human(
            edge, counts, "reciprocal_held",
            f"the opposite direction is also published (edge #{getattr(mirror, 'id', '?')}); "
            "a human must decide which direction is correct",
        )


EXCERPT_REJECTION_NOTE = "Automated cleanup: the evidence excerpt does not support this link"


def reopen_cleared_rejections(session, counts):
    """Send an automated evidence rejection back to a person once the rules clear it.

    The evidence rules get better; a link rejected under an older version (Elastic ->
    eBay, before "Elasticsearch" named Elastic; SSR Mining -> Bank of Montreal, before
    "16% sold to Bank of Montreal" was read) should not stay rejected when the current
    rules would keep it. It goes back to the review queue, never straight to the site,
    and a person's own rejection is never reopened.
    """
    rejected = session.query(Edge).filter(
        Edge.review_status == "rejected",
        Edge.review_note.like(f"{EXCERPT_REJECTION_NOTE}%"),
    ).all()
    for edge in rejected:
        verdict, reason = evidence_support(edge.evidence_excerpt, edge.source_node, edge.target_node)
        if verdict == "unsupported":
            continue
        if verdict == "backwards":
            reason = "the excerpt says the supply runs the other way"
        elif verdict == "named":
            direction, phrase = evidence_direction(edge.evidence_excerpt, edge.source_node, edge.target_node)
            reason = (
                f'the excerpt says the supply runs the other way ("{phrase[:150]}")' if direction == "backward"
                else "the current evidence rules no longer reject it"
            )
        hold_for_human(edge, counts, "reopened_after_rule_fix", f"reopened after an evidence-rule fix: {reason}")


def reject_held_junk(session, counts):
    """Links waiting for a person whose excerpt the evidence rules call junk.

    Hundreds of links were held before those rules existed ("Evidence excerpt does not
    name both companies"); the ones that are plainly junk should not cost a person a
    click. Anything the rules find doubtful stays in the queue.
    """
    # The pipeline's session does not autoflush; without this the query cannot see the
    # holds made earlier in this same run.
    session.flush()
    held = session.query(Edge).filter(
        Edge.review_status == "pending",
        Edge.review_note.like(f"{HELD_NOTE_PREFIX}%"),
    ).all()
    for edge in held:
        if not requires_source_evidence(edge.source_url) or filer_documented_direction(edge):
            continue
        if not has_usable_evidence(edge.evidence_excerpt):
            continue  # missing evidence is not junk evidence; a person can find a source
        verdict, reason = evidence_support(edge.evidence_excerpt, edge.source_node, edge.target_node)
        if verdict == "unsupported":
            edge.review_status = "rejected"
            edge.review_note = f"Automated cleanup: the evidence excerpt does not support this link ({reason})."[:1000]
            edge.reviewed_at = datetime.now(timezone.utc)
            counts["rejected_held_junk"] = counts.get("rejected_held_junk", 0) + 1


def cleanup_reviewed_edges():
    session = SessionLocal()
    counts = {"rejected_non_supply": 0, "rejected_unsupported_ai": 0, "pending_role_labels": 0}
    try:
        register_company_names(session.query(Node.ticker, Node.name).filter(Node.ticker.is_not(None)).all())
        reopen_cleared_rejections(session, counts)
        session.flush()
        # Direction first: the reciprocal check must see the corrected foundry edges.
        correct_foundry_direction(session, counts)
        recheck_concentration_edges(session, counts)
        repair_endpoint_products(session, counts)
        resolve_reciprocal_duplicates(session, counts)
        hold_impossible_share_totals(session, counts)
        approved_edges = session.query(Edge).filter(Edge.review_status == "approved").all()
        for edge in approved_edges:
            if not str(edge.evidence_excerpt or "").strip():
                # Six links were published with a citation but no excerpt at all.
                hold_for_human(edge, counts, "pending_missing_evidence",
                               "published without an evidence excerpt; a human must confirm the source")
                continue
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

            if direction_contested(edge) and model_verdict(edge):
                # Held, not merely pending: an "Automated cleanup" note put the edge back
                # in the review queue, the panel approved it again, and this step demoted
                # it again - the same four edges, every day.
                hold_for_human(edge, counts, "pending_wrong_direction",
                               "the approving model's own rationale says the direction is backwards")
                continue

            if generic_word_entity_edge(edge) and model_verdict(edge):
                hold_for_human(edge, counts, "pending_generic_entity",
                               "a company was matched from a place or generic word in the excerpt, not its name")
                continue

            problem = evidence_problem(edge)
            if problem:
                verdict, reason = problem
                if verdict == "unsupported":
                    edge.review_status = "rejected"
                    edge.review_note = f"Automated cleanup: the evidence excerpt does not support this link ({reason})."[:1000]
                    edge.reviewed_at = datetime.now(timezone.utc)
                    counts["rejected_unsupported_excerpt"] = counts.get("rejected_unsupported_excerpt", 0) + 1
                else:
                    hold_for_human(edge, counts, f"pending_excerpt_{verdict}", reason)

        reject_held_junk(session, counts)
        session.commit()
        print("Reviewed edge cleanup:", counts)
    finally:
        session.close()


if __name__ == "__main__":
    cleanup_reviewed_edges()
