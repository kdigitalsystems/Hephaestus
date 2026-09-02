import argparse
import json
import os
from datetime import datetime, timezone

from sqlalchemy.exc import IntegrityError

from database import SessionLocal
from models import Edge, Node


BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_PATH = os.path.join(BASE_DIR, "data", "edge_review_decisions.json")


def edge_key(edge):
    return {
        "source_ticker": edge.source_node.ticker if edge.source_node else "",
        "target_ticker": edge.target_node.ticker if edge.target_node else "",
        "source_name": edge.source_node.name if edge.source_node else "",
        "target_name": edge.target_node.name if edge.target_node else "",
    }


def export_decisions(path):
    session = SessionLocal()
    try:
        edges = (
            session.query(Edge)
            .filter(Edge.review_status.in_(("approved", "rejected")))
            .order_by(Edge.id.asc())
            .all()
        )
        payload = {
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "decisions": [
                {
                    "edge_id": edge.id,
                    **edge_key(edge),
                    "dependency_type": edge.dependency_type,
                    "product": edge.product,
                    "confidence_score": edge.confidence_score,
                    "source_url": edge.source_url,
                    "source_title": edge.source_title,
                    "evidence_excerpt": edge.evidence_excerpt,
                    "review_status": edge.review_status,
                    "review_note": edge.review_note,
                    "reviewed_at": edge.reviewed_at.isoformat() if edge.reviewed_at else None,
                }
                for edge in edges
            ],
        }
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as handle:
            json.dump(payload, handle, indent=2)
        print(f"Exported {len(edges)} review decision(s) to {path}")
    finally:
        session.close()


def apply_decisions(path):
    with open(path) as handle:
        payload = json.load(handle)

    decisions = payload.get("decisions", [])
    session = SessionLocal()
    counts = {"applied": 0, "missing": 0}
    try:
        for decision in decisions:
            try:
                result = apply_decision(session, decision)
                counts[result] += 1
                session.commit()
            except IntegrityError:
                session.rollback()
                dedupe_edges(session)
                result = apply_decision(session, decision)
                counts[result] += 1
                session.commit()
        print("Applied review decisions:", counts)
    finally:
        session.close()


def apply_decision(session, decision):
    source = session.query(Node).filter(Node.ticker == decision.get("source_ticker")).first()
    target = session.query(Node).filter(Node.ticker == decision.get("target_ticker")).first()
    if not source or not target:
        return "missing"

    edge = (
        session.query(Edge)
        .filter(
            Edge.source_id == source.id,
            Edge.target_id == target.id,
            Edge.dependency_type == decision.get("dependency_type"),
        )
        .first()
    )

    if not edge:
        # A rebuilt database re-discovers the relationship under its original label,
        # while the decision carries the label chosen during review. Adopt that edge
        # only when it is the single unreviewed candidate for the pair and source, so a
        # different reviewed relationship between the same companies is never rewritten.
        candidates = (
            session.query(Edge)
            .filter(
                Edge.source_id == source.id,
                Edge.target_id == target.id,
                Edge.source_url == decision.get("source_url"),
            )
            .order_by(Edge.id.asc())
            .all()
        )
        unreviewed = [candidate for candidate in candidates if (candidate.review_status or "pending") == "pending"]
        if len(unreviewed) == 1:
            edge = unreviewed[0]

    if not edge:
        edge = Edge(source_id=source.id, target_id=target.id, dependency_type=decision["dependency_type"])
        session.add(edge)

    edge.dependency_type = decision["dependency_type"]
    edge.product = decision.get("product")
    edge.confidence_score = decision.get("confidence_score")
    edge.source_url = decision.get("source_url")
    edge.source_title = decision.get("source_title")
    edge.evidence_excerpt = decision.get("evidence_excerpt")
    edge.review_status = decision["review_status"]
    edge.review_note = decision.get("review_note")
    edge.reviewed_at = datetime.now(timezone.utc)
    return "applied"


REVIEW_STATUS_RANK = {"approved": 2, "rejected": 1, "pending": 0}


def duplicate_edge_rank(edge):
    """A reviewed decision always outranks confidence; lower ids win ties."""
    return (
        REVIEW_STATUS_RANK.get(edge.review_status or "pending", 0),
        edge.confidence_score or 0,
        -(edge.id or 0),
    )


def dedupe_edges(session):
    edges = session.query(Edge).order_by(Edge.id.asc()).all()
    by_key = {}
    deleted = 0
    for edge in edges:
        key = (edge.source_id, edge.target_id, edge.dependency_type)
        existing = by_key.get(key)
        if not existing:
            by_key[key] = edge
            continue

        if duplicate_edge_rank(edge) > duplicate_edge_rank(existing):
            keep, remove = edge, existing
            by_key[key] = edge
        else:
            keep, remove = existing, edge

        if not keep.product and remove.product:
            keep.product = remove.product
        if not keep.source_url and remove.source_url:
            keep.source_url = remove.source_url
        if not keep.source_title and remove.source_title:
            keep.source_title = remove.source_title
        if not keep.evidence_excerpt and remove.evidence_excerpt:
            keep.evidence_excerpt = remove.evidence_excerpt
        if not keep.review_note and remove.review_note:
            keep.review_note = remove.review_note
        keep.confidence_score = max(keep.confidence_score or 0, remove.confidence_score or 0)
        session.delete(remove)
        deleted += 1

    if deleted:
        session.flush()
        print(f"Removed {deleted} duplicate edge(s).")


def main():
    parser = argparse.ArgumentParser(description="Export or apply tracked Hephaestus edge review decisions.")
    parser.add_argument("mode", choices=["export", "apply"])
    parser.add_argument("--path", default=DEFAULT_PATH)
    args = parser.parse_args()

    if args.mode == "export":
        export_decisions(args.path)
    else:
        apply_decisions(args.path)


if __name__ == "__main__":
    main()
