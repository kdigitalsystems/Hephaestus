from datetime import datetime, timezone

from audit_data_quality import has_non_supply_label, has_reversed_role_label
from database import SessionLocal
from models import Edge


def cleanup_reviewed_edges():
    session = SessionLocal()
    counts = {"rejected_non_supply": 0, "pending_role_labels": 0}
    try:
        approved_edges = session.query(Edge).filter(Edge.review_status == "approved").all()
        for edge in approved_edges:
            if has_non_supply_label(edge.dependency_type) or has_non_supply_label(edge.product):
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

        session.commit()
        print("Reviewed edge cleanup:", counts)
    finally:
        session.close()


if __name__ == "__main__":
    cleanup_reviewed_edges()
