import json
import os
import re
import socket
from ipaddress import ip_address
from pathlib import Path
from datetime import date, timedelta
from urllib.parse import urljoin, urlparse, urlunparse

import requests

from sec_sources import MAX_DOCUMENT_BYTES, decode_body, html_to_text, relevant_windows


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
    "component",
    "components",
    "device",
    "devices",
    "recall",
    "contract",
    "solicitation",
    "award",
    "authorization",
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
HEALTH_SECTOR_MARKERS = (
    "health",
    "medical",
    "pharmaceutical",
    "biotechnology",
    "biotech",
    "drug",
    "life sciences",
)
ELECTRONICS_SECTOR_MARKERS = (
    "technology",
    "semiconductor",
    "communication",
    "communications",
    "electronics",
    "hardware",
    "telecom",
    "wireless",
)
DEFENSE_PROCUREMENT_MARKERS = (
    "aerospace",
    "defense",
    "industrial",
    "technology",
    "engineering",
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


def quoted_query(value):
    cleaned = clean_company_query(value)
    return f'"{cleaned}"' if cleaned else ""


def sector_matches(sector="", industry="", markers=()):
    haystack = f"{sector} {industry}".lower()
    return any(marker in haystack for marker in markers)


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
    # A page with no supply-chain vocabulary at all (cookie banners, soft-404s) must
    # not become an evidence section that crowds out real sources.
    relevant = relevant_windows(text, terms=SUPPLY_SOURCE_TERMS, max_chars=max_chars, require_match=True)
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


def redacted_url(url):
    """Log only scheme and host: configured URLs may carry API keys in their query."""
    parsed = urlparse(str(url or ""))
    return f"{parsed.scheme}://{parsed.netloc}" if parsed.netloc else "<invalid url>"


def is_public_http_url(url):
    """Refuse anything that is not a public http(s) origin.

    Configured source files and redirects from third-party pages must not be able to
    make the collector read localhost services, cloud metadata, or private hosts and
    publish the response as evidence.
    """
    parsed = urlparse(str(url or ""))
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return False
    host = parsed.hostname.lower()
    if host == "localhost" or host.endswith((".local", ".internal", ".localhost")):
        return False
    try:
        addresses = {info[4][0] for info in socket.getaddrinfo(host, None)}
    except (socket.gaierror, UnicodeError):
        # Unresolvable now; the request itself will fail with a clear error.
        return True
    for address in addresses:
        try:
            candidate = ip_address(address)
        except ValueError:
            continue
        if (
            candidate.is_private
            or candidate.is_loopback
            or candidate.is_link_local
            or candidate.is_reserved
            or candidate.is_multicast
            or candidate.is_unspecified
        ):
            return False
    return True


def fetch_url_text(url, timeout=15):
    if not is_public_http_url(url):
        raise requests.RequestException(f"refusing non-public source URL {redacted_url(url)}")
    response = requests.get(url, headers=source_headers(), timeout=timeout)
    response.raise_for_status()
    final_url = getattr(response, "url", url) or url
    if final_url != url and not is_public_http_url(final_url):
        raise requests.RequestException(f"source redirected to a non-public URL {redacted_url(final_url)}")
    content_type = response.headers.get("content-type", "")
    if "json" in content_type:
        return json.dumps(response.json(), ensure_ascii=True)
    # Decode from bytes: response.text assumes ISO-8859-1 when no charset is declared
    # and garbles UTF-8 company names.
    return html_to_text(decode_body(response.content[:MAX_DOCUMENT_BYTES], content_type))


def fetch_json(url, params=None, timeout=20):
    response = requests.get(url, params=params, headers=source_headers(), timeout=timeout)
    response.raise_for_status()
    return response.json()


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
            print(f"  [-] Configured source unavailable for {ticker}: {redacted_url(url)} ({type(exc).__name__})")
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
    # Yahoo profile websites are often deep links; IR paths belong on the origin,
    # not appended to /investors/index.html.
    parsed = urlparse(website)
    origin = urlunparse((parsed.scheme, parsed.netloc, "/", "", "", ""))
    urls = [website]
    urls.extend(urljoin(origin, path.lstrip("/")) for path in IR_PATHS)
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
            "generated_internal_id",
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

    # The API endpoint is POST-only and unusable as a citation; cite the public award
    # pages instead so the dashboard's source link leads to the evidence.
    lines = []
    for row in rows[:max_awards]:
        description = row.get("Award Description") or row.get("Description") or ""
        agency = row.get("Awarding Agency") or ""
        amount = row.get("Award Amount") or ""
        award_id = row.get("Award ID") or ""
        internal_id = row.get("generated_internal_id") or ""
        link = f" ({usaspending_award_url(internal_id)})" if internal_id else ""
        lines.append(
            f"Award {award_id}{link}: {row.get('Recipient Name') or company_name} received "
            f"{amount} from {agency} for {description}."
        )
    text = "\n".join(lines)
    return source_section(
        "Government Procurement - USAspending",
        company_name,
        ticker,
        text,
        url="https://www.usaspending.gov/",
        max_chars=max_chars,
    )


def usaspending_award_url(internal_id):
    return f"https://www.usaspending.gov/award/{internal_id}"


def sam_opportunities_params(company_name, api_key, limit=5, days_back=365):
    today = date.today()
    posted_from = today - timedelta(days=days_back)
    return {
        "api_key": api_key,
        "limit": limit,
        "postedFrom": posted_from.strftime("%m/%d/%Y"),
        "postedTo": today.strftime("%m/%d/%Y"),
        "title": clean_company_query(company_name),
    }


def sam_opportunities_text(ticker, company_name="", sector="", industry="", max_opportunities=5, max_chars=2600):
    api_key = os.environ.get("HEPHAESTUS_SAM_API_KEY")
    if not api_key or not company_name:
        return ""
    if not sector_matches(sector, industry, DEFENSE_PROCUREMENT_MARKERS):
        return ""

    url = "https://api.sam.gov/prod/opportunities/v2/search"
    try:
        payload = fetch_json(url, params=sam_opportunities_params(company_name, api_key, limit=max_opportunities))
    except requests.RequestException as exc:
        # requests embeds the full request URL, including the api_key query
        # parameter, in its error messages; never let the key reach the job log.
        print(f"  [-] SAM.gov opportunities unavailable for {ticker}: {str(exc).replace(api_key, '***')}")
        return ""

    opportunities = payload.get("opportunitiesData") or payload.get("data") or []
    lines = []
    for opportunity in opportunities[:max_opportunities]:
        title = opportunity.get("title") or opportunity.get("noticeTitle") or ""
        agency = opportunity.get("fullParentPathName") or opportunity.get("department") or opportunity.get("organizationName") or ""
        notice_type = opportunity.get("type") or opportunity.get("noticeType") or ""
        description = html_to_text(opportunity.get("description") or opportunity.get("synopsis") or "")
        posted = opportunity.get("postedDate") or ""
        notice_id = opportunity.get("noticeId") or ""
        link = f" (https://sam.gov/opp/{notice_id}/view)" if notice_id else ""
        lines.append(f"{posted} {notice_type} contract opportunity from {agency}: {title}{link}. {description}")
    # The API endpoint requires a key; cite the public search page and per-notice pages.
    return source_section(
        "Government Procurement - SAM.gov Opportunities",
        company_name,
        ticker,
        "\n".join(lines),
        url="https://sam.gov/search/",
        max_chars=max_chars,
    )


def openfda_search(endpoint, search, limit=5):
    return fetch_json(f"https://api.fda.gov/{endpoint}.json", params={"search": search, "limit": limit})


def openfda_device_510k_text(ticker, company_name="", sector="", industry="", limit=5, max_chars=2200):
    if not company_name or not sector_matches(sector, industry, HEALTH_SECTOR_MARKERS):
        return ""
    query = quoted_query(company_name)
    if not query:
        return ""
    try:
        payload = openfda_search("device/510k", f"applicant:{query}", limit=limit)
    except requests.RequestException as exc:
        print(f"  [-] openFDA 510(k) unavailable for {ticker}: {exc}")
        return ""

    lines = []
    for row in payload.get("results", [])[:limit]:
        k_number = row.get("k_number") or ""
        link = f" (https://www.accessdata.fda.gov/scripts/cdrh/cfdocs/cfpmn/pmn.cfm?ID={k_number})" if k_number else ""
        lines.append(
            f"FDA 510(k) {k_number}{link}: {row.get('applicant') or company_name} "
            f"received clearance for the {row.get('device_name') or row.get('advisory_committee_description') or 'unnamed'} medical device "
            f"on {row.get('decision_date') or 'unknown date'}."
        )
    return source_section(
        "Regulatory Dataset - openFDA Device 510(k)",
        company_name,
        ticker,
        "\n".join(lines),
        url="https://open.fda.gov/apis/device/510k/",
        max_chars=max_chars,
    )


def openfda_device_recall_text(ticker, company_name="", sector="", industry="", limit=5, max_chars=2200):
    if not company_name or not sector_matches(sector, industry, HEALTH_SECTOR_MARKERS):
        return ""
    query = quoted_query(company_name)
    if not query:
        return ""
    try:
        payload = openfda_search("device/recall", f"firm_name:{query}", limit=limit)
    except requests.RequestException as exc:
        print(f"  [-] openFDA device recall unavailable for {ticker}: {exc}")
        return ""

    lines = []
    for row in payload.get("results", [])[:limit]:
        event_number = row.get("res_event_number") or ""
        link = f" (https://www.accessdata.fda.gov/scripts/cdrh/cfdocs/cfres/res.cfm?id={event_number})" if event_number else ""
        lines.append(
            f"FDA device recall {event_number}{link}: {row.get('firm_name') or company_name} "
            f"recalled {row.get('product_description') or 'a device'} because {row.get('reason_for_recall') or 'a recall reason was reported'}."
        )
    return source_section(
        "Regulatory Dataset - openFDA Device Recall",
        company_name,
        ticker,
        "\n".join(lines),
        url="https://open.fda.gov/apis/device/recall/",
        max_chars=max_chars,
    )


def openfda_drug_enforcement_text(ticker, company_name="", sector="", industry="", limit=5, max_chars=2200):
    if not company_name or not sector_matches(sector, industry, HEALTH_SECTOR_MARKERS):
        return ""
    query = quoted_query(company_name)
    if not query:
        return ""
    try:
        payload = openfda_search("drug/enforcement", f"recalling_firm:{query}", limit=limit)
    except requests.RequestException as exc:
        print(f"  [-] openFDA drug enforcement unavailable for {ticker}: {exc}")
        return ""

    lines = []
    for row in payload.get("results", [])[:limit]:
        lines.append(
            f"FDA drug enforcement {row.get('recall_number') or ''}: {row.get('recalling_firm') or company_name} "
            f"recalled {row.get('product_description') or 'a product'} because {row.get('reason_for_recall') or 'a recall reason was reported'}."
        )
    return source_section(
        "Regulatory Dataset - openFDA Drug Enforcement",
        company_name,
        ticker,
        "\n".join(lines),
        url="https://open.fda.gov/apis/drug/enforcement/",
        max_chars=max_chars,
    )


def openfda_regulatory_text(ticker, company_name="", sector="", industry="", max_chars=5200):
    sections = [
        openfda_device_510k_text(ticker, company_name=company_name, sector=sector, industry=industry),
        openfda_device_recall_text(ticker, company_name=company_name, sector=sector, industry=industry),
        openfda_drug_enforcement_text(ticker, company_name=company_name, sector=sector, industry=industry),
    ]
    return truncate_join(sections, max_chars)


def fcc_equipment_authorization_text(ticker, company_name="", sector="", industry="", limit=5, max_chars=2600):
    if not company_name or not sector_matches(sector, industry, ELECTRONICS_SECTOR_MARKERS):
        return ""
    url = "https://opendata.fcc.gov/resource/3b3k-34jp.json"
    params = {
        "$limit": limit,
        "$q": clean_company_query(company_name),
    }
    try:
        rows = fetch_json(url, params=params)
    except requests.RequestException as exc:
        print(f"  [-] FCC equipment authorization unavailable for {ticker}: {exc}")
        return ""

    lines = []
    for row in rows[:limit]:
        grantee = row.get("grantee_name") or row.get("applicant_name") or row.get("name") or company_name
        fcc_id = row.get("fcc_id") or row.get("fccid") or row.get("application_id") or ""
        equipment = row.get("equipment_class") or row.get("product_description") or row.get("description") or ""
        grant_date = row.get("grant_date") or row.get("date") or ""
        lines.append(f"FCC equipment authorization {fcc_id}: {grantee} received authorization for {equipment} on {grant_date}.")
    return source_section(
        "Regulatory Dataset - FCC Equipment Authorization",
        company_name,
        ticker,
        "\n".join(lines),
        url="https://www.fcc.gov/oet/ea/fccid",
        max_chars=max_chars,
    )


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
            f"{row.get('Mfr_Name') or company_name} is a registered vehicle manufacturer: "
            f"{row.get('PrincipalFirstName') or ''} {row.get('VehicleTypes') or ''} "
            f"{row.get('Mfr_CommonName') or ''} {row.get('Country') or ''} {row.get('StateProvince') or ''}"
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


def collect_section(ticker, collector, *args, **kwargs):
    """Isolate each collector so one unexpected error cannot discard every other source."""
    try:
        return collector(*args, **kwargs)
    except Exception as exc:
        name = getattr(collector, "__name__", "source collector")
        print(f"  [-] {name} unavailable for {ticker}: {exc}")
        return ""


def get_additional_supply_chain_text(company_name, ticker, sector="", industry="", max_chars=8500):
    sections = []
    if bool_env("HEPHAESTUS_USE_CONFIGURED_SOURCE_URLS", default=True):
        sections.append(collect_section(ticker, configured_source_text, ticker, company_name=company_name))
    if bool_env("HEPHAESTUS_USE_IR_SOURCES", default=True):
        sections.append(collect_section(ticker, company_ir_text, ticker, company_name=company_name))
    if bool_env("HEPHAESTUS_USE_PROCUREMENT_SOURCE", default=True):
        sections.append(collect_section(ticker, usaspending_text, ticker, company_name=company_name))
        sections.append(collect_section(ticker, sam_opportunities_text, ticker, company_name=company_name, sector=sector, industry=industry))
    if bool_env("HEPHAESTUS_USE_REGULATORY_SOURCE", default=True):
        sections.append(collect_section(ticker, openfda_regulatory_text, ticker, company_name=company_name, sector=sector, industry=industry))
        sections.append(collect_section(ticker, fcc_equipment_authorization_text, ticker, company_name=company_name, sector=sector, industry=industry))
        sections.append(collect_section(ticker, nhtsa_manufacturer_text, ticker, company_name=company_name, sector=sector, industry=industry))
    sections.append(collect_section(ticker, yahoo_news_article_text, ticker, company_name=company_name))
    return truncate_join(sections, max_chars)
