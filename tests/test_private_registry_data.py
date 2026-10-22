"""Extra checks on the private-company registry data that backend/private_companies.py does not make.

The structural rules (ids, aliases, collisions with public companies, description figures and superlatives,
dates, edge excerpts that name both ends and fit a supply relationship) live in `private_companies.py validate`
and its own tests. This file only adds what that validator leaves open for the curated data itself:

* the registry stays sorted by id (so reviews and diffs stay readable),
* sectors are real dashboard sectors,
* sources are checkable pages (https, not Wikipedia),
* a description is exactly one sentence,
* seed edges name a product, and each end is a registry company or a company of the public universe.
"""

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = ROOT / "data" / "private_companies.json"
EDGES_PATH = ROOT / "data" / "private_seed_edges.json"
DASHBOARD_LITE = ROOT / "docs" / "dashboard_lite.json"

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


@pytest.fixture(scope="module")
def public_tickers():
    data = json.loads(DASHBOARD_LITE.read_text(encoding="utf-8"))
    tickers = {c["ticker"] for companies in data["industries"].values() for c in companies}
    assert len(tickers) > 1000
    return tickers


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


def test_edge_ends_are_registry_ids_or_public_tickers(seed_edges, registry, public_tickers):
    registry_ids = {c["id"] for c in registry}
    for e in seed_edges:
        for end in (e["source"], e["target"]):
            assert end in registry_ids or end in public_tickers, f"{e['source']} -> {e['target']}: unknown end {end}"


def test_edges_name_a_product_and_use_https_sources(seed_edges):
    for e in seed_edges:
        label = f"{e['source']} -> {e['target']}"
        assert isinstance(e.get("product"), str) and e["product"].strip(), f"{label}: product"
        assert e["source_url"].startswith("https://"), label
        assert "wikipedia.org" not in e["source_url"], label
