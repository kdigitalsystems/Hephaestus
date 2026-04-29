import os
from database import SessionLocal
from models import Node, Edge

# A curated list of heavy-hitter hardware dependencies
SEED_EDGES = [
    # Foundries & Equipment
    {"source": "ASML", "target": "TSM", "type": "EUV Lithography Equipment"},
    {"source": "TSM", "target": "AMD", "type": "Advanced Silicon Fabrication"},
    {"source": "TSM", "target": "NVDA", "type": "Advanced Silicon Fabrication"},
    {"source": "TSM", "target": "AAPL", "type": "Advanced Silicon Fabrication"},
    
    # Memory (HBM)
    {"source": "MU", "target": "NVDA", "type": "HBM3e Memory"},
    {"source": "MU", "target": "AMD", "type": "HBM3e Memory"},
    
    # Cooling & Infrastructure
    {"source": "VRT", "target": "NVDA", "type": "Data Center Liquid Cooling"},
    
    # System Integrators / OEMs
    {"source": "NVDA", "target": "SMCI", "type": "AI Accelerator Chips"},
    {"source": "AMD", "target": "SMCI", "type": "MI-Series / EPYC Processors"},
    {"source": "INTC", "target": "DELL", "type": "Server CPUs"}
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
                
            # 2. Check if this edge already exists to prevent duplicates
            existing_edge = session.query(Edge).filter(
                Edge.source_id == source_node.id,
                Edge.target_id == target_node.id
            ).first()
            
            # 3. Insert the connection
            if not existing_edge:
                new_edge = Edge(
                    source_id=source_node.id,
                    target_id=target_node.id,
                    dependency_type=edge_data["type"],
                    confidence_score=1.0, # 100% confidence for manual hardcoded seeds
                    source_url="Manual System Jumpstart"
                )
                session.add(new_edge)
                edges_added += 1
                print(f"  [+] Linked: {source_node.ticker} ➔ {target_node.ticker} ({edge_data['type']})")
            else:
                print(f"  [=] Link already exists: {source_node.ticker} ➔ {target_node.ticker}")
                
        session.commit()
        print(f"--- Edge Seeding Complete. Created {edges_added} new relationships. ---")
        
    except Exception as e:
        session.rollback()
        print(f"Database error: {e}")
    finally:
        session.close()

if __name__ == "__main__":
    seed_manual_edges()
