import json
import os
import re
from pathlib import Path
from urllib.parse import urljoin

import requests

from sec_sources import html_to_text, relevant_windows


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE_CONFIG = ROOT / "data" / "source_urls.json"
DEFAULT_USER_AGENT = os.environ.get(
    "HEPHAESTUS_SOURCE_USER_AGENT",
    "HephaestusTerminal/1.0 research@saqibdesktop.local",
)
SUPPLY_SOURCE_TERMS = (
    "supplier",
    "suppliers",
    "supply chain",
    "customer",
    "customers",
    "manufacturer",
    "manufacturing",
    "contract manufacturer",
    "foundry",
    "distribution",
    "distributor",
    "logistics",
    "raw material",
    "purchase agreement",
    "master services",
    "strategic supplier",
    "sole source",
    "single source",
)
IR_PATHS = (
    "/investors",
    "/investor-relations",
    "/investor",
    "/newsroom",
    "/news",
    "/press-releases",
)
REGULATED_SECTOR_MARKERS = (
    "automobiles",
    "auto",
    "health",
    "medical",
    "pharmaceutical",
    "biotechnology",
    "aerospace",
    "defense",
)


def bool_env(name, default=True):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}


def source_headers():
    return {
        "User-Agent": DEFAULT_USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
    }


def normalize_ticker(value):
    return str(value or "").strip().upper()


def clean_company_query(name):
    name = re.sub(r"\(.*?\)", "", str(name or ""))
    name = re.sub(
        r"\b(inc|corp|corporation|company|co|plc|ltd|llc|holdings|holding|group|sa|ag)\b\.?",
        "",
        name,
        flags=re.IGNORECASE,
    )
    return re.sub(r"\s+", " ", name.replace(",", " ")).strip()


def truncate_join(sections, max_chars):
    output = []
    total = 0
    for section in sections:
        if not section:
            continue
        remaining = max_chars - total
        if remaining <= 0:
            break
        output.append(section[:remaining])
        total += len(output[-1])
    return "\n".join(output)[:max_chars]


def source_section(label, company_name, ticker, text, url="", title="", max_chars=1800):
    relevant = relevant_windows(text, terms=SUPPLY_SOURCE_TERMS, max_chars=max_chars)
    if not relevant:
        return ""
    citation = title or url or label
    if url and url not in citation:
        citation = f"{citation}; {url}"
    return (
        f"SOURCE: {label} ({citation})\n"
        f"COMPANY: {company_name or ticker} ({ticker})\n"
        f"DATA:\n{relevant}\n"
    )


def fetch_url_text(url, timeout=15):
    response = requests.get(url, headers=source_headers(), timeout=timeout)
    response.raise_for_status()
    content_type = response.headers.get("content-type", "")
    if "json" in content_type:
        return json.dumps(response.json(), ensure_ascii=True)
    return html_to_text(response.text)


def load_source_config(path=None):
    path = Path(path or os.environ.get("HEPHAESTUS_SOURCE_CONFIG") or DEFAULT_SOURCE_CONFIG)
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def configured_entries(config, ticker):
    ticker = normalize_ticker(ticker)
    if not config:
        return []
    if isinstance(config, list):
        return config
    sources = config.get("sources", config)
    entries = []
    entries.extend(sources.get(ticker, []))
    entries.extend(sources.get("*", []))
    return entries


def configured_source_text(ticker, company_name="", max_urls=4, max_chars=3500, config=None):
    config = load_source_config() if config is None else config
    sections = []
    for entry in configured_entries(config, ticker)[:max_urls]:
        url = entry.get("url") if isinstance(entry, dict) else str(entry)
        if not url:
            continue
        title = entry.get("title") if isinstance(entry, dict) else url
        label = entry.get("source_type", "Configured Web Source") if isinstance(entry, dict) else "Configured Web Source"
        try:
            text = fetch_url_text(url)
        except requests.RequestException as exc:
            print(f"  [-] Configured source unavailable for {ticker}: {url} ({exc})")
            continue
        sections.append(source_section(label, company_name, ticker, text, url=url, title=title))
    return truncate_join(sections, max_chars)


def yahoo_company_website(ticker):
    try:
        from yahooquery import Ticker

        profile = Ticker(ticker).summary_profile
        if isinstance(profile, dict):
            details = profile.get(ticker) or profile.get(normalize_ticker(ticker)) or {}
            return details.get("website") or ""
    except Exception as exc:
        print(f"  [-] Yahoo profile unavailable for {ticker}: {exc}")
    return ""


def candidate_ir_urls(website):
    if not website:
        return []
    if not website.startswith(("http://", "https://")):
        website = f"https://{website}"
    urls = [website]
    urls.extend(urljoin(website.rstrip("/") + "/", path.lstrip("/")) for path in IR_PATHS)
    return list(dict.fromkeys(urls))


def company_ir_text(ticker, company_name="", website="", max_urls=3, max_chars=3500):
    website = website or yahoo_company_website(ticker)
    sections = []
    for url in candidate_ir_urls(website)[:max_urls]:
        try:
            text = fetch_url_text(url)
        except requests.RequestException:
            continue
        section = source_section("Company IR / Website", company_name, ticker, text, url=url)
        if section:
            sections.append(section)
    return truncate_join(sections, max_chars)


def usaspending_payload(company_name, limit=5):
    return {
        "filters": {
            "recipient_search_text": [clean_company_query(company_name) or company_name],
            "award_type_codes": ["A", "B", "C", "D"],
        },
        "fields": [
            "Award ID",
            "Recipient Name",
            "Start Date",
            "End Date",
            "Award Amount",
            "Awarding Agency",
            "Award Description",
        ],
        "page": 1,
        "limit": limit,
        "sort": "Award Amount",
        "order": "desc",
    }


def usaspending_text(ticker, company_name="", max_awards=5, max_chars=2600):
    if not company_name:
        return ""
    url = "https://api.usaspending.gov/api/v2/search/spending_by_award/"
    try:
        response = requests.post(
            url,
            json=usaspending_payload(company_name, limit=max_awards),
            headers=source_headers(),
            timeout=20,
        )
        response.raise_for_status()
        rows = response.json().get("results", [])
    except requests.RequestException as exc:
        print(f"  [-] USAspending unavailable for {ticker}: {exc}")
        return ""

    lines = []
    for row in rows[:max_awards]:
        description = row.get("Award Description") or row.get("Description") or ""
        agency = row.get("Awarding Agency") or ""
        amount = row.get("Award Amount") or ""
        award_id = row.get("Award ID") or ""
        lines.append(
            f"Award {award_id}: {row.get('Recipient Name') or company_name} received "
            f"{amount} from {agency} for {description}."
        )
    text = "\n".join(lines)
    return source_section("Government Procurement - USAspending", company_name, ticker, text, url=url, max_chars=max_chars)


def nhtsa_manufacturer_text(ticker, company_name="", sector="", industry="", max_chars=1600):
    haystack = f"{sector} {industry}".lower()
    if not any(marker in haystack for marker in ("auto", "automobile", "vehicle", "truck", "motor")):
        return ""
    query = clean_company_query(company_name)
    if not query:
        return ""
    url = f"https://vpic.nhtsa.dot.gov/api/vehicles/GetManufacturerDetails/{query}?format=json"
    try:
        response = requests.get(url, headers=source_headers(), timeout=15)
        response.raise_for_status()
        rows = response.json().get("Results", [])
    except requests.RequestException as exc:
        print(f"  [-] NHTSA unavailable for {ticker}: {exc}")
        return ""

    lines = []
    for row in rows[:4]:
        lines.append(
            f"{row.get('Mfr_Name') or company_name}: {row.get('PrincipalFirstName') or ''} "
            f"{row.get('VehicleTypes') or ''} {row.get('Mfr_CommonName') or ''} "
            f"{row.get('Country') or ''} {row.get('StateProvince') or ''}"
        )
    return source_section("Regulatory Dataset - NHTSA", company_name, ticker, "\n".join(lines), url=url, max_chars=max_chars)


def yahoo_news_article_text(ticker, company_name="", max_articles=2, max_chars=2600):
    if not bool_env("HEPHAESTUS_FETCH_NEWS_ARTICLES", default=False):
        return ""
    try:
        from yahooquery import Ticker

        articles = Ticker(ticker).news(count=max_articles)
    except Exception as exc:
        print(f"  [-] Yahoo article fetch unavailable for {ticker}: {exc}")
        return ""

    sections = []
    for article in articles[:max_articles]:
        url = article.get("link") or article.get("url")
        if not url:
            continue
        try:
            text = fetch_url_text(url)
        except requests.RequestException:
            continue
        sections.append(
            source_section(
                "Press / News Article",
                company_name,
                ticker,
                text,
                url=url,
                title=article.get("title") or url,
                max_chars=max_chars // max(1, max_articles),
            )
        )
    return truncate_join(sections, max_chars)


def get_additional_supply_chain_text(company_name, ticker, sector="", industry="", max_chars=8500):
    sections = []
    if bool_env("HEPHAESTUS_USE_CONFIGURED_SOURCE_URLS", default=True):
        sections.append(configured_source_text(ticker, company_name=company_name))
    if bool_env("HEPHAESTUS_USE_IR_SOURCES", default=True):
        sections.append(company_ir_text(ticker, company_name=company_name))
    if bool_env("HEPHAESTUS_USE_PROCUREMENT_SOURCE", default=True):
        sections.append(usaspending_text(ticker, company_name=company_name))
    if bool_env("HEPHAESTUS_USE_REGULATORY_SOURCE", default=True):
        sections.append(nhtsa_manufacturer_text(ticker, company_name=company_name, sector=sector, industry=industry))
    sections.append(yahoo_news_article_text(ticker, company_name=company_name))
    return truncate_join(sections, max_chars)
