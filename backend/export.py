import os
import json
import math
from database import SessionLocal
from models import Node

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS_DIR = os.path.join(BASE_DIR, "docs")
EXPORT_PATH = os.path.join(DOCS_DIR, "dashboard_data.json")

# ---------------------------------------------------------
# RELEVANCE FILTERS
# ---------------------------------------------------------
# Set to 0 temporarily for DEV mode so your test actually shows data.
MIN_MARKET_CAP = 0 
IGNORED_SECTORS = ["Shell Companies", "Uncategorized", "Financial Services", "Real Estate"]

def clean_num(val):
    """Indestructible cleaner that catches Numpy/Pandas Infinity and NaN types."""
    if val is None:
        return None
    try:
        # Force conversion to native Python float. This breaks the Numpy float64 disguise.
        f_val = float(val)
        if math.isnan(f_val) or math.isinf(f_val):
            return None
        return f_val
    except (ValueError, TypeError):
        # If it's a string like "N/A" that can't be cast to float, just return it.
        return val

def export_to_json():
    session = SessionLocal()
    try:
        nodes = session.query(Node).all()
        dashboard_data = { "industries": {} }
        
        exported_count = 0
        filtered_count = 0
        
        for node in nodes:
            # 1. Filter out "Dead" or unlisted tickers
            if not node.market_cap or not node.current_price:
                filtered_count += 1
                continue
                
            # 2. Filter out Micro-caps
            if node.market_cap < MIN_MARKET_CAP:
                filtered_count += 1
                continue
                
            # 3. Filter out irrelevant sectors
            sector = node.sector if node.sector else "Uncategorized"
            if sector in IGNORED_SECTORS:
                filtered_count += 1
                continue
                
            if sector not in dashboard_data["industries"]:
                dashboard_data["industries"][sector] = []
                
            dashboard_data["industries"][sector].append({
                "id": node.id,
                "name": node.name,
                "ticker": node.ticker,
                "industry": node.industry or "N/A",
                "price": clean_num(node.current_price),
                "change": clean_num(node.percent_change) or 0.0,
                "market_cap": clean_num(node.market_cap),
                "enterprise_value": clean_num(node.enterprise_value),
                "trailing_pe": clean_num(node.trailing_pe),
                "forward_pe": clean_num(node.forward_pe),
                "price_to_book": clean_num(node.price_to_book),
                "dividend": node.dividend_yield or "N/A",
                "high_52w": clean_num(node.fifty_two_week_high),
                "low_52w": clean_num(node.fifty_two_week_low),
                "revenue": clean_num(node.total_revenue),
                "margin": clean_num(node.gross_margin),
                "target_price": clean_num(node.target_price),
                "recommendation": node.recommendation or "N/A",
                "ceo": node.ceo_name or "N/A",
                "employees": node.employees,
                "summary": node.business_summary or "No summary available.",
                "last_updated": node.last_updated.strftime('%Y-%m-%d') if node.last_updated else "N/A"
            })
            exported_count += 1
            
        os.makedirs(DOCS_DIR, exist_ok=True)
        
        # allow_nan=False guarantees Python crashes BEFORE writing bad JSON to the file
        with open(EXPORT_PATH, "w") as f:
            json.dump(dashboard_data, f, indent=2, allow_nan=False)
            
        print(f"Export Complete: Kept {exported_count} prime companies.")
        print(f"Filtered out {filtered_count} irrelevant companies.")
        
    except Exception as e:
        print(f"Error exporting database: {e}")
    finally:
        session.close()

if __name__ == "__main__":
    export_to_json()
