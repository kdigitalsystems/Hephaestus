import argparse
from collections import Counter

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
    "investor",
    "acquisition",
    "acquired",
    "merger",
    "option deal",
    "funding",
    "shareholder",
)


def has_reversed_role_label(dependency_type):
    dep_type = (dependency_type or "").lower()
    if any(label in dep_type for label in NON_DIRECTIONAL_ROLE_LABELS):
        return False
    return any(marker in dep_type for marker in REVERSED_ROLE_MARKERS)


def has_non_supply_label(dependency_type):
    dep_type = (dependency_type or "").lower()
    return any(marker in dep_type for marker in NON_SUPPLY_MARKERS)


def audit_database(fail_on_warnings=False):
    session = SessionLocal()
    try:
        tickers = [ticker for (ticker,) in session.query(Node.ticker).filter(Node.ticker != None).all()]
        duplicate_tickers = [ticker for ticker, count in Counter(tickers).items() if count > 1]

        edges = session.query(Edge).all()
        edge_keys = [
            (edge.source_id, edge.target_id, edge.dependency_type)
            for edge in edges
        ]
        duplicate_edge_keys = [
            key for key, count in Counter(edge_keys).items() if count > 1
        ]
        reversed_edges = [edge for edge in edges if has_reversed_role_label(edge.dependency_type)]
        non_supply_edges = [edge for edge in edges if has_non_supply_label(edge.dependency_type)]
        self_edges = [edge for edge in edges if edge.source_id == edge.target_id]

        print("--- Hephaestus Data Quality Audit ---")
        print(f"Nodes: {session.query(Node).count()}")
        print(f"Edges: {len(edges)}")
        print(f"Duplicate tickers: {len(duplicate_tickers)}")
        print(f"Duplicate exact edges: {len(duplicate_edge_keys)}")
        print(f"Role-label direction warnings: {len(reversed_edges)}")
        print(f"Non-supply relationship warnings: {len(non_supply_edges)}")
        print(f"Self-edge warnings: {len(self_edges)}")

        if duplicate_tickers:
            print("Duplicate ticker examples:", ", ".join(duplicate_tickers[:10]))

        for label, flagged_edges in (
            ("Role-label", reversed_edges),
            ("Non-supply", non_supply_edges),
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
    audit_database(fail_on_warnings=args.fail_on_warnings)
