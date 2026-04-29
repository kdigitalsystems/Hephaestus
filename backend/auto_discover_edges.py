import os
import time
import wikipedia
from sqlalchemy import func
from database import SessionLocal
from models import Node, Edge
from parser import extract_dependencies

def auto_discover_supply_chain(limit=5):
    """
    Finds companies with no supply chain edges, scrapes their data,
    and uses the local LLM to extract and build relationships.
    """
    print(f"--- Starting Titan Queue: Auto-Discovering Edges (Limit: {limit}) ---")
    session = SessionLocal()
    
    try:
        # Find nodes that do not exist as a source or target in the Edge table
        lonely_nodes = session.query(Node).outerjoin(
            Edge, (Node.id == Edge.source_id) | (Node.id == Edge.target_id)
        ).filter(Edge.id == None, Node.market_cap > 300_000_000).limit(limit).all()

        if not lonely_nodes:
            print("All prime companies currently have tracked edges!")
            return

        print(f"Found {len(lonely_nodes)} companies needing supply chain mapping.")

        edges_created = 0

        for company in lonely_nodes:
            print(f"\n[?] Researching: {company.name} ({company.ticker})")
            
            try:
                # 1. Scrape Wikipedia for the corporate profile
                # We append "company" or "corporation" to avoid disambiguation pages
                search_query = f"{company.name} corporation"
                wiki_page = wikipedia.page(wikipedia.search(search_query)[0], auto_suggest=False)
                
                # Grab the first 3000 characters (fits safely in Llama 3's context window)
                text_to_analyze = wiki_page.content[:3000]
                print(f"  [+] Scraped {len(text_to_analyze)} characters from Wikipedia: {wiki_page.title}")
                
            except Exception as e:
                print(f"  [-] Failed to scrape data for {company.name}: {e}")
                continue

            # 2. Feed the text to your local RTX 3080 Ti via Ollama
            print("  [*] Running local AI extraction...")
            extraction = extract_dependencies(text_to_analyze)
            dependencies = extraction.get("dependencies", [])
            
            if not dependencies:
                print("  [-] AI found no hardware/supply chain links in this text.")
                continue

            # 3. Match the AI's findings back to our database
            for dep in dependencies:
                source_name = dep.get('source_company')
                target_name = dep.get('target_company')
                dep_type = dep.get('dependency_type', 'Strategic Dependency')
                
                # Safety check
                if not source_name or not target_name:
                    continue

                # Fuzzy search the database for the supplier and buyer
                # We use ilike for case-insensitive matching
                source_node = session.query(Node).filter(Node.name.ilike(f"%{source_name}%")).first()
                target_node = session.query(Node).filter(Node.name.ilike(f"%{target_name}%")).first()

                # If the AI hallucinates a company not in our DB, we skip it to keep the DB clean
                if not source_node or not target_node:
                    continue
                    
                # Prevent duplicate edges
                existing_edge = session.query(Edge).filter(
                    Edge.source_id == source_node.id,
                    Edge.target_id == target_node.id
                ).first()

                if not existing_edge:
                    new_edge = Edge(
                        source_id=source_node.id,
                        target_id=target_node.id,
                        dependency_type=dep_type,
                        confidence_score=dep.get('confidence_score', 0.8),
                        source_url=wiki_page.url
                    )
                    session.add(new_edge)
                    edges_created += 1
                    print(f"  [+] NEW EDGE LINKED: {source_node.ticker} ➔ {target_node.ticker} ({dep_type})")
                else:
                    print(f"  [=] Edge already exists: {source_node.ticker} ➔ {target_node.ticker}")

            # Commit after every company so we don't lose progress if it crashes
            session.commit()
            time.sleep(2) # Prevent Wikipedia API throttling

        print(f"\n--- Titan Queue Complete. Generated {edges_created} proprietary relationships. ---")

    except Exception as e:
        print(f"Database error: {e}")
        session.rollback()
    finally:
        session.close()

if __name__ == "__main__":
    # You can change this limit to 50 or 100 when letting it run overnight
    auto_discover_supply_chain(limit=5)
