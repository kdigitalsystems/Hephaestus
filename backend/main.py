import sys
import os
import time
from scraper import scrape_article
from parser import extract_dependencies
from database import SessionLocal
from models import Node, Edge

# Define the path for a text file containing URLs to scrape daily
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
URL_LIST_PATH = os.path.join(BASE_DIR, "target_urls.txt")

def process_source(url: str):
    """
    End-to-end pipeline for a single URL: Scrape -> Extract via LLM -> Save to Database
    """
    print(f"\n--- Starting Processing for: {url} ---")
    
    # 1. Scrape the text
    text = scrape_article(url)
    if not text:
        print("Failed to retrieve text or text was empty. Skipping.")
        return

    # 2. Extract dependencies using the local Ollama model
    print("Routing text to local LLM for extraction...")
    extraction = extract_dependencies(text)
    
    # Check if the model returned data and if it contains the 'dependencies' key
    if not extraction or "dependencies" not in extraction:
        print("No valid JSON dependencies returned by the model. Skipping.")
        return
        
    dependencies = extraction.get("dependencies", [])
    
    if not dependencies:
        print("Model processed the text but found no supply chain dependencies.")
        return
        
    print(f"Found {len(dependencies)} dependencies. Saving to database...")

    # 3. Save to database
    session = SessionLocal()
    try:
        for dep in dependencies:
            # Safely handle potential missing keys from the LLM output
            source_name = dep.get('source_company')
            target_name = dep.get('target_company')
            dep_type = dep.get('dependency_type', 'Unknown')
            conf_score = dep.get('confidence_score', 0.5)

            if not source_name or not target_name:
                print(f"Skipping malformed dependency entry: {dep}")
                continue

            # Get or create source node
            source_node = session.query(Node).filter_by(name=source_name).first()
            if not source_node:
                source_node = Node(name=source_name, entity_type="Company")
                session.add(source_node)
                session.flush() # Get ID before committing

            # Get or create target node
            target_node = session.query(Node).filter_by(name=target_name).first()
            if not target_node:
                target_node = Node(name=target_name, entity_type="Company")
                session.add(target_node)
                session.flush()

            # Create the edge
            new_edge = Edge(
                source_id=source_node.id,
                target_id=target_node.id,
                dependency_type=dep_type,
                confidence_score=conf_score,
                source_url=url
            )
            session.add(new_edge)
            
        session.commit()
        print("Database update successful.")
        
    except Exception as e:
        session.rollback()
        print(f"Database error while saving data for {url}: {e}")
    finally:
        session.close()

def batch_process_from_file(file_path: str):
    """
    Reads a list of URLs from a text file and processes them sequentially.
    """
    if not os.path.exists(file_path):
        print(f"URL list file not found at {file_path}. Creating a blank one.")
        with open(file_path, "w") as f:
            f.write("# Add target URLs below, one per line.\n")
        return

    with open(file_path, "r") as f:
        urls = [line.strip() for line in f if line.strip() and not line.startswith("#")]

    if not urls:
        print(f"No URLs found in {file_path}.")
        return

    print(f"Found {len(urls)} URLs to process in batch mode.")
    
    for url in urls:
        process_source(url)
        # Add a short sleep between requests to avoid overwhelming target servers
        time.sleep(2)

if __name__ == "__main__":
    # If a URL is passed as a command-line argument, process just that URL.
    # Otherwise, default to reading from the target_urls.txt file.
    if len(sys.argv) > 1:
        target_url = sys.argv[1]
        process_source(target_url)
    else:
        batch_process_from_file(URL_LIST_PATH)
