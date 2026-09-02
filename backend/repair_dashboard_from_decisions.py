import json
from pathlib import Path

from export import (
    FALLBACK_LINKED_SECTOR,
    FOUNDRY_TERMS,
    annotate_dashboard_data,
    merge_relationships,
    publish_dashboard,
    summarize_review,
)
from evidence_quality import unsupported_ai_evidence


ROOT = Path(__file__).resolve().parents[1]
DASHBOARD_PATH = ROOT / "docs" / "dashboard_data.json"
DECISIONS_PATH = ROOT / "data" / "edge_review_decisions.json"


def relationship_key(source_ticker, target_ticker, dependency_type):
    return f"{source_ticker}->{target_ticker}:{dependency_type or 'Supply Link'}".upper()


def looks_like_tsm_foundry_decision(decision):
    text = " ".join(
        str(decision.get(field) or "")
        for field in ("dependency_type", "product", "evidence_excerpt", "review_note")
    ).lower()
    return any(term in text for term in FOUNDRY_TERMS)


def canonical_decision_direction(decision):
    target_ticker = decision.get("target_ticker") or ""
    if target_ticker == "TSM" and looks_like_tsm_foundry_decision(decision):
        decision = dict(decision)
        for left, right in (
            ("source_ticker", "target_ticker"),
            ("source_name", "target_name"),
        ):
            decision[left], decision[right] = decision.get(right), decision.get(left)
    return decision


def source_type(source_url):
    source_url = source_url or ""
    if source_url.startswith(("http://", "https://")):
        return "Web Source"
    if "Manual" in source_url:
        return "Manual"
    if "AI" in source_url:
        return "AI Research"
    return "Source"


def blank_company(next_id, ticker, name):
    return {
        "id": next_id,
        "name": name or ticker,
        "ticker": ticker,
        "industry": "Reviewed relationship endpoint",
        "price": None,
        "change": 0,
        "market_cap": None,
        "enterprise_value": None,
        "trailing_pe": None,
        "forward_pe": None,
        "price_to_book": None,
        "dividend": "N/A",
        "high_52w": None,
        "low_52w": None,
        "revenue": None,
        "margin": None,
        "target_price": None,
        "recommendation": "N/A",
        "ceo": "N/A",
        "employees": None,
        "summary": (
            "This company is included because it has an approved supply-chain "
            "relationship, but market metrics were not available in the latest export."
        ),
        "last_updated": "N/A",
        "upstream": [],
        "downstream": [],
    }


def decision_relationship(decision, connected_ticker, connected_name):
    source_url = decision.get("source_url") or "Unknown"
    reviewed_at = decision.get("reviewed_at") or ""
    return {
        "edge_id": decision.get("edge_id"),
        "relationship_key": relationship_key(
            decision.get("source_ticker"),
            decision.get("target_ticker"),
            decision.get("dependency_type"),
        ),
        "name": connected_name or connected_ticker,
        "ticker": connected_ticker or "",
        "type": decision.get("dependency_type") or "Supply Link",
        "product": decision.get("product") or decision.get("dependency_type") or "Supply Link",
        "confidence": decision.get("confidence_score"),
        "source": source_url,
        "source_title": decision.get("source_title") or source_url,
        "source_type": source_type(source_url),
        "review_status": decision.get("review_status") or "approved",
        "review_summary": summarize_review(decision.get("review_note"), source_url, decision.get("review_status") or "approved"),
        "revenue_share": decision.get("revenue_share"),
        "evidence_excerpt": decision.get("evidence_excerpt") or "",
        "last_verified": reviewed_at[:10] if reviewed_at else "N/A",
    }


def repair_dashboard_from_decisions(dashboard_path=DASHBOARD_PATH, decisions_path=DECISIONS_PATH):
    dashboard = json.loads(Path(dashboard_path).read_text(encoding="utf-8"))
    decisions_payload = json.loads(Path(decisions_path).read_text(encoding="utf-8"))
    decisions = decisions_payload.get("decisions", decisions_payload if isinstance(decisions_payload, list) else [])
    unsupported_approvals = {
        id(decision)
        for decision in decisions
        if decision.get("review_status") == "approved"
        and unsupported_ai_evidence(decision.get("source_url"), decision.get("evidence_excerpt"))
    }

    quality = dashboard.setdefault("quality", {})
    quality["approved_count"] = sum(
        decision.get("review_status") == "approved" and id(decision) not in unsupported_approvals
        for decision in decisions
    )
    quality["rejected_count"] = sum(
        decision.get("review_status") == "rejected" or id(decision) in unsupported_approvals
        for decision in decisions
    )

    industries = dashboard.setdefault("industries", {})
    industries.setdefault(FALLBACK_LINKED_SECTOR, [])
    companies = [company for sector_companies in industries.values() for company in sector_companies]
    by_ticker = {company.get("ticker"): company for company in companies if company.get("ticker")}
    next_id = max((int(company.get("id") or 0) for company in companies), default=0)
    for company in companies:
        company["upstream"] = []
        company["downstream"] = []

    def ensure_company(ticker, name):
        nonlocal next_id
        if not ticker:
            return None
        if ticker in by_ticker:
            return by_ticker[ticker]
        next_id += 1
        company = blank_company(next_id, ticker, name)
        industries[FALLBACK_LINKED_SECTOR].append(company)
        by_ticker[ticker] = company
        return company

    for raw_decision in decisions:
        if raw_decision.get("review_status") != "approved":
            continue
        if id(raw_decision) in unsupported_approvals:
            continue
        decision = canonical_decision_direction(raw_decision)
        source = ensure_company(decision.get("source_ticker"), decision.get("source_name"))
        target = ensure_company(decision.get("target_ticker"), decision.get("target_name"))
        if not source or not target or source.get("ticker") == target.get("ticker"):
            continue
        source.setdefault("downstream", []).append(
            decision_relationship(decision, target.get("ticker"), target.get("name"))
        )
        target.setdefault("upstream", []).append(
            decision_relationship(decision, source.get("ticker"), source.get("name"))
        )

    for sector, sector_companies in list(industries.items()):
        unique_companies = []
        seen_tickers = set()
        for company in sector_companies:
            ticker = company.get("ticker")
            if ticker in seen_tickers:
                continue
            seen_tickers.add(ticker)
            company["upstream"] = merge_relationships(company.get("upstream", []))
            company["downstream"] = merge_relationships(company.get("downstream", []))
            if sector == FALLBACK_LINKED_SECTOR and not company["upstream"] and not company["downstream"]:
                continue
            unique_companies.append(company)
        industries[sector] = unique_companies

    history_path = Path(dashboard_path).with_name("link_history.json")
    annotate_dashboard_data(dashboard, history_path)
    return publish_dashboard(dashboard, str(dashboard_path), str(history_path))


if __name__ == "__main__":
    data = repair_dashboard_from_decisions()
    companies = [company for sector_companies in data["industries"].values() for company in sector_companies]
    keys = {
        relationship.get("relationship_key")
        for company in companies
        for side in ("upstream", "downstream")
        for relationship in company.get(side, [])
        if relationship.get("relationship_key")
    }
    print(f"Dashboard repaired from decisions: {len(companies)} companies, {len(keys)} unique links")
