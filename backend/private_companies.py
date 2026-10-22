"""Private companies: the hand-maintained registry, and the helpers that recognise them.

Companies without a stock listing (OpenAI, Anthropic, Stripe, ...) are nodes of the graph
like any other, so a cloud provider supplying a lab or a payment processor serving a
retailer shows up as a relationship. A private node has `entity_type == "Private Company"`,
its registry id as `ticker`, and never any market data: no price, market cap, valuation,
funding or revenue is stored or shown for it.

The registry lives in data/private_companies.json and the curated supply edges that go
with it in data/private_seed_edges.json; both change only by pull request.
`python3 backend/private_companies.py validate` checks them (CI runs the same checks as a
test); seed_private_companies.py writes them into the database.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse

PRIVATE_ENTITY_TYPE = "Private Company"
# The value of an exported company's "listing" key; public companies have no such key.
PRIVATE_LISTING = "private"

# What an exported private company carries on top of a public one's entry, and the market
# fields it must not carry (export.py writes them; validate_dashboard_data.py enforces them).
PRIVATE_ONLY_KEYS = ("listing", "website", "hq", "founded", "aliases")
PRIVATE_MARKET_FIELDS = (
    "price", "change", "market_cap", "enterprise_value", "trailing_pe", "forward_pe", "price_to_book",
    "high_52w", "low_52w", "revenue", "margin", "target_price", "employees",
)
PRIVATE_PLACEHOLDER_FIELDS = ("dividend", "recommendation", "ceo")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REGISTRY_PATH = os.path.join(BASE_DIR, "data", "private_companies.json")
SEED_EDGES_PATH = os.path.join(BASE_DIR, "data", "private_seed_edges.json")
DASHBOARD_PATH = os.path.join(BASE_DIR, "docs", "dashboard_data.json")

# Every US-listed ticker is 1-5 characters, so six or more can never be a real one.
ID_PATTERN = re.compile(r"^[A-Z][A-Z0-9]{5,13}$")
TICKER_PATTERN = re.compile(r"^[A-Z][A-Z0-9.\-]{0,13}$")
DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]")
# Names and descriptions are published verbatim, so markup has no place in them.
MARKUP_CHARACTERS = re.compile(r"[<>]")

REGISTRY_FIELDS = {
    "id", "name", "aliases", "sector", "industry", "description", "website", "hq", "founded",
    "sources", "checked", "listed_as",
}
REQUIRED_REGISTRY_FIELDS = {"id", "name", "sector", "industry", "description", "sources", "checked"}
EDGE_FIELDS = {"source", "target", "dependency_type", "product", "source_url", "source_title", "evidence_excerpt", "checked"}
REQUIRED_EDGE_FIELDS = EDGE_FIELDS - {"product"}

MAX_NAME_LENGTH = 80
MAX_DESCRIPTION_LENGTH = 280
MAX_HQ_LENGTH = 120
FIRST_FOUNDING_YEAR = 1800
MAX_EXCERPT_LENGTH = 1500

# Export drops these sectors, so a private company filed under one would vanish from the
# site. The union of export.py's list and auto_discover_edges.py's wider one (a drift test
# keeps them in step); importing either here would load the database layer.
IGNORED_SECTORS = {
    "shell companies", "financial services", "real estate", "financial", "asset management", "insurance", "banks",
}
PLACEHOLDER_VALUES = {"", "n/a", "none", "null", "uncategorized", "pending update", "linked companies", "unknown"}

# A legal form or a holding-company word is not part of a company's identity: "OpenAI, Inc.",
# "OpenAI Group, PBC" and "OpenAI" are one company. (evidence_quality drops the same words when
# it reads mentions.)
LEGAL_FORM_WORDS = frozenset({
    "inc", "incorporated", "corp", "corporation", "co", "company", "ltd", "limited", "llc", "llp", "lp", "plc",
    "pbc", "gmbh", "ag", "se", "nv", "sa", "bv", "sas", "srl", "spa", "group", "holding", "holdings",
})

# The registry description is a neutral fact, not a pitch: no money, no headcount, no rank.
FIGURE_PATTERN = re.compile(
    r"[$€£¥%]|\b\d[\d,.]*\s*(?:k|m|b|bn|mm|thousand|million|billion|trillion|percent|employees|people|staff)\b"
    r"|\b(?:valuation|valued|funding|funded|raised|raises|revenues?|profits?|headcount|employees|staff|"
    r"workforce|billion|million|trillion|unicorn|series\s+[a-z])\b",
    re.IGNORECASE,
)
SUPERLATIVE_PATTERN = re.compile(
    r"\b(?:largest|biggest|leading|best|top|most|greatest|fastest|foremost|premier|world-class|world['’]s|"
    r"number[\s-]one|first-ever|unrivaled|unrivalled|unparalleled|cutting-edge|state-of-the-art|revolutionary|the\s+first)\b|#\s?1\b",
    re.IGNORECASE,
)


class RegistryError(Exception):
    """A registry or seed-edge file that cannot be used; `problems` lists what is wrong."""

    def __init__(self, problems):
        self.problems = list(problems)
        super().__init__("; ".join(self.problems))


# --- Recognising a private company --------------------------------------------------------

def is_private(node):
    """A database node (or any object with `entity_type`) that is a registry company."""
    return getattr(node, "entity_type", None) == PRIVATE_ENTITY_TYPE


def is_private_entry(company):
    """An exported dashboard entry of a private company."""
    return isinstance(company, dict) and company.get("listing") == PRIVATE_LISTING


def parse_metadata(node):
    """The node's `metadata_json` as a dict ({} when absent or not a JSON object)."""
    raw = getattr(node, "metadata_json", None)
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def node_aliases(node):
    """The exact strings filings use for a private company, from its registry entry."""
    aliases = parse_metadata(node).get("aliases")
    if not isinstance(aliases, list):
        return []
    return [alias.strip() for alias in aliases if isinstance(alias, str) and alias.strip()]


def build_metadata(company):
    """The `metadata_json` content for a registry entry."""
    return {
        "website": company.get("website"),
        "hq": company.get("hq"),
        "founded": company.get("founded"),
        "aliases": list(company.get("aliases") or []),
        "registry": True,
    }


def metadata_text(company):
    return json.dumps(build_metadata(company), sort_keys=True)


def collision_key(name):
    """One key for every spelling of a company name, to tell whether two names are one company.

    supplier_dependence.company_key (one key for every listing of a company: share classes,
    warrants, parentheticals) with the legal form dropped as well, so "OpenAI, Inc." and
    "OpenAI Global, LLC" are not told apart from "OpenAI" by their suffix alone.
    """
    # Imported here, not at the top: the filing readers (supplier_dependence among them) import
    # this module, and a module-level import back would make the two circular.
    from supplier_dependence import company_key

    # "L.L.C." and "N.V." are one token each; company_key would split them into letters.
    text = re.sub(r"\b([A-Za-z])\.", r"\1", str(name or ""))
    words = company_key(text).split()
    while len(words) > 1 and words[-1] in LEGAL_FORM_WORDS:
        words.pop()
    return " ".join(words)


def company_names(company):
    """The display name followed by its aliases."""
    return [company.get("name"), *(company.get("aliases") or [])]


class PublicIndex:
    """The public universe's tickers and names, to refuse a registry entry that is already in it."""

    def __init__(self, companies):
        """`companies`: (ticker, name) of every public company."""
        self.tickers = {}
        self.names = defaultdict(list)
        for ticker, name in companies:
            symbol = str(ticker or "").strip().upper()
            if symbol:
                self.tickers[symbol] = name
            key = collision_key(name)
            if key:
                self.names[key].append((symbol, name))

    def collisions(self, company):
        """Why this registry entry is a company the public universe already holds (empty when it is not).

        The node named by `listed_as` is exempt: it is the same company on purpose, and the
        seeder is about to fold the private node into it.
        """
        graduating = str(company.get("listed_as") or "").strip().upper()
        found = []
        company_id = str(company.get("id") or "").strip().upper()
        if company_id in self.tickers:
            found.append(f"id {company_id} is the ticker of public company {self.tickers[company_id]!r}")
        reported = set()
        labelled = [("name", company.get("name")), *(("alias", alias) for alias in company.get("aliases") or [])]
        for kind, value in labelled:
            for symbol, public_name in self.names.get(collision_key(value), []):
                if symbol != graduating and (value, symbol) not in reported:
                    reported.add((value, symbol))
                    found.append(f"{kind} {value!r} is the same company as public {symbol} ({public_name!r})")
        return found


def public_companies_from_dashboard(dashboard):
    """(ticker, name) of every public company a dashboard export publishes."""
    rows = []
    for companies in (dashboard.get("industries") or {}).values():
        for company in companies if isinstance(companies, list) else []:
            if isinstance(company, dict) and company.get("ticker") and not is_private_entry(company):
                rows.append((company["ticker"], company.get("name") or ""))
    return rows


# --- Validation ---------------------------------------------------------------------------

def _clean_text(value):
    return (
        isinstance(value, str) and value == " ".join(value.split()) and value != ""
        and not CONTROL_CHARACTERS.search(value) and not MARKUP_CHARACTERS.search(value)
    )


def _text_problem(entry, field, maximum, required=True):
    value = entry.get(field)
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value.strip():
        return f"{field} must be a non-empty string"
    if not _clean_text(value):
        return f"{field} must be one line of plain text (no repeated spaces, newlines, control characters or angle brackets)"
    if len(value) > maximum:
        return f"{field} is {len(value)} characters; the limit is {maximum}"
    return None


def http_url(value, https_only=False):
    """The URL when it is a plain http(s) URL with a host and no embedded credentials, else None."""
    if not isinstance(value, str) or value != value.strip() or re.search(r"\s", value) or len(value) > 300:
        return None
    try:
        parsed = urlparse(value)
    except ValueError:
        return None
    allowed = ("https",) if https_only else ("http", "https")
    if parsed.scheme not in allowed or not parsed.hostname or "@" in parsed.netloc:
        return None
    return value


def _valid_date(value, today):
    if not isinstance(value, str) or not DATE_PATTERN.match(value):
        return False
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return False
    # A day of slack: the date is written in the maintainer's timezone, CI runs in UTC.
    return parsed <= today + timedelta(days=1)


def entry_problems(entry, today=None):
    """What is wrong with one registry entry on its own (cross-entry checks are in registry_problems)."""
    today = today or date.today()
    if not isinstance(entry, dict):
        return ["must be an object"]
    problems = []
    unknown = sorted(set(entry) - REGISTRY_FIELDS)
    if unknown:
        problems.append(f"unknown field(s) {unknown}; allowed: {sorted(REGISTRY_FIELDS)}")
    missing = sorted(REQUIRED_REGISTRY_FIELDS - set(entry))
    if missing:
        problems.append(f"missing field(s) {missing}")

    company_id = entry.get("id")
    if "id" in entry and not (isinstance(company_id, str) and ID_PATTERN.match(company_id)):
        problems.append(f"id {company_id!r} must be 6-14 capital letters or digits starting with a letter (a real ticker is never longer than 5)")
    if "name" in entry:
        problem = _text_problem(entry, "name", MAX_NAME_LENGTH)
        if problem:
            problems.append(problem)
        elif not collision_key(entry["name"]):
            problems.append("name has no letters or digits")

    aliases = entry.get("aliases", [])
    if not isinstance(aliases, list):
        problems.append("aliases must be a list")
        aliases = []
    own_key = collision_key(entry.get("name")) if isinstance(entry.get("name"), str) else ""
    for alias in aliases:
        if not isinstance(alias, str) or not _clean_text(alias) or len(alias) > MAX_NAME_LENGTH:
            problems.append(f"alias {alias!r} must be one line of plain text of at most {MAX_NAME_LENGTH} characters")
            continue
        key = collision_key(alias)
        if not key:
            problems.append(f"alias {alias!r} has no letters or digits")
        elif len(key.split()) < 2 and key != own_key:
            # "Scale", "Ramp", "Plaid" and "Mistral" are ordinary words before they are companies.
            problems.append(f"alias {alias!r} is a single word; use the full name filings print (for example 'Scale AI', not 'Scale')")
    if len({alias.lower() for alias in aliases if isinstance(alias, str)}) != len(aliases):
        problems.append("aliases repeat")

    for field in ("sector", "industry"):
        if field in entry:
            problem = _text_problem(entry, field, MAX_NAME_LENGTH)
            if problem:
                problems.append(problem)
            elif entry[field].strip().lower() in PLACEHOLDER_VALUES:
                problems.append(f"{field} {entry[field]!r} is a placeholder; name the real {field}")
    sector = entry.get("sector")
    if isinstance(sector, str) and sector.strip().lower() in IGNORED_SECTORS:
        problems.append(f"sector {sector!r} is dropped by the export, so the company would never be published; file it under the sector it operates in (Technology, Industrials, ...)")

    if "description" in entry:
        problem = _text_problem(entry, "description", MAX_DESCRIPTION_LENGTH)
        if problem:
            problems.append(problem)
        else:
            figure = FIGURE_PATTERN.search(entry["description"])
            if figure:
                problems.append(f"description mentions {figure.group(0).strip()!r}; no valuation, funding, revenue or headcount figures are stored for a private company")
            superlative = SUPERLATIVE_PATTERN.search(entry["description"])
            if superlative:
                problems.append(f"description says {superlative.group(0)!r}; state what the company does, without superlatives")

    website = entry.get("website")
    if website is not None and http_url(website, https_only=True) is None:
        problems.append(f"website {website!r} must be an https URL or null")
    problem = _text_problem(entry, "hq", MAX_HQ_LENGTH, required=False)
    if problem:
        problems.append(problem)
    founded = entry.get("founded")
    if founded is not None and (isinstance(founded, bool) or not isinstance(founded, int) or not FIRST_FOUNDING_YEAR <= founded <= today.year):
        problems.append(f"founded {founded!r} must be a year between {FIRST_FOUNDING_YEAR} and {today.year}, or null")

    sources = entry.get("sources")
    if "sources" in entry:
        if not isinstance(sources, list) or not sources:
            problems.append("sources must be a non-empty list of URLs that back the name, HQ, founding year and website")
        else:
            problems.extend(f"source {source!r} must be an http(s) URL" for source in sources if http_url(source) is None)
    if "checked" in entry and not _valid_date(entry["checked"], today):
        problems.append(f"checked {entry['checked']!r} must be an ISO date (YYYY-MM-DD) that is not in the future")

    listed_as = entry.get("listed_as")
    if listed_as is not None:
        if not (isinstance(listed_as, str) and TICKER_PATTERN.match(listed_as)):
            problems.append(f"listed_as {listed_as!r} must be a ticker or null")
        elif listed_as == company_id:
            problems.append("listed_as must be the public ticker, not the registry id")
    return problems


def registry_problems(payload, public_index=None, today=None):
    """Every problem in a registry payload, as readable strings (empty when it is valid).

    `public_index` (a PublicIndex) adds the check that no entry is already a public company.
    """
    if not isinstance(payload, dict) or payload.get("version") != 1 or not isinstance(payload.get("companies"), list):
        return ['the file must be an object {"version": 1, "companies": [...]}']
    problems = [f"unknown top-level field {key!r}" for key in sorted(set(payload) - {"version", "companies"})]
    owners = {}
    ids = {}
    for position, entry in enumerate(payload["companies"], start=1):
        label = entry.get("id") if isinstance(entry, dict) and isinstance(entry.get("id"), str) else f"entry {position}"
        problems.extend(f"{label}: {problem}" for problem in entry_problems(entry, today))
        if not isinstance(entry, dict):
            continue
        if isinstance(entry.get("id"), str):
            if entry["id"] in ids:
                problems.append(f"{label}: id is used by entry {ids[entry['id']]} as well")
            ids.setdefault(entry["id"], position)
        names = [name for name in company_names(entry) if isinstance(name, str)]
        for name in names:
            key = collision_key(name)
            if not key:
                continue
            other = owners.setdefault(key, (label, name))
            if other[0] != label:
                problems.append(f"{label}: {name!r} is the same company as {other[1]!r} of {other[0]} (names and aliases must be unique across companies)")
        if public_index is not None:
            problems.extend(f"{label}: {problem}" for problem in public_index.collisions(entry))
    return problems


def edge_entry_problems(entry, today=None):
    """What is wrong with one curated seed edge on its own, without the database."""
    today = today or date.today()
    if not isinstance(entry, dict):
        return ["must be an object"]
    # Imported here: evidence_quality reads private_companies for registry aliases.
    from evidence_quality import has_usable_evidence, is_non_supply_label, is_role_label, requires_source_evidence

    problems = []
    unknown = sorted(set(entry) - EDGE_FIELDS)
    if unknown:
        problems.append(f"unknown field(s) {unknown}; allowed: {sorted(EDGE_FIELDS)}")
    missing = sorted(REQUIRED_EDGE_FIELDS - set(entry))
    if missing:
        problems.append(f"missing field(s) {missing}")
    for field in ("source", "target"):
        if field in entry and not (isinstance(entry[field], str) and TICKER_PATTERN.match(entry[field])):
            problems.append(f"{field} {entry[field]!r} must be a ticker")
    if entry.get("source") is not None and entry.get("source") == entry.get("target"):
        problems.append("source and target are the same company")
    if "dependency_type" in entry:
        problem = _text_problem(entry, "dependency_type", MAX_NAME_LENGTH)
        if problem:
            problems.append(problem)
        elif is_role_label(entry["dependency_type"]) or is_non_supply_label(entry["dependency_type"]):
            problems.append(f"dependency_type {entry['dependency_type']!r} is not a supply relationship (investments, ownership and partnerships are not supply edges)")
    problem = _text_problem(entry, "product", MAX_NAME_LENGTH, required=False)
    if problem:
        problems.append(problem)
    url = entry.get("source_url")
    if "source_url" in entry:
        if http_url(url) is None or not requires_source_evidence(url):
            problems.append(f"source_url {url!r} must be the real http(s) URL of the page the excerpt comes from, never a label such as 'Manual ...'")
    if "source_title" in entry:
        problem = _text_problem(entry, "source_title", 200)
        if problem:
            problems.append(problem)
        elif entry["source_title"].lower().startswith("manual"):
            problems.append("source_title must name the source document, not a 'Manual ...' label")
    if "evidence_excerpt" in entry:
        excerpt = entry["evidence_excerpt"]
        if not isinstance(excerpt, str) or not has_usable_evidence(excerpt):
            problems.append("evidence_excerpt must be the verbatim sentence(s) from the source page (at least 20 characters)")
        elif len(excerpt) > MAX_EXCERPT_LENGTH or CONTROL_CHARACTERS.search(excerpt.replace("\n", " ").replace("\t", " ")):
            problems.append(f"evidence_excerpt must be plain text of at most {MAX_EXCERPT_LENGTH} characters")
    if "checked" in entry and not _valid_date(entry["checked"], today):
        problems.append(f"checked {entry['checked']!r} must be an ISO date (YYYY-MM-DD) that is not in the future")
    return problems


def edge_node_problems(entry, source_node, target_node):
    """Why the excerpt of a seed edge cannot support it between these two nodes (empty when it can).

    The same tests the review step applies first (evidence_quality), so an edge that passes
    here is not rejected or held by the pipeline's own rules the same night.
    """
    from evidence_quality import evidence_direction, evidence_support, mentions_company

    excerpt = " ".join(str(entry.get("evidence_excerpt") or "").split())
    problems = []
    for role, node in (("source", source_node), ("target", target_node)):
        if not mentions_company(excerpt, node):
            problems.append(f"the excerpt does not name {role} {node.ticker} ({node.name}) by its name or an alias")
    if problems:
        return problems
    verdict, reason = evidence_support(excerpt, source_node, target_node)
    if verdict == "unsupported":
        return [f"the pipeline would reject this excerpt: {reason}"]
    direction, phrase = evidence_direction(excerpt, source_node, target_node)
    if direction == "backward":
        return [f'the excerpt says the supply runs the other way ("{phrase[:100]}"); swap source and target']
    return []


def seed_edge_problems(payload, registry_ids=None, today=None):
    """Every problem in a seed-edge payload that does not need the database (empty when valid).

    `registry_ids`, when given, adds the check that every edge touches a registry company.
    """
    if not isinstance(payload, dict) or payload.get("version") != 1 or not isinstance(payload.get("edges"), list):
        return ['the file must be an object {"version": 1, "edges": [...]}']
    problems = [f"unknown top-level field {key!r}" for key in sorted(set(payload) - {"version", "edges"})]
    seen = {}
    for position, entry in enumerate(payload["edges"], start=1):
        label = edge_label(entry, position)
        problems.extend(f"{label}: {problem}" for problem in edge_entry_problems(entry, today))
        if not isinstance(entry, dict):
            continue
        key = (entry.get("source"), entry.get("target"), entry.get("dependency_type"))
        if key in seen:
            problems.append(f"{label}: repeats entry {seen[key]}")
        seen.setdefault(key, position)
        if registry_ids is not None and entry.get("source") not in registry_ids and entry.get("target") not in registry_ids:
            problems.append(f"{label}: neither end is a registry company; public-to-public relationships are found by discovery")
    return problems


def edge_label(entry, position):
    if isinstance(entry, dict) and entry.get("source") and entry.get("target"):
        return f"edge {position} ({entry['source']} -> {entry['target']})"
    return f"edge {position}"


# --- Loading ------------------------------------------------------------------------------

def read_json(path):
    """The parsed file, or None when it does not exist; RegistryError when it is not JSON."""
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        raise RegistryError([f"{path} could not be read: {exc}"]) from exc


def load_registry(path=REGISTRY_PATH, public_index=None):
    """The validated registry entries ([] when the file does not exist); RegistryError otherwise."""
    payload = read_json(path)
    if payload is None:
        return []
    problems = registry_problems(payload, public_index)
    if problems:
        raise RegistryError(problems)
    return payload["companies"]


def load_seed_edges(path=SEED_EDGES_PATH):
    """The edge entries of the seed file ([] when it does not exist); RegistryError when its shape is wrong.

    Entries are not validated here: the seeder judges each on its own and skips the bad ones.
    """
    payload = read_json(path)
    if payload is None:
        return []
    if not isinstance(payload, dict) or payload.get("version") != 1 or not isinstance(payload.get("edges"), list):
        raise RegistryError(['the file must be an object {"version": 1, "edges": [...]}'])
    return payload["edges"]


def registry_node(company):
    """A node-shaped stand-in for a registry entry, for the checks that read names and aliases."""
    return SimpleNamespace(
        name=company["name"], ticker=company["id"], sector=company.get("sector"), industry=company.get("industry"),
        entity_type=PRIVATE_ENTITY_TYPE, metadata_json=metadata_text(company),
    )


def database_public_companies(path):
    """(ticker, name) of the public companies in a SQLite graph file."""
    connection = sqlite3.connect(f"{Path(path).resolve().as_uri()}?mode=ro", uri=True)
    try:
        return connection.execute(
            "SELECT ticker, name FROM nodes WHERE ticker IS NOT NULL AND entity_type != ?", (PRIVATE_ENTITY_TYPE,)
        ).fetchall()
    finally:
        connection.close()


# --- Command line -------------------------------------------------------------------------

def validate_files(registry_path=REGISTRY_PATH, edges_path=SEED_EDGES_PATH, dashboard_path=DASHBOARD_PATH, database_path=None):
    """Problems in the registry and seed-edge files, as readable strings.

    The public universe the registry must not repeat comes from the published dashboard (and
    from a database file when one is given): CI has no database, the nightly seeder checks the
    real one.
    """
    problems = []
    public = []
    try:
        if dashboard_path and os.path.exists(dashboard_path):
            public.extend(public_companies_from_dashboard(read_json(dashboard_path) or {}))
        if database_path:
            public.extend(database_public_companies(database_path))
    except (RegistryError, sqlite3.Error) as exc:
        problems.append(f"could not read the public companies to compare with: {exc}")
    try:
        registry, edges = read_json(registry_path), read_json(edges_path)
    except RegistryError as exc:
        return problems + exc.problems
    if registry is None:
        return problems + [f"{registry_path} does not exist"]
    problems.extend(registry_problems(registry, PublicIndex(public)))
    if edges is None:
        return problems
    companies = registry.get("companies") if isinstance(registry, dict) and isinstance(registry.get("companies"), list) else []
    companies = [company for company in companies if isinstance(company, dict) and isinstance(company.get("id"), str)]
    problems.extend(seed_edge_problems(edges, {company["id"] for company in companies}))
    problems.extend(_edge_name_problems(edges, companies, public))
    return problems


def _edge_name_problems(edges, companies, public):
    """The excerpt of each seed edge must name both ends, as far as the ends are known here."""
    from evidence_quality import registered_company_names

    if not isinstance(edges, dict) or not isinstance(edges.get("edges"), list):
        return []
    nodes = {company["id"]: registry_node(company) for company in companies if isinstance(company.get("name"), str)}
    for ticker, name in public:
        nodes.setdefault(str(ticker).upper(), SimpleNamespace(name=name, ticker=ticker, entity_type="Company", metadata_json=None))
    problems = []
    with registered_company_names([(node.ticker, node.name) for node in nodes.values()]):
        for position, entry in enumerate(edges["edges"], start=1):
            if not isinstance(entry, dict) or edge_entry_problems(entry):
                continue  # already reported
            source, target = nodes.get(entry.get("source")), nodes.get(entry.get("target"))
            if source is None or target is None:
                continue  # an end the files cannot see (a public company outside the dashboard); the seeder checks it against the database
            problems.extend(f"{edge_label(entry, position)}: {problem}" for problem in edge_node_problems(entry, source, target))
    return problems


def main(argv=None):
    parser = argparse.ArgumentParser(description="Check the private-company registry and its curated seed edges.")
    commands = parser.add_subparsers(dest="command", required=True)
    validate = commands.add_parser("validate", help="List every problem in the registry and seed-edge files; exit 1 if there are any.")
    validate.add_argument("path", nargs="?", default=REGISTRY_PATH, help="the registry file (default: data/private_companies.json)")
    validate.add_argument("--edges", default=SEED_EDGES_PATH, help="the seed-edge file (default: data/private_seed_edges.json)")
    validate.add_argument("--dashboard", default=DASHBOARD_PATH, help="published dashboard whose public companies the registry must not repeat")
    validate.add_argument("--db", default=None, help="also check against the public companies of this SQLite graph file")
    args = parser.parse_args(argv)

    problems = validate_files(args.path, args.edges, args.dashboard, args.db)
    if problems:
        print(f"{len(problems)} problem(s) in the private-company files:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    registry, edges = read_json(args.path) or {}, read_json(args.edges) or {}
    print(f"Private companies OK: {len(registry.get('companies', []))} registry entries, {len(edges.get('edges', []))} seed edges.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
