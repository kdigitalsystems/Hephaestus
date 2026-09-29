"""The review page's decisions reach the database as human verdicts, exactly once."""
import json
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from models import Base, Edge, Node  # noqa: E402

HELD = "Ollama consensus review hold: "


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    nodes = {ticker: Node(name=name, ticker=ticker, market_cap=1e10) for ticker, name in (
        ("GLW", "Corning Incorporated"), ("AAPL", "Apple Inc."), ("SNX", "TD SYNNEX Corporation"),
        ("VALE", "Vale S.A."), ("BHP", "BHP Group Limited"), ("MSI", "Motorola Solutions, Inc."), ("URI", "United Rentals, Inc."))}
    session.add_all(nodes.values())
    session.commit()
    return session, nodes


def edge(session, nodes, source, target, label, note=HELD + "held.", status="pending", evidence="x" * 30):
    row = Edge(source_id=nodes[source].id, target_id=nodes[target].id, dependency_type=label, review_status=status,
               review_note=note, evidence_excerpt=evidence, source_url="AI Multi-Source Research")
    session.add(row)
    session.commit()
    return row


def write(tmp_path, name, decisions, reviewed_on="2026-09-29"):
    path = tmp_path / name
    path.write_text(json.dumps({"reviewed_on": reviewed_on, "decisions": decisions}), encoding="utf-8")
    return path


def decision(row, source, target, action, note=""):
    return {"edge_id": row.id, "source_ticker": source, "target_ticker": target, "type": row.dependency_type, "action": action, "note": note}


def test_decisions_apply_as_human_verdicts_once(db, tmp_path):
    from apply_human_review import apply_human_review
    from audit_data_quality import needs_human_confirmation

    session, nodes = db
    keep = edge(session, nodes, "SNX", "AAPL", "Distribution")
    junk = edge(session, nodes, "VALE", "BHP", "Iron Ore")
    flip = edge(session, nodes, "AAPL", "GLW", "Cover glass")
    write(tmp_path, "review-20260929-100000.json", [
        decision(keep, "SNX", "AAPL", "approve", "Checked the 10-K."),
        decision(junk, "VALE", "BHP", "reject"),
        decision(flip, "AAPL", "GLW", "reverse"),
    ])

    counts = apply_human_review(tmp_path, session=session)

    assert counts == {"approved": 1, "rejected": 1, "reversed": 1}
    assert keep.review_status == "approved" and keep.review_note == "Human review (2026-09-29): approved. Checked the 10-K."
    assert junk.review_status == "rejected"
    assert (flip.source_id, flip.target_id, flip.review_status) == (nodes["GLW"].id, nodes["AAPL"].id, "approved")
    # Human verdicts: every automated rule leaves them alone.
    assert not any(needs_human_confirmation(row) for row in (keep, junk, flip))
    # A second run changes nothing, and in particular does not flip the edge back.
    assert apply_human_review(tmp_path, session=session) == {"unchanged": 3}
    assert (flip.source_id, flip.target_id) == (nodes["GLW"].id, nodes["AAPL"].id)


def test_reversing_into_an_existing_edge_keeps_one(db, tmp_path):
    from apply_human_review import apply_human_review

    session, nodes = db
    backwards = edge(session, nodes, "URI", "MSI", "Radios")
    existing = edge(session, nodes, "MSI", "URI", "Radios")
    write(tmp_path, "review-a.json", [decision(backwards, "URI", "MSI", "reverse")])

    assert apply_human_review(tmp_path, session=session) == {"reversed_into_existing": 1}
    assert existing.review_status == "approved" and f"#{backwards.id}" in existing.review_note
    assert backwards.review_status == "rejected"


def test_a_later_session_overrides_an_earlier_one_and_ids_can_change(db, tmp_path):
    from apply_human_review import apply_human_review

    session, nodes = db
    row = edge(session, nodes, "SNX", "AAPL", "Distribution")
    write(tmp_path, "review-20260929-100000.json", [decision(row, "SNX", "AAPL", "approve")])
    later = decision(row, "SNX", "AAPL", "reject")
    later["edge_id"] = 99999  # the database was rebuilt; companies and label still find it
    write(tmp_path, "review-20260930-100000.json", [later], reviewed_on="2026-09-30")

    apply_human_review(tmp_path, session=session)

    assert row.review_status == "rejected" and "2026-09-30" in row.review_note


def test_invalid_files_are_refused_before_anything_changes(db, tmp_path):
    from apply_human_review import apply_human_review, check_files

    session, nodes = db
    row = edge(session, nodes, "SNX", "AAPL", "Distribution")
    write(tmp_path, "review-a.json", [decision(row, "SNX", "AAPL", "approve")])
    write(tmp_path, "review-b.json", [{"edge_id": "7", "action": "publish"}])

    problems = check_files(tmp_path)
    assert any('"edge_id" must be an integer' in problem for problem in problems)
    assert any('"action" must be one of' in problem for problem in problems)
    with pytest.raises(SystemExit):
        apply_human_review(tmp_path, session=session)
    assert row.review_status == "pending"


def test_the_queue_lists_why_each_link_waits_and_what_the_evidence_suggests(db):
    from review_queue import build_review_queue

    session, nodes = db
    backwards = edge(session, nodes, "AAPL", "GLW", "Cover glass",
                     note=HELD + 'the excerpt says the supply runs the other way ("...").',
                     evidence="Corning is one of the main suppliers to Apple Inc. for iPhone cover glass.")
    fresh = edge(session, nodes, "SNX", "AAPL", "Distribution", note=None,
                 evidence="TD SYNNEX distributes Apple products to resellers across North America.")
    published_mirror = edge(session, nodes, "GLW", "AAPL", "Glass", status="approved", note="Human review (2026-09-01): approved.",
                            evidence="Corning supplies cover glass to Apple.")

    queue = build_review_queue(session)
    items = {item["edge_id"]: item for item in queue["items"]}

    assert queue["pending_count"] == 2 and not queue["truncated"]
    assert items[backwards.id]["category"] == "backwards_evidence"
    assert items[backwards.id]["suggestion"]["action"] == "reverse"
    assert "GLW supplies AAPL" in items[backwards.id]["suggestion"]["reason"]
    assert items[backwards.id]["mirror_edge_ids"] == [published_mirror.id]
    assert items[fresh.id]["category"] == "awaiting_models"
