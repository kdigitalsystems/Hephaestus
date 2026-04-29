import os
import argparse
from database import SessionLocal, init_db
from models import Node
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import GetAssetsRequest
from alpaca.trading.enums import AssetClass, AssetStatus

API_KEY = "PKU4JFER6M3KG4N2ZGA9"
SECRET_KEY = "1AEXwFm9h6u1MK7riEp9oZgF6hVt6eWDSn7PmHcg"

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
