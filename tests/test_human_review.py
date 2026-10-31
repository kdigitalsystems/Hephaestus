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


def test_the_queue_puts_the_largest_companies_first(db):
    from review_queue import build_review_queue

    session, nodes = db
    nodes["AAPL"].market_cap, nodes["GLW"].market_cap = 3.5e12, 4e10
    nodes["VALE"].market_cap, nodes["BHP"].market_cap = 5e10, 1.4e11
    nodes["MSI"].market_cap, nodes["URI"].market_cap = None, 6e10
    session.commit()
    small = edge(session, nodes, "VALE", "BHP", "Iron Ore")
    unpriced = edge(session, nodes, "MSI", "URI", "Radios")
    apple = edge(session, nodes, "GLW", "AAPL", "Cover glass")

    queue = build_review_queue(session)
    # The larger endpoint decides: Apple's link first, then BHP's, then United Rentals'.
    assert [item["edge_id"] for item in queue["items"]] == [apple.id, small.id, unpriced.id]
    first = queue["items"][0]
    assert (first["source_market_cap"], first["target_market_cap"]) == (4e10, 3.5e12)
    assert queue["items"][2]["source_market_cap"] is None

    # A truncated queue keeps the largest links, not the oldest.
    truncated = build_review_queue(session, limit=1)
    assert truncated["truncated"] and [item["edge_id"] for item in truncated["items"]] == [apple.id]


# --- a person's approval can correct the label (the review models relabel real supply links) ---

CURATED_EXCERPT = (
    "Under this definitive agreement, OpenAI will work with AMD as a core strategic compute partner to drive "
    "large-scale deployments of AMD technology starting with the AMD Instinct MI450 series."
)


def relabelled_edge(session, nodes, source, target, label="strategic partnership", status="rejected"):
    row = Edge(source_id=nodes[source].id, target_id=nodes[target].id, dependency_type=label, review_status=status,
               review_note="Ollama consensus review: Consensus 2/3 for reject (votes reject:2, approve:1).",
               evidence_excerpt=CURATED_EXCERPT, source_url="https://www.sec.gov/Archives/edgar/data/2488/x.htm",
               source_title="AMD and OpenAI Announce Strategic Partnership (Form 8-K Exhibit 99.1)", product="AMD Instinct GPUs")
    session.add(row)
    session.commit()
    return row


def with_type(row, source, target, set_type, note="Checked the source page."):
    return {**decision(row, source, target, "approve", note), "set_type": set_type}


def test_an_approval_can_correct_the_label_and_a_rerun_changes_nothing(db, tmp_path):
    from apply_human_review import apply_human_review

    session, nodes = db
    row = relabelled_edge(session, nodes, "SNX", "AAPL")
    write(tmp_path, "review-20261023-100000.json", [with_type(row, "SNX", "AAPL", "Supply Relationship")])

    assert apply_human_review(tmp_path, session=session) == {"approved": 1}
    assert (row.dependency_type, row.review_status) == ("Supply Relationship", "approved")
    assert row.review_note == "Human review (2026-09-29): approved. Checked the source page."
    # Re-running finds the edge under its NEW label and changes nothing (a missing edge would count as "missing").
    assert apply_human_review(tmp_path, session=session) == {"unchanged": 1}
    assert row.dependency_type == "Supply Relationship"


def test_without_the_correction_cleanup_rejects_the_link_again_even_though_a_person_approved_it(db, tmp_path, monkeypatch):
    """The reason set_type exists: cleanup judges the label, not who decided."""
    import cleanup_reviewed_edges
    from apply_human_review import apply_human_review

    session, nodes = db
    plain = relabelled_edge(session, nodes, "SNX", "AAPL")
    fixed = relabelled_edge(session, nodes, "VALE", "BHP")
    write(tmp_path, "review-20261023-100000.json", [
        decision(plain, "SNX", "AAPL", "approve"),
        with_type(fixed, "VALE", "BHP", "Supply Relationship"),
    ])
    apply_human_review(tmp_path, session=session)
    assert plain.review_status == fixed.review_status == "approved"

    engine = session.get_bind()
    monkeypatch.setattr(cleanup_reviewed_edges, "SessionLocal", sessionmaker(bind=engine, autoflush=False))
    cleanup_reviewed_edges.cleanup_reviewed_edges()
    session.expire_all()
    assert plain.review_status == "rejected" and "not an operational supply-chain relationship" in plain.review_note
    assert (fixed.review_status, fixed.dependency_type) == ("approved", "Supply Relationship")
    assert fixed.review_note.startswith("Human review")


def test_a_relabel_into_a_label_the_pair_already_has_approves_that_edge_and_retires_the_copy(db, tmp_path):
    """The real 2026-10-24 state: the panel had relabelled the curated edge 'strategic partnership' and rejected it,
    and the seeder then created a fresh 'Supply Relationship' edge, which the panel rejected too (or left pending).
    The person's verdict goes to the edge that already carries the right label; the relabelled copy is retired."""
    from apply_human_review import apply_human_review

    session, nodes = db
    row = relabelled_edge(session, nodes, "SNX", "AAPL")
    fresh_rejected = relabelled_edge(session, nodes, "SNX", "AAPL", label="Supply Relationship", status="rejected")
    other = relabelled_edge(session, nodes, "VALE", "BHP")
    fresh_pending = relabelled_edge(session, nodes, "VALE", "BHP", label="Supply Relationship", status="pending")
    write(tmp_path, "review-20261023-100000.json", [
        with_type(row, "SNX", "AAPL", "Supply Relationship"), with_type(other, "VALE", "BHP", "Supply Relationship"),
    ])

    assert apply_human_review(tmp_path, session=session) == {"relabelled_into_existing": 2}

    for kept, retired in ((fresh_rejected, row), (fresh_pending, other)):
        assert (kept.review_status, kept.dependency_type) == ("approved", "Supply Relationship")
        assert kept.review_note.startswith("Human review (2026-09-29): approved (the relabelled copy of #")
        assert (retired.review_status, retired.dependency_type) == ("rejected", "strategic partnership")
        assert f"relabelled; kept as #{kept.id}" in retired.review_note
    # A second night finds both again by id and changes nothing.
    assert apply_human_review(tmp_path, session=session) == {"unchanged": 2}


def test_the_merged_pair_survives_cleanup_and_publishes_one_edge(db, tmp_path, monkeypatch):
    import cleanup_reviewed_edges
    from apply_human_review import apply_human_review

    session, nodes = db
    row = relabelled_edge(session, nodes, "SNX", "AAPL")
    fresh = relabelled_edge(session, nodes, "SNX", "AAPL", label="Supply Relationship", status="rejected")
    write(tmp_path, "review-20261023-100000.json", [with_type(row, "SNX", "AAPL", "Supply Relationship")])
    apply_human_review(tmp_path, session=session)

    monkeypatch.setattr(cleanup_reviewed_edges, "SessionLocal", sessionmaker(bind=session.get_bind(), autoflush=False))
    cleanup_reviewed_edges.cleanup_reviewed_edges()
    cleanup_reviewed_edges.cleanup_reviewed_edges()
    session.expire_all()
    assert [(e.id == fresh.id, e.review_status) for e in session.query(Edge).order_by(Edge.id)] == [(False, "rejected"), (True, "approved")]


def test_set_type_equal_to_the_current_label_is_a_plain_approval(db, tmp_path):
    from apply_human_review import apply_human_review

    session, nodes = db
    row = relabelled_edge(session, nodes, "SNX", "AAPL", label="Supply Relationship", status="pending")
    write(tmp_path, "review-20261023-100000.json", [with_type(row, "SNX", "AAPL", "Supply Relationship")])
    assert apply_human_review(tmp_path, session=session) == {"approved": 1}
    assert row.dependency_type == "Supply Relationship"


def test_the_edge_is_found_by_its_companies_after_a_rebuild_changed_the_ids(db, tmp_path):
    from apply_human_review import apply_human_review

    session, nodes = db
    row = relabelled_edge(session, nodes, "SNX", "AAPL")
    stale = {**with_type(row, "SNX", "AAPL", "Supply Relationship"), "edge_id": 99999}
    write(tmp_path, "review-20261023-100000.json", [stale])
    assert apply_human_review(tmp_path, session=session) == {"approved": 1}
    assert apply_human_review(tmp_path, session=session) == {"unchanged": 1}  # now found under the new label
    assert row.dependency_type == "Supply Relationship"


@pytest.mark.parametrize(
    ("overrides", "fragment"),
    [
        ({"action": "reject"}, "can only be used with"),
        ({"action": "reverse"}, "can only be used with"),
        ({"set_type": ""}, "1-80 characters"),
        ({"set_type": " Supply Relationship"}, "surrounding spaces"),
        ({"set_type": "x" * 81}, "1-80 characters"),
        ({"set_type": 7}, "1-80 characters"),
        ({"set_type": "strategic partnership"}, "not a supply-chain label"),
        ({"set_type": "Collaboration"}, "not a supply-chain label"),
        ({"set_type": "Supplier"}, "not a supply-chain label"),
        ({"set_type": "unknown"}, "not a supply-chain label"),
        ({"set_type": "n/a"}, "not a supply-chain label"),
        ({"set_type": "None"}, "not a supply-chain label"),
    ],
)
def test_set_type_is_validated_the_way_cleanup_will_judge_it(overrides, fragment):
    from apply_human_review import validate_payload

    entry = {"edge_id": 1, "source_ticker": "AMD", "target_ticker": "OPENAI", "type": "collaboration",
             "action": "approve", "set_type": "Supply Relationship", **overrides}
    errors = validate_payload({"decisions": [entry]})
    assert any(fragment in error for error in errors), errors
    assert validate_payload({"decisions": [{**entry, "action": "approve", "set_type": "Supply Relationship"}]}) == []


def test_ordinary_supply_labels_pass_validation():
    from apply_human_review import validate_payload

    for label in ("Supply Relationship", "Cloud Infrastructure Provider", "Content Provider", "Contract Manufacturing"):
        entry = {"edge_id": 1, "source_ticker": "A", "target_ticker": "B", "type": "x", "action": "approve", "set_type": label}
        assert validate_payload({"decisions": [entry]}) == [], label


def test_a_relabel_alone_is_reported_even_when_the_verdict_was_already_recorded(db, tmp_path):
    from apply_human_review import apply_human_review

    session, nodes = db
    row = relabelled_edge(session, nodes, "SNX", "AAPL", status="approved")
    row.review_note = "Human review (2026-09-29): approved. Checked the source page."
    session.commit()
    write(tmp_path, "review-20261023-100000.json", [with_type(row, "SNX", "AAPL", "Supply Relationship")])
    assert apply_human_review(tmp_path, session=session) == {"approved": 1}
    assert row.dependency_type == "Supply Relationship"
