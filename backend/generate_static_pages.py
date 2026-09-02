"""Pre-render one static HTML page per linked company so search engines can index them.

The dashboard routes companies behind URL fragments (#company?ticker=AMD), which
crawlers do not index, so nobody searching "AMD suppliers" ever lands on the site.
This writes docs/company/<TICKER>.html for every company that has published
relationships, a directory page, and a sitemap that lists them. Output is derived
only from docs/dashboard_data.json, so a re-run without new data is byte-identical.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from html import escape
from pathlib import Path

from generate_change_feed import SITE_URL, write_text_atomic


ROOT = Path(__file__).resolve().parents[1]
DOCS_DIR = ROOT / "docs"
DEFAULT_DASHBOARD_PATH = DOCS_DIR / "dashboard_data.json"
DEFAULT_OUTPUT_DIR = DOCS_DIR / "company"
DEFAULT_SITEMAP_PATH = DOCS_DIR / "sitemap.xml"
STYLESHEET_VERSION = "20260902-static1"
MAX_EVIDENCE_CHARS = 400
PLACEHOLDER_VALUES = {"", "n/a", "none", "uncategorized", "pending update", "reviewed relationship endpoint", "linked companies"}


def meaningful(value):
    text = " ".join(str(value or "").split())
    return text if text.lower() not in PLACEHOLDER_VALUES else ""


def sentence_name(name):
    return display_name(name).rstrip(".")


def page_filename(ticker):
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", str(ticker or "").upper())
    return f"{safe}.html"


def display_name(name):
    value = " ".join(str(name or "").split())
    for pattern in (
        r"\s+(Class\s+[A-Z]\s+|New\s+)?Common Stock$",
        r"\s+Ordinary Shares.*$",
        r"\s+American Depositary Shares.*$",
        r"\s+Depositary Shares.*$",
        r"\s+Warrants.*$",
    ):
        value = re.sub(pattern, "", value, flags=re.IGNORECASE).strip()
    return value or "Unknown company"


def format_market_cap(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number <= 0:
        return None
    for threshold, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M")):
        if number >= threshold:
            return f"${number / threshold:.1f}{suffix}"
    return f"${number:,.0f}"


def linked_companies(dashboard):
    companies = []
    for sector, rows in (dashboard.get("industries") or {}).items():
        if not isinstance(rows, list):
            continue
        for company in rows:
            if not isinstance(company, dict) or not company.get("ticker"):
                continue
            if company.get("upstream") or company.get("downstream"):
                companies.append({**company, "sector": company.get("sector") or sector})
    companies.sort(key=lambda company: str(company["ticker"]).upper())
    return companies


def verification_label(link):
    summary = link.get("review_summary") or {}
    if summary.get("label"):
        return summary["label"]
    return "Reviewed" if str(link.get("review_status") or "").lower() == "approved" else "Awaiting review"


def revenue_share_text(link, side):
    try:
        share = float(link.get("revenue_share"))
    except (TypeError, ValueError):
        return ""
    if share <= 0:
        return ""
    value = f"{share:g}%"
    return f"{value} of revenue" if side == "downstream" else f"{value} of {link.get('ticker') or 'supplier'} revenue"


def render_link(link, side, known_tickers):
    ticker = str(link.get("ticker") or "").upper()
    name = display_name(link.get("name") or ticker)
    label = f"{name} ({ticker})" if ticker else name
    if ticker in known_tickers:
        heading = f'<a href="{escape(page_filename(ticker))}">{escape(label)}</a>'
    else:
        heading = escape(label)
    details = [escape(str(link.get("type") or "Supply Link"))]
    product = str(link.get("product") or "")
    if product and product != link.get("type"):
        details.append(escape(product))
    details.append(escape(verification_label(link)))
    share = revenue_share_text(link, side)
    if share:
        details.append(escape(share))
    source = str(link.get("source") or "")
    if source.startswith(("http://", "https://")):
        title = str(link.get("source_title") or "").strip()
        title = title if title and title != source else "Source document"
        details.append(f'<a href="{escape(source)}" rel="noopener noreferrer">{escape(title)}</a>')
    evidence = " ".join(str(link.get("evidence_excerpt") or "").split())
    if len(evidence) > MAX_EVIDENCE_CHARS:
        evidence = evidence[:MAX_EVIDENCE_CHARS].rsplit(" ", 1)[0] + "…"
    quote = f"<blockquote>{escape(evidence)}</blockquote>" if evidence else ""
    return f"<li><p>{heading}<br><small>{' · '.join(details)}</small></p>{quote}</li>"


def theme_bootstrap():
    return (
        "<script>try{document.documentElement.dataset.theme=localStorage.getItem('hephaestus_theme')==='light'?'light':'dark';}"
        "catch(e){document.documentElement.dataset.theme='dark';}</script>"
    )


def render_company_page(company, generated_on, known_tickers):
    ticker = str(company["ticker"]).upper()
    name = display_name(company.get("name"))
    upstream = company.get("upstream") or []
    downstream = company.get("downstream") or []
    sector = meaningful(company.get("sector"))
    industry = meaningful(company.get("industry"))
    supplier_names = ", ".join(sentence_name(link.get("name") or link.get("ticker")) for link in upstream[:4])
    customer_names = ", ".join(sentence_name(link.get("name") or link.get("ticker")) for link in downstream[:4])
    description_parts = [f"{name} ({ticker}) has {len(upstream)} tracked supplier{'s' if len(upstream) != 1 else ''} and {len(downstream)} tracked customer{'s' if len(downstream) != 1 else ''} in the Hephaestus supply-chain graph."]
    if supplier_names:
        description_parts.append(f"Suppliers include {supplier_names}.")
    if customer_names:
        description_parts.append(f"Customers include {customer_names}.")
    description = " ".join(description_parts)
    canonical = f"{SITE_URL}company/{page_filename(ticker)}"
    market_cap = format_market_cap(company.get("market_cap"))
    facts = [item for item in (
        f"Sector: {sector}" if sector else "",
        f"Industry: {industry}" if industry else "",
        f"Market cap: {market_cap}" if market_cap else "",
    ) if item]
    json_ld = json.dumps({
        "@context": "https://schema.org",
        "@type": "Organization",
        "name": name,
        "tickerSymbol": ticker,
        "url": canonical,
        "description": description,
    }, ensure_ascii=True)

    def section(title, links, side, empty_text):
        if not links:
            return f"<h2>{escape(title)}</h2><p>{escape(empty_text)}</p>"
        items = "".join(render_link(link, side, known_tickers) for link in links)
        return f"<h2>{escape(title)} ({len(links)})</h2><ul class=\"static-links\">{items}</ul>"

    return (
        "<!DOCTYPE html>\n<html lang=\"en\">\n<head>\n"
        "<meta charset=\"UTF-8\">\n<meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\">\n"
        f"<title>{escape(name)} ({escape(ticker)}) suppliers and customers | Hephaestus</title>\n"
        f"<meta name=\"description\" content=\"{escape(description)}\">\n"
        f"<link rel=\"canonical\" href=\"{escape(canonical)}\">\n"
        f"{theme_bootstrap()}\n"
        "<link rel=\"alternate\" type=\"application/rss+xml\" title=\"Hephaestus supply-chain changes\" href=\"../feed.xml\">\n"
        f"<link rel=\"stylesheet\" href=\"../styles.css?v={STYLESHEET_VERSION}\">\n"
        f"<script type=\"application/ld+json\">{json_ld}</script>\n"
        "</head>\n<body>\n<main class=\"methodology-page static-company\">\n"
        f"<a class=\"back-link\" href=\"../#company?ticker={escape(ticker)}\">&larr; Open the interactive brief for {escape(ticker)}</a>\n"
        f"<p class=\"eyebrow\">{escape(' · '.join(facts) if facts else 'Supply-chain relationships')}</p>\n"
        f"<h1>{escape(name)} ({escape(ticker)})</h1>\n"
        f"<p>{escape(description)} Every relationship below was extracted from public text, reviewed, and is shown with its verification label and evidence excerpt. Data as of {escape(generated_on)}.</p>\n"
        f"{section('Suppliers', upstream, 'upstream', f'No tracked suppliers for {name} yet.')}\n"
        f"{section('Customers', downstream, 'downstream', f'No tracked customers for {name} yet.')}\n"
        "<h2>Explore further</h2>\n<ul>\n"
        f"<li><a href=\"../#exposure?ticker={escape(ticker)}\">Who is exposed to {escape(ticker)}</a> — dependents two hops deep</li>\n"
        f"<li><a href=\"../#company?ticker={escape(ticker)}\">Interactive company brief</a> — market data, chart, and evidence dialogs</li>\n"
        "<li><a href=\"index.html\">All companies with tracked relationships</a></li>\n"
        "<li><a href=\"../methodology.html\">How relationships are found and verified</a></li>\n"
        "</ul>\n"
        "<p><small>Hephaestus is research tooling, not investment advice. Verify anything you rely on against the cited source.</small></p>\n"
        "</main>\n</body>\n</html>\n"
    )


def render_index_page(companies, generated_on):
    by_sector = {}
    for company in companies:
        by_sector.setdefault(str(company.get("sector") or "Other"), []).append(company)
    sections = []
    for sector in sorted(by_sector):
        items = "".join(
            f'<li><a href="{escape(page_filename(company["ticker"]))}">{escape(display_name(company.get("name")))} ({escape(str(company["ticker"]).upper())})</a>'
            f" — {len(company.get('upstream') or [])} supplier{'s' if len(company.get('upstream') or []) != 1 else ''}, "
            f"{len(company.get('downstream') or [])} customer{'s' if len(company.get('downstream') or []) != 1 else ''}</li>"
            for company in sorted(by_sector[sector], key=lambda item: str(item["ticker"]).upper())
        )
        sections.append(f"<h2>{escape(sector)} ({len(by_sector[sector])})</h2><ul>{items}</ul>")
    description = f"{len(companies)} public companies with reviewed supplier and customer relationships in the Hephaestus supply-chain graph."
    return (
        "<!DOCTYPE html>\n<html lang=\"en\">\n<head>\n"
        "<meta charset=\"UTF-8\">\n<meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\">\n"
        "<title>Companies with tracked supply-chain relationships | Hephaestus</title>\n"
        f"<meta name=\"description\" content=\"{escape(description)}\">\n"
        f"<link rel=\"canonical\" href=\"{escape(SITE_URL)}company/index.html\">\n"
        f"{theme_bootstrap()}\n"
        f"<link rel=\"stylesheet\" href=\"../styles.css?v={STYLESHEET_VERSION}\">\n"
        "</head>\n<body>\n<main class=\"methodology-page static-company\">\n"
        "<a class=\"back-link\" href=\"../\">&larr; Back to the dashboard</a>\n"
        "<p class=\"eyebrow\">Company index</p>\n<h1>Companies with tracked supply-chain relationships</h1>\n"
        f"<p>{escape(description)} Data as of {escape(generated_on)}. See the <a href=\"../methodology.html\">methodology</a> for how relationships are found and verified.</p>\n"
        + "\n".join(sections)
        + "\n</main>\n</body>\n</html>\n"
    )


def render_sitemap(companies, generated_on):
    entries = [
        (f"{SITE_URL}", "daily", "1.0", generated_on),
        (f"{SITE_URL}methodology.html", "monthly", "0.6", None),
        (f"{SITE_URL}company/index.html", "daily", "0.8", generated_on),
    ]
    entries.extend((f"{SITE_URL}company/{page_filename(company['ticker'])}", "weekly", "0.7", generated_on) for company in companies)
    urls = []
    for location, changefreq, priority, lastmod in entries:
        lastmod_tag = f"<lastmod>{escape(lastmod)}</lastmod>" if lastmod else ""
        urls.append(f"  <url><loc>{escape(location)}</loc>{lastmod_tag}<changefreq>{changefreq}</changefreq><priority>{priority}</priority></url>")
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "\n".join(urls)
        + "\n</urlset>\n"
    )


def generate_static_pages(dashboard_path=DEFAULT_DASHBOARD_PATH, output_dir=DEFAULT_OUTPUT_DIR, sitemap_path=DEFAULT_SITEMAP_PATH):
    dashboard = json.loads(Path(dashboard_path).read_text(encoding="utf-8"))
    generated_on = str(dashboard.get("generated_at") or "")[:10] or "unknown date"
    companies = linked_companies(dashboard)
    known_tickers = {str(company["ticker"]).upper() for company in companies}
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    written = set()
    for company in companies:
        filename = page_filename(company["ticker"])
        write_text_atomic(output_dir / filename, render_company_page(company, generated_on, known_tickers))
        written.add(filename)
    write_text_atomic(output_dir / "index.html", render_index_page(companies, generated_on))
    written.add("index.html")

    # A company that lost its last relationship must not keep a stale page.
    removed = 0
    for stale in output_dir.glob("*.html"):
        if stale.name not in written:
            os.unlink(stale)
            removed += 1

    write_text_atomic(sitemap_path, render_sitemap(companies, generated_on))
    return {"pages": len(companies), "removed": removed, "generated_on": generated_on}


def main():
    parser = argparse.ArgumentParser(description="Pre-render static company pages and the sitemap from the published dashboard data.")
    parser.add_argument("--dashboard", type=Path, default=DEFAULT_DASHBOARD_PATH)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--sitemap", type=Path, default=DEFAULT_SITEMAP_PATH)
    args = parser.parse_args()
    result = generate_static_pages(args.dashboard, args.output_dir, args.sitemap)
    print(f"Static pages: {result['pages']} company page(s) written, {result['removed']} stale removed, data as of {result['generated_on']} -> {args.output_dir}, {args.sitemap}")


if __name__ == "__main__":
    main()
