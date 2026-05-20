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
EXPORT_AI_RESEARCH = os.environ.get("HEPHAESTUS_EXPORT_AI_RESEARCH", "0") == "1"

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

def edge_payload(edge, node):
    connected_node = node
    source_url = edge.source_url or "Unknown"
    if source_url.startswith(("http://", "https://")):
        source_type = "Web Source"
    elif "Manual" in source_url:
        source_type = "Manual"
    elif "AI" in source_url:
        source_type = "AI Research"
    else:
        source_type = "Source"

    return {
        "edge_id": edge.id,
        "name": connected_node.name,
        "ticker": connected_node.ticker or "",
        "type": edge.dependency_type,
        "product": edge.product or edge.dependency_type,
        "confidence": clean_num(edge.confidence_score),
        "source": source_url,
        "source_type": source_type,
        "last_verified": edge.last_verified.strftime('%Y-%m-%d') if edge.last_verified else "N/A"
    }

def should_export_node(node):
    if not node.market_cap or not node.current_price:
        return False
    if node.market_cap < MIN_MARKET_CAP:
        return False

    sector = node.sector if node.sector else "Uncategorized"
    return sector not in IGNORED_SECTORS

def should_export_edge(edge):
    source_url = edge.source_url or ""
    if "AI" in source_url and not EXPORT_AI_RESEARCH:
        return False
    return True

def export_to_json():
    session = SessionLocal()
    try:
        nodes = session.query(Node).all()
        dashboard_data = { "industries": {} }
        
        for node in nodes:
            if not should_export_node(node):
                continue
                
            sector = node.sector if node.sector else "Uncategorized"
                
            if sector not in dashboard_data["industries"]:
                dashboard_data["industries"][sector] = []
            
            # --- NEW: X-RAY LOGIC ---
            # Grab all companies that supply THIS node (Upstream)
            upstream = []
            for edge in node.supplied_by:
                if not should_export_edge(edge):
                    continue
                if edge.source_node and should_export_node(edge.source_node):
                    upstream.append(edge_payload(edge, edge.source_node))
            
            # Grab all companies that THIS node supplies (Downstream)
            downstream = []
            for edge in node.supplies_to:
                if not should_export_edge(edge):
                    continue
                if edge.target_node and should_export_node(edge.target_node):
                    downstream.append(edge_payload(edge, edge.target_node))
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
            
        mode = "manual plus AI research" if EXPORT_AI_RESEARCH else "reviewed/manual only"
        print(f"Export Complete with Supply Chain X-Ray metrics included ({mode}).")
        
    except Exception as e:
        print(f"Error exporting database: {e}")
    finally:
        session.close()

if __name__ == "__main__":
    export_to_json()
