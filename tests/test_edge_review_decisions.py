"""The tracked data/edge_review_decisions.json must never lose a decision silently."""
import json
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import edge_review_decisions as decisions  # noqa: E402
from models import Base, Edge, Node  # noqa: E402


def decision(source, target, status="approved", label="Components", **extra):
    return {
        "edge_id": extra.pop("edge_id", 1), "source_ticker": source, "target_ticker": target, "source_name": f"{source} Inc.",
        "target_name": f"{target} Inc.", "dependency_type": label, "product": "Parts", "confidence_score": 0.9,
        "revenue_share": None, "source_url": "Manual System Jumpstart", "source_title": "Manual System Jumpstart",
        "evidence_excerpt": "", "review_status": status, "review_note": "Human review (2026-09-29): approved.",
        "reviewed_at": "2026-09-29T08:00:00", **extra,
    }


@pytest.fixture
def database(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'graph.db'}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False)
    monkeypatch.setattr(decisions, "SessionLocal", Session)
    return Session


def seed(Session, *tickers):
    session = Session()
    session.add_all(Node(name=f"{ticker} Inc.", ticker=ticker, sector="Technology") for ticker in tickers)
    session.commit()
    session.close()


def write(path, rows):
    path.write_text(json.dumps({"exported_at": "2026-09-29T08:00:00+00:00", "decisions": rows}, indent=2), encoding="utf-8")


def tracked(path):
    return [(row["source_ticker"], row["target_ticker"], row["review_status"]) for row in json.loads(path.read_text(encoding="utf-8"))["decisions"]]


def test_a_decision_for_a_company_this_database_lacks_survives_apply_then_export(database, tmp_path, capsys):
    """A reviewer's two approved decisions became one: the new runner had no GHOST, apply
    counted it 'missing' and nothing noticed, and the next export rewrote the file from the
    database alone."""
    path = tmp_path / "edge_review_decisions.json"
    write(path, [decision("SUP", "CUS", edge_id=1), decision("GHOST", "CUS", edge_id=2)])
    seed(database, "SUP", "CUS")

    counts = decisions.apply_decisions(str(path))
    out = capsys.readouterr().out
    decisions.export_decisions(str(path))

    assert counts == {"applied": 1, "missing": 1}
    assert "WARNING: 1 review decision(s) were not applied" in out and "GHOST->CUS" in out
    assert ("GHOST", "CUS", "approved") in tracked(path), "the decision it could not apply must stay in the file"
    assert len(tracked(path)) == 2
    assert "kept 1" in capsys.readouterr().out
    # Once the company arrives the decision applies like any other.
    seed(database, "GHOST")
    assert decisions.apply_decisions(str(path)) == {"applied": 2, "missing": 0}
    decisions.export_decisions(str(path))
    assert sorted(tracked(path)) == [("GHOST", "CUS", "approved"), ("SUP", "CUS", "approved")]


def test_the_warning_is_also_a_github_annotation(database, tmp_path, capsys, monkeypatch):
    path = tmp_path / "edge_review_decisions.json"
    write(path, [decision("GHOST", "CUS")])
    seed(database, "CUS")
    monkeypatch.setenv("GITHUB_ACTIONS", "true")

    decisions.apply_decisions(str(path))

    assert "::warning title=Review decisions not applied::1 review decision(s)" in capsys.readouterr().out


def test_exporting_from_a_fresh_database_does_not_wipe_the_file(database, tmp_path):
    """rebuild_db.sh exported before applying: a database with no edges overwrote every decision."""
    path = tmp_path / "edge_review_decisions.json"
    rows = [decision("SUP", "CUS", edge_id=1), decision("SUP", "CUS", "rejected", label="Marketing", edge_id=2)]
    write(path, rows)
    seed(database, "SUP", "CUS")

    decisions.export_decisions(str(path))

    assert json.loads(path.read_text(encoding="utf-8"))["decisions"] == rows


def test_a_decision_for_a_pair_the_database_holds_is_the_databases_to_change(database, tmp_path):
    path = tmp_path / "edge_review_decisions.json"
    write(path, [decision("SUP", "CUS", label="Components", edge_id=1), decision("SUP", "BIZ", label="Parts", edge_id=2),
                 decision("GHOST", "CUS", edge_id=3)])
    seed(database, "SUP", "CUS", "BIZ")
    decisions.apply_decisions(str(path))

    session = database()
    relabelled = session.query(Edge).filter(Edge.dependency_type == "Components").one()
    relabelled.dependency_type = "Power Components"      # relabelled during review
    session.query(Edge).filter(Edge.dependency_type == "Parts").one().review_status = "pending"  # `review_edges.py pend`
    session.commit()
    session.close()

    decisions.export_decisions(str(path))

    rows = json.loads(path.read_text(encoding="utf-8"))["decisions"]
    assert [(row["source_ticker"], row["dependency_type"]) for row in rows] == [("SUP", "Power Components"), ("GHOST", "Components")]


def test_an_unreadable_file_is_never_overwritten(database, tmp_path):
    path = tmp_path / "edge_review_decisions.json"
    path.write_text('{"decisions": [{"source_ticker": "SUP"', encoding="utf-8")  # truncated by a crash
    seed(database, "SUP", "CUS")

    with pytest.raises(SystemExit, match="refusing to overwrite"):
        decisions.export_decisions(str(path))

    assert path.read_text(encoding="utf-8") == '{"decisions": [{"source_ticker": "SUP"'
    path.write_text('{"decisions": "none"}', encoding="utf-8")
    with pytest.raises(SystemExit, match="no list of decisions"):
        decisions.export_decisions(str(path))


def test_the_tracked_file_is_written_atomically(database, tmp_path, monkeypatch):
    path = tmp_path / "edge_review_decisions.json"
    rows = [decision("SUP", "CUS")]
    write(path, rows)
    seed(database, "SUP", "CUS")
    before = path.read_bytes()

    def explode(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(decisions.json, "dump", explode)
    with pytest.raises(OSError):
        decisions.export_decisions(str(path))

    # The original is intact (it used to be truncated in place) and no temp file is left behind.
    assert path.read_bytes() == before
    assert sorted(item.name for item in tmp_path.iterdir()) == ["edge_review_decisions.json", "graph.db"]


def test_a_first_export_creates_the_file(database, tmp_path):
    path = tmp_path / "nested" / "edge_review_decisions.json"
    seed(database, "SUP", "CUS")
    session = database()
    sup, cus = session.query(Node).filter_by(ticker="SUP").one(), session.query(Node).filter_by(ticker="CUS").one()
    session.add(Edge(source_id=sup.id, target_id=cus.id, dependency_type="Components", review_status="approved", review_note="Human review (x): ok."))
    session.commit()
    session.close()

    decisions.export_decisions(str(path))

    assert tracked(path) == [("SUP", "CUS", "approved")]
