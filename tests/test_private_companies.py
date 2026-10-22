"""The private-company registry: what a valid entry is, and how every rule rejects a bad one."""
import copy
import json
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import private_companies as private
from private_companies import (
    PRIVATE_ENTITY_TYPE,
    PublicIndex,
    RegistryError,
    collision_key,
    edge_entry_problems,
    edge_node_problems,
    entry_problems,
    registry_problems,
    seed_edge_problems,
)

TODAY = date(2026, 10, 22)


def company(**overrides):
    entry = {
        "id": "OPENAI", "name": "OpenAI", "aliases": ["OpenAI, Inc.", "OpenAI Global, LLC"],
        "sector": "Technology", "industry": "Artificial Intelligence",
        "description": "AI research and products company; maker of ChatGPT and the GPT model family.",
        "website": "https://openai.com", "hq": "San Francisco, California, USA", "founded": 2015,
        "sources": ["https://openai.com/about"], "checked": "2026-10-22", "listed_as": None,
    }
    entry.update(overrides)
    return entry


def second_company(**overrides):
    entry = company(
        id="ANTHROPIC", name="Anthropic", aliases=["Anthropic, PBC"],
        description="AI safety and research company; maker of the Claude family of AI models.",
        website="https://www.anthropic.com", founded=2021,
    )
    entry.update(overrides)
    return entry


def registry(*companies):
    return {"version": 1, "companies": list(companies)}


def seed_edge(**overrides):
    entry = {
        "source": "ORCL", "target": "OPENAI", "dependency_type": "Cloud Infrastructure Provider", "product": "AI compute capacity",
        "source_url": "https://www.sec.gov/Archives/edgar/data/1341439/000095017025090000/orcl-20250531.htm",
        "source_title": "Oracle 10-K FY2025",
        "evidence_excerpt": "Oracle provides cloud infrastructure capacity to OpenAI, Inc. under a multi-year agreement.",
        "checked": "2026-10-22",
    }
    entry.update(overrides)
    return entry


def node(name, ticker, private_entry=None):
    if private_entry is not None:
        return private.registry_node(private_entry)
    return SimpleNamespace(name=name, ticker=ticker, entity_type="Company", metadata_json=None, sector="Technology", industry="Software")


def problems_of(entry):
    return entry_problems(entry, TODAY)


def assert_rejected(entry, fragment):
    found = problems_of(entry)
    assert any(fragment in problem for problem in found), f"expected a problem mentioning {fragment!r}, got {found}"


# --- the files that ship ----------------------------------------------------------------------

def test_the_shipped_registry_and_seed_edges_are_valid():
    """The same check the nightly seeder relies on, against the published public universe."""
    assert private.validate_files() == []


def test_the_shipped_files_load():
    assert isinstance(private.load_registry(), list)
    assert isinstance(private.load_seed_edges(), list)


# --- recognising a private company ------------------------------------------------------------

def test_is_private_reads_the_entity_type():
    assert private.is_private(SimpleNamespace(entity_type=PRIVATE_ENTITY_TYPE))
    assert not private.is_private(SimpleNamespace(entity_type="Company"))
    assert not private.is_private(SimpleNamespace())
    assert not private.is_private(None)


def test_is_private_entry_reads_the_exported_listing():
    assert private.is_private_entry({"listing": "private"})
    assert not private.is_private_entry({"listing": "public"})
    assert not private.is_private_entry({"ticker": "AMD"})
    assert not private.is_private_entry(None)


def test_metadata_round_trips_and_survives_garbage():
    entry = company()
    holder = SimpleNamespace(metadata_json=private.metadata_text(entry))
    metadata = private.parse_metadata(holder)
    assert metadata == {
        "website": "https://openai.com", "hq": "San Francisco, California, USA", "founded": 2015,
        "aliases": ["OpenAI, Inc.", "OpenAI Global, LLC"], "registry": True,
    }
    assert private.node_aliases(holder) == ["OpenAI, Inc.", "OpenAI Global, LLC"]
    for garbage in (None, "", "not json", "[1, 2]", "42"):
        assert private.parse_metadata(SimpleNamespace(metadata_json=garbage)) == {}
        assert private.node_aliases(SimpleNamespace(metadata_json=garbage)) == []
    assert private.node_aliases(SimpleNamespace(metadata_json=json.dumps({"aliases": "OpenAI"}))) == []


# --- names ------------------------------------------------------------------------------------

@pytest.mark.parametrize("first, second", [
    ("OpenAI, Inc.", "OpenAI"),
    ("OpenAI Group, PBC", "OpenAI"),
    ("OpenAI Group, Inc. Class A Common Stock", "OpenAI, Inc."),
    ("Alphabet Inc. Class A Common Stock", "Alphabet Inc. Class C Capital Stock"),
    ("Alphabet Inc. Class A Common Stock", "Alphabet"),
    ("Cingulate Inc. Warrants", "Cingulate"),
    ("Mistral AI S.A.S.", "Mistral AI"),
    ("Acme Holdings N.V.", "Acme Holdings"),
    ("The Boring Company", "Boring"),
])
def test_one_company_has_one_collision_key(first, second):
    assert collision_key(first) == collision_key(second)


def test_distinct_companies_keep_distinct_keys():
    assert collision_key("Scale AI") != collision_key("Scale")
    assert collision_key("Toro Company") == collision_key("Toro Corp.")  # a legal form is not an identity
    assert collision_key("Block Inc") != collision_key("Blockbuster")
    assert collision_key("") == collision_key(None) == ""


# --- one entry --------------------------------------------------------------------------------

def test_a_valid_entry_has_no_problems():
    assert problems_of(company()) == []
    assert problems_of(second_company()) == []


def test_optional_fields_may_be_absent_or_null():
    entry = company(website=None, hq=None, founded=None)
    del entry["aliases"]
    del entry["listed_as"]
    assert problems_of(entry) == []


@pytest.mark.parametrize("bad_id", ["OPEN", "AB", "openai", "OPEN-AI", "1PENAI", "OPENAI_X", "A" * 15, "OPEN AI", "", None, 12345678])
def test_an_id_must_be_six_to_fourteen_capitals_or_digits(bad_id):
    assert_rejected(company(id=bad_id), "id ")


@pytest.mark.parametrize("good_id", ["OPENAI", "ANTHROPIC", "STRIPE1", "A12345", "DATABRICKS", "A" * 14])
def test_good_ids_pass(good_id):
    assert problems_of(company(id=good_id)) == []


def test_unknown_and_missing_fields_are_named():
    assert_rejected(company(valuation="$157B"), "unknown field")
    assert_rejected(company(market_cap=1), "unknown field")
    entry = company()
    del entry["description"]
    del entry["sources"]
    assert_rejected(entry, "missing field")
    assert problems_of("not an object") == ["must be an object"]


@pytest.mark.parametrize("name", ["", "   ", "Open  AI", "Open\nAI", "Open<b>AI</b>", "X" * 81, None, 7, "(())"])
def test_a_name_is_one_clean_line(name):
    assert problems_of(company(name=name))


@pytest.mark.parametrize("sector", ["Financial Services", "financial services", "Real Estate", "Shell Companies", "Insurance", "Banks", "Asset Management", "Financial"])
def test_a_sector_the_export_drops_is_refused(sector):
    assert_rejected(company(sector=sector), "dropped by the export")


@pytest.mark.parametrize("field", ["sector", "industry"])
@pytest.mark.parametrize("placeholder", ["Uncategorized", "Pending Update", "Linked Companies", "N/A", "unknown", ""])
def test_a_placeholder_sector_or_industry_is_refused(field, placeholder):
    assert problems_of(company(**{field: placeholder}))


def test_a_payments_company_is_filed_under_technology():
    assert problems_of(company(id="STRIPE1", name="Stripe", aliases=["Stripe, Inc."], sector="Technology", industry="Payments Infrastructure")) == []


def test_the_description_is_one_short_neutral_sentence():
    assert problems_of(company(description="x" * 280)) == []
    assert_rejected(company(description="x" * 281), "limit is 280")
    assert problems_of(company(description=""))
    assert problems_of(company(description=None))


@pytest.mark.parametrize("description", [
    "AI company valued at the highest level in its sector.",
    "AI lab that raised money from investors in several rounds.",
    "AI lab with a $157 billion valuation.",
    "AI lab with 3,000 employees.",
    "AI lab with over 3000 staff.",
    "Payments company processing 1.4 trillion dollars a year.",
    "AI lab that closed a Series E round.",
    "AI lab with annual revenue growing quickly.",
    "AI lab that is 40% owned by its staff.",
    "AI lab and a unicorn.",
    "AI lab headcount grew last year.",
    "Company funding came from investors.",
])
def test_a_description_with_figures_about_money_or_people_is_refused(description):
    assert_rejected(company(description=description), "description")


@pytest.mark.parametrize("description", [
    "The largest AI lab in the world.",
    "A leading provider of AI models.",
    "The best-known maker of chat assistants.",
    "Maker of the most capable language models.",
    "World-class research organisation.",
    "Builds the world's fastest inference chips.",
    "The first company to ship the product.",
    "Number one choice for developers.",
])
def test_a_description_with_superlatives_is_refused(description):
    assert_rejected(company(description=description), "description")


def test_ordinary_descriptions_are_not_flagged():
    for description in (
        "Payments infrastructure company; its software lets businesses accept payments online.",
        "Data and AI platform company; maker of a lakehouse analytics product.",
        "Autonomous trucking developer; operates driverless freight routes in Texas.",
        "Maker of the Claude family of AI models, built with safety research in mind.",
    ):
        assert problems_of(company(description=description)) == [], description


@pytest.mark.parametrize("website", ["http://openai.com", "javascript:alert(1)", "https://user:pass@openai.com", "openai.com", "https://", "https://open ai.com", "ftp://openai.com", 5])
def test_a_website_is_an_https_url_or_null(website):
    assert_rejected(company(website=website), "website")


@pytest.mark.parametrize("founded", [1799, 2027, "2015", 2015.5, True, 0, -5])
def test_founded_is_a_plausible_year_or_null(founded):
    assert_rejected(company(founded=founded), "founded")


def test_founded_bounds_are_inclusive():
    assert problems_of(company(founded=1800)) == []
    assert problems_of(company(founded=TODAY.year)) == []


@pytest.mark.parametrize("sources", [[], None, "https://openai.com", ["not a url"], ["https://ok.example", "ftp://x.example"], [3]])
def test_sources_are_a_non_empty_list_of_urls(sources):
    assert problems_of(company(sources=sources))


@pytest.mark.parametrize("checked", ["2026-13-01", "22 Oct 2026", "2026-10-30", "20261022", None, "2026-1-5", ""])
def test_checked_is_a_real_date_not_in_the_future(checked):
    assert_rejected(company(checked=checked), "checked")


def test_checked_allows_a_day_of_timezone_slack():
    assert problems_of(company(checked="2026-10-23")) == []
    assert problems_of(company(checked="2026-10-24"))


def test_aliases_must_be_distinctive():
    assert_rejected(company(aliases=["OpenAI", "Open"]), "single word")
    assert_rejected(company(name="Scale AI", id="SCALEAI", aliases=["Scale"]), "single word")
    assert_rejected(company(name="Mistral AI", id="MISTRALAI", aliases=["Mistral"]), "single word")
    # The full legal name is exactly what is wanted.
    assert problems_of(company(name="Scale AI", id="SCALEAI", aliases=["Scale AI, Inc."])) == []
    # A single word is fine when it is the company's own name.
    assert problems_of(company(aliases=["OpenAI"])) == []


def test_aliases_are_plain_unique_strings():
    assert_rejected(company(aliases="OpenAI, Inc."), "aliases must be a list")
    assert_rejected(company(aliases=["OpenAI, Inc.", "openai, inc."]), "repeat")
    assert_rejected(company(aliases=[5]), "alias")
    assert_rejected(company(aliases=[""]), "alias")
    assert_rejected(company(aliases=["A" * 81]), "alias")
    assert_rejected(company(aliases=["Open<AI> Inc"]), "alias")


def test_listed_as_is_a_ticker_or_null():
    assert problems_of(company(listed_as="OPAI")) == []
    assert_rejected(company(listed_as="open ai"), "listed_as")
    assert_rejected(company(listed_as="OPENAI"), "listed_as must be the public ticker")
    assert_rejected(company(listed_as=5), "listed_as")


# --- the registry as a whole ------------------------------------------------------------------

def test_a_valid_registry_has_no_problems_and_an_empty_one_is_fine():
    assert registry_problems(registry(company(), second_company()), today=TODAY) == []
    assert registry_problems(registry(), today=TODAY) == []


@pytest.mark.parametrize("payload", [None, [], {}, {"companies": []}, {"version": 2, "companies": []}, {"version": 1}, {"version": 1, "companies": {}}, "text"])
def test_the_file_shape_is_checked(payload):
    assert registry_problems(payload, today=TODAY)


def test_an_unknown_top_level_field_is_refused():
    assert registry_problems({"version": 1, "companies": [], "notes": "x"}, today=TODAY)


def test_ids_must_be_unique():
    found = registry_problems(registry(company(), company(name="Another", aliases=[])), today=TODAY)
    assert any("id is used by entry 1" in problem for problem in found)


def test_names_and_aliases_are_unique_across_companies_whatever_their_legal_form():
    clash_by_alias = second_company(aliases=["OpenAI, Inc."])
    clash_by_name = second_company(name="OpenAI Inc.", aliases=[])
    clash_by_alias_to_name = second_company(aliases=["Open AI Global LLC", "OpenAI"])
    for other in (clash_by_alias, clash_by_name, clash_by_alias_to_name):
        found = registry_problems(registry(company(), other), today=TODAY)
        assert any("same company as" in problem for problem in found), other


def test_a_company_may_repeat_its_own_name_among_its_aliases():
    assert registry_problems(registry(company(aliases=["OpenAI, Inc.", "OpenAI Inc"])), today=TODAY) == []


# --- against the public universe --------------------------------------------------------------

PUBLIC = [
    ("MSFT", "Microsoft Corporation Common Stock"),
    ("GOOGL", "Alphabet Inc. Class A Common Stock"),
    ("GOOG", "Alphabet Inc. Class C Capital Stock"),
    ("ABCDEF", "Some Foreign Ordinary Shares"),
    ("OPAI", "OpenAI Group, Inc. Common Stock"),
]


def test_an_entry_that_is_a_public_company_is_refused_by_name_whatever_the_legal_form():
    index = PublicIndex(PUBLIC)
    assert registry_problems(registry(company(id="MICROSOFT", name="Microsoft Corp", aliases=[])), index, TODAY)
    found = registry_problems(registry(company(id="ALPHABET", name="Alphabet", aliases=[])), index, TODAY)
    assert any("GOOGL" in problem for problem in found) and any("GOOG " in problem or "GOOG)" in problem for problem in found)


def test_an_entry_is_refused_when_one_alias_is_a_public_company():
    found = registry_problems(registry(company(aliases=["OpenAI, Inc.", "Microsoft Corporation"])), PublicIndex(PUBLIC), TODAY)
    assert any("alias 'Microsoft Corporation'" in problem and "MSFT" in problem for problem in found)


def test_an_id_equal_to_a_public_ticker_is_refused():
    found = registry_problems(registry(company(id="ABCDEF")), PublicIndex(PUBLIC), TODAY)
    assert any("id ABCDEF is the ticker of public company" in problem for problem in found)


def test_a_share_class_suffix_cannot_hide_a_collision():
    index = PublicIndex([("GOOGL", "Alphabet Inc. Class A Common Stock")])
    assert index.collisions(company(id="ALPHABET", name="Alphabet Inc. Class C Capital Stock", aliases=[]))
    assert index.collisions(company(id="ALPHABET", name="Alphabet Inc.", aliases=[]))


def test_the_graduating_listing_is_not_a_collision():
    """OpenAI -> OPAI is the same company on purpose; any other lookalike still is one."""
    entry = company(listed_as="OPAI")
    assert PublicIndex(PUBLIC).collisions(entry) == []
    assert PublicIndex(PUBLIC).collisions(company(listed_as="MSFT"))  # names OPAI as a lookalike
    assert PublicIndex(PUBLIC).collisions(company())


def test_an_unrelated_registry_entry_is_not_a_collision():
    assert PublicIndex(PUBLIC).collisions(second_company()) == []


def test_the_public_universe_is_read_from_a_dashboard_without_its_private_companies():
    dashboard = {"industries": {"Technology": [
        {"ticker": "MSFT", "name": "Microsoft"}, {"ticker": "OPENAI", "name": "OpenAI", "listing": "private"}, {"name": "No ticker"},
    ], "Other": None}}
    assert private.public_companies_from_dashboard(dashboard) == [("MSFT", "Microsoft")]
    assert private.public_companies_from_dashboard({}) == []


# --- seed edges -------------------------------------------------------------------------------

def test_a_valid_seed_edge_has_no_problems():
    assert edge_entry_problems(seed_edge(), TODAY) == []
    assert seed_edge_problems({"version": 1, "edges": [seed_edge()]}, {"OPENAI"}, TODAY) == []
    assert seed_edge_problems({"version": 1, "edges": []}, set(), TODAY) == []


@pytest.mark.parametrize("url", ["Manual System Jumpstart", "Manual", "manual seed", "AI Multi-Source Research", "", None, "ftp://sec.gov/x", "www.sec.gov/x", "https://", "https://user:pw@sec.gov/x"])
def test_a_seed_edge_cites_a_real_page_never_a_label(url):
    assert any("source_url" in problem for problem in edge_entry_problems(seed_edge(source_url=url), TODAY))


def test_a_manual_title_is_refused_too():
    assert any("source_title" in problem for problem in edge_entry_problems(seed_edge(source_title="Manual System Jumpstart"), TODAY))


@pytest.mark.parametrize("excerpt", ["", "short", "n/a", None, 12])
def test_a_seed_edge_needs_a_real_excerpt(excerpt):
    assert any("evidence_excerpt" in problem for problem in edge_entry_problems(seed_edge(evidence_excerpt=excerpt), TODAY))


@pytest.mark.parametrize("label", ["Investment", "Strategic Partnership", "Customer", "Supplier", "manufacturer -> customer", ""])
def test_a_seed_edge_is_a_supply_relationship(label):
    assert any("dependency_type" in problem for problem in edge_entry_problems(seed_edge(dependency_type=label), TODAY))


@pytest.mark.parametrize("label", ["Cloud Infrastructure Provider", "AI Accelerator Chips", "Payment Processing", "Data Licensing", "Contract Manufacturing"])
def test_supply_labels_pass(label):
    assert edge_entry_problems(seed_edge(dependency_type=label), TODAY) == []


def test_seed_edge_shape_problems():
    assert any("unknown field" in problem for problem in edge_entry_problems(seed_edge(valuation=1), TODAY))
    assert any("missing field" in problem for problem in edge_entry_problems({"source": "ORCL"}, TODAY))
    assert any("same company" in problem for problem in edge_entry_problems(seed_edge(source="OPENAI"), TODAY))
    assert any("source" in problem for problem in edge_entry_problems(seed_edge(source="not a ticker"), TODAY))
    assert any("checked" in problem for problem in edge_entry_problems(seed_edge(checked="soon"), TODAY))
    assert edge_entry_problems("text") == ["must be an object"]
    assert edge_entry_problems(seed_edge(product=None), TODAY) == []


def test_seed_edges_are_unique_and_touch_the_registry():
    found = seed_edge_problems({"version": 1, "edges": [seed_edge(), seed_edge()]}, {"OPENAI"}, TODAY)
    assert any("repeats entry 1" in problem for problem in found)
    found = seed_edge_problems({"version": 1, "edges": [seed_edge(source="ORCL", target="MSFT")]}, {"OPENAI"}, TODAY)
    assert any("neither end is a registry company" in problem for problem in found)
    assert seed_edge_problems({"version": 1, "edges": [seed_edge(source="ORCL", target="MSFT")]}, None, TODAY) == []
    assert seed_edge_problems({"edges": []}, None, TODAY)
    assert seed_edge_problems({"version": 1, "edges": [], "x": 1}, None, TODAY)


def test_the_excerpt_must_name_both_companies_by_name_or_alias():
    openai = node(None, None, company())
    oracle = node("Oracle Corporation Common Stock", "ORCL")
    assert edge_node_problems(seed_edge(), oracle, openai) == []  # "OpenAI, Inc." is an alias
    # The alias is what names it: without the registry aliases the same excerpt names nobody.
    bare = node(None, None, company(aliases=[], name="Another Lab", id="ANOTHER"))
    assert any("does not name target ANOTHER" in problem for problem in edge_node_problems(seed_edge(target="ANOTHER"), oracle, bare))
    only_one = seed_edge(evidence_excerpt="Oracle provides cloud infrastructure capacity to its largest AI customer under a multi-year agreement.")
    assert any("does not name target OPENAI" in problem for problem in edge_node_problems(only_one, oracle, openai))
    neither = seed_edge(evidence_excerpt="Cloud infrastructure capacity is provided under a multi-year agreement to AI labs.")
    assert len(edge_node_problems(neither, oracle, openai)) == 2


def test_an_excerpt_the_pipeline_would_reject_is_refused_up_front():
    openai = node(None, None, company())
    oracle = node("Oracle Corporation Common Stock", "ORCL")
    past = seed_edge(evidence_excerpt="Oracle formerly provided cloud infrastructure capacity to OpenAI until 2019.")
    assert any("pipeline would reject" in problem for problem in edge_node_problems(past, oracle, openai))
    sale = seed_edge(evidence_excerpt="Oracle sold its stake in OpenAI to a group of investors last year.")
    assert any("pipeline would reject" in problem for problem in edge_node_problems(sale, oracle, openai))


def test_an_excerpt_saying_the_supply_runs_the_other_way_is_refused():
    openai = node(None, None, company())
    oracle = node("Oracle Corporation Common Stock", "ORCL")
    backwards = seed_edge(evidence_excerpt="OpenAI supplies cloud infrastructure capacity to Oracle under a multi-year agreement.")
    assert any("other way" in problem for problem in edge_node_problems(backwards, oracle, openai))


# --- loading ----------------------------------------------------------------------------------

def write(tmp_path, name, payload):
    path = tmp_path / name
    path.write_text(payload if isinstance(payload, str) else json.dumps(payload), encoding="utf-8")
    return path


def test_loading_returns_validated_entries(tmp_path):
    path = write(tmp_path, "registry.json", registry(company(), second_company()))
    assert [entry["id"] for entry in private.load_registry(path)] == ["OPENAI", "ANTHROPIC"]


def test_a_missing_file_is_an_empty_registry_and_a_broken_one_is_an_error(tmp_path):
    assert private.load_registry(tmp_path / "absent.json") == []
    assert private.load_seed_edges(tmp_path / "absent.json") == []
    with pytest.raises(RegistryError) as raised:
        private.load_registry(write(tmp_path, "garbage.json", "{not json"))
    assert "could not be read" in raised.value.problems[0]
    with pytest.raises(RegistryError) as raised:
        private.load_registry(write(tmp_path, "bad.json", registry(company(sector="Real Estate"))))
    assert any("dropped by the export" in problem for problem in raised.value.problems)
    with pytest.raises(RegistryError):
        private.load_seed_edges(write(tmp_path, "shape.json", {"edges": []}))


def test_loading_seed_edges_leaves_judging_each_entry_to_the_seeder(tmp_path):
    path = write(tmp_path, "edges.json", {"version": 1, "edges": [seed_edge(source_url="Manual"), seed_edge()]})
    assert len(private.load_seed_edges(path)) == 2


# --- the command line -------------------------------------------------------------------------

def run_cli(tmp_path, registry_payload, edges_payload=None, dashboard=None, extra=()):
    registry_path = write(tmp_path, "private_companies.json", registry_payload)
    edges_path = write(tmp_path, "private_seed_edges.json", edges_payload if edges_payload is not None else {"version": 1, "edges": []})
    dashboard_path = write(tmp_path, "dashboard.json", dashboard or {"industries": {}})
    return private.main(["validate", str(registry_path), "--edges", str(edges_path), "--dashboard", str(dashboard_path), *extra])


def test_validate_exits_zero_on_a_valid_registry(tmp_path, capsys):
    assert run_cli(tmp_path, registry(company(), second_company()), {"version": 1, "edges": [seed_edge()]}) == 0
    assert "2 registry entries, 1 seed edges" in capsys.readouterr().out


def test_validate_exits_nonzero_and_lists_every_problem(tmp_path, capsys):
    bad = registry(company(id="OPEN", description="Valued at $5 billion."), second_company(sector="Real Estate"))
    assert run_cli(tmp_path, bad) == 1
    err = capsys.readouterr().err
    assert "problem(s) in the private-company files" in err
    assert "OPEN: id 'OPEN'" in err and "OPEN: description mentions" in err and "ANTHROPIC: sector 'Real Estate'" in err


def test_validate_checks_the_registry_against_the_published_dashboard(tmp_path, capsys):
    dashboard = {"industries": {"Technology": [{"ticker": "OPAI", "name": "OpenAI Group, Inc. Common Stock"}]}}
    assert run_cli(tmp_path, registry(company()), dashboard=dashboard) == 1
    assert "same company as public OPAI" in capsys.readouterr().err


def test_validate_reports_a_seed_edge_whose_excerpt_misses_an_end(tmp_path, capsys):
    dashboard = {"industries": {"Technology": [{"ticker": "ORCL", "name": "Oracle Corporation Common Stock"}]}}
    edges = {"version": 1, "edges": [seed_edge(evidence_excerpt="Oracle provides cloud infrastructure capacity to a large AI customer.")]}
    assert run_cli(tmp_path, registry(company()), edges, dashboard) == 1
    assert "does not name target OPENAI" in capsys.readouterr().err
    good = {"version": 1, "edges": [seed_edge()]}
    assert run_cli(tmp_path, registry(company()), good, dashboard) == 0


def test_validate_leaves_an_unseen_public_end_to_the_seeder(tmp_path):
    """An end the files cannot see (not in the dashboard) is checked against the database at seeding."""
    assert run_cli(tmp_path, registry(company()), {"version": 1, "edges": [seed_edge()]}) == 0


def test_validate_reports_unreadable_files(tmp_path, capsys):
    path = write(tmp_path, "private_companies.json", "{broken")
    code = private.main(["validate", str(path), "--edges", str(tmp_path / "none.json"), "--dashboard", str(tmp_path / "none2.json")])
    assert code == 1 and "could not be read" in capsys.readouterr().err
    assert private.main(["validate", str(tmp_path / "absent.json"), "--edges", str(tmp_path / "none.json"), "--dashboard", str(tmp_path / "none2.json")]) == 1


def test_validate_can_compare_with_a_database_file(tmp_path, capsys):
    import sqlite3

    database = tmp_path / "graph.db"
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE nodes (ticker TEXT, name TEXT, entity_type TEXT)")
    connection.execute("INSERT INTO nodes VALUES ('OPAI', 'OpenAI Group, Inc. Common Stock', 'Company')")
    connection.execute("INSERT INTO nodes VALUES ('ANTHROPIC', 'Anthropic', 'Private Company')")
    connection.commit()
    connection.close()
    assert private.database_public_companies(database) == [("OPAI", "OpenAI Group, Inc. Common Stock")]
    assert run_cli(tmp_path, registry(company()), extra=["--db", str(database)]) == 1
    assert "same company as public OPAI" in capsys.readouterr().err
    assert run_cli(tmp_path, registry(second_company()), extra=["--db", str(database)]) == 0


# --- drift guards -----------------------------------------------------------------------------

def test_the_ignored_sectors_cover_what_the_export_and_discovery_drop():
    import auto_discover_edges
    import export

    ignored = {sector.lower() for sector in export.IGNORED_SECTORS} | {sector.lower() for sector in auto_discover_edges.IGNORED_SECTORS}
    assert ignored <= private.IGNORED_SECTORS


def test_the_module_loads_without_importing_any_other_backend_module():
    """The filing readers import this module; a module-level import back into them would make a cycle."""
    import ast

    tree = ast.parse(Path(private.__file__).read_text(encoding="utf-8"))
    imported = set()
    for statement in tree.body:
        if isinstance(statement, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in statement.names)
        elif isinstance(statement, ast.ImportFrom) and statement.module:
            imported.add(statement.module.split(".")[0])
    backend_modules = {path.stem for path in (ROOT / "backend").glob("*.py")} - {"private_companies"}
    assert not imported & backend_modules, imported & backend_modules


@pytest.mark.parametrize("first", ["private_companies", "supplier_dependence", "evidence_quality", "customer_concentration"])
def test_the_modules_import_in_any_order(first):
    import subprocess

    code = f"import {first}, private_companies, supplier_dependence, evidence_quality, customer_concentration"
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT / "backend", capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_the_registry_never_mutates_its_input():
    payload = registry(company(), second_company())
    before = copy.deepcopy(payload)
    registry_problems(payload, PublicIndex(PUBLIC), TODAY)
    assert payload == before
