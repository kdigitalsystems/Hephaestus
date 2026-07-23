import argparse
from collections import Counter
from collections import defaultdict
import sys

from sqlalchemy import inspect

from database import engine
from database import SessionLocal
from models import Edge, Node

REVERSED_ROLE_MARKERS = (
    "customer",
    "buyer",
    "client",
    "end-user",
    "outsourcing partner",
)

NON_DIRECTIONAL_ROLE_LABELS = (
    "customer relationship management",
    "customer data",
    "customer behavior",
    "supplier/customer",
    "customer/supplier",
)

NON_SUPPLY_MARKERS = (
    "competitor",
    "collaboration",
    "co-commercialization",
    "breach incident",
    "data breach",
    "equity stake",
    "investor",
    "acquisition",
    "acquired",
    "alleged liability",
    "asset sale",
    "banned",
    "merger",
    "license agreement",
    "licensing agreement",
    "option deal",
    "funding",
    "generic substitutes",
    "historical acquisition",
    "intellectual property theft",
    "joint exploration agreement",
    "joint vaccine",
    "joint venture",
    "legal dispute",
    "lawsuit",
    "neither supply chain",
    "neither supply-chain",
    "news report",
    "not a supply chain relationship",
    "not a supply-chain relationship",
    "not current supply chain",
    "not an operational supply chain",
    "not an operational supply-chain",
    "not known to be a customer",
    "not to purchase",
    "patent",
    "prohibited",
    "royalty",
    "shareholder",
    "spin-off",
    "spinoff",
    "stolen",
    "parent company of",
    "theft",
    "trade secret",
    "rights to",
    "sale_of_assets",
    "settlement",
    "sold its subsidiary",
    "spun off",
    "spun-off",
    "suing",
    "unknown operational supply chain",
    "zero emission vehicle credit",
    "merged company",
    "ownership",
    "competition",
)

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
)

REQUIRED_TABLES = ("nodes", "edges")


class DatabaseSchemaError(RuntimeError):
    pass


def has_reversed_role_label(dependency_type):
    dep_type = (dependency_type or "").lower()
    if any(label in dep_type for label in NON_DIRECTIONAL_ROLE_LABELS):
        return False
    return any(marker in dep_type for marker in REVERSED_ROLE_MARKERS)


def has_non_supply_label(*labels):
    label_text = " ".join(label or "" for label in labels).lower()
    return any(marker in label_text for marker in NON_SUPPLY_MARKERS)


def has_invalid_dependency_label(value):
    return str(value or "").strip().lower() in INVALID_DEPENDENCY_LABELS


def has_speculative_supply_label(*labels):
    label_text = " ".join(label or "" for label in labels).lower()
    return any(marker in label_text for marker in SPECULATIVE_SUPPLY_MARKERS)


def has_wrong_direction_review(*labels):
    label_text = " ".join(label or "" for label in labels).lower()
    return any(marker in label_text for marker in WRONG_DIRECTION_REVIEW_MARKERS)


def normalized_evidence(value):
    return " ".join(str(value or "").lower().split())


def reciprocal_same_evidence_edges(edges):
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

    flagged = []
    seen = set()
    for (source, target), rows in by_direction.items():
        reverse_key = (target, source)
        if reverse_key not in by_direction or (target, source, source) in seen:
            continue
        for edge, evidence in rows:
            for reverse_edge, reverse_evidence in by_direction[reverse_key]:
                if evidence == reverse_evidence:
                    flagged.extend([edge, reverse_edge])
                    seen.add((source, target, target))
                    break
    unique = []
    seen_edges = set()
    for edge in flagged:
        identity = id(edge)
        if identity in seen_edges:
            continue
        seen_edges.add(identity)
        unique.append(edge)
    return unique


def validate_database_schema():
    inspector = inspect(engine)
    missing_tables = [table for table in REQUIRED_TABLES if table not in inspector.get_table_names()]
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
        tickers = [ticker for (ticker,) in session.query(Node.ticker).filter(Node.ticker != None).all()]
        duplicate_tickers = [ticker for ticker, count in Counter(tickers).items() if count > 1]

        edges = session.query(Edge).all()
        published_edges = [
            edge for edge in edges
            if edge.review_status == "approved" or "Manual" in (edge.source_url or "")
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
        wrong_direction_edges = [
            edge
            for edge in published_edges
            if has_wrong_direction_review(edge.evidence_excerpt, edge.review_note)
        ]
        reciprocal_duplicate_edges = reciprocal_same_evidence_edges(published_edges)
        self_edges = [edge for edge in published_edges if edge.source_id == edge.target_id]
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
        print(f"Reciprocal duplicate evidence warnings: {len(reciprocal_duplicate_edges)}")
        print(f"Self-edge warnings: {len(self_edges)}")

        if duplicate_tickers:
            print("Duplicate ticker examples:", ", ".join(duplicate_tickers[:10]))

        for label, flagged_edges in (
            ("Role-label", reversed_edges),
            ("Non-supply", non_supply_edges),
            ("Speculative", speculative_edges),
            ("Wrong-direction", wrong_direction_edges),
            ("Reciprocal duplicate evidence", reciprocal_duplicate_edges),
            ("Self-edge", self_edges),
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
            + len(self_edges)
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
    except DatabaseSchemaError as exc:
        print(exc, file=sys.stderr)
        raise SystemExit(1) from exc
