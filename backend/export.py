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
REVIEW_QUEUE_LIMIT = int(os.environ.get("HEPHAESTUS_REVIEW_QUEUE_LIMIT", "250"))
FOUNDRY_TERMS = (
    "advanced silicon fabrication",
    "advanced manufacturing services",
    "chip fabrication",
    "chip manufacturing",
    "chip production",
    "contract manufacturing",
    "foundry",
    "outsourced production",
    "semiconductor chips",
    "semiconductor manufacturing",
    "silicon fabrication",
    "silicon wafers",
)

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
        "source_title": edge.source_title or source_url,
        "source_type": source_type,
        "review_status": edge.review_status or "pending",
        "evidence_excerpt": edge.evidence_excerpt or "",
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
    if edge.review_status == "rejected":
        return False
    if edge.review_status == "approved":
        return True
    if "Manual" in source_url:
        return True
    if "AI" in source_url and EXPORT_AI_RESEARCH:
        return True
    return False

def looks_like_tsm_foundry_edge(edge):
    text = " ".join(
        str(value or "")
        for value in (
            edge.dependency_type,
            edge.product,
            edge.evidence_excerpt,
            edge.review_note,
        )
    ).lower()
    return any(term in text for term in FOUNDRY_TERMS)

def canonical_edge_nodes(edge):
    """Return supplier, customer after correcting known foundry direction errors."""
    source = edge.source_node
    target = edge.target_node
    if not source or not target:
        return source, target

    target_ticker = target.ticker or ""
    if (
        target_ticker == "TSM"
        and looks_like_tsm_foundry_edge(edge)
    ):
        return target, source

    return source, target

def review_edge_payload(edge):
    return {
        "edge_id": edge.id,
        "source_ticker": edge.source_node.ticker if edge.source_node else "",
        "source_name": edge.source_node.name if edge.source_node else "",
        "target_ticker": edge.target_node.ticker if edge.target_node else "",
        "target_name": edge.target_node.name if edge.target_node else "",
        "type": edge.dependency_type,
        "product": edge.product or edge.dependency_type,
        "confidence": clean_num(edge.confidence_score),
        "source_url": edge.source_url or "",
        "source_title": edge.source_title or edge.source_url or "",
        "evidence_excerpt": edge.evidence_excerpt or "",
        "review_status": edge.review_status or "pending",
        "review_note": edge.review_note or "",
        "last_verified": edge.last_verified.strftime('%Y-%m-%d') if edge.last_verified else "N/A"
    }

def export_to_json():
    session = SessionLocal()
    try:
        nodes = session.query(Node).all()
        pending_edges = session.query(Edge).filter(Edge.review_status == "pending").order_by(
            Edge.confidence_score.desc().nullslast(),
            Edge.id.asc()
        ).limit(REVIEW_QUEUE_LIMIT).all()
        rejected_count = session.query(Edge).filter(Edge.review_status == "rejected").count()
        approved_count = session.query(Edge).filter(Edge.review_status == "approved").count()

        dashboard_data = {
            "industries": {},
            "quality": {
                "pending_count": session.query(Edge).filter(Edge.review_status == "pending").count(),
                "approved_count": approved_count,
                "rejected_count": rejected_count,
                "review_queue": [review_edge_payload(edge) for edge in pending_edges]
            }
        }
        
        for node in nodes:
            if not should_export_node(node):
                continue
                
            sector = node.sector if node.sector else "Uncategorized"
                
            if sector not in dashboard_data["industries"]:
                dashboard_data["industries"][sector] = []
            
            # --- X-RAY LOGIC ---
            upstream = []
            downstream = []
            seen_edges = set()
            for edge in [*node.supplied_by, *node.supplies_to]:
                if not should_export_edge(edge):
                    continue
                if edge.id in seen_edges:
                    continue
                seen_edges.add(edge.id)

                supplier_node, customer_node = canonical_edge_nodes(edge)
                if node.id == customer_node.id and should_export_node(supplier_node):
                    upstream.append(edge_payload(edge, supplier_node))
                elif node.id == supplier_node.id and should_export_node(customer_node):
                    downstream.append(edge_payload(edge, customer_node))
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
