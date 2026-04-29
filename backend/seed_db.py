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

# Load the keys safely
API_KEY, SECRET_KEY = get_alpaca_credentials()

def seed_database_from_alpaca(limit=None):
    """Fetches active US equities from Alpaca and seeds the database."""
    print("--- Fetching All Active Assets from Alpaca ---")
    
    trading_client = TradingClient(API_KEY, SECRET_KEY, paper=True)
    
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
        seen_names = set()
        
        new_nodes = []
        for asset in assets:
            if '.' in asset.symbol or '-' in asset.symbol:
                continue
                
            if asset.symbol not in existing_tickers and asset.tradable and asset.name not in seen_names:
                new_node = Node(
                    name=asset.name,
                    ticker=asset.symbol,
                    sector="Pending Update",
                    entity_type="Company"
                )
                new_nodes.append(new_node)
                seen_names.add(asset.name)
        
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
        print(f"Error during seeding: {e}")
    finally:
        if 'session' in locals():
            session.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None, help="Limit the number of stocks to seed")
    args = parser.parse_args()
    
    init_db()
    seed_database_from_alpaca(limit=args.limit)
