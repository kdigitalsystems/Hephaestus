"""Apply a person's review decisions from data/human_review/*.json.

docs/review.html writes one file per review session and opens it as a pull request.
Each decision names an edge and one of approve / reject / reverse. They are applied
as human verdicts, which every automated rule leaves alone, and then persisted in
data/edge_review_decisions.json by the normal decisions export.

Re-running is safe: a decision that is already in effect changes nothing, and a
reversal is applied once (an edge already running the other way is left as it is).
Files are applied in name order, so a later session overrides an earlier one.
"""
import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HUMAN_REVIEW_DIR = ROOT / "data" / "human_review"
ACTIONS = ("approve", "reject", "reverse")
HUMAN_NOTE_PREFIX = "Human review"
MAX_NOTE_LENGTH = 500
REVIEWED_ON = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def validate_payload(payload):
    """Problems with one decisions file, as readable strings (empty when valid)."""
    errors = []
    if not isinstance(payload, dict) or not isinstance(payload.get("decisions"), list):
        return ["the file must be an object with a \"decisions\" list"]
    reviewed_on = payload.get("reviewed_on")
    if reviewed_on is not None and not (isinstance(reviewed_on, str) and REVIEWED_ON.match(reviewed_on)):
        errors.append("\"reviewed_on\" must be a YYYY-MM-DD date")
    for index, decision in enumerate(payload["decisions"]):
        where = f"decision {index + 1}"
        if not isinstance(decision, dict):
            errors.append(f"{where} must be an object")
            continue
        if not isinstance(decision.get("edge_id"), int):
            errors.append(f"{where}: \"edge_id\" must be an integer")
        if decision.get("action") not in ACTIONS:
            errors.append(f"{where}: \"action\" must be one of {', '.join(ACTIONS)}")
        for field in ("source_ticker", "target_ticker", "type"):
            if not isinstance(decision.get(field), str) or not decision.get(field):
                errors.append(f"{where}: \"{field}\" must be a non-empty string")
        note = decision.get("note", "")
        if not isinstance(note, str) or len(note) > MAX_NOTE_LENGTH:
            errors.append(f"{where}: \"note\" must be text of at most {MAX_NOTE_LENGTH} characters")
    return errors


def decision_files(directory=HUMAN_REVIEW_DIR):
    return sorted(Path(directory).glob("*.json")) if Path(directory).is_dir() else []


def check_files(directory=HUMAN_REVIEW_DIR):
    problems = []
    for path in decision_files(directory):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            problems.append(f"{path.name}: not valid JSON ({exc})")
            continue
        problems.extend(f"{path.name}: {error}" for error in validate_payload(payload))
    return problems


def runs(edge, source_ticker, target_ticker, dependency_type):
    return (
        edge is not None
        and edge.source_node is not None
        and edge.target_node is not None
        and edge.source_node.ticker == source_ticker
        and edge.target_node.ticker == target_ticker
        and edge.dependency_type == dependency_type
    )


def find_edge(session, decision):
    """The decision's edge, found by id first and by its companies and label otherwise.

    Ids survive normal runs but not a database rebuild, so the companies and label are
    recorded too. Returns (edge, "original" | "reversed") or (None, None).
    """
    from models import Edge, Node

    source, target, label = decision["source_ticker"], decision["target_ticker"], decision["type"]
    edge = session.get(Edge, decision["edge_id"])
    if runs(edge, source, target, label):
        return edge, "original"
    if runs(edge, target, source, label):
        return edge, "reversed"
    for first, second, orientation in ((source, target, "original"), (target, source, "reversed")):
        match = (
            session.query(Edge)
            .join(Node, Edge.source_id == Node.id)
            .filter(Node.ticker == first, Edge.dependency_type == label)
            .filter(Edge.target_node.has(Node.ticker == second))
            .first()
        )
        if match is not None:
            return match, orientation
    return None, None


def human_note(verb, reviewed_on, note):
    """"Human review (2026-09-29): approved." - never a model or automated prefix."""
    detail = f". {note.strip()}" if note and note.strip() else "."
    return f"{HUMAN_NOTE_PREFIX} ({reviewed_on}): {verb}{detail}"


def set_verdict(edge, status, note, now):
    if edge.review_status == status and str(edge.review_note or "") == note:
        return False
    edge.review_status = status
    edge.review_note = note[:1000]
    edge.reviewed_at = now
    return True


def apply_decision(session, decision, reviewed_on, now):
    from models import Edge

    edge, orientation = find_edge(session, decision)
    if edge is None:
        return "missing"
    action = decision["action"]
    note = decision.get("note", "")
    if action == "approve":
        if orientation == "reversed":
            return "skipped_direction_changed"
        return "approved" if set_verdict(edge, "approved", human_note("approved", reviewed_on, note), now) else "unchanged"
    if action == "reject":
        if orientation == "reversed":
            return "skipped_direction_changed"
        return "rejected" if set_verdict(edge, "rejected", human_note("rejected", reviewed_on, note), now) else "unchanged"

    # reverse
    if orientation == "reversed":
        return "unchanged"
    mirror = (
        session.query(Edge)
        .filter(
            Edge.source_id == edge.target_id,
            Edge.target_id == edge.source_id,
            Edge.dependency_type == edge.dependency_type,
            Edge.id != edge.id,
        )
        .first()
    )
    if mirror is not None:
        # The reversed edge already exists under the same label: approve it and retire
        # this copy, instead of breaking the one-edge-per-label constraint.
        set_verdict(mirror, "approved", human_note(f"approved as the reversal of #{edge.id}", reviewed_on, note), now)
        set_verdict(edge, "rejected", human_note(f"reversed; kept as #{mirror.id}", reviewed_on, note), now)
        return "reversed_into_existing"
    edge.source_id, edge.target_id = edge.target_id, edge.source_id
    set_verdict(edge, "approved", human_note("reversed and approved", reviewed_on, note), now)
    return "reversed"


def apply_human_review(directory=HUMAN_REVIEW_DIR, session=None, now=None):
    from database import SessionLocal

    problems = check_files(directory)
    if problems:
        raise SystemExit("Human review files are invalid:\n  " + "\n  ".join(problems))
    own_session = session is None
    session = session or SessionLocal()
    now = now or datetime.now(timezone.utc)
    counts = {}
    try:
        for path in decision_files(directory):
            payload = json.loads(path.read_text(encoding="utf-8"))
            reviewed_on = payload.get("reviewed_on") or now.date().isoformat()
            for decision in payload["decisions"]:
                result = apply_decision(session, decision, reviewed_on, now)
                counts[result] = counts.get(result, 0) + 1
                session.flush()
            session.commit()
        print("Applied human review decisions:", counts or "none")
        return counts
    finally:
        if own_session:
            session.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="Only validate the decision files.")
    parser.add_argument("--dir", default=str(HUMAN_REVIEW_DIR))
    args = parser.parse_args()
    if args.check:
        problems = check_files(args.dir)
        for problem in problems:
            print(problem, file=sys.stderr)
        count = len(decision_files(args.dir))
        print(f"Human review files: {count} checked, {len(problems)} problem(s).")
        raise SystemExit(1 if problems else 0)
    apply_human_review(args.dir)


if __name__ == "__main__":
    main()
