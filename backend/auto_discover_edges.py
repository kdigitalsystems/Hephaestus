import os
import time
import re
import argparse
import wikipedia
import json
import warnings
from sqlalchemy import func, or_
from thefuzz import fuzz

from database import SessionLocal
from models import Node, Edge
from parser import extract_dependencies
from yahooquery import Ticker

# --- CONFIGURATION ---
# Wikimedia requires a custom User-Agent to avoid 403 Forbidden errors
wikipedia.set_user_agent("HephaestusTerminal/1.0 (research@saqibdesktop.local)")
# Suppress BeautifulSoup warnings from the wikipedia library
warnings.filterwarnings("ignore", category=UserWarning, module='wikipedia')

def clean_company_name(name):
    """Strips Wall Street jargon so Wikipedia/Search can find the actual entity."""
    stopwords = [
        r'\bInc\.?\b', r'\bCorp\.?\b', r'\bCorporation\b', r'\bCompany\b',
        r'\bLLC\b', r'\bPlc\b', r'\bLtd\.?\b', r'\bADR\b', r'\bCommon Stock\b',
        r'\bClass A\b', r'\bClass B\b', r'\bOrdinary Shares\b', r'\bTrust\b'
    ]
    clean_name = name
    for word in stopwords:
        clean_name = re.sub(word, '', clean_name, flags=re.IGNORECASE)
    return clean_name.replace(',', '').strip()

class EntityResolver:
    """
    The Dynamic Resolution Engine: Matches AI-extracted names to your 12,000 tickers.
    Uses fuzzy logic + Market Cap weighting to resolve 'Google' to 'GOOGL' instead of 'GOOP'.
    """
    @staticmethod
    def resolve(session, name_or_ticker):
        if not name_or_ticker or len(str(name_or_ticker)) < 2:
            return None
        
        search_val = str(name_or_ticker).strip()

        # 1. ATTEMPT: Exact Ticker Match (Highest Priority)
        node = session.query(Node).filter(Node.ticker == search_val.upper()).first()
        if node:
            return node

        # 2. ATTEMPT: Gather potentials for fuzzy matching
        # We filter for companies > $100M to automatically ignore penny stocks/SPAC noise
        potentials = session.query(Node).filter(
            or_(
                Node.name.ilike(f"%{search_val}%"),
                Node.ticker.ilike(f"%{search_val}%")
            )
        ).filter(Node.market_cap > 100_000_000).all()

        if not potentials:
            return None

        # 3. ATTEMPT: Weighted Fuzzy Logic
        best_match = None
        highest_score = 0

        for p in potentials:
            # token_set_ratio handles "Google" matching "Alphabet Inc (Google)" perfectly
            score = fuzz.token_set_ratio(search_val, p.name)
            
            # TIE-BREAKER: If scores are tied or very close, the company with the larger 
            # Market Cap is likely the one the AI/News is talking about.
            if score > highest_score:
                highest_score = score
                best_match = p
            elif abs(score - highest_score) < 5 and best_match:
                if (p.market_cap or 0) > (best_match.market_cap or 0):
                    best_match = p

        # We only accept the match if the fuzzy score is high enough (>85)
        return best_match if highest_score > 85 else None

class IntelGatherer:
    @staticmethod
    def get_wiki_data(company_name, ticker):
        """Fetches corporate Wikipedia data focusing on supply chain/operations."""
        try:
            search_term = clean_company_name(company_name)
            # Add context keywords to force Wikipedia away from ancient history/biographies
            search_query = f"{search_term} {ticker} corporate supply chain"
            
            wiki_results = wikipedia.search(search_query)
            if not wiki_results:
                return ""

            # Try to pick the most 'corporate' looking title from top 3
            selected_title = wiki_results[0]
            for result in wiki_results[:3]:
                if any(k in result.lower() for k in ["inc", "corp", "company", "corporation", "(company)"]):
                    selected_title = result
                    break
            
            page = wikipedia.page(selected_title, auto_suggest=False)
            content = page.content
            
            # Target specific sections likely to contain B2B relationships
            target_sections = ["Operations", "Products", "Supply chain", "Partnerships", "Customers", "Infrastructure"]
            relevant_text = ""
            for section in target_sections:
                if section in content:
                    start = content.find(section)
                    relevant_text += content[start:start+2500]
            
            # Fallback to summary and first chunk if specific sections aren't named
            if not relevant_text:
                relevant_text = page.summary + "\n" + content[:3500]

            return f"SOURCE: WIKIPEDIA (Page: {page.title})\nDATA:\n{relevant_text}\n"
        except:
            return ""

    @staticmethod
    def get_yahoo_news(ticker):
        """Fetches the latest 5 news headlines/summaries via YahooQuery."""
        try:
            t = Ticker(ticker)
            news = t.news(count=5)
            blob = "SOURCE: RECENT NEWS HEADLINES\n"
            for article in news:
                blob += f"- {article.get('title')}: {article.get('summary')}\n"
            return blob
        except:
            return ""

def auto_discover_supply_chain(limit=5):
    print(f"--- Starting Refined Titan Queue (Limit: {limit}) ---")
    session = SessionLocal()
    
    # Noise sectors to skip (SPACs, Mutual Funds, Banks)
    IGNORED_SECTORS = [
        "Financial Services", "Real Estate", "Financial", 
        "Asset Management", "Insurance", "Banks"
    ]

    try:
        # Research companies with high market cap that lack supply chain data
        query = session.query(Node).outerjoin(
            Edge, or_(Node.id == Edge.source_id, Node.id == Edge.target_id)
        ).filter(
            Edge.id == None, 
            Node.market_cap > 1_000_000_000, # Start with $1B+ giants
            ~Node.sector.in_(IGNORED_SECTORS)
        ).order_by(Node.market_cap.desc())
        
        lonely_nodes = query.limit(limit).all()

        if not lonely_nodes:
            print("All prime companies currently have tracked edges!")
            return

        for company in lonely_nodes:
            print(f"\n[?] Researching: {company.name} ({company.ticker})")
            
            # 1. Gather Intelligence
            intel_blob = ""
            intel_blob += IntelGatherer.get_wiki_data(company.name, company.ticker)
            intel_blob += IntelGatherer.get_yahoo_news(company.ticker)
            
            if len(intel_blob) < 400:
                print(f"  [-] Insufficient data found for {company.ticker}.")
                continue

            # 2. GPU Extraction via parser.py
            print(f"  [*] GPU is analyzing {len(intel_blob)} characters...")
            extraction = extract_dependencies(intel_blob)
            dependencies = extraction.get("dependencies", [])
            
            if dependencies:
                print(f"  [AI FOUND]: {len(dependencies)} potential relationships.")
            else:
                print("  [-] No modern B2B relationships identified.")
                continue

            # 3. Dynamic Resolution and Database Linking
            for dep in dependencies:
                # Try to resolve Source (Supplier)
                s_node = EntityResolver.resolve(session, dep.get('source_ticker')) or \
                         EntityResolver.resolve(session, dep.get('source_company'))
                
                # Try to resolve Target (Customer)
                t_node = EntityResolver.resolve(session, dep.get('target_ticker')) or \
                         EntityResolver.resolve(session, dep.get('target_company'))

                if s_node and t_node:
                    # Prevent circular or self-referential links
                    if s_node.id == t_node.id:
                        continue

                    # Prevent duplicate edges
                    existing = session.query(Edge).filter(
                        Edge.source_id == s_node.id, 
                        Edge.target_id == t_node.id
                    ).first()

                    if not existing:
                        # Fix confidence scores (e.g., 90 -> 0.9)
                        conf = dep.get('confidence_score', 0.8)
                        if conf > 1: conf = conf / 100.0 if conf > 10 else conf / 10.0

                        new_edge = Edge(
                            source_id=s_node.id,
                            target_id=t_node.id,
                            dependency_type=dep.get('dependency_type', 'Supply Link'),
                            confidence_score=conf,
                            source_url="AI Multi-Source Research"
                        )
                        session.add(new_edge)
                        print(f"  [+] DYNAMICALLY LINKED: {s_node.ticker} ➔ {t_node.ticker} ({dep.get('product')})")
                else:
                    # Log the failure for debugging
                    s_name = dep.get('source_company')
                    t_name = dep.get('target_company')
                    print(f"  [!] Filtered non-equity or private entity: '{s_name}' or '{t_name}'")
            
            session.commit()
            time.sleep(1.5) # Prevent API rate limiting

        print(f"\n--- Titan Queue Complete. Refresh your dashboard to see new X-Ray data. ---")

    except Exception as e:
        print(f"CRITICAL ERROR: {e}")
        session.rollback()
    finally:
        session.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=5, help="Number of companies to research")
    args = parser.parse_args()
    auto_discover_supply_chain(limit=args.limit)
