import json
import os
import time
import re
import argparse
from datetime import datetime, timedelta, timezone
import wikipedia
import warnings
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from thefuzz import fuzz

from database import SessionLocal
from models import Node, Edge
from parser import extract_dependencies
from yahooquery import search as yq_search, Ticker
from sec_sources import get_sec_exhibit_supply_chain_text, get_sec_supply_chain_text, latest_annual_filing
from additional_sources import get_additional_supply_chain_text
from customer_concentration import describe_share, extract_disclosures
from evidence_quality import (
    has_non_supply_relationship,
    has_usable_evidence,
    is_customer_role_label,
    is_role_label,
    is_supplier_role_label,
)

# --- CONFIGURATION ---
wikipedia.set_user_agent("HephaestusTerminal/1.0 (research@saqibdesktop.local)")
warnings.filterwarnings("ignore", category=UserWarning, module='wikipedia')
USE_SEC_SOURCE = os.environ.get("HEPHAESTUS_USE_SEC_SOURCE", "1") != "0"
USE_SEC_EXHIBITS = os.environ.get("HEPHAESTUS_USE_SEC_EXHIBITS", "1") != "0"
USE_ADDITIONAL_SOURCES = os.environ.get("HEPHAESTUS_USE_ADDITIONAL_SOURCES", "1") != "0"
# Deterministic extraction of "customers >= 10% of revenue" disclosures from annual reports.
USE_CUSTOMER_CONCENTRATION = os.environ.get("HEPHAESTUS_USE_CUSTOMER_CONCENTRATION", "1") != "0"
CONCENTRATION_CONFIDENCE = 0.9
CONTEXT_MAX_CHARS = int(os.environ.get("HEPHAESTUS_CONTEXT_MAX_CHARS", "15000"))
# Wall-clock budget for the discovery loop (0 = unlimited). The scheduled job has
# a fixed timeout, and a queue that does not fit in it starves the review, export,
# and publish steps that follow: nothing reaches the site at all.
DISCOVERY_MAX_SECONDS = float(os.environ.get("HEPHAESTUS_DISCOVERY_MAX_SECONDS", "0"))
# Machine-readable run summary, published to the site by write_status.py.
DISCOVERY_SUMMARY_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "reports", "discovery_summary.json")


def write_discovery_summary(summary, path=DISCOVERY_SUMMARY_PATH):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(summary, handle, indent=2, sort_keys=True)


# A company whose sources yielded nothing stays edgeless, and without a cooldown it
# would be researched again every single day while the rest of the queue waits.
RESEARCH_COOLDOWN_DAYS = float(os.environ.get("HEPHAESTUS_RESEARCH_COOLDOWN_DAYS", "30"))
# Customer-concentration disclosures come from the annual filing and need no GPU, so
# they are swept over the whole universe independently of the LLM queue.
CONCENTRATION_SWEEP_LIMIT = int(os.environ.get("HEPHAESTUS_CONCENTRATION_SWEEP_LIMIT", "0"))
CONCENTRATION_SWEEP_MAX_SECONDS = float(os.environ.get("HEPHAESTUS_CONCENTRATION_SWEEP_MAX_SECONDS", "900"))
CONCENTRATION_RECHECK_DAYS = float(os.environ.get("HEPHAESTUS_CONCENTRATION_RECHECK_DAYS", "120"))
IGNORED_SECTORS = ["Financial Services", "Real Estate", "Financial", "Asset Management", "Insurance", "Banks", "Shell Companies"]


def not_in_ignored_sector():
    """SQL's NOT IN is NULL for a NULL sector, which silently excluded such companies."""
    return or_(Node.sector.is_(None), ~Node.sector.in_(IGNORED_SECTORS))


def utc_now():
    return datetime.now(timezone.utc)


def is_due(checked_at, every_days, now=None):
    """True when a timestamp is missing or older than the interval (0 = always due)."""
    if not every_days or every_days <= 0 or checked_at is None:
        return True
    now = now or utc_now()
    if checked_at.tzinfo is None:
        checked_at = checked_at.replace(tzinfo=timezone.utc)
    return checked_at < now - timedelta(days=every_days)


def budget_exhausted(started, max_seconds, now=None):
    """True once the discovery loop has used its wall-clock budget."""
    if not max_seconds or max_seconds <= 0:
        return False
    now = time.monotonic() if now is None else now
    return (now - started) >= max_seconds
# Wikipedia is the lowest-quality source; without its own budget it could consume the
# whole context window and push the SEC filing text out entirely.
WIKI_MAX_CHARS = int(os.environ.get("HEPHAESTUS_WIKI_MAX_CHARS", "4000"))
# A ticker supplied by the model is only trusted when it names the same company.
NAME_MATCH_MIN_SCORE = 60
# An excerpt must quote the collected source text (allowing minor rewording).
EXCERPT_MATCH_MIN_SCORE = 85

def clean_company_name(name):
    """Aggressively strips Wall Street jargon, ADRs, and geographic tags."""
    name = re.sub(r'\(.*?\)', '', name)
    name = re.sub(
        r'(American Depositary|Sponsored ADR|Unsponsored ADR|ADR|Representing|Each representing).*',
        '',
        name,
        flags=re.IGNORECASE,
    )

    stopwords = [
        r'\bInc\.?(?=\s|,|$)', r'\bCorp\.?(?=\s|,|$)', r'\bCorporation\b', r'\bCompany\b',
        r'\bLLC\b', r'\bPlc\b', r'\bLtd\.?(?=\s|,|$)', r'\bCommon Stock\b',
        r'\bClass A\b', r'\bClass B\b', r'\bOrdinary Shares\b', r'\bTrust\b',
        r'\bHoldings\b', r'\bHolding\b', r'\bGroup\b', r'\bS A\b', r'\bAG\b'
    ]
    clean_name = name
    for word in stopwords:
        clean_name = re.sub(word, '', clean_name, flags=re.IGNORECASE)

    clean_name = clean_name.replace(',', '')
    clean_name = re.sub(r'\s+', ' ', clean_name)
    return clean_name.strip()

def is_reversed_role_dependency(dependency_type):
    """Detect bare role labels ("Customer", "Major Buyer") that usually mean the LLM emitted customer -> supplier.

    Descriptive labels that merely contain a role word, such as "Customer Support
    Outsourcing" or "End-User Hardware", name a real service and keep their direction.
    """
    return is_role_label(dependency_type)

def is_non_supply_dependency(dependency_type=None, product=None, evidence=None):
    """Non-supply words are matched against the relationship label only.

    Products and verbatim filing excerpts only trigger on explicit phrases, because
    words like "acquired", "competition" or "partnership" are ordinary in 10-K prose
    that describes genuine supply relationships.
    """
    return has_non_supply_relationship(dependency_type, product, evidence)


def normalized_text(value):
    return " ".join(str(value or "").lower().split())


def excerpt_supported_by_source(excerpt, source_text):
    """The published guarantee is that AI evidence quotes the collected source text."""
    needle = normalized_text(excerpt)
    haystack = normalized_text(source_text)
    if not needle or not haystack:
        return False
    if needle in haystack:
        return True
    return fuzz.partial_ratio(needle, haystack) >= EXCERPT_MATCH_MIN_SCORE


SOURCE_HEADER_URL_PATTERN = re.compile(r"^SOURCE:.*?(https?://[^\s()]+)", re.MULTILINE)


def collector_source_urls(source_text):
    """URLs that appear in our own SOURCE header lines, not in scraped page bodies."""
    return set(SOURCE_HEADER_URL_PATTERN.findall(str(source_text or "")))


def verified_source_url(url, source_text):
    """Only keep a provenance URL that one of our collectors actually emitted.

    Scraped pages can contain text shaped like a SOURCE header; a URL the model did
    not copy from our own headers would otherwise be published as trusted provenance.
    """
    url = str(url or "").strip().rstrip(".,;)")
    if url.startswith(("http://", "https://")) and url in collector_source_urls(source_text):
        return url
    return None


DEFAULT_SOURCE_TITLE = "AI Multi-Source Research"


def collector_source_title(url, source_text):
    """Human-readable citation for a verified URL, taken from our own SOURCE header.

    "SOURCE: SEC EDGAR (10-K filed 2025-10-31, https://…)" becomes
    "SEC EDGAR (10-K filed 2025-10-31)".
    """
    if not url:
        return DEFAULT_SOURCE_TITLE
    for line in str(source_text or "").splitlines():
        if not line.startswith("SOURCE:") or url not in line:
            continue
        title = line[len("SOURCE:"):].strip()
        title = re.sub(r"[;,]?\s*" + re.escape(url), "", title)
        title = re.sub(r"\(\s*\)", "", title)
        title = " ".join(title.split()).strip(" ;,")
        return title or DEFAULT_SOURCE_TITLE
    return DEFAULT_SOURCE_TITLE


def has_invalid_dependency_label(value):
    return str(value or "").strip().lower() in {"news", "unknown"}


def is_speculative_dependency(*labels):
    label_text = " ".join(str(label or "") for label in labels).strip().lower()
    speculative_markers = [
        "likely",
        "might",
        "may be",
        "not explicitly stated",
        "no evidence",
        "suggesting",
        "would be",
        "would use",
        "not found in source text",
    ]
    return any(marker in label_text for marker in speculative_markers)

def normalize_dependency(dep):
    """Keep edge direction as supplier/provider -> customer/receiver.

    A customer-side role label ("Customer") almost always means the model emitted
    customer -> supplier, so the endpoints are swapped. A supplier-side role label
    ("Supplier", "Service Provider") is wrong in either direction about as often,
    so the direction is left for the reviewer; only the useless label is replaced.
    """
    dep = dict(dep)
    dependency_type = dep.get("dependency_type")
    if is_customer_role_label(dependency_type):
        dep["source_company"], dep["target_company"] = dep.get("target_company"), dep.get("source_company")
        dep["source_ticker"], dep["target_ticker"] = dep.get("target_ticker"), dep.get("source_ticker")
        dep["dependency_type"] = "Supply Relationship"
    elif is_supplier_role_label(dependency_type):
        dep["dependency_type"] = "Supply Relationship"
    return dep

def known_company_names(session):
    """Display name -> cleaned name for every listed company, for concentration matching."""
    names = {}
    for name, in session.query(Node.name).filter(Node.ticker.is_not(None)).all():
        cleaned = clean_company_name(str(name or ""))
        if cleaned:
            names[name] = cleaned
    return names


def discover_customer_concentration(session, company, known_names):
    """Create pending edges from the filer's own 10% customer disclosures.

    The filer is the supplier and the named customer the receiver; the disclosed
    share of revenue is stored on the edge so the dashboard can show magnitude.
    """
    if not USE_CUSTOMER_CONCENTRATION:
        return 0
    try:
        filing, text = latest_annual_filing(company.ticker)
    except Exception as exc:
        print(f"  [-] Customer concentration source unavailable for {company.ticker}: {exc}")
        return 0
    if filing and not text:
        # The filing exists but its document could not be read (a 403 burst, a timeout).
        # Leaving the stamp unset keeps the company in the queue instead of silencing
        # it for the whole recheck interval.
        print(f"  [-] Customer concentration document unreadable for {company.ticker}; will retry.")
        return 0
    # Stamped once the lookup completed, including "no annual filing on file", so such a
    # company stops occupying a sweep slot on every run.
    company.concentration_checked_at = utc_now()
    if not filing:
        return 0

    created = 0
    filer_aliases = (company.name, clean_company_name(company.name or ""), company.ticker)
    for disclosure in extract_disclosures(text, known_names, filer_aliases):
        customer = resolve_counterparty(session, None, disclosure.customer_name)
        if not customer or customer.id == company.id:
            continue
        label = f"{filing['form']} filed {filing.get('filing_date') or 'unknown date'}"
        dep = {
            "dependency_type": "Revenue Concentration",
            "product": describe_share(disclosure.share_pct, company.ticker),
            "confidence_score": CONCENTRATION_CONFIDENCE,
            "evidence_excerpt": f"{clean_company_name(company.name or '')} ({company.ticker}) {label}: {disclosure.sentence}",
            "evidence_source_url": filing["url"],
            "evidence_source_title": f"SEC EDGAR ({label}; customer-concentration disclosure)",
            "revenue_share": disclosure.share_pct,
        }
        edge, was_created = upsert_pending_edge(session, company, customer, dep)
        if disclosure.share_pct is not None and (edge.revenue_share is None or edge.revenue_share < disclosure.share_pct):
            edge.revenue_share = disclosure.share_pct
        if was_created:
            created += 1
            print(f"  [+] CUSTOMER CONCENTRATION: {company.ticker} -> {customer.ticker} ({dep['product']})")
    return created


def sweep_customer_concentration(session, known_names, limit=CONCENTRATION_SWEEP_LIMIT, max_seconds=CONCENTRATION_SWEEP_MAX_SECONDS):
    """Check the annual filings of the largest companies not checked recently."""
    if limit <= 0 or not known_names:
        return {"checked": 0, "created": 0}
    now = utc_now()
    cutoff = now - timedelta(days=CONCENTRATION_RECHECK_DAYS)
    companies = (
        session.query(Node)
        .filter(Node.ticker.is_not(None), Node.market_cap > 1_000_000_000, not_in_ignored_sector())
        .filter(or_(Node.concentration_checked_at.is_(None), Node.concentration_checked_at < cutoff))
        .order_by(Node.market_cap.desc())
        .limit(limit)
        .all()
    )
    started = time.monotonic()
    checked = created = 0
    for company in companies:
        if budget_exhausted(started, max_seconds):
            break
        created += discover_customer_concentration(session, company, known_names)
        checked += 1
        session.commit()
    print(f"Concentration sweep: {checked} filing(s) checked, {created} disclosure edge(s) created, {time.monotonic() - started:.0f}s elapsed.")
    return {"checked": checked, "created": created}


# First words that name a category, a place, or a virtue rather than a company.
GENERIC_NAME_WORDS = {
    "general", "american", "united", "national", "international", "first", "global", "standard",
    "advanced", "universal", "allied", "consolidated", "digital", "energy", "capital", "financial",
    "north", "south", "west", "east", "royal", "pacific", "atlantic", "central", "western", "eastern",
    "southern", "northern", "alpha", "beta", "delta", "gamma", "omega", "apex", "summit", "pioneer",
    "liberty", "freedom", "great", "best", "premier", "prime", "super", "world", "texas", "california",
    "china", "japan", "taiwan", "boston", "chicago", "new", "old", "mid", "trans", "inter",
}


def loosely_mentions_company(text, node):
    """Does the excerpt name the company by any reasonable handle?

    The review step's strong-alias test (full cleaned name, its first two words, a
    ticker of three or more letters) is right for approving, but too strict for
    deciding what never reaches review: "GE supplies engines", "Ford buys batteries"
    and "Micron ships DRAM" name the company perfectly well. Here a ticker of two or
    more letters, or a distinctive first word, also counts; the review step still
    applies the strict test before anything is published.
    """
    from review_edges_with_ollama import mentions_company  # lazy: that module is the review CLI

    if mentions_company(text, node):
        return True
    lowered = " ".join(str(text or "").lower().split())
    handles = []
    ticker = str(getattr(node, "ticker", "") or "").strip().lower()
    if len(ticker) >= 2:
        handles.append(ticker)
    cleaned = clean_company_name(str(getattr(node, "name", "") or "")).lower()
    words = re.findall(r"[a-z0-9&']+", cleaned)
    if words and len(words[0]) >= 4 and words[0] not in GENERIC_NAME_WORDS:
        handles.append(words[0])
    return any(re.search(r"(?<![a-z0-9])" + re.escape(handle) + r"(?![a-z0-9])", lowered) for handle in handles)


def discovery_hold_reason(session, source_node, target_node, excerpt):
    """Why this edge should not be created at all.

    Only clear junk is dropped here: an excerpt naming neither company by any handle,
    or a relationship whose opposite direction a human already approved. Anything
    ambiguous is created and left to the review step, which holds it for a human.
    """
    if not (loosely_mentions_company(excerpt, source_node) and loosely_mentions_company(excerpt, target_node)):
        return "excerpt does not name both companies"
    mirror = (
        session.query(Edge)
        .filter(Edge.source_id == target_node.id, Edge.target_id == source_node.id, Edge.review_status == "approved")
        .first()
    )
    if mirror:
        return f"opposite direction already approved as edge #{mirror.id}"
    return None


def upsert_pending_edge(session, source_node, target_node, dep):
    dep_type = dep.get('dependency_type') or 'Supply Link'
    try:
        conf = float(dep.get('confidence_score', 0.8))
    except (TypeError, ValueError):
        conf = 0.0
    if conf > 1:
        conf = conf / 100.0 if conf > 10 else conf / 10.0
    conf = max(0.0, min(1.0, conf))
    evidence_source_url = str(dep.get('evidence_source_url') or '').strip()
    has_citation = evidence_source_url.startswith(('http://', 'https://'))
    if not has_citation:
        evidence_source_url = DEFAULT_SOURCE_TITLE
    source_title = str(dep.get('evidence_source_title') or '').strip() if has_citation else ''
    source_title = source_title or DEFAULT_SOURCE_TITLE

    existing = session.query(Edge).filter(
        Edge.source_id == source_node.id,
        Edge.target_id == target_node.id,
        Edge.dependency_type == dep_type
    ).first()

    if existing:
        if dep.get('product') and not existing.product:
            existing.product = dep.get('product')
        if dep.get('evidence_excerpt') and not existing.evidence_excerpt:
            existing.evidence_excerpt = dep.get('evidence_excerpt')
        existing.confidence_score = max(existing.confidence_score or 0, conf)
        if not existing.source_url or (existing.source_url == DEFAULT_SOURCE_TITLE and has_citation):
            # A real citation always upgrades an uncited edge.
            existing.source_url = evidence_source_url
            existing.source_title = source_title
        elif not existing.source_title:
            existing.source_title = DEFAULT_SOURCE_TITLE
        return existing, False

    new_edge = Edge(
        source_id=source_node.id,
        target_id=target_node.id,
        dependency_type=dep_type,
        product=dep.get('product'),
        confidence_score=conf,
        source_url=evidence_source_url,
        source_title=source_title,
        evidence_excerpt=dep.get('evidence_excerpt'),
        revenue_share=dep.get('revenue_share'),
        review_status="pending"
    )
    try:
        with session.begin_nested():
            session.add(new_edge)
            session.flush()
        return new_edge, True
    except IntegrityError:
        existing = session.query(Edge).filter(
            Edge.source_id == source_node.id,
            Edge.target_id == target_node.id,
            Edge.dependency_type == dep_type
        ).first()
        if existing:
            return existing, False
        raise

# Common trade names that share no words with the listed company name.
KNOWN_NAME_ALIASES = {
    "tsmc": {"TSM"},
    "google": {"GOOG", "GOOGL"},
    "facebook": {"META"},
    "foxconn": {"HNHPF", "HNHAF"},
    "3m": {"MMM"},
}


def name_consistent(node, company_name):
    """Reject a ticker match whose company name clearly names a different business.

    Accepts a fuzzy name match, a known trade-name alias, or an all-caps acronym that
    matches the company's initials (TSMC, IBM, AMD). Anything else - including
    "Apple Inc." paired with Microsoft's ticker - is rejected.
    """
    if not node or not company_name:
        return True
    raw = str(company_name).strip()
    expected = clean_company_name(raw).lower().strip()
    actual = clean_company_name(str(node.name or "")).lower().strip()
    if not expected or not actual:
        return True
    if fuzz.token_set_ratio(expected, actual) >= NAME_MATCH_MIN_SCORE:
        return True
    ticker = str(node.ticker or "").upper()
    if ticker in KNOWN_NAME_ALIASES.get(expected, set()):
        return True
    if raw.isupper() and 2 <= len(raw) <= 6:
        if raw == ticker:
            return True
        initials = "".join(word[0] for word in re.findall(r"[A-Za-z]+", str(node.name or ""))).lower()
        return raw.lower() in initials
    return False


def resolve_counterparty(session, ticker, company_name):
    """Resolve a model-supplied (ticker, name) pair without trusting a hallucinated ticker."""
    if ticker:
        node = EntityResolver.resolve_ticker(session, ticker)
        if node and name_consistent(node, company_name):
            return node
        if node:
            print(f"  [!] Ticker {ticker} names {node.name}, not '{company_name}'; resolving by name instead.")
    if company_name:
        node = EntityResolver.resolve(session, company_name)
        if node and name_consistent(node, company_name):
            return node
    return None


class EntityResolver:
    """Dynamic Resolution Engine with Yahoo Finance API Fallback."""
    @staticmethod
    def resolve_ticker(session, ticker):
        ticker = str(ticker or "").strip().upper()
        if len(ticker) < 1:
            return None
        return session.query(Node).filter(Node.ticker == ticker).first()

    @staticmethod
    def resolve(session, name_or_ticker):
        if not name_or_ticker or len(str(name_or_ticker)) < 2:
            return None

        search_val = str(name_or_ticker).strip()
        search_upper = search_val.upper()

        node = session.query(Node).filter(Node.ticker == search_upper).first()
        if node:
            return node

        search_lower = search_val.lower()
        potentials = session.query(Node).filter(
            or_(
                Node.name.ilike(f"%{search_lower}%"),
                Node.ticker.ilike(f"%{search_lower}%")
            )
        ).filter(Node.market_cap > 100_000_000).all()

        if potentials:
            best_match = None
            highest_score = 0
            for p in potentials:
                score = fuzz.token_set_ratio(search_lower, p.name.lower())
                if score > highest_score:
                    highest_score = score
                    best_match = p
                elif abs(score - highest_score) < 5 and best_match:
                    if (p.market_cap or 0) > (best_match.market_cap or 0):
                        best_match = p
            if highest_score > 85:
                return best_match

        try:
            yq_results = yq_search(search_val)
            quotes = [quote for quote in (yq_results.get('quotes') or []) if isinstance(quote, dict)]
            equities = [quote for quote in quotes if str(quote.get('quoteType') or '').upper() in ('', 'EQUITY')]
            candidates = equities or quotes
            if candidates:
                discovered_ticker = candidates[0].get('symbol')
                if discovered_ticker:
                    node = session.query(Node).filter(Node.ticker == discovered_ticker.upper()).first()
                    if node:
                        return node
        except Exception as e:
            print(f"  [!] YahooQuery resolution failed for '{search_val}': {e}")

        return None

class IntelGatherer:
    @staticmethod
    def wiki_section(content, section, max_chars=1500):
        """Return the body of a Wikipedia section by heading, not by first word occurrence."""
        # Real headings are often compound ("== Operations and structure ==").
        heading = re.search(rf"^=+\s*{re.escape(section)}\b[^=\n]*=+\s*$", content, re.M | re.I)
        if not heading:
            return ""
        start = heading.end()
        next_heading = re.search(r"^==[^=].*?==\s*$", content[start:], re.M)
        end = start + next_heading.start() if next_heading else len(content)
        return content[start:min(end, start + max_chars)].strip()

    @staticmethod
    def get_wiki_data(company_name, ticker):
        try:
            search_term = clean_company_name(company_name)
            search_queries = [
                f"{search_term} {ticker} company",
                f"{search_term} company",
                search_term
            ]

            wiki_results = []
            for query in search_queries:
                wiki_results = wikipedia.search(query)
                if wiki_results:
                    break

            if not wiki_results:
                return ""

            try:
                page = wikipedia.page(wiki_results[0], auto_suggest=False)
            except wikipedia.DisambiguationError as e:
                page = wikipedia.page(e.options[0], auto_suggest=False)
            except wikipedia.PageError:
                return ""

            content = page.content
            target_sections = ["Operations", "Products", "Supply chain", "Partnerships", "Customers", "Infrastructure", "Manufacturing"]
            relevant_text = "\n".join(
                text
                for text in (IntelGatherer.wiki_section(content, section) for section in target_sections)
                if text
            )

            if not relevant_text:
                relevant_text = page.summary + "\n" + content[:3500]
            relevant_text = relevant_text[:WIKI_MAX_CHARS]

            return f"SOURCE: WIKIPEDIA (Page: {page.title}; {page.url})\nDATA:\n{relevant_text}\n"
        except Exception:
            return ""

    @staticmethod
    def get_yahoo_news(ticker):
        try:
            t = Ticker(ticker)
            news = t.news(count=5)
            blob = ""
            for article in news:
                article_url = article.get('link') or article.get('url') or ''
                blob += f"SOURCE: RECENT NEWS ({article.get('title')}; {article_url})\nDATA:\n{article.get('summary')}\n"
            return blob
        except Exception as e:
            print(f"  [-] Yahoo news unavailable for {ticker}: {e}")
            return ""

    @staticmethod
    def get_sec_data(company_name, ticker):
        if not USE_SEC_SOURCE:
            return ""
        try:
            return get_sec_supply_chain_text(ticker, company_name=company_name)
        except Exception as e:
            print(f"  [-] SEC data unavailable for {ticker}: {e}")
            return ""

    @staticmethod
    def get_sec_exhibits(company_name, ticker):
        if not USE_SEC_EXHIBITS:
            return ""
        try:
            return get_sec_exhibit_supply_chain_text(ticker, company_name=company_name)
        except Exception as e:
            print(f"  [-] SEC exhibits unavailable for {ticker}: {e}")
            return ""

    @staticmethod
    def get_additional_sources(company):
        if not USE_ADDITIONAL_SOURCES:
            return ""
        try:
            return get_additional_supply_chain_text(
                company.name,
                company.ticker,
                sector=company.sector,
                industry=company.industry,
            )
        except Exception as e:
            print(f"  [-] Additional sources unavailable for {company.ticker}: {e}")
            return ""

def auto_discover_supply_chain(limit=5, target_sectors=None, deep_dive=False):
    print(f"--- Starting Refined Titan Queue (Limit: {limit}) ---")
    if target_sectors:
        print(f"--- Targeting Sectors: {', '.join(target_sectors)} ---")
    if deep_dive:
        print("--- DEEP DIVE MODE: Researching heavily-connected nodes ---")

    session = SessionLocal()

    try:
        query = session.query(Node).outerjoin(
            Edge, or_(Node.id == Edge.source_id, Node.id == Edge.target_id)
        ).filter(Node.market_cap > 1_000_000_000)

        if not deep_dive:
            query = query.filter(Edge.id.is_(None))

        cooldown_cutoff = utc_now() - timedelta(days=RESEARCH_COOLDOWN_DAYS or 0)
        if not deep_dive and RESEARCH_COOLDOWN_DAYS > 0:
            query = query.filter(or_(Node.last_researched_at.is_(None), Node.last_researched_at < cooldown_cutoff))

        if target_sectors:
            query = query.filter(Node.sector.in_(target_sectors))
        else:
            query = query.filter(not_in_ignored_sector())

        # The edge outer join yields one row per incident edge; without DISTINCT the
        # limit is consumed by a few well-connected companies in --deep-dive mode.
        lonely_nodes = query.distinct().order_by(Node.market_cap.desc()).limit(limit).all()

        if not deep_dive and len(lonely_nodes) < limit and RESEARCH_COOLDOWN_DAYS > 0:
            # The queue is "companies with no edge", but the concentration sweep gives
            # edges to filers and to big named customers, which would exclude them from
            # LLM research forever. Once the edgeless companies are exhausted, top up
            # with the largest companies whose cooldown has expired.
            chosen = {node.id for node in lonely_nodes}
            top_up = (
                session.query(Node)
                .filter(Node.market_cap > 1_000_000_000, not_in_ignored_sector())
                .filter(or_(Node.last_researched_at.is_(None), Node.last_researched_at < cooldown_cutoff))
                .filter(Node.id.notin_(chosen) if chosen else True)
            )
            if target_sectors:
                top_up = top_up.filter(Node.sector.in_(target_sectors))
            extra = top_up.order_by(Node.market_cap.desc()).limit(limit - len(lonely_nodes)).all()
            if extra:
                print(f"--- Queue topped up with {len(extra)} already-linked company/companies past the research cooldown ---")
            lonely_nodes.extend(extra)

        known_names = known_company_names(session) if USE_CUSTOMER_CONCENTRATION and USE_SEC_SOURCE else {}
        # The filing sweep is independent of the LLM queue and must run even on a day
        # the queue is empty.
        sweep = sweep_customer_concentration(session, known_names)

        if not lonely_nodes:
            print("No actionable companies found in queue!")
            # Still today's summary: without it write_status.py would publish the
            # previous run's counts as if this run had researched them.
            write_discovery_summary({
                "generated_at": utc_now().isoformat(timespec="seconds"),
                "companies_analyzed": 0,
                "extraction_failures": 0,
                "deferred": 0,
                "elapsed_seconds": 0,
                "budget_seconds": DISCOVERY_MAX_SECONDS,
                "concentration_sweep": sweep,
            })
            return
        extraction_attempts = 0
        extraction_failures = 0
        last_extraction_error = ""
        started = time.monotonic()
        deferred = 0

        for index, company in enumerate(lonely_nodes):
            if budget_exhausted(started, DISCOVERY_MAX_SECONDS):
                deferred = len(lonely_nodes) - index
                print(
                    f"\n[!] Discovery time budget of {DISCOVERY_MAX_SECONDS:.0f}s reached after {index} companies; "
                    f"{deferred} deferred to the next run so review and publish still happen today."
                )
                break
            print(f"\n[->] Researching: {company.name} ({company.ticker}) | Sector: {company.sector}")

            if known_names and is_due(company.concentration_checked_at, CONCENTRATION_RECHECK_DAYS):
                concentration_edges = discover_customer_concentration(session, company, known_names)
                if concentration_edges:
                    session.commit()

            intel_blob = ""
            intel_blob += IntelGatherer.get_wiki_data(company.name, company.ticker)
            intel_blob += IntelGatherer.get_sec_data(company.name, company.ticker)
            intel_blob += IntelGatherer.get_sec_exhibits(company.name, company.ticker)
            intel_blob += IntelGatherer.get_additional_sources(company)
            intel_blob += IntelGatherer.get_yahoo_news(company.ticker)

            # THE CONTEXT CAP
            intel_blob = intel_blob[:CONTEXT_MAX_CHARS]

            if len(intel_blob) < 400:
                print(f"  [-] Insufficient data found for {company.ticker}.")
                company.last_researched_at = utc_now()
                session.commit()
                continue

            clean_target_name = clean_company_name(company.name)
            print(f"  [*] GPU is analyzing {len(intel_blob)} characters for {company.ticker}...")

            extraction = extract_dependencies(intel_blob, target_name=clean_target_name, target_ticker=company.ticker)
            extraction_attempts += 1
            if extraction.get("error"):
                extraction_failures += 1
                last_extraction_error = str(extraction["error"])
            else:
                # A failed model call is not research; the company stays in the queue.
                company.last_researched_at = utc_now()
                session.commit()
            dependencies = extraction.get("dependencies", [])

            if dependencies:
                print(f"  [AI FOUND]: {len(dependencies)} potential relationships.")
            else:
                print("  [-] No modern B2B relationships identified.")
                continue

            for dep in dependencies:
                dep = normalize_dependency(dep)
                if is_non_supply_dependency(
                    dep.get('dependency_type'),
                    dep.get('product'),
                    dep.get('evidence_excerpt'),
                ) or has_invalid_dependency_label(dep.get('dependency_type')):
                    print(f"  [!] Ignored non-supply relationship: {dep.get('dependency_type')}")
                    continue
                if is_speculative_dependency(dep.get('evidence_excerpt')):
                    print(f"  [!] Ignored speculative relationship: {dep.get('dependency_type')}")
                    continue
                if not has_usable_evidence(dep.get('evidence_excerpt')):
                    print(f"  [!] Ignored relationship without source-backed evidence: {dep.get('dependency_type')}")
                    continue
                if not excerpt_supported_by_source(dep.get('evidence_excerpt'), intel_blob):
                    print(f"  [!] Ignored relationship whose excerpt is not in the collected source text: {dep.get('dependency_type')}")
                    continue
                dep['evidence_source_url'] = verified_source_url(dep.get('evidence_source_url'), intel_blob)
                dep['evidence_source_title'] = collector_source_title(dep['evidence_source_url'], intel_blob)

                s_node = resolve_counterparty(session, dep.get('source_ticker'), dep.get('source_company'))
                t_node = resolve_counterparty(session, dep.get('target_ticker'), dep.get('target_company'))

                if s_node and t_node:
                    if s_node.id == t_node.id:
                        continue

                    if s_node.id != company.id and t_node.id != company.id:
                        print(f"  [!] Ignored tangential competitor link: {s_node.ticker} -> {t_node.ticker}")
                        continue

                    hold = discovery_hold_reason(session, s_node, t_node, dep.get('evidence_excerpt'))
                    if hold:
                        print(f"  [!] Ignored relationship {s_node.ticker} -> {t_node.ticker}: {hold}")
                        continue

                    edge, created = upsert_pending_edge(session, s_node, t_node, dep)
                    if created:
                        print(f"  [+] DYNAMICALLY LINKED: {s_node.ticker} -> {t_node.ticker} ({dep.get('product')})")
                    else:
                        print(f"  [=] Link already exists: {s_node.ticker} -> {t_node.ticker} ({edge.dependency_type})")
                else:
                    s_name = dep.get('source_company')
                    t_name = dep.get('target_company')
                    print(f"  [!] Filtered non-equity or private entity: '{s_name}' or '{t_name}'")

            session.commit()
            time.sleep(1.5)

        print("\n--- Titan Queue Complete. Refresh your dashboard to see new X-Ray data. ---")
        print(
            f"Extraction summary: {extraction_attempts} companies analyzed, {extraction_failures} extraction failure(s), "
            f"{deferred} deferred, {time.monotonic() - started:.0f}s elapsed."
        )
        write_discovery_summary({
            "generated_at": utc_now().isoformat(timespec="seconds"),
            "companies_analyzed": extraction_attempts,
            "extraction_failures": extraction_failures,
            "deferred": deferred,
            "elapsed_seconds": round(time.monotonic() - started),
            "budget_seconds": DISCOVERY_MAX_SECONDS,
            "concentration_sweep": sweep,
        })
        if extraction_attempts and extraction_failures == extraction_attempts:
            # A dead extractor must not look like a quiet day.
            print(
                "  [!] EVERY extraction failed, so discovery produced nothing. "
                f"Last error: {last_extraction_error}. Check HEPHAESTUS_EXTRACTION_MODEL and that Ollama has that model."
            )

    except Exception as e:
        print(f"CRITICAL ERROR: {e}")
        session.rollback()
        raise
    finally:
        session.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Hephaestus Supply Chain Discovery Engine")
    parser.add_argument("--limit", type=int, default=5, help="Number of companies to research")
    parser.add_argument("--sectors", nargs='*', default=None, help="Optional: Specific sectors to target")
    parser.add_argument("--deep-dive", action="store_true", help="Research companies even if they already have connections")
    args = parser.parse_args()

    auto_discover_supply_chain(limit=args.limit, target_sectors=args.sectors, deep_dive=args.deep_dive)
