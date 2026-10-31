import os
import argparse
from database import SessionLocal, init_db
from models import Node
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import GetAssetsRequest
from alpaca.trading.enums import AssetClass, AssetStatus


def get_alpaca_credentials():
    """Loads keys from the local SSH folder, falling back to env variables for GitHub Actions."""
    api_key = os.environ.get("ALPACA_API_KEY")
    secret_key = os.environ.get("ALPACA_SECRET_KEY")

    # If environment variables exist (GitHub Actions), use them
    if api_key and secret_key:
        return api_key, secret_key

    # Otherwise, read from the local Fulshear desktop file
    key_path = os.path.expanduser("~/.ssh/alpaca_paper_keys")
    if os.path.exists(key_path):
        with open(key_path, "r") as f:
            for line in f:
                line = line.strip()
                # Split by the first colon only
                if line.startswith("Key:"):
                    api_key = line.split(":", 1)[1].strip()
                elif line.startswith("Secret_Key:"):
                    secret_key = line.split(":", 1)[1].strip()

    if not api_key or not secret_key:
        raise ValueError("Missing Alpaca API credentials. Check ~/.ssh/alpaca_paper_keys or env vars.")
        
    return api_key, secret_key

VALID_EXCHANGES = ("NASDAQ", "NYSE", "ARCA", "BATS")


def new_company_nodes(assets, existing_tickers):
    """The Node rows for the assets worth adding, and a count of what was skipped per exchange.

    `assets` are raw dicts from Alpaca's /assets endpoint, read defensively: on 2026-10-31 the
    list gained assets on a new exchange (TXSE, the Texas Stock Exchange) that alpaca-py's strict
    enum did not know, its parser rejected the WHOLE list, and the pipeline could not start. Here an
    exchange the filter does not keep is simply skipped, whatever it is called.
    """
    new_nodes = []
    seen_names = set()
    skipped_exchanges = {}
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        symbol, name = asset.get("symbol"), asset.get("name")
        if not symbol or not name:
            continue

        # 1. Filter out structural variants (Warrants, Preferred Shares, Class B)
        if "." in symbol or "-" in symbol:
            continue

        # 2. Filter out OTC / Pink Sheets and any exchange we do not keep (Keep only Major Exchanges)
        exchange = asset.get("exchange") or ""
        if exchange not in VALID_EXCHANGES:
            skipped_exchanges[exchange or "(none)"] = skipped_exchanges.get(exchange or "(none)", 0) + 1
            continue

        # 3. The Quality Filter: Must be marginable and tradable
        if not asset.get("marginable") or not asset.get("tradable"):
            continue

        # 4. Final check: Is it new to us?
        if symbol not in existing_tickers and name not in seen_names:
            new_nodes.append(Node(name=name, ticker=symbol, sector="Pending Update", entity_type="Company"))
            seen_names.add(name)
    return new_nodes, skipped_exchanges


def seed_database_from_alpaca(limit=None):
    """Fetches active US equities from Alpaca and seeds the database."""
    print("--- Fetching All Active Assets from Alpaca ---")

    api_key, secret_key = get_alpaca_credentials()

    # raw_data=True: plain dicts instead of alpaca-py's pydantic models, whose enums reject an exchange
    # code the library has not heard of (see new_company_nodes).
    trading_client = TradingClient(api_key, secret_key, paper=True, raw_data=True)

    search_params = GetAssetsRequest(
        asset_class=AssetClass.US_EQUITY,
        status=AssetStatus.ACTIVE
    )

    try:
        assets = trading_client.get_all_assets(search_params)
        print(f"Retrieved {len(assets)} active assets from Alpaca.")

        session = SessionLocal()

        existing_nodes = session.query(Node.ticker).all()
        existing_tickers = {node[0] for node in existing_nodes if node[0]}

        new_nodes, skipped_exchanges = new_company_nodes(assets, existing_tickers)
        if skipped_exchanges:
            shown = ", ".join(f"{name} {count}" for name, count in sorted(skipped_exchanges.items(), key=lambda item: -item[1])[:8])
            print(f"Skipped assets on exchanges outside {'/'.join(VALID_EXCHANGES)}: {shown}.")

        # Apply the development limit if provided
        if limit and len(new_nodes) > limit:
            new_nodes = new_nodes[:limit]
            print(f"DEV MODE: Limiting Alpaca insertions to {limit} companies.")

        if new_nodes:
            print(f"Adding {len(new_nodes)} clean common-stock companies to the database...")
            session.add_all(new_nodes)
            session.commit()
            print("--- Alpaca Seeding Complete ---")
        else:
            print("--- Database already contains all active Alpaca assets. ---")

    except Exception as e:
        if 'session' in locals():
            session.rollback()
        print(f"Error during seeding: {e}")
        # Exit non-zero so `set -e` pipelines stop instead of exporting an unseeded graph.
        raise
    finally:
        if 'session' in locals():
            session.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None, help="Limit the number of stocks to seed")
    args = parser.parse_args()
    
    init_db()
    seed_database_from_alpaca(limit=args.limit)
