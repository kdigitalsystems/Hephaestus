import json
import sys
import tempfile
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import edge_review_decisions
from auto_discover_edges import upsert_pending_edge
from models import Base, Edge, Node


def memory_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def test_auto_discovery_upsert_keeps_one_edge_for_duplicate_dependency():
    Session = memory_session()
    session = Session()
    source = Node(name="Eli Lilly", ticker="LLY")
    target = Node(name="Pfizer", ticker="PFE")
    session.add_all([source, target])
    session.commit()

    dependency = {
        "dependency_type": "Raw Materials",
        "product": "Abemaciclib",
        "confidence_score": 0.8,
        "evidence_excerpt": "test evidence",
    }

    _, first_created = upsert_pending_edge(session, source, target, dependency)
    _, second_created = upsert_pending_edge(session, source, target, dependency)
    session.commit()

    assert first_created is True
    assert second_created is False
    assert session.query(Edge).count() == 1


def test_review_decision_apply_updates_existing_unique_edge():
    Session = memory_session()
    original_session_local = edge_review_decisions.SessionLocal
    edge_review_decisions.SessionLocal = Session
    try:
        session = Session()
        source = Node(name="TSM", ticker="TSM")
        target = Node(name="AMD", ticker="AMD")
        session.add_all([source, target])
        session.commit()
        session.add(
            Edge(
                source_id=source.id,
                target_id=target.id,
                dependency_type="Advanced Silicon Fabrication",
                source_url="Manual System Jumpstart",
                review_status="approved",
            )
        )
        session.commit()

        payload = {
            "decisions": [
                {
                    "source_ticker": "TSM",
                    "target_ticker": "AMD",
                    "dependency_type": "Advanced Silicon Fabrication",
                    "product": "Advanced process node chip fabrication",
                    "confidence_score": 1.0,
                    "source_url": "Manual System Jumpstart",
                    "source_title": "Manual System Jumpstart",
                    "evidence_excerpt": None,
                    "review_status": "approved",
                    "review_note": "Curated seed relationship",
                }
            ]
        }
        path = Path(tempfile.mkstemp(suffix=".json")[1])
        path.write_text(json.dumps(payload))

        edge_review_decisions.apply_decisions(str(path))

        session = Session()
        edges = session.query(Edge).all()
        assert len(edges) == 1
        assert edges[0].product == "Advanced process node chip fabrication"
    finally:
        edge_review_decisions.SessionLocal = original_session_local
