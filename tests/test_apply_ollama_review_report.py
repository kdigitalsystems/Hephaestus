"""Applying a saved review report must not be a way around the nightly reviewer's rules."""
import csv
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import apply_ollama_review_report as report  # noqa: E402
from models import Base, Edge, Node  # noqa: E402

FIELDS = ["edge_id", "source", "target", "model_action", "consensus_votes", "supplier_side", "customer_side", "model_confidence",
          "applied", "result", "relationship_type", "product", "reason", "model_reviews"]


@pytest.fixture
def graph(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'graph.db'}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False)
    monkeypatch.setattr(report, "SessionLocal", Session)
    session = Session()
    nodes = {t: Node(name=f"{t} Inc.", ticker=t, sector="Technology") for t in ("SUP", "CUS", "OTH")}
    session.add_all(nodes.values())
    session.flush()

    def edge(source, target, label, status="pending"):
        row = Edge(source_id=nodes[source].id, target_id=nodes[target].id, dependency_type=label, review_status=status)
        session.add(row)
        session.flush()
        return row

    rows = {
        "taken": edge("SUP", "CUS", "Cables", "approved"),
        "rename": edge("SUP", "CUS", "Components"),
        "plain": edge("SUP", "OTH", "Parts"),
        "backwards": edge("OTH", "SUP", "Power"),
    }
    session.commit()
    ids = {name: row.id for name, row in rows.items()}
    session.close()
    return Session, ids, tmp_path


def csv_row(edge_id, action, label="", confidence=0.95):
    return {"edge_id": edge_id, "source": "S", "target": "T", "model_action": action, "consensus_votes": f"{action}:3",
            "supplier_side": "source", "customer_side": "target", "model_confidence": confidence, "applied": "False",
            "result": "held", "relationship_type": label, "product": "x", "reason": "The excerpt supports it.", "model_reviews": "[]"}


def run(monkeypatch, tmp_path, rows, *flags):
    path = tmp_path / "report.csv"
    with open(path, "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    monkeypatch.setattr(sys, "argv", ["apply_ollama_review_report.py", str(path), *flags])
    report.main()


def status_of(Session, edge_id):
    session = Session()
    try:
        return session.get(Edge, edge_id).review_status
    finally:
        session.close()


def test_one_colliding_verdict_does_not_lose_the_rest_of_the_report(graph, monkeypatch, capsys):
    Session, ids, tmp_path = graph
    run(monkeypatch, tmp_path, [
        csv_row(ids["rename"], "approve", label="Cables"),   # renames into a label another edge already uses
        csv_row(ids["plain"], "approve", label="Parts"),
    ])

    out = capsys.readouterr().out
    assert status_of(Session, ids["rename"]) == "pending", "the colliding verdict is left as it was"
    assert status_of(Session, ids["plain"]) == "approved", "it used to be lost with the failed final commit"
    assert f"Edge #{ids['rename']} left as it was" in out and "'conflicts': 1" in out


def test_reversals_are_held_for_a_human_unless_the_run_opts_in(graph, monkeypatch, capsys):
    Session, ids, tmp_path = graph
    run(monkeypatch, tmp_path, [csv_row(ids["backwards"], "reverse", label="Power", confidence=0.80)])

    assert status_of(Session, ids["backwards"]) == "pending"
    assert "'reversed': 0" in capsys.readouterr().out

    run(monkeypatch, tmp_path, [csv_row(ids["backwards"], "reverse", label="Power", confidence=0.80)], "--apply-reversals")
    assert "'reversed': 1" in capsys.readouterr().out
    session = Session()
    flipped = session.get(Edge, ids["backwards"])
    assert (flipped.source_node.ticker, flipped.target_node.ticker) == ("SUP", "OTH") and flipped.review_status == "approved"
    session.close()
