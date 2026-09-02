from database import SessionLocal
from models import Node, Edge

# A curated list of heavy-hitter hardware dependencies
SEED_EDGES = [
    # Foundries & Equipment
    {"source": "ASML", "target": "TSM", "type": "Semiconductor Equipment", "product": "EUV lithography systems"},
    {"source": "TSM", "target": "AMD", "type": "Advanced Silicon Fabrication", "product": "Advanced process node chip fabrication"},
    {"source": "TSM", "target": "NVDA", "type": "Advanced Silicon Fabrication", "product": "Advanced process node chip fabrication"},
    {"source": "TSM", "target": "AAPL", "type": "Advanced Silicon Fabrication", "product": "Advanced process node chip fabrication"},
    
    # Memory (HBM)
    {"source": "MU", "target": "NVDA", "type": "High-Bandwidth Memory", "product": "HBM3e memory"},
    {"source": "MU", "target": "AMD", "type": "High-Bandwidth Memory", "product": "HBM3e memory"},
    
    # Cooling & Infrastructure
    {"source": "VRT", "target": "NVDA", "type": "Data Center Cooling", "product": "Liquid cooling infrastructure"},
    
    # System Integrators / OEMs
    {"source": "NVDA", "target": "SMCI", "type": "AI Accelerator Chips", "product": "GPU accelerators"},
    {"source": "AMD", "target": "SMCI", "type": "Server Processors", "product": "MI-series accelerators and EPYC CPUs"},
    {"source": "INTC", "target": "DELL", "type": "Server CPUs", "product": "Xeon server processors"}
]

def seed_manual_edges():
    print("--- Seeding Manual Supply Chain Edges ---")
    session = SessionLocal()
    
    try:
        edges_added = 0
        for edge_data in SEED_EDGES:
            # 1. Find the database IDs for the source and target companies
            source_node = session.query(Node).filter(Node.ticker == edge_data["source"]).first()
            target_node = session.query(Node).filter(Node.ticker == edge_data["target"]).first()
            
            if not source_node:
                print(f"  [!] Skipping: Could not find source ticker '{edge_data['source']}' in database.")
                continue
            if not target_node:
                print(f"  [!] Skipping: Could not find target ticker '{edge_data['target']}' in database.")
                continue
                
            # 2. Check if this exact edge already exists to prevent duplicates. Other
            #    relationships between the same pair (e.g. a reviewed AI edge with a
            #    different dependency type) are legitimate and must be left alone.
            existing_edge = session.query(Edge).filter(
                Edge.source_id == source_node.id,
                Edge.target_id == target_node.id,
                Edge.dependency_type == edge_data["type"],
            ).first()
            
            # 3. Insert the connection
            if not existing_edge:
                new_edge = Edge(
                    source_id=source_node.id,
                    target_id=target_node.id,
                    dependency_type=edge_data["type"],
                    product=edge_data.get("product"),
                    confidence_score=1.0, # 100% confidence for manual hardcoded seeds
                    source_url="Manual System Jumpstart",
                    source_title="Manual System Jumpstart",
                    review_status="approved",
                    review_note="Curated seed relationship"
                )
                session.add(new_edge)
                edges_added += 1
                print(f"  [+] Linked: {source_node.ticker} -> {target_node.ticker} ({edge_data['type']})")
            else:
                if edge_data.get("product"):
                    existing_edge.product = edge_data["product"]
                existing_edge.confidence_score = 1.0
                existing_edge.source_url = "Manual System Jumpstart"
                existing_edge.source_title = "Manual System Jumpstart"
                existing_edge.review_status = "approved"
                existing_edge.review_note = "Curated seed relationship"
                print(f"  [=] Link already exists: {source_node.ticker} -> {target_node.ticker}")
                
        session.commit()
        print(f"--- Edge Seeding Complete. Created {edges_added} new relationships. ---")
        
    except Exception as e:
        session.rollback()
        print(f"Database error: {e}")
    finally:
        session.close()

if __name__ == "__main__":
    seed_manual_edges()
