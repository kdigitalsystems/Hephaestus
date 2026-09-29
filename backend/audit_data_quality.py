import argparse
import re
from collections import Counter
from collections import defaultdict
import sys

from sqlalchemy import inspect
from sqlalchemy.exc import SQLAlchemyError

from database import engine
from customer_concentration import filer_documented_direction, implausible_share
from database import SessionLocal
from models import Edge, Node
from evidence_quality import (
    bound_by_generic_word,
    evidence_direction,
    evidence_support,
    has_non_supply_relationship,
    is_endpoint_label,
    is_role_label,
    register_company_names,
    requires_source_evidence,
    unsupported_ai_evidence,
)
from thefuzz import fuzz

INVALID_DEPENDENCY_LABELS = {
    "news",
    "unknown",
}

SPECULATIVE_SUPPLY_MARKERS = (
    "likely",
    "might",
    "may be",
    "not explicitly stated",
    "no evidence",
    "suggesting",
    "would be",
    "would use",
    "not found in source text",
)

WRONG_DIRECTION_REVIEW_MARKERS = (
    "votes reverse",
    "not the other way",
    "direction of the edge is backwards",
    "edge is backwards",
    "source is the customer",
    "source is a customer",
    "target is the supplier",
    "target) supplies",
    # The panel's usual wording. Only the four phrasings above were matched, so 65 of
    # 344 consensus-approved links were published with a rationale saying "the
    # direction is backwards" / "should be reversed".
    "is backwards",
    "has the relationship backwards",
    "relationship backwards",
    "should be reversed",
    "direction is reversed",
    "direction should be reversed",
    "direction is wrong",
    "direction is incorrect",
    "wrong direction",
    "opposite direction",
    "reverse the direction",
    "reverse the edge",
)

REQUIRED_TABLES = ("nodes", "edges")


class DatabaseSchemaError(RuntimeError):
    pass


class DatabaseAccessError(RuntimeError):
    pass


def has_reversed_role_label(dependency_type):
    return is_role_label(dependency_type)


def has_non_supply_label(dependency_type=None, product=None, evidence=None, note=None):
    """Positional order is (dependency_type, product, evidence_excerpt, review_note)."""
    return has_non_supply_relationship(dependency_type, product, evidence, note)


def has_invalid_dependency_label(value):
    return str(value or "").strip().lower() in INVALID_DEPENDENCY_LABELS


def has_speculative_supply_label(*labels):
    label_text = " ".join(label or "" for label in labels).lower()
    return any(marker in label_text for marker in SPECULATIVE_SUPPLY_MARKERS)


# "Ollama consensus review: votes reverse:2, approve:1. Lead rationale ..." is the
# panel reporting how it *fixed* the direction. Matching the tally re-opened the very
# edges the review step had just corrected, every run, forever.
CONSENSUS_TALLY = re.compile(r"^ollama consensus review[^.]*?(?:votes [^.]*)?\.\s*", re.IGNORECASE)


def strip_consensus_tally(text):
    return CONSENSUS_TALLY.sub("", str(text or ""), count=1)


def has_wrong_direction_review(*labels):
    label_text = " ".join(strip_consensus_tally(label) for label in labels).lower()
    return any(marker in label_text for marker in WRONG_DIRECTION_REVIEW_MARKERS)


def normalized_evidence(value):
    return " ".join(str(value or "").lower().split())


RECIPROCAL_EVIDENCE_SIMILARITY = 90


def reciprocal_same_evidence_pairs(edges):
    """(edge, mirror) pairs publishing one fact in both directions.

    Exact equality caught 1 of 9 real cases: the same sentence reaches the two edges with
    a different trailing clause or punctuation, so near-identical evidence counts too.
    """
    by_direction = defaultdict(list)
    for edge in edges:
        if not edge.source_node or not edge.target_node:
            continue
        evidence = normalized_evidence(edge.evidence_excerpt)
        if not evidence:
            continue
        source = edge.source_node.ticker or edge.source_node.name
        target = edge.target_node.ticker or edge.target_node.name
        by_direction[(source, target)].append((edge, evidence))

    pairs = []
    paired = set()
    for (source, target), rows in by_direction.items():
        if (target, source) in paired:
            continue
        for edge, evidence in rows:
            mirror = next(
                (
                    candidate
                    for candidate, candidate_evidence in by_direction.get((target, source), [])
                    if candidate_evidence == evidence or fuzz.ratio(evidence, candidate_evidence) >= RECIPROCAL_EVIDENCE_SIMILARITY
                ),
                None,
            )
            if mirror is not None:
                pairs.append((edge, mirror))
                paired.add((source, target))
                break
    return pairs


# Must match review_edges_with_ollama.HELD_NOTE_PREFIX; importing that module pulls in
# the Ollama client, which neither the audit nor the cleanup step needs.
HELD_NOTE_PREFIX = "Ollama consensus review"


# Every automated reviewer's note starts with one of these. "Ollama review:" (the older
# single-model reviewer) was once missing, so ~80 model verdicts counted as human ones
# and were exempt from every safeguard that sends model approvals to a person.
MODEL_NOTE_PREFIXES = ("ollama consensus", "ollama review", "ollama report review")


def is_model_note(note):
    return str(note or "").strip().lower().startswith(MODEL_NOTE_PREFIXES)


def needs_human_confirmation(edge):
    """Fresh or model-approved edges go to a human; a human's own verdict stands."""
    note = str(edge.review_note or "")
    if edge.review_status == "pending":
        return not note.startswith(HELD_NOTE_PREFIX)
    if edge.review_status == "approved":
        return is_model_note(note)
    return False


def is_published(edge):
    return edge.review_status != "rejected" and (edge.review_status == "approved" or "Manual" in (edge.source_url or ""))


def direction_contested(edge):
    """The model that approved this edge said, in its own rationale, that it is backwards.

    A supplier's own customer disclosure fixes the direction, so its edges are exempt:
    the panel says "backwards" about P&G -> Walmart as readily as about TSM -> ASML.
    """
    if filer_documented_direction(edge):
        return False
    return has_wrong_direction_review(edge.evidence_excerpt, edge.review_note)


def model_verdict(edge):
    """Published on a model's say-so alone: not a human verdict and not a curated seed.

    Curated seeds stay published whatever their review status, so holding one would
    change nothing and its warning would fail every run.
    """
    return needs_human_confirmation(edge) and requires_source_evidence(edge.source_url)


def direction_settled(edge):
    """A filing, a human or a curated seed fixed this edge's direction."""
    return filer_documented_direction(edge) or not model_verdict(edge)


def contested_reciprocal_edges(edges):
    """Published edges whose opposite direction is also published, and that need a human.

    One relationship published both ways is nearly always one fact read twice: VEEV <->
    LLY, PWR <-> XEL, INTC <-> TSM. The old test required near-identical evidence, and
    reworded excerpts slipped through (15 pairs). Within a pair, a side whose direction a
    filing, a human or a curated seed settled is kept. When neither side is settled, a
    side labelled with a real product beats one labelled with a bare role
    ("manufacturer -> customer"); otherwise both go to a human, because the models'
    own direction calls are what produced the pair.
    """
    by_pair = defaultdict(list)
    for edge in edges:
        if edge.source_id is None or edge.target_id is None or edge.source_id == edge.target_id:
            continue
        by_pair[(edge.source_id, edge.target_id)].append(edge)
    contested = []
    for (source_id, target_id), forward in by_pair.items():
        backward = by_pair.get((target_id, source_id))
        if not backward:
            continue
        if any(direction_settled(edge) for edge in forward) and any(direction_settled(edge) for edge in backward):
            continue  # both directions confirmed independently: a genuine two-way relationship
        if any(direction_settled(edge) for edge in backward):
            contested.extend(edge for edge in forward if not direction_settled(edge))
            continue
        if any(direction_settled(edge) for edge in forward):
            continue  # the backward side is collected when its own key is visited
        forward_labelled = any(not is_role_label(edge.dependency_type) for edge in forward)
        backward_labelled = any(not is_role_label(edge.dependency_type) for edge in backward)
        if forward_labelled and not backward_labelled:
            continue  # the role-labelled backward side is collected on its own visit
        contested.extend(forward)
    return contested


# TSMC is the foundry for the companies that name it; a model that reads "X's chips are
# made by TSMC" often writes X -> TSM. Its own suppliers (ASML's lithography, gases,
# power) are the opposite and must keep their direction: the old export-time flip
# published ASML's EUV machines as TSM -> ASML.
FOUNDRY_SERVICE_TERMS = (
    "advanced silicon fabrication",
    "advanced manufacturing services",
    "chip fabrication",
    "chip manufacturing",
    "chip production",
    "contract manufacturing",
    "foundry",
    "outsourced production",
    "semiconductor chips",
    "semiconductor manufacturing",
    "silicon fabrication",
)
FAB_INPUT_TERMS = (
    "lithography", "euv", "equipment", "tool", "machine", "material", "chemical", "gas",
    "photoresist", "substrate", "silicon wafers", "electricity", "power", "water",
    "construction", "cleanroom", "software", "design automation", "intellectual property",
)
FOUNDRY_CUSTOMER_EVIDENCE = re.compile(
    r"\b(?:customers?\s+of|outsourc\w*\s+(?:\w+\s+){0,4}?production\s+to|manufactured\s+by|fabricated\s+by)\s+"
    r"(?:tsmc|taiwan\s+semiconductor)",
    re.IGNORECASE,
)


def backwards_foundry_edge(edge):
    """X -> TSM describing TSMC's foundry work for X, which is really TSM -> X."""
    target = getattr(edge, "target_node", None)
    if str(getattr(target, "ticker", "") or "").upper() != "TSM":
        return False
    described = f"{edge.dependency_type or ''} {edge.product or ''}".lower()
    if any(term in described for term in FAB_INPUT_TERMS):
        return False
    return any(term in described for term in FOUNDRY_SERVICE_TERMS) or bool(
        FOUNDRY_CUSTOMER_EVIDENCE.search(str(edge.evidence_excerpt or ""))
    )


def endpoint_label_as_product(edge):
    """The published product is just an endpoint's industry or sector name.

    The review prompt shows each company's industry and the models copied it back as
    the product: "Auto Parts", "Semiconductors", "Drug Manufacturers - Specialty & Generic".
    """
    return is_endpoint_label(edge.product, edge.source_node, edge.target_node)


def generic_word_entity_edge(edge):
    """An endpoint was bound from a place or generic word in the excerpt, not its name."""
    if not requires_source_evidence(edge.source_url):
        return False
    return any(bound_by_generic_word(edge.evidence_excerpt, node) for node in (edge.source_node, edge.target_node))


def evidence_problem(edge):
    """(verdict, reason) when a model-approved edge's excerpt does not support it, else None.

    131 approved links had an excerpt that never named one of the two companies. About a
    quarter are fine ("It also reported its top customers as ... Microsoft"); the rest
    were trivia, other companies, rivals or asset sales. See evidence_support.
    """
    if not model_verdict(edge) or filer_documented_direction(edge):
        return None
    verdict, reason = evidence_support(edge.evidence_excerpt, edge.source_node, edge.target_node)
    if verdict == "named":
        # Both named: does the wording say the supply runs the other way? 36 of 203 such
        # links were published backwards ("Corning is one of the main suppliers to Apple"
        # as Apple -> Corning). Held, not reversed: the same reading flags some junk.
        direction, phrase = evidence_direction(edge.evidence_excerpt, edge.source_node, edge.target_node)
        if direction == "backward":
            return "backwards", f'the excerpt says the supply runs the other way ("{phrase[:150]}")'
        return None
    return (verdict, reason) if verdict in {"unsupported", "backwards", "unclear"} else None


def reciprocal_same_evidence_edges(edges):
    unique = []
    seen = set()
    for edge in (side for pair in reciprocal_same_evidence_pairs(edges) for side in pair):
        if id(edge) not in seen:
            seen.add(id(edge))
            unique.append(edge)
    return unique


def validate_database_schema():
    try:
        inspector = inspect(engine)
        missing_tables = [table for table in REQUIRED_TABLES if table not in inspector.get_table_names()]
    except SQLAlchemyError as exc:
        raise DatabaseAccessError(
            "Unable to inspect the Hephaestus database. Stop concurrent database jobs "
            "and run the audit from the repository's WSL shell."
        ) from exc
    if missing_tables:
        missing = ", ".join(missing_tables)
        raise DatabaseSchemaError(
            f"Database schema is missing required table(s): {missing}. "
            "Run `python backend/database.py` and seed or rebuild the database before auditing."
        )


def audit_database(fail_on_warnings=False):
    validate_database_schema()
    session = SessionLocal()
    try:
        register_company_names(session.query(Node.ticker, Node.name).filter(Node.ticker.is_not(None)).all())
        tickers = [ticker for (ticker,) in session.query(Node.ticker).filter(Node.ticker.is_not(None)).all()]
        duplicate_tickers = [ticker for ticker, count in Counter(tickers).items() if count > 1]

        edges = session.query(Edge).all()
        # Mirror export.should_export_edge: a rejected edge is never published, even
        # when it came from a manual seed, so it must not fail the audit either.
        published_edges = [
            edge for edge in edges
            if edge.review_status != "rejected"
            and (edge.review_status == "approved" or "Manual" in (edge.source_url or ""))
        ]
        edge_keys = [
            (edge.source_id, edge.target_id, edge.dependency_type)
            for edge in published_edges
        ]
        duplicate_edge_keys = [
            key for key, count in Counter(edge_keys).items() if count > 1
        ]
        reversed_edges = [edge for edge in published_edges if has_reversed_role_label(edge.dependency_type)]
        non_supply_edges = [
            edge
            for edge in published_edges
            if has_invalid_dependency_label(edge.dependency_type)
            or has_non_supply_label(
                edge.dependency_type,
                edge.product,
                edge.evidence_excerpt,
                edge.review_note,
            )
        ]
        speculative_edges = [
            edge
            for edge in published_edges
            if has_speculative_supply_label(edge.evidence_excerpt, edge.review_note)
        ]
        # Each check mirrors a cleanup rule that runs first, so a warning here means the
        # cleanup missed something, never that it deliberately kept a human's verdict.
        wrong_direction_edges = [
            edge
            for edge in published_edges
            if direction_contested(edge) and model_verdict(edge)
        ]
        reciprocal_duplicate_edges = contested_reciprocal_edges(published_edges)
        foundry_direction_edges = [edge for edge in published_edges if backwards_foundry_edge(edge)]
        industry_product_edges = [edge for edge in published_edges if endpoint_label_as_product(edge)]
        generic_entity_edges = [
            edge for edge in published_edges
            if generic_word_entity_edge(edge) and model_verdict(edge)
        ]
        unsupported_evidence_edges = [edge for edge in published_edges if evidence_problem(edge)]
        self_edges = [edge for edge in published_edges if edge.source_id == edge.target_id]
        unsupported_ai_edges = [
            edge for edge in published_edges
            if unsupported_ai_evidence(edge.source_url, edge.evidence_excerpt)
        ]
        implausible_share_edges = [
            edge for edge in published_edges
            if edge.dependency_type == "Revenue Concentration"
            and str(edge.review_note or "").startswith("Ollama consensus")
            and implausible_share(edge.revenue_share, edge.target_node.market_cap if edge.target_node else None)
        ]
        ai_edges = [edge for edge in edges if "AI" in (edge.source_url or "")]
        manual_edges = [edge for edge in edges if "Manual" in (edge.source_url or "")]
        status_counts = Counter(edge.review_status or "pending" for edge in edges)

        print("--- Hephaestus Data Quality Audit ---")
        print(f"Nodes: {session.query(Node).count()}")
        print(f"Edges: {len(edges)}")
        print(f"Published edges: {len(published_edges)}")
        print(f"Manual/reviewed edges: {len(manual_edges)}")
        print(f"Unreviewed AI edges: {len(ai_edges)}")
        print("Review statuses:", dict(sorted(status_counts.items())))
        print(f"Duplicate tickers: {len(duplicate_tickers)}")
        print(f"Duplicate exact edges: {len(duplicate_edge_keys)}")
        print(f"Role-label direction warnings: {len(reversed_edges)}")
        print(f"Non-supply relationship warnings: {len(non_supply_edges)}")
        print(f"Speculative relationship warnings: {len(speculative_edges)}")
        print(f"Wrong-direction review warnings: {len(wrong_direction_edges)}")
        print(f"Reciprocal direction warnings: {len(reciprocal_duplicate_edges)}")
        print(f"Backwards TSMC foundry warnings: {len(foundry_direction_edges)}")
        print(f"Industry-as-product warnings: {len(industry_product_edges)}")
        print(f"Generic-word entity warnings: {len(generic_entity_edges)}")
        print(f"Excerpt-does-not-support-edge warnings: {len(unsupported_evidence_edges)}")
        print(f"Self-edge warnings: {len(self_edges)}")
        print(f"Unsupported AI evidence warnings: {len(unsupported_ai_edges)}")
        print(f"Implausible revenue-share warnings: {len(implausible_share_edges)}")

        if duplicate_tickers:
            print("Duplicate ticker examples:", ", ".join(duplicate_tickers[:10]))

        for label, flagged_edges in (
            ("Role-label", reversed_edges),
            ("Non-supply", non_supply_edges),
            ("Speculative", speculative_edges),
            ("Wrong-direction", wrong_direction_edges),
            ("Reciprocal direction", reciprocal_duplicate_edges),
            ("Backwards TSMC foundry", foundry_direction_edges),
            ("Industry as product", industry_product_edges),
            ("Generic-word entity", generic_entity_edges),
            ("Excerpt does not support edge", unsupported_evidence_edges),
            ("Self-edge", self_edges),
            ("Unsupported AI evidence", unsupported_ai_edges),
            ("Implausible revenue share", implausible_share_edges),
        ):
            for edge in flagged_edges[:10]:
                source = edge.source_node.ticker if edge.source_node else edge.source_id
                target = edge.target_node.ticker if edge.target_node else edge.target_id
                print(f"{label}: {source} -> {target} ({edge.dependency_type})")

        if duplicate_edge_keys:
            print("Duplicate edge examples:", duplicate_edge_keys[:10])

        warning_count = (
            len(duplicate_tickers)
            + len(duplicate_edge_keys)
            + len(reversed_edges)
            + len(non_supply_edges)
            + len(speculative_edges)
            + len(wrong_direction_edges)
            + len(reciprocal_duplicate_edges)
            + len(foundry_direction_edges)
            + len(industry_product_edges)
            + len(generic_entity_edges)
            + len(unsupported_evidence_edges)
            + len(self_edges)
            + len(unsupported_ai_edges)
            + len(implausible_share_edges)
        )
        if fail_on_warnings and warning_count:
            raise SystemExit(1)
    finally:
        session.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Audit Hephaestus node and edge data quality.")
    parser.add_argument("--fail-on-warnings", action="store_true", help="Exit non-zero when warnings are found.")
    args = parser.parse_args()
    try:
        audit_database(fail_on_warnings=args.fail_on_warnings)
    except (DatabaseSchemaError, DatabaseAccessError, SQLAlchemyError) as exc:
        print(exc, file=sys.stderr)
        raise SystemExit(1) from exc
