import json
from pathlib import Path

from export import (
    FALLBACK_LINKED_SECTOR,
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


def source_type(source_url):
    source_url = source_url or ""
    if source_url.startswith(("http://", "https://")):
        return "Web Source"
    if "Manual" in source_url:
        return "Manual"
    if "AI" in source_url:
        return "AI Research"
    return "Source"


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
    for company in companies:
        company["upstream"] = []
        company["downstream"] = []

    # A company the export left out is left out for a reason (a financial, real-estate or
    # shell company, or one this database does not hold). This step used to re-add it as a
    # blank "Reviewed relationship endpoint" page, which published the very companies the
    # export excludes; it now only attaches links to companies the export published.
    skipped = []
    for raw_decision in decisions:
        if raw_decision.get("review_status") != "approved":
            continue
        if id(raw_decision) in unsupported_approvals:
            continue
        # Directions are corrected in the database before the decisions are exported;
        # flipping them again here published ASML's EUV machines as TSM -> ASML.
        decision = raw_decision
        source = by_ticker.get(decision.get("source_ticker"))
        target = by_ticker.get(decision.get("target_ticker"))
        if not source or not target:
            skipped.append(f"{decision.get('source_ticker')}->{decision.get('target_ticker')}")
            continue
        if source.get("ticker") == target.get("ticker"):
            continue
        source.setdefault("downstream", []).append(
            decision_relationship(decision, target.get("ticker"), target.get("name"))
        )
        target.setdefault("upstream", []).append(
            decision_relationship(decision, source.get("ticker"), source.get("name"))
        )
    if skipped:
        shown = ", ".join(skipped[:10]) + (f" and {len(skipped) - 10} more" if len(skipped) > 10 else "")
        print(f"Repair left out {len(skipped)} approved decision(s) whose company the export does not publish: {shown}")

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
