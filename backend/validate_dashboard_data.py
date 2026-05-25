import argparse
import json
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA_PATH = ROOT / "docs" / "dashboard_data.json"

REQUIRED_COMPANY_FIELDS = {
    "id",
    "name",
    "ticker",
    "industry",
    "price",
    "change",
    "market_cap",
    "upstream",
    "downstream",
}

REQUIRED_EDGE_FIELDS = {
    "edge_id",
    "relationship_key",
    "name",
    "ticker",
    "type",
    "product",
    "source_type",
    "review_status",
}


def load_dashboard_data(path):
    with Path(path).open(encoding="utf-8") as handle:
        return json.load(handle)


def iter_companies(data):
    for sector, companies in data.get("industries", {}).items():
        if not isinstance(companies, list):
            raise AssertionError(f"Sector {sector!r} must contain a list of companies")
        for company in companies:
            yield sector, company


def validate_dashboard_data(data):
    errors = []

    if not isinstance(data.get("industries"), dict) or not data["industries"]:
        errors.append("dashboard_data.json must contain a non-empty industries object")

    quality = data.get("quality")
    if not isinstance(quality, dict):
        errors.append("dashboard_data.json must contain a quality object")
    else:
        for field in ("pending_count", "approved_count", "rejected_count", "review_queue"):
            if field not in quality:
                errors.append(f"quality.{field} is missing")
        if not isinstance(quality.get("review_queue", []), list):
            errors.append("quality.review_queue must be a list")

    companies = list(iter_companies(data))
    tickers = []
    for sector, company in companies:
        missing = REQUIRED_COMPANY_FIELDS - set(company)
        if missing:
            errors.append(f"{sector}/{company.get('ticker', company.get('name', '<unknown>'))} missing fields: {sorted(missing)}")

        ticker = company.get("ticker")
        if not ticker:
            errors.append(f"{sector}/{company.get('name', '<unknown>')} has no ticker")
        else:
            tickers.append(ticker)

        for side in ("upstream", "downstream"):
            relationships = company.get(side, [])
            if not isinstance(relationships, list):
                errors.append(f"{ticker}.{side} must be a list")
                continue

            connected_tickers = []
            for relationship in relationships:
                missing_edge_fields = REQUIRED_EDGE_FIELDS - set(relationship)
                if missing_edge_fields:
                    errors.append(f"{ticker}.{side} edge missing fields: {sorted(missing_edge_fields)}")
                connected_ticker = relationship.get("ticker")
                if connected_ticker == ticker:
                    errors.append(f"{ticker}.{side} contains a self-link")
                if connected_ticker:
                    connected_tickers.append(connected_ticker)
                if relationship.get("review_status") == "rejected":
                    errors.append(f"{ticker}.{side} contains rejected edge {relationship.get('edge_id')}")

            duplicate_relationships = [
                rel_ticker
                for rel_ticker, count in Counter(connected_tickers).items()
                if count > 1
            ]
            if duplicate_relationships:
                errors.append(f"{ticker}.{side} has duplicate connected tickers: {duplicate_relationships[:10]}")

    duplicate_tickers = [ticker for ticker, count in Counter(tickers).items() if count > 1]
    if duplicate_tickers:
        errors.append(f"Duplicate company tickers in export: {duplicate_tickers[:20]}")

    if errors:
        raise AssertionError("\n".join(errors))

    return {
        "companies": len(companies),
        "sectors": len(data.get("industries", {})),
        "linked_companies": sum(
            1
            for _, company in companies
            if company.get("upstream") or company.get("downstream")
        ),
    }


def main():
    parser = argparse.ArgumentParser(description="Validate published Hephaestus dashboard JSON.")
    parser.add_argument("path", nargs="?", default=DEFAULT_DATA_PATH)
    args = parser.parse_args()

    summary = validate_dashboard_data(load_dashboard_data(args.path))
    print(
        "Dashboard data OK: "
        f"{summary['companies']} companies, "
        f"{summary['sectors']} sectors, "
        f"{summary['linked_companies']} linked companies"
    )


if __name__ == "__main__":
    main()
