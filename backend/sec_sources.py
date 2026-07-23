import os
import re
from html import unescape
from functools import lru_cache

import requests

try:
    from bs4 import BeautifulSoup
except ImportError:
    BeautifulSoup = None


SEC_BASE_URL = "https://data.sec.gov"
SEC_ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data"
SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
DEFAULT_USER_AGENT = os.environ.get(
    "HEPHAESTUS_SEC_USER_AGENT",
    "HephaestusTerminal/1.0 research@saqibdesktop.local",
)
DEFAULT_FORMS = ("10-K", "20-F", "40-F", "10-Q")
EXHIBIT_FORMS = ("8-K", "10-K", "10-Q", "20-F", "40-F")
FORM_PRIORITY = {form: index for index, form in enumerate(DEFAULT_FORMS)}
SUPPLY_CHAIN_TERMS = (
    "customer",
    "customers",
    "supplier",
    "suppliers",
    "supply chain",
    "manufacturing",
    "manufacture",
    "contract manufacturer",
    "foundry",
    "outsourced",
    "third-party manufacturers",
    "third party manufacturers",
    "distribution",
    "distributor",
    "raw material",
    "sole source",
    "single source",
    "logistics",
    "purchase obligations",
    "concentration",
    "accounted for",
    "net sales",
)
EXHIBIT_TERMS = (
    "EX-10",
    "agreement",
    "supply",
    "supplier",
    "customer",
    "manufacturing",
    "purchase",
    "distribution",
    "logistics",
    "services",
)


def sec_headers():
    return {
        "User-Agent": DEFAULT_USER_AGENT,
        "Accept-Encoding": "gzip, deflate",
        "Host": "data.sec.gov",
    }


def sec_archive_headers():
    headers = sec_headers()
    headers["Host"] = "www.sec.gov"
    return headers


def normalize_ticker(value):
    return str(value or "").strip().upper()


@lru_cache(maxsize=1)
def load_ticker_cik_map(timeout=20):
    response = requests.get(SEC_TICKERS_URL, headers=sec_archive_headers(), timeout=timeout)
    response.raise_for_status()
    payload = response.json()
    return {
        normalize_ticker(entry.get("ticker")): str(entry.get("cik_str")).zfill(10)
        for entry in payload.values()
        if entry.get("ticker") and entry.get("cik_str") is not None
    }


def ticker_to_cik(ticker, ticker_map=None):
    ticker_map = ticker_map or load_ticker_cik_map()
    return ticker_map.get(normalize_ticker(ticker))


def recent_filings(cik, forms=DEFAULT_FORMS, limit=2, timeout=20):
    url = f"{SEC_BASE_URL}/submissions/CIK{str(cik).zfill(10)}.json"
    response = requests.get(url, headers=sec_headers(), timeout=timeout)
    response.raise_for_status()
    recent = response.json().get("filings", {}).get("recent", {})
    filings_by_form = {form: [] for form in forms}
    for index, form in enumerate(recent.get("form", [])):
        if form not in forms:
            continue
        accession = recent.get("accessionNumber", [])[index]
        primary_doc = recent.get("primaryDocument", [])[index]
        filing_date = recent.get("filingDate", [None])[index]
        if not accession or not primary_doc:
            continue
        filings_by_form[form].append(
            {
                "form": form,
                "accession": accession,
                "primary_document": primary_doc,
                "filing_date": filing_date,
                "url": filing_url(cik, accession, primary_doc),
            }
        )

    filings = []
    for form in forms:
        filings.extend(
            sorted(
                filings_by_form.get(form, []),
                key=lambda filing: filing.get("filing_date") or "",
                reverse=True,
            )
        )
        if len(filings) >= limit:
            break
    return filings[:limit]


def filing_url(cik, accession, primary_document):
    accession_path = str(accession).replace("-", "")
    cik_int = str(int(str(cik)))
    return f"{SEC_ARCHIVE_URL}/{cik_int}/{accession_path}/{primary_document}"


def filing_directory_url(cik, accession):
    accession_path = str(accession).replace("-", "")
    cik_int = str(int(str(cik)))
    return f"{SEC_ARCHIVE_URL}/{cik_int}/{accession_path}"


def filing_index_url(cik, accession):
    return f"{filing_directory_url(cik, accession)}/index.json"


def html_to_text(html):
    if BeautifulSoup is None:
        text = re.sub(r"<(script|style|ix:header|ix:hidden)\b.*?</\1>", " ", html, flags=re.IGNORECASE | re.DOTALL)
        text = re.sub(r"<[^>]+>", " ", text)
        text = unescape(text)
        return re.sub(r"\s+", " ", text).strip()

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "ix:header", "ix:hidden"]):
        tag.decompose()
    text = soup.get_text(" ")
    text = unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def relevant_windows(text, terms=SUPPLY_CHAIN_TERMS, window=650, max_chars=5000):
    lowered = text.lower()
    spans = []
    for term in terms:
        start = 0
        term = term.lower()
        while True:
            index = lowered.find(term, start)
            if index == -1:
                break
            spans.append((max(0, index - window), min(len(text), index + window)))
            start = index + len(term)

    if not spans:
        return text[:max_chars]

    spans.sort()
    merged = []
    for start, end in spans:
        if not merged or start > merged[-1][1] + 80:
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)

    chunks = []
    total = 0
    for start, end in merged:
        chunk = text[start:end].strip()
        if not chunk:
            continue
        remaining = max_chars - total
        if remaining <= 0:
            break
        chunks.append(chunk[:remaining])
        total += len(chunks[-1])
    return "\n...\n".join(chunks)


def fetch_filing_text(filing, timeout=30):
    response = requests.get(filing["url"], headers=sec_archive_headers(), timeout=timeout)
    response.raise_for_status()
    return html_to_text(response.text)


def fetch_filing_index(filing, timeout=20):
    response = requests.get(
        filing_index_url(filing["cik"], filing["accession"]),
        headers=sec_archive_headers(),
        timeout=timeout,
    )
    response.raise_for_status()
    return response.json()


def is_relevant_exhibit(item):
    name = str(item.get("name") or "")
    doc_type = str(item.get("type") or "")
    description = str(item.get("description") or "")
    haystack = f"{name} {doc_type} {description}".lower()
    if not name.lower().endswith((".htm", ".html", ".txt")):
        return False
    return any(term.lower() in haystack for term in EXHIBIT_TERMS)


def exhibit_documents(filing, index_payload=None, limit=4):
    index_payload = index_payload if index_payload is not None else fetch_filing_index(filing)
    items = index_payload.get("directory", {}).get("item", [])
    docs = []
    for item in items:
        if not is_relevant_exhibit(item):
            continue
        name = item.get("name")
        docs.append(
            {
                "name": name,
                "type": item.get("type") or "",
                "description": item.get("description") or name,
                "url": f"{filing_directory_url(filing['cik'], filing['accession'])}/{name}",
                "filing": filing,
            }
        )
        if len(docs) >= limit:
            break
    return docs


def fetch_exhibit_text(exhibit, timeout=25):
    response = requests.get(exhibit["url"], headers=sec_archive_headers(), timeout=timeout)
    response.raise_for_status()
    return html_to_text(response.text)


def get_sec_supply_chain_text(ticker, company_name="", filing_limit=2, max_chars=5500):
    cik = ticker_to_cik(ticker)
    if not cik:
        return ""

    sections = []
    for filing in recent_filings(cik, limit=filing_limit):
        filing["cik"] = cik
        try:
            text = fetch_filing_text(filing)
        except requests.RequestException as exc:
            print(f"  [-] SEC filing unavailable for {ticker} {filing.get('form')}: {exc}")
            continue
        relevant = relevant_windows(text, max_chars=max_chars // filing_limit)
        if relevant:
            title = f"{filing['form']} filed {filing.get('filing_date') or 'unknown date'}"
            sections.append(
                f"SOURCE: SEC EDGAR ({title}, {filing['url']})\n"
                f"COMPANY: {company_name or ticker} ({ticker})\n"
                f"DATA:\n{relevant}\n"
            )
    return "\n".join(sections)[:max_chars]


def get_sec_exhibit_supply_chain_text(ticker, company_name="", filing_limit=4, exhibit_limit=4, max_chars=4500):
    cik = ticker_to_cik(ticker)
    if not cik:
        return ""

    sections = []
    filings = recent_filings(cik, forms=EXHIBIT_FORMS, limit=filing_limit)
    for filing in filings:
        filing["cik"] = cik
        try:
            exhibits = exhibit_documents(filing, limit=exhibit_limit)
        except requests.RequestException as exc:
            print(f"  [-] SEC exhibit index unavailable for {ticker} {filing.get('form')}: {exc}")
            continue

        for exhibit in exhibits:
            try:
                text = fetch_exhibit_text(exhibit)
            except requests.RequestException as exc:
                print(f"  [-] SEC exhibit unavailable for {ticker} {exhibit.get('name')}: {exc}")
                continue
            relevant = relevant_windows(text, max_chars=max_chars // max(1, exhibit_limit))
            if not relevant:
                continue
            sections.append(
                f"SOURCE: SEC EDGAR EXHIBIT ({exhibit.get('type') or 'Exhibit'}; "
                f"{exhibit.get('description')}; {exhibit['url']})\n"
                f"COMPANY: {company_name or ticker} ({ticker})\n"
                f"DATA:\n{relevant}\n"
            )
            if len("\n".join(sections)) >= max_chars:
                return "\n".join(sections)[:max_chars]
    return "\n".join(sections)[:max_chars]
