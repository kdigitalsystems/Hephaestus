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
# Annual reports first: issuers file 8-Ks so often that listing them first consumed the
# whole filing budget, and 8-K exhibits are press releases rather than contracts.
EXHIBIT_FORMS = ("10-K", "20-F", "40-F", "10-Q", "8-K")
# Inline-XBRL filings can exceed 30 MB; only the first few MB are needed for windows.
MAX_DOCUMENT_BYTES = int(os.environ.get("HEPHAESTUS_MAX_DOCUMENT_BYTES", str(6 * 1024 * 1024)))
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
    recent = response.json().get("filings", {}).get("recent", {}) or {}
    form_list = list(recent.get("form") or [])
    accessions = list(recent.get("accessionNumber") or [])
    documents = list(recent.get("primaryDocument") or [])
    dates = list(recent.get("filingDate") or [])
    filings_by_form = {form: [] for form in forms}
    # The parallel arrays are indexed together; a missing or shorter array must not
    # raise IndexError and silently drop the whole SEC source for a company.
    for index in range(min(len(form_list), len(accessions), len(documents))):
        form = form_list[index]
        if form not in forms:
            continue
        accession = accessions[index]
        primary_doc = documents[index]
        filing_date = dates[index] if index < len(dates) else None
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
        text = re.sub(r"<!--.*?-->", " ", html, flags=re.DOTALL)
        text = re.sub(r"<(script|style|ix:header|ix:hidden)\b.*?</\1>", " ", text, flags=re.IGNORECASE | re.DOTALL)
        text = re.sub(r"<[^>]+>", " ", text)
        text = unescape(text)
        return re.sub(r"\s+", " ", text).strip()

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "ix:header", "ix:hidden"]):
        tag.decompose()
    # get_text() already decodes entities; unescaping again would turn escaped
    # markup in the filing text back into live markup.
    text = soup.get_text(" ")
    return re.sub(r"\s+", " ", text).strip()


def decode_body(body, content_type=""):
    """Decode a response body without requests' ISO-8859-1 default for charset-less HTML."""
    match = re.search(r"charset=([\w.\-]+)", str(content_type or ""), re.IGNORECASE)
    if match:
        try:
            return body.decode(match.group(1), errors="replace")
        except LookupError:
            pass
    try:
        return body.decode("utf-8")
    except UnicodeDecodeError:
        return body.decode("cp1252", errors="replace")


def read_response_text(response, max_bytes=MAX_DOCUMENT_BYTES):
    """Read at most max_bytes from a streamed response and decode it."""
    chunks = []
    total = 0
    for chunk in response.iter_content(chunk_size=65536):
        if not chunk:
            continue
        chunks.append(chunk)
        total += len(chunk)
        if total >= max_bytes:
            break
    return decode_body(b"".join(chunks)[:max_bytes], response.headers.get("content-type", ""))


def fetch_document_text(url, headers, timeout, max_bytes=MAX_DOCUMENT_BYTES):
    response = requests.get(url, headers=headers, timeout=timeout, stream=True)
    try:
        response.raise_for_status()
        return read_response_text(response, max_bytes)
    finally:
        response.close()


def relevant_windows(text, terms=SUPPLY_CHAIN_TERMS, window=650, max_chars=5000, require_match=False):
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
        # Filings are worth keeping even without a term hit; web pages are not, or a
        # cookie banner becomes a full evidence section.
        return "" if require_match else text[:max_chars]

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
    # The separators were never budgeted; enforce the cap on the joined result.
    return "\n...\n".join(chunks)[:max_chars]


# Filing bodies are large and several collectors read the same annual report in one
# run; cache the extracted text per URL for the life of the process.
_FILING_TEXT_CACHE = {}


def fetch_filing_text(filing, timeout=30):
    url = filing["url"]
    if url not in _FILING_TEXT_CACHE:
        _FILING_TEXT_CACHE[url] = html_to_text(fetch_document_text(url, sec_archive_headers(), timeout))
    return _FILING_TEXT_CACHE[url]


ANNUAL_FORMS = ("10-K", "20-F", "40-F")


def latest_annual_filing(ticker):
    """Return (filing, full text) for the issuer's most recent annual report, or (None, "")."""
    cik = ticker_to_cik(ticker)
    if not cik:
        return None, ""
    filings = recent_filings(cik, forms=ANNUAL_FORMS, limit=1)
    if not filings:
        return None, ""
    filing = filings[0]
    filing["cik"] = cik
    try:
        return filing, fetch_filing_text(filing)
    except requests.RequestException as exc:
        print(f"  [-] SEC annual filing unavailable for {ticker}: {exc}")
        return filing, ""


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
    return html_to_text(fetch_document_text(exhibit["url"], sec_archive_headers(), timeout))


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
