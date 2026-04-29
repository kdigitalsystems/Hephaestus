import os
import time
import re
import argparse
import wikipedia
from sqlalchemy import func
from database import SessionLocal
from models import Node, Edge
from parser import extract_dependencies
from yahooquery import Ticker
from sec_api import ExtractorApi

# --- CONFIGURATION ---
SEC_API_KEY = "YOUR_SEC_API_KEY_HERE"
extractor = ExtractorApi(SEC_API_KEY)

# Wikimedia requires a custom User-Agent to avoid 403 errors
wikipedia.set_user_agent("HephaestusTerminal/1.0 (research@saqibdesktop.local)")

def clean_company_name(name):
    """Strips Wall Street jargon for better search results."""
    stopwords = [r'\bInc\.?\b', r'\bCorp\.?\b', r'\bCorporation\b', r'\bCompany\b',
                 r'\bLLC\b', r'\bPlc\b', r'\bLtd\.?\b', r'\bADR\b', r'\bCommon Stock\b']
    clean_name = name
    for word in stopwords:
        clean_name = re.sub(word, '', clean_name, flags=re.IGNORECASE)
    return clean_name.replace(',', '').strip()

class IntelGatherer:
    @staticmethod
    def get_wiki_data(company_name):
        try:
            search_term = clean_company_name(company_name)
            wiki_results = wikipedia.search(f"{search_term} company")
            if wiki_results:
                page = wikipedia.page(wiki_results[0], auto_suggest=False)
                return f"SOURCE: WIKIPEDIA\n{page.content[:3000]}\n"
        except: return ""
        return ""

    @staticmethod
    def get_yahoo_news(ticker):
        try:
            print(f"  [*] Pulling news for {ticker}...")
            t = Ticker(ticker)
            news = t.news(count=5)
            blob = "SOURCE: RECENT NEWS HEADLINES\n"
            for article in news:
                blob += f"- {article.get('title')}: {article.get('summary')}\n"
            return blob
        except: return ""

    @staticmethod
    def get_sec_risk_factors(ticker):
        # Note: This is a simplified fetcher. In production, use QueryApi 
        # to find the specific latest 10-K URL for the ticker.
        print(f"  [*] Searching SEC filings for {ticker}...")
        try:
            # Placeholder: In a full build, use sec-api QueryApi here to get URL
            # For now, we'll rely on News and Wiki if SEC URL is complex to find
            return "" 
        except: return ""

def auto_discover_supply_chain(limit=5):
    print(f"--- Starting Multi-Source Titan Queue (Limit: {limit}) ---")
    session = SessionLocal()
    
    try:
        # Target companies with no edges and high market cap
        lonely_nodes = session.query(Node).outerjoin(
            Edge, (Node.id == Edge.source_id) | (Node.id == Edge.target_id)
        ).filter(Edge.id == None, Node.market_cap > 300_000_000).limit(limit).all()

        if not lonely_nodes:
            print("No lonely nodes found.")
            return

        for company in lonely_nodes:
            print(f"\n[?] Researching: {company.name} ({company.ticker})")
            
            # 1. GATHER MULTI-SOURCE INTEL
            intel_blob = ""
            intel_blob += IntelGatherer.get_wiki_data(company.name)
            intel_blob += IntelGatherer.get_yahoo_news(company.ticker)
            
            if len(intel_blob) < 100:
                print(f"  [-] Insufficient data found for {company.ticker}. Skipping.")
                continue

            # 2. GPU EXTRACTION
            print(f"  [*] 3080 Ti is analyzing {len(intel_blob)} chars of data...")
            extraction = extract_dependencies(intel_blob)
            dependencies = extraction.get("dependencies", [])
            
            if not dependencies:
                print("  [-] No relationships found by AI.")
                continue

            # 3. DB MATCHING & INSERTION
            for dep in dependencies:
                source_name = dep.get('source_company')
                target_name = dep.get('target_company')
                
                # Try to find these companies in our existing DB
                source_node = session.query(Node).filter(Node.name.ilike(f"%{source_name}%")).first()
                target_node = session.query(Node).filter(Node.name.ilike(f"%{target_name}%")).first()

                if source_node and target_node:
                    existing = session.query(Edge).filter(
                        Edge.source_id == source_node.id, Edge.target_id == target_node.id
                    ).first()

                    if not existing:
                        new_edge = Edge(
                            source_id=source_node.id,
                            target_id=target_node.id,
                            dependency_type=dep.get('dependency_type', 'Supply Link'),
                            confidence_score=dep.get('confidence_score', 0.8),
                            source_url="Multi-Source Intelligence"
                        )
                        session.add(new_edge)
                        print(f"  [+] LINKED: {source_node.ticker} ➔ {target_node.ticker}")
            
            session.commit()
            time.sleep(1) # Polite delay

    except Exception as e:
        print(f"ERROR: {e}")
        session.rollback()
    finally:
        session.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=5)
    args = parser.parse_args()
    auto_discover_supply_chain(limit=args.limit)
