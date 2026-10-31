"""Extra checks on the private-company registry data that backend/private_companies.py does not make.

The structural rules (ids, aliases, collisions with public companies, description figures and superlatives,
dates, edge excerpts that name both ends and fit a supply relationship) live in `private_companies.py validate`
and its own tests. This file only adds what that validator leaves open for the curated data itself:

* the registry stays sorted by id (so reviews and diffs stay readable),
* sectors are real dashboard sectors,
* sources are checkable pages (https, not Wikipedia),
* a description is exactly one sentence,
* seed edges name a product, and each end is a registry company or has the shape of a US ticker (whether a
  listed end is still in the published universe is not asserted here: see the edge-end test).
"""

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = ROOT / "data" / "private_companies.json"
EDGES_PATH = ROOT / "data" / "private_seed_edges.json"
TICKER_SHAPE = re.compile(r"^[A-Z]{1,5}$")

DASHBOARD_SECTORS = {
    "Technology",
    "Industrials",
    "Communication Services",
    "Healthcare",
    "Consumer Cyclical",
    "Energy",
    "Basic Materials",
    "Consumer Defensive",
    "Utilities",
}


@pytest.fixture(scope="module")
def registry():
    return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))["companies"]


@pytest.fixture(scope="module")
def seed_edges():
    return json.loads(EDGES_PATH.read_text(encoding="utf-8"))["edges"]


def test_registry_is_sorted_by_id(registry):
    ids = [c["id"] for c in registry]
    assert ids == sorted(ids)


def test_sectors_are_dashboard_sectors(registry):
    for c in registry:
        assert c["sector"] in DASHBOARD_SECTORS, (c["id"], c["sector"])


def test_description_is_one_sentence(registry):
    for c in registry:
        text = c["description"]
        assert text.endswith("."), c["id"]
        assert len(re.findall(r"[.!?](?:\s|$)", text)) == 1, f"{c['id']}: description must be a single sentence"


def test_sources_are_https_pages_and_never_wikipedia(registry):
    for c in registry:
        assert len(c["sources"]) == len(set(c["sources"])), c["id"]
        for url in c["sources"]:
            assert url.startswith("https://"), (c["id"], url)
            assert "wikipedia.org" not in url, f"{c['id']}: Wikipedia is not an accepted source"


def test_edge_ends_are_registry_ids_or_tickers(seed_edges, registry):
    # Whether a listed end is in the published universe is deliberately not asserted: that file changes every
    # night, and a company can leave it (a delisting, a merger, a day without market data) without the curated
    # relationship being wrong. GETY did on 2026-10-31 and turned every build on main red. The nightly seeder
    # reports an end it cannot find in the database ("Private seed edge not seeded").
    registry_ids = {c["id"] for c in registry}
    for e in seed_edges:
        for end in (e["source"], e["target"]):
            assert end in registry_ids or TICKER_SHAPE.match(end), f"{e['source']} -> {e['target']}: {end} is neither a registry id nor a ticker"


def test_edges_name_a_product_and_use_https_sources(seed_edges):
    for e in seed_edges:
        label = f"{e['source']} -> {e['target']}"
        assert isinstance(e.get("product"), str) and e["product"].strip(), f"{label}: product"
        assert e["source_url"].startswith("https://"), label
        assert "wikipedia.org" not in e["source_url"], label
