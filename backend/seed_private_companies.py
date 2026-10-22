"""Write the private-company registry and its curated supply edges into the graph.

Reads data/private_companies.json and data/private_seed_edges.json and makes the database
match: a node per registry company, a pending edge per seed edge. Safe to run every night:

- a node is created, or updated only in the fields that changed, so `last_updated` (which
  the discovery step reads as "the registry changed") stays put on a run that changes nothing;
  market fields are never touched;
- an entry that is already a public company (same id, name or alias) is refused, and said so;
- an entry with `listed_as` set (the company went public) hands its edges to that public
  node and the private node is deleted;
- seed edges are created as pending with their real source page, so the review panel judges
  the verbatim excerpt like any other edge; existing edges are left alone.

It must run after the public universe is seeded and before update_metrics, discovery and
`edge_review_decisions.py apply`: a decision about a private company needs its node.
Empty files are a no-op. A registry that does not validate stops the run before it touches
anything (CI validates the same file, so that means a real inconsistency); a seed edge that
cannot be used is skipped and reported, and the rest still run.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import or_

from database import SessionLocal
from edge_review_decisions import DEFAULT_PATH as DECISIONS_PATH
from edge_review_decisions import REVIEW_STATUS_RANK, decision_key, duplicate_edge_rank, load_decisions, parse_reviewed_at
from evidence_quality import registered_company_names
from export import write_json_atomic
from models import Edge, Node
from private_companies import (
    PRIVATE_ENTITY_TYPE,
    REGISTRY_PATH,
    SEED_EDGES_PATH,
    PublicIndex,
    RegistryError,
    build_metadata,
    edge_entry_problems,
    edge_label,
    edge_node_problems,
    is_private,
    load_registry,
    load_seed_edges,
    metadata_text,
    parse_metadata,
)

# The same confidence discovery gives a deterministic, quoted-from-the-filing edge.
SEED_EDGE_CONFIDENCE = 0.9


@dataclass
class SeedReport:
    created: list = field(default_factory=list)
    updated: dict = field(default_factory=dict)
    unchanged: int = 0
    refused: dict = field(default_factory=dict)
    graduated: list = field(default_factory=list)
    graduating_later: list = field(default_factory=list)
    orphans: list = field(default_factory=list)
    pruned: list = field(default_factory=list)
    edges_created: list = field(default_factory=list)
    edges_existing: int = 0
    edges_skipped: list = field(default_factory=list)
    retargets: list = field(default_factory=list)  # (private id, public ticker, public name) to carry into the decisions file


def warn(title, message):
    print(f"  [!] {title}: {message}")
    if os.environ.get("GITHUB_ACTIONS"):
        print(f"::warning title={title}::{message}")


def public_index(session):
    rows = session.query(Node.ticker, Node.name).filter(Node.ticker.is_not(None), Node.entity_type != PRIVATE_ENTITY_TYPE).all()
    return PublicIndex(rows)


# --- Companies -----------------------------------------------------------------------------

def desired_fields(company):
    return {
        "name": company["name"],
        "entity_type": PRIVATE_ENTITY_TYPE,
        "sector": company["sector"],
        "industry": company["industry"],
        "business_summary": company["description"],
    }


def upsert_company(session, company, node, report):
    """Create the node or update the fields that differ; a run that changes nothing writes nothing."""
    desired = desired_fields(company)
    if node is None:
        session.add(Node(ticker=company["id"], metadata_json=metadata_text(company), **desired))
        report.created.append(company["id"])
        return
    changed = [name for name, value in desired.items() if getattr(node, name) != value]
    for name in changed:
        setattr(node, name, desired[name])
    if parse_metadata(node) != build_metadata(company):
        node.metadata_json = metadata_text(company)
        changed.append("metadata")
    if changed:
        report.updated[company["id"]] = changed
    else:
        report.unchanged += 1


def graduate(session, private, public, report):
    """Fold a private node into the public node it became: its edges move, duplicates go, it is deleted.

    Where an edge of the private node and one of the public node say the same thing (same
    supplier, customer and type), the better-reviewed one is kept, as the duplicate-edge cleanup
    would. A link between the company and its own public listing is dropped.
    """
    session.flush()
    private_ticker, public_ticker, public_name = private.ticker, public.ticker, public.name
    edges = {edge.id: edge for edge in [*private.supplied_by, *private.supplies_to]}
    moved = dropped = self_links = 0
    for edge in sorted(edges.values(), key=lambda item: item.id):
        source_id = public.id if edge.source_id == private.id else edge.source_id
        target_id = public.id if edge.target_id == private.id else edge.target_id
        if source_id == target_id:
            session.delete(edge)
            self_links += 1
            session.flush()
            continue
        twin = session.query(Edge).filter(
            Edge.source_id == source_id, Edge.target_id == target_id,
            Edge.dependency_type == edge.dependency_type, Edge.id != edge.id,
        ).first()
        if twin is not None:
            dropped += 1
            if duplicate_edge_rank(twin) >= duplicate_edge_rank(edge):
                session.delete(edge)
                session.flush()
                continue
            session.delete(twin)
            session.flush()  # the unique constraint must see the twin gone before the edge takes its place
        edge.source_id, edge.target_id = source_id, target_id
        session.flush()
        moved += 1
    # Edges moved by id still sit in the old node's relationship collections; reload them
    # before the delete cascades over whatever the node is believed to own.
    session.expire_all()
    remaining = session.query(Edge).filter(or_(Edge.source_id == private.id, Edge.target_id == private.id)).count()
    if remaining:
        raise RuntimeError(f"{private_ticker} still has {remaining} edge(s) after graduating; refusing to delete it")
    session.delete(private)
    session.flush()
    report.graduated.append(
        f"{private_ticker} -> {public_ticker}: moved {moved} edge(s), dropped {dropped} duplicate(s) (kept the better-reviewed), "
        f"dropped {self_links} link(s) to its own listing, deleted the private node. Remove the entry from data/private_companies.json in a later PR."
    )
    report.retargets.append((private_ticker, public_ticker, public_name))


def seed_registry(session, companies, report, prune=False):
    index = public_index(session)
    for company in companies:
        company_id, listed_as = company["id"], company.get("listed_as")
        node = session.query(Node).filter(Node.ticker == company_id).first()
        if node is not None and not is_private(node):
            report.refused[company_id] = [f"id {company_id} is already the ticker of public company {node.name!r}"]
            continue
        if listed_as:
            public = session.query(Node).filter(Node.ticker == listed_as, Node.entity_type != PRIVATE_ENTITY_TYPE).first()
            if public is not None:
                if node is not None:
                    graduate(session, node, public, report)
                else:
                    report.graduated.append(f"{company_id} -> {listed_as}: already folded into {listed_as}. Remove the entry from data/private_companies.json.")
                continue
            report.graduating_later.append(f"{company_id}: listed_as {listed_as} is not in the database yet, so it stays private for now")
        reasons = index.collisions(company)
        if reasons:
            report.refused[company_id] = reasons
            continue
        upsert_company(session, company, node, report)
    session.flush()

    registry_ids = {company["id"] for company in companies}
    for orphan in session.query(Node).filter(Node.entity_type == PRIVATE_ENTITY_TYPE).all():
        if orphan.ticker in registry_ids:
            continue
        if prune:
            session.delete(orphan)
            report.pruned.append(orphan.ticker)
        else:
            report.orphans.append(orphan.ticker)
    session.flush()


# --- Seed edges ----------------------------------------------------------------------------

def seed_edges(session, entries, companies=(), report=None):
    """Create a pending edge for each usable seed edge; leave existing edges and unusable entries alone."""
    report = report if report is not None else SeedReport()
    # A seed edge written before a company went public still names the private id.
    renamed = {company["id"]: company["listed_as"] for company in companies if company.get("listed_as")}

    def resolve(ticker):
        node = session.query(Node).filter(Node.ticker == ticker).first()
        if node is None and ticker in renamed:
            node = session.query(Node).filter(Node.ticker == renamed[ticker]).first()
        return node

    rows = session.query(Node.ticker, Node.name).filter(Node.ticker.is_not(None)).all()
    with registered_company_names(rows):
        for position, entry in enumerate(entries, start=1):
            label = edge_label(entry, position)
            problems = edge_entry_problems(entry)
            if problems:
                report.edges_skipped.append((label, "rejected: " + "; ".join(problems)))
                continue
            source, target = resolve(entry["source"]), resolve(entry["target"])
            missing = [ticker for ticker, node in ((entry["source"], source), (entry["target"], target)) if node is None]
            if missing:
                report.edges_skipped.append((label, f"skipped: {', '.join(missing)} is not in the database (a registry company that is refused or not seeded, or a ticker the universe lacks)"))
                continue
            problems = edge_node_problems(entry, source, target)
            if problems:
                report.edges_skipped.append((label, "rejected: " + "; ".join(problems)))
                continue
            existing = session.query(Edge).filter(
                Edge.source_id == source.id, Edge.target_id == target.id, Edge.dependency_type == entry["dependency_type"]
            ).first()
            if existing is not None:
                report.edges_existing += 1
                continue
            session.add(Edge(
                source_id=source.id,
                target_id=target.id,
                dependency_type=entry["dependency_type"],
                product=entry.get("product"),
                confidence_score=SEED_EDGE_CONFIDENCE,
                source_url=entry["source_url"],
                source_title=entry["source_title"],
                evidence_excerpt=" ".join(entry["evidence_excerpt"].split()),
                review_status="pending",
            ))
            session.flush()  # the next entry's lookup must see this one
            report.edges_created.append(f"{source.ticker} -> {target.ticker} ({entry['dependency_type']})")
    return report


# --- Review decisions ----------------------------------------------------------------------

def decision_rank(decision):
    reviewed = parse_reviewed_at(decision.get("reviewed_at")) or datetime.min.replace(tzinfo=timezone.utc)
    return REVIEW_STATUS_RANK.get(decision.get("review_status") or "pending", 0), reviewed


def retarget_decisions(decisions, old_ticker, new_ticker, new_name):
    """The decisions with those about `old_ticker` rewritten to `new_ticker`, and whether any changed.

    Without this a graduated company's decisions would name a node that no longer exists, be
    reported as not applied on every run, and be carried in the file forever. A verdict is
    carried over as it stands; when the public company already had a decision for the same
    link the better-reviewed one is kept, and a decision that would link the company to itself
    is dropped.
    """
    entries, changed = [], False  # (decision, was it rewritten)
    for decision in decisions:
        item = dict(decision)
        touched = False
        for side in ("source", "target"):
            if item.get(f"{side}_ticker") == old_ticker:
                item[f"{side}_ticker"], item[f"{side}_name"] = new_ticker, new_name
                touched = changed = True
        if not (touched and item["source_ticker"] == item["target_ticker"]):
            entries.append((item, touched))
    groups = {}
    for position, (item, _touched) in enumerate(entries):
        groups.setdefault(decision_key(item), []).append(position)
    dropped = set()
    for positions in groups.values():
        if len(positions) > 1 and any(entries[position][1] for position in positions):
            # The better-reviewed decision stays; on a tie the one that was already there.
            best = max(positions, key=lambda position: (decision_rank(entries[position][0]), not entries[position][1]))
            dropped.update(position for position in positions if position != best)
    return [item for position, (item, _touched) in enumerate(entries) if position not in dropped], changed


def retarget_decisions_file(path, old_ticker, new_ticker, new_name):
    """Carry a graduated company's review decisions in the tracked file over to its public ticker."""
    if not os.path.exists(path):
        return False
    decisions = load_decisions(path)
    result, changed = retarget_decisions(decisions, old_ticker, new_ticker, new_name)
    if changed:
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
        payload["decisions"] = result
        write_json_atomic(path, payload)
    return changed


# --- Run -----------------------------------------------------------------------------------

def print_report(report, companies, entries):
    print(f"--- Private companies: {len(companies)} registry entr{'y' if len(companies) == 1 else 'ies'}, {len(entries)} seed edge(s) ---")
    for company_id in report.created:
        print(f"  [+] created {company_id}")
    for company_id, fields in report.updated.items():
        print(f"  [~] updated {company_id}: {', '.join(fields)}")
    for line in report.graduated:
        print(f"  [>] GRADUATED {line}")
    for line in report.graduating_later:
        print(f"  [~] {line}")
    for company_id, reasons in report.refused.items():
        warn("Private company refused", f"{company_id}: {'; '.join(reasons)}. Set listed_as to the public ticker if the company has gone public, or fix the entry.")
    for company_id in report.orphans:
        warn("Private company not in the registry", f"{company_id} is in the database but no longer in data/private_companies.json; run seed_private_companies.py --prune-orphans to delete it and its edges.")
    for company_id in report.pruned:
        print(f"  [-] deleted {company_id} (not in the registry)")
    for edge in report.edges_created:
        print(f"  [+] pending edge {edge}")
    for label, reason in report.edges_skipped:
        warn("Private seed edge not seeded", f"{label} {reason}")
    print(
        f"Private companies: {len(report.created)} created, {len(report.updated)} updated, {report.unchanged} unchanged, "
        f"{len(report.refused)} refused, {len(report.graduated)} graduated, {len(report.orphans)} orphaned. "
        f"Seed edges: {len(report.edges_created)} created, {report.edges_existing} already present, {len(report.edges_skipped)} not seeded."
    )


def run(registry_path=REGISTRY_PATH, edges_path=SEED_EDGES_PATH, decisions_path=DECISIONS_PATH, prune=False, session_factory=None):
    """Seed the registry and the seed edges; returns the SeedReport. RegistryError when a file is unusable."""
    companies = load_registry(registry_path)
    entries = load_seed_edges(edges_path)
    report = SeedReport()
    session = (session_factory or SessionLocal)()
    try:
        seed_registry(session, companies, report, prune=prune)
        seed_edges(session, entries, companies, report)
        session.commit()
    finally:
        session.close()  # a failure before the commit leaves nothing behind
    print_report(report, companies, entries)
    for old_ticker, new_ticker, new_name in report.retargets:
        if retarget_decisions_file(decisions_path, old_ticker, new_ticker, new_name):
            print(f"  [~] moved the review decisions about {old_ticker} to {new_ticker} in {os.path.basename(decisions_path)}")
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description="Seed the private-company registry and its curated supply edges.")
    parser.add_argument("--registry", default=REGISTRY_PATH)
    parser.add_argument("--edges", default=SEED_EDGES_PATH)
    parser.add_argument("--prune-orphans", action="store_true", help="delete private nodes (and their edges) that are no longer in the registry")
    args = parser.parse_args(argv)
    try:
        run(args.registry, args.edges, prune=args.prune_orphans)
    except RegistryError as exc:
        print("The private-company files cannot be used; nothing was changed:", file=sys.stderr)
        for problem in exc.problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
