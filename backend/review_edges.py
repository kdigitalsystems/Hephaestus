import argparse
from datetime import datetime, timezone

from sqlalchemy.exc import IntegrityError

from database import SessionLocal
from edge_review_decisions import DEFAULT_PATH as DECISIONS_PATH, export_decisions
from models import Edge, Node

VALID_STATUSES = {"pending", "approved", "rejected"}


def persist_decisions(args):
    """Keep data/edge_review_decisions.json in sync so the next pipeline run cannot revert this review."""
    if getattr(args, "no_persist", False):
        print("Note: decision not persisted; run `python3 backend/edge_review_decisions.py export` before the next pipeline run.")
        return
    export_decisions(DECISIONS_PATH)


def format_edge(edge):
    source = f"{edge.source_node.ticker} {edge.source_node.name}" if edge.source_node else str(edge.source_id)
    target = f"{edge.target_node.ticker} {edge.target_node.name}" if edge.target_node else str(edge.target_id)
    confidence = "N/A" if edge.confidence_score is None else f"{edge.confidence_score:.2f}"
    return (
        f"[{edge.id}] {source} -> {target}\n"
        f"    status={edge.review_status or 'pending'} confidence={confidence}\n"
        f"    type={edge.dependency_type}\n"
        f"    product={edge.product or 'N/A'}\n"
        f"    source={edge.source_title or edge.source_url or 'N/A'}\n"
        f"    evidence={edge.evidence_excerpt or 'N/A'}"
    )


def list_edges(args):
    session = SessionLocal()
    try:
        query = session.query(Edge)
        if args.status:
            query = query.filter(Edge.review_status == args.status)
        if args.source:
            query = query.filter(Edge.source_node.has(Node.ticker == args.source.upper()))
        if args.target:
            query = query.filter(Edge.target_node.has(Node.ticker == args.target.upper()))

        edges = query.order_by(Edge.confidence_score.desc().nullslast(), Edge.id.asc()).limit(args.limit).all()
        for edge in edges:
            print(format_edge(edge))
    finally:
        session.close()


def set_status(args):
    session = SessionLocal()
    try:
        edge = session.get(Edge, args.edge_id)
        if not edge:
            raise SystemExit(f"Edge {args.edge_id} not found.")

        edge.review_status = args.status
        if args.note:
            # An omitted --note must not erase the existing rationale.
            edge.review_note = args.note
        edge.reviewed_at = datetime.now(timezone.utc) if args.status in {"approved", "rejected"} else None
        session.commit()
        print(format_edge(edge))
    finally:
        session.close()
    persist_decisions(args)


def edit_edge(args):
    session = SessionLocal()
    try:
        edge = session.get(Edge, args.edge_id)
        if not edge:
            raise SystemExit(f"Edge {args.edge_id} not found.")

        if args.type:
            edge.dependency_type = args.type
        if args.product:
            edge.product = args.product
        if args.source_url:
            edge.source_url = args.source_url
        if args.source_title:
            edge.source_title = args.source_title
        if args.evidence:
            edge.evidence_excerpt = args.evidence
        if args.note:
            edge.review_note = args.note

        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            raise SystemExit(
                f"Edge {args.edge_id} was not changed: another edge with dependency type "
                f"{args.type!r} already exists between these two companies."
            )
        print(format_edge(edge))
    finally:
        session.close()
    persist_decisions(args)


def build_parser():
    parser = argparse.ArgumentParser(description="Review and curate Hephaestus supply-chain edges.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="List edges for review.")
    list_parser.add_argument("--status", choices=sorted(VALID_STATUSES), default="pending")
    list_parser.add_argument("--source", help="Filter by source ticker.")
    list_parser.add_argument("--target", help="Filter by target ticker.")
    list_parser.add_argument("--limit", type=int, default=25)
    list_parser.set_defaults(func=list_edges)

    for status in ("approve", "reject", "pend"):
        status_parser = subparsers.add_parser(status, help=f"Mark an edge as {status}.")
        status_parser.add_argument("edge_id", type=int)
        status_parser.add_argument("--note", default="")
        status_parser.add_argument("--no-persist", action="store_true", help="Do not refresh data/edge_review_decisions.json.")
        status_parser.set_defaults(
            func=set_status,
            status={"approve": "approved", "reject": "rejected", "pend": "pending"}[status]
        )

    edit_parser = subparsers.add_parser("edit", help="Edit edge metadata.")
    edit_parser.add_argument("edge_id", type=int)
    edit_parser.add_argument("--type")
    edit_parser.add_argument("--product")
    edit_parser.add_argument("--source-url")
    edit_parser.add_argument("--source-title")
    edit_parser.add_argument("--evidence")
    edit_parser.add_argument("--note")
    edit_parser.add_argument("--no-persist", action="store_true", help="Do not refresh data/edge_review_decisions.json.")
    edit_parser.set_defaults(func=edit_edge)

    return parser


if __name__ == "__main__":
    args = build_parser().parse_args()
    args.func(args)
