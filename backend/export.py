import os
import json
import math
from database import SessionLocal
from models import Node, Edge

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS_DIR = os.path.join(BASE_DIR, "docs")
EXPORT_PATH = os.path.join(DOCS_DIR, "dashboard_data.json")

MIN_MARKET_CAP = 0 
IGNORED_SECTORS = ["Shell Companies", "Uncategorized", "Financial Services", "Real Estate"]

def clean_num(val):
    if val is None:
        return None
    try:
        f_val = float(val)
        if math.isnan(f_val) or math.isinf(f_val):
            return None
        return f_val
    except (ValueError, TypeError):
        return val

def export_to_json():
    session = SessionLocal()
    try:
        nodes = session.query(Node).all()
        dashboard_data = { "industries": {} }
        
        for node in nodes:
            if not node.market_cap or not node.current_price:
                continue
            if node.market_cap < MIN_MARKET_CAP:
                continue
                
            sector = node.sector if node.sector else "Uncategorized"
            if sector in IGNORED_SECTORS:
                continue
                
            if sector not in dashboard_data["industries"]:
                dashboard_data["industries"][sector] = []
            
            # --- NEW: X-RAY LOGIC ---
            # Grab all companies that supply THIS node (Upstream)
            upstream = []
            for edge in node.supplied_by:
                if edge.source_node:
                    upstream.append({
                        "name": edge.source_node.name,
                        "ticker": edge.source_node.ticker or "",
                        "type": edge.dependency_type
                    })
            
            # Grab all companies that THIS node supplies (Downstream)
            downstream = []
            for edge in node.supplies_to:
                if edge.target_node:
                    downstream.append({
                        "name": edge.target_node.name,
                        "ticker": edge.target_node.ticker or "",
                        "type": edge.dependency_type
                    })
            # ------------------------

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
                "last_updated": node.last_updated.strftime('%Y-%m-%d') if node.last_updated else "N/A",
                "upstream": upstream,       # Added to JSON
                "downstream": downstream    # Added to JSON
            })
            
        os.makedirs(DOCS_DIR, exist_ok=True)
        
        with open(EXPORT_PATH, "w") as f:
            json.dump(dashboard_data, f, indent=2, allow_nan=False)
            
        print("Export Complete with Supply Chain X-Ray metrics included.")
        
    except Exception as e:
        print(f"Error exporting database: {e}")
    finally:
        session.close()

if __name__ == "__main__":
    export_to_json()
