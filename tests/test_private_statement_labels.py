"""Private companies named in real annual reports: the hand-labelled links (tests/data/private_statement_labels.json).

The fixture holds the sentences the readers produced a link from for a private company (`links`), read one by one
against the filing, and sentences that name a private company and must not produce one (`near_misses`: competitor
lists, investments, auditors ...), plus the true statements the readers do not read yet (`known_misses`: recall).
Every sentence is quoted from an SEC 10-K or 20-F. The registry in the fixture is a scratch registry for the
evaluation, not the real one; the public names are the listed companies the sentences also name.
"""
import json
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import auto_discover_edges
from customer_concentration import extract_disclosures
from models import Base, Node
from supplier_dependence import extract_supplier_dependencies

LABELS = json.loads((ROOT / "tests" / "data" / "private_statement_labels.json").read_text(encoding="utf-8"))
# Wrong links the reader still produces, each for a reason no sentence-level rule can see.
KNOWN_WRONG = {
    ("SHIP", "FIDELITY"): "a ship manager that shares the short name of a registry company: a registry must not list a bare common brand",
}


def labelled_world():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, autoflush=False)()
    for item in LABELS["universe"]:
        session.add(Node(name=item["name"], ticker=item["ticker"], market_cap=item["market_cap"], sector="Technology"))
    for item in LABELS["registry"]:
        session.add(Node(
            name=item["name"], ticker=item["id"], entity_type="Private Company", sector="Technology", market_cap=None,
            metadata_json=json.dumps({"aliases": item["aliases"], "registry": True}),
        ))
    session.commit()
    known = auto_discover_edges.known_company_names(session)
    return known, auto_discover_edges.build_supplier_index(session, known), {item["id"]: item["name"] for item in LABELS["registry"]}


KNOWN, INDEX, PRIVATE_NAMES = labelled_world()


def produced(item):
    """Does the reader make a link between this filer and this private company from the sentence?"""
    filer = (item["filer"], auto_discover_edges.clean_company_name(item["filer"]), item["filer_ticker"])
    name = PRIVATE_NAMES[item["private"]]
    if item["direction"] == "supplier":
        return name in {found.supplier_name for found in extract_supplier_dependencies(item["sentence"], INDEX, filer)}
    return any(d.customer_name == name and d.share_pct is not None for d in extract_disclosures(item["sentence"], KNOWN, filer, require_customer_cue=False))


def test_every_right_link_is_still_read_and_no_wrong_one_is():
    lost, kept_wrong = [], []
    for item in LABELS["links"]:
        read = produced(item)
        if item["label"] == "right" and not read:
            lost.append((item["id"], item["sentence"][:80]))
        if item["label"] == "wrong" and read and (item["filer_ticker"], item["private"]) not in KNOWN_WRONG:
            kept_wrong.append((item["id"], item["why"]))
    assert not lost, lost
    assert not kept_wrong, kept_wrong


def test_the_known_wrong_links_are_labelled_wrong():
    wrong = {(item["filer_ticker"], item["private"]) for item in LABELS["links"] if item["label"] == "wrong"}
    assert set(KNOWN_WRONG) <= wrong


def test_no_near_miss_becomes_a_link():
    produced_links = [item["id"] for item in LABELS["near_misses"] if produced(item)]
    assert not produced_links, produced_links


def test_the_labelled_set_is_what_it_claims_to_be():
    links = LABELS["links"]
    assert {item["label"] for item in links} <= {"right", "wrong", "doubtful"}
    assert sum(item["label"] == "right" for item in links) >= LABELS["minimum_right"] >= 25
    assert len(LABELS["near_misses"]) >= LABELS["minimum_near_misses"] >= 40
    assert len(LABELS["known_misses"]) >= 15
    # Both directions, and links the rules were tuned on beside links read before any rule was written for them.
    assert {item["direction"] for item in links} == {"supplier", "customer"}
    assert {item["set"] for item in links} == {"tuning", "cold"}
    # The wrong rows are the families the rules were written for.
    assert sum(item["label"] == "wrong" for item in links) >= 6
    for item in links + LABELS["near_misses"] + LABELS["known_misses"]:
        assert item["private"] in PRIVATE_NAMES and item["sentence"].strip() and item["filer_ticker"] and item["why"]
