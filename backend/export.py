import os
import json
from database import SessionLocal
from models import Node, Edge

# Define paths to save the JSON directly into your GitHub Pages folder
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS_DIR = os.path.join(BASE_DIR, "docs")
EXPORT_PATH = os.path.join(DOCS_DIR, "supply_chain_data.json")

def export_to_json():
    """
    Queries the local SQLite database and exports the supply chain 
    graph into a static JSON file for the frontend.
    """
    session = SessionLocal()
    
    try:
        # Fetch all nodes and edges
        nodes = session.query(Node).all()
        edges = session.query(Edge).all()
        
        # Format for graph visualization libraries (like Cytoscape.js)
        graph_data = {
            "nodes": [],
            "edges": []
        }
        
        for node in nodes:
            graph_data["nodes"].append({
                "data": {
                    "id": str(node.id),
                    "name": node.name,
                    "type": node.entity_type,
                    "level": node.hierarchy_level
                }
            })
            
        for edge in edges:
            graph_data["edges"].append({
                "data": {
                    "id": f"e_{edge.id}",
                    "source": str(edge.source_id),
                    "target": str(edge.target_id),
                    "label": edge.dependency_type,
                    "weight": edge.confidence_score
                }
            })
            
        # Ensure the docs directory exists
        os.makedirs(DOCS_DIR, exist_ok=True)
        
        # Write the JSON payload
        with open(EXPORT_PATH, "w") as f:
            json.dump(graph_data, f, indent=2)
            
        print(f"Successfully exported {len(nodes)} nodes and {len(edges)} edges to {EXPORT_PATH}")
        print("Ready for git commit and push to GitHub Pages.")
        
    except Exception as e:
        print(f"Error exporting database: {e}")
    finally:
        session.close()

if __name__ == "__main__":
    export_to_json()
