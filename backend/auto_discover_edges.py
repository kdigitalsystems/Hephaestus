import os
import time
import re
import argparse
import wikipedia
import warnings
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from thefuzz import fuzz

from database import SessionLocal
from models import Node, Edge
from parser import extract_dependencies
from yahooquery import search as yq_search, Ticker
from sec_sources import get_sec_exhibit_supply_chain_text, get_sec_supply_chain_text
from additional_sources import get_additional_supply_chain_text
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
CONTEXT_MAX_CHARS = int(os.environ.get("HEPHAESTUS_CONTEXT_MAX_CHARS", "15000"))
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
    if not evidence_source_url.startswith(('http://', 'https://')):
        evidence_source_url = "AI Multi-Source Research"

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
        if not existing.source_title:
            existing.source_title = "AI Multi-Source Research"
        if not existing.source_url or (
            existing.source_url == "AI Multi-Source Research" and evidence_source_url.startswith(('http://', 'https://'))
        ):
            existing.source_url = evidence_source_url
        return existing, False

    new_edge = Edge(
        source_id=source_node.id,
        target_id=target_node.id,
        dependency_type=dep_type,
        product=dep.get('product'),
        confidence_score=conf,
        source_url=evidence_source_url,
        source_title="AI Multi-Source Research",
        evidence_excerpt=dep.get('evidence_excerpt'),
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

        if target_sectors:
            query = query.filter(Node.sector.in_(target_sectors))
        else:
            IGNORED_SECTORS = ["Financial Services", "Real Estate", "Financial", "Asset Management", "Insurance", "Banks", "Shell Companies"]
            query = query.filter(~Node.sector.in_(IGNORED_SECTORS))

        # The edge outer join yields one row per incident edge; without DISTINCT the
        # limit is consumed by a few well-connected companies in --deep-dive mode.
        lonely_nodes = query.distinct().order_by(Node.market_cap.desc()).limit(limit).all()

        if not lonely_nodes:
            print("No actionable companies found in queue!")
            return

        for company in lonely_nodes:
            print(f"\n[->] Researching: {company.name} ({company.ticker}) | Sector: {company.sector}")

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
                continue

            clean_target_name = clean_company_name(company.name)
            print(f"  [*] GPU is analyzing {len(intel_blob)} characters for {company.ticker}...")

            extraction = extract_dependencies(intel_blob, target_name=clean_target_name, target_ticker=company.ticker)
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

                s_node = resolve_counterparty(session, dep.get('source_ticker'), dep.get('source_company'))
                t_node = resolve_counterparty(session, dep.get('target_ticker'), dep.get('target_company'))

                if s_node and t_node:
                    if s_node.id == t_node.id:
                        continue

                    if s_node.id != company.id and t_node.id != company.id:
                        print(f"  [!] Ignored tangential competitor link: {s_node.ticker} -> {t_node.ticker}")
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
