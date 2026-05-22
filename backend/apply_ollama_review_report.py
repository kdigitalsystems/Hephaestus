import argparse
import csv
from datetime import datetime, timezone

from database import SessionLocal
from models import Edge

NON_SUPPLY_REVIEW_MARKERS = [
    "acquisition",
    "acquired",
    "acquires",
    "asset purchase",
    "business unit purchase",
    "collaboration",
    "co-commercialization",
    "competitor",
    "competition",
    "equity stake",
    "funding",
    "historical acquisition",
    "investment",
    "joint venture",
    "license agreement",
    "licensing agreement",
    "merger",
    "option deal",
    "ownership",
    "partnership",
    "patent",
    "royalty",
    "shareholder",
    "spin-off",
    "spinoff",
    "transfer of rights",
]


def has_non_supply_review_label(row):
    text = " ".join([
        row.get("relationship_type") or "",
        row.get("product") or "",
        row.get("reason") or "",
    ]).lower()
    return any(marker in text for marker in NON_SUPPLY_REVIEW_MARKERS)


def allowed(row, args):
    action = row["model_action"]
    try:
        confidence = float(row["model_confidence"])
    except (TypeError, ValueError):
        confidence = 0.0

    if action == "approve":
        return confidence >= args.min_approve
    if action == "reverse":
        return confidence >= args.min_reverse
    if action == "reject":
        return confidence >= args.min_reject
    return False


def update_metadata(edge, row):
    edge.dependency_type = (row.get("relationship_type") or edge.dependency_type)[:255]
    edge.product = (row.get("product") or edge.product or "")[:255]
    edge.confidence_score = float(row["model_confidence"])
    edge.review_note = f"Ollama report review: {row.get('reason', '')}"[:1000]
    edge.reviewed_at = datetime.now(timezone.utc)


def apply_row(session, row):
    edge = session.get(Edge, int(row["edge_id"]))
    if not edge or edge.review_status != "pending":
        return "skipped"

    action = "reject" if has_non_supply_review_label(row) else row["model_action"]
    if action == "approve":
        update_metadata(edge, row)
        edge.review_status = "approved"
        return "approved"

    if action == "reject":
        update_metadata(edge, row)
        edge.review_status = "rejected"
        return "rejected"

    if action == "reverse":
        existing = (
            session.query(Edge)
            .filter(
                Edge.source_id == edge.target_id,
                Edge.target_id == edge.source_id,
                Edge.dependency_type == (row.get("relationship_type") or edge.dependency_type),
                Edge.id != edge.id,
            )
            .first()
        )
        if existing:
            update_metadata(existing, row)
            existing.review_status = "approved"
            edge.review_status = "rejected"
            edge.review_note = f"Ollama report review: duplicate reversed edge approved as #{existing.id}. {row.get('reason', '')}"[:1000]
        else:
            edge.source_id, edge.target_id = edge.target_id, edge.source_id
            update_metadata(edge, row)
            edge.review_status = "approved"
        return "reversed"

    return "skipped"


def main():
    parser = argparse.ArgumentParser(description="Apply high-confidence decisions from an Ollama edge review CSV.")
    parser.add_argument("report")
    parser.add_argument("--min-approve", type=float, default=0.75)
    parser.add_argument("--min-reject", type=float, default=0.85)
    parser.add_argument("--min-reverse", type=float, default=0.75)
    args = parser.parse_args()

    counts = {"approved": 0, "rejected": 0, "reversed": 0, "skipped": 0}
    session = SessionLocal()
    try:
        with open(args.report, newline="") as handle:
            for row in csv.DictReader(handle):
                if row.get("applied") == "True":
                    counts["skipped"] += 1
                    continue
                if not allowed(row, args):
                    counts["skipped"] += 1
                    continue
                result = apply_row(session, row)
                counts[result] += 1
        session.commit()
        print("Applied report:", counts)
    finally:
        session.close()


if __name__ == "__main__":
    main()
