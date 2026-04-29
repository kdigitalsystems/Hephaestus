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
from yahooquery import Ticker, search as yq_search

# --- CONFIGURATION ---
wikipedia.set_user_agent("HephaestusTerminal/1.0 (research@saqibdesktop.local)")
warnings.filterwarnings("ignore", category=UserWarning, module='wikipedia')

def clean_company_name(name):
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
    Dynamic Resolution Engine with Yahoo Finance API Fallback.
    """
    @staticmethod
    def resolve(session, name_or_ticker):
        if not name_or_ticker or len(str(name_or_ticker)) < 2:
            return None
        
        search_val = str(name_or_ticker).strip()
        search_upper = search_val.upper()

        # 1. ATTEMPT: Exact Ticker Match
        node = session.query(Node).filter(Node.ticker == search_upper).first()
        if node:
            return node

        # 2. ATTEMPT: Database Fuzzy Match
        search_lower = search_val.lower()
        potentials = session.query(Node).filter(
            or_(
                Node.name.ilike(f"%{search_lower}%"),
                Node.ticker.ilike(f"%{search_lower}%")
            )
        ).filter(Node.market_cap > 100_000_000).all()

        if potentials:
            best_match = None
            highest_score = 0
            for p in potentials:
                score = fuzz.token_set_ratio(search_lower, p.name.lower())
                
                if score > highest_score:
                    highest_score = score
                    best_match = p
                elif abs(score - highest_score) < 5 and best_match:
                    if (p.market_cap or 0) > (best_match.market_cap or 0):
                        best_match = p
            
            if highest_score > 85:
                return best_match

        # 3. ATTEMPT: Dynamic Financial API Translation (Yahoo Finance)
        try:
            yq_results = yq_search(search_val)
            quotes = yq_results.get('quotes', [])
            
            if quotes:
                # Grab the most relevant ticker symbol
                discovered_ticker = quotes[0].get('symbol')
                if discovered_ticker:
                    node = session.query(Node).filter(Node.ticker == discovered_ticker.upper()).first()
                    if node:
                        return node
        except Exception:
            pass # Fail silently on network errors

        return None

class IntelGatherer:
    @staticmethod
    def get_wiki_data(company_name, ticker):
        try:
            search_term = clean_company_name(company_name)
            search_query = f"{search_term} {ticker} corporate supply chain operations"
            
            wiki_results = wikipedia.search(search_query)
            if not wiki_results:
                return ""

            selected_title = wiki_results[0]
            for result in wiki_results[:3]:
                if any(k in result.lower() for k in ["inc", "corp", "company", "corporation", "(company)"]):
                    selected_title = result
                    break
            
            page = wikipedia.page(selected_title, auto_suggest=False)
            content = page.content
            
            target_sections = ["Operations", "Products", "Supply chain", "Partnerships", "Customers", "Infrastructure"]
            relevant_text = ""
            for section in target_sections:
                if section in content:
                    start = content.find(section)
                    relevant_text += content[start:start+2500]
            
            if not relevant_text:
                relevant_text = page.summary + "\n" + content[:3500]

            return f"SOURCE: WIKIPEDIA (Page: {page.title})\nDATA:\n{relevant_text}\n"
        except:
            return ""

    @staticmethod
    def get_yahoo_news(ticker):
        try:
            t = Ticker(ticker)
            news = t.news(count=5)
            blob = "SOURCE: RECENT NEWS HEADLINES\n"
            for article in news:
                blob += f"- {article.get('title')}: {article.get('summary')}\n"
            return blob
        except:
            return ""


def auto_discover_supply_chain(limit=5, target_sectors=None):
    print(f"--- Starting Refined Titan Queue (Limit: {limit}) ---")
    if target_sectors:
        print(f"--- Targeting Sectors: {', '.join(target_sectors)} ---")

    session = SessionLocal()

    try:
        # Base query: Companies with no edges, > $1B Market Cap
        query = session.query(Node).outerjoin(
            Edge, or_(Node.id == Edge.source_id, Node.id == Edge.target_id)
        ).filter(
            Edge.id == None,
            Node.market_cap > 1_000_000_000
        )

        # DYNAMIC FILTER: Apply sectors if provided via CLI
        if target_sectors:
            query = query.filter(Node.sector.in_(target_sectors))
        else:
            # If no sectors provided, just ignore the obvious noise (Banks, Shell Companies)
            IGNORED_SECTORS = ["Financial Services", "Real Estate", "Financial", "Asset Management", "Insurance", "Banks", "Shell Companies"]
            query = query.filter(~Node.sector.in_(IGNORED_SECTORS))

        lonely_nodes = query.order_by(Node.market_cap.desc()).limit(limit).all()

        if not lonely_nodes:
            print("No actionable companies found in queue!")
            return

        for company in lonely_nodes:
            print(f"\n[?] Researching: {company.name} ({company.ticker}) | Sector: {company.sector}")

            intel_blob = ""
            intel_blob += IntelGatherer.get_wiki_data(company.name, company.ticker)
            intel_blob += IntelGatherer.get_yahoo_news(company.ticker)

            # THE CONTEXT CAP: Prevent Ollama/GPU freeze on massive pages like Palantir
            intel_blob = intel_blob[:6500]

            if len(intel_blob) < 400:
                print(f"  [-] Insufficient data found for {company.ticker}.")
                continue

            print(f"  [*] GPU is analyzing {len(intel_blob)} characters...")
            extraction = extract_dependencies(intel_blob)
            dependencies = extraction.get("dependencies", [])

            # ... [The rest of your existing dynamic resolution and saving logic remains exactly the same here] ...

            if dependencies:
                print(f"  [AI FOUND]: {len(dependencies)} potential relationships.")
            else:
                print("  [-] No modern B2B relationships identified.")
                continue

            for dep in dependencies:
                s_node = EntityResolver.resolve(session, dep.get('source_ticker')) or \
                         EntityResolver.resolve(session, dep.get('source_company'))

                t_node = EntityResolver.resolve(session, dep.get('target_ticker')) or \
                         EntityResolver.resolve(session, dep.get('target_company'))

                if s_node and t_node:
                    if s_node.id == t_node.id:
                        continue

                    # FOCUS CONSTRAINT
                    if s_node.id != company.id and t_node.id != company.id:
                        print(f"  [!] Ignored tangential competitor link: {s_node.ticker} ➔ {t_node.ticker}")
                        continue

                    existing = session.query(Edge).filter(
                        Edge.source_id == s_node.id,
                        Edge.target_id == t_node.id
                    ).first()

                    if not existing:
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
                    s_name = dep.get('source_company')
                    t_name = dep.get('target_company')
                    print(f"  [!] Filtered non-equity or private entity: '{s_name}' or '{t_name}'")

            session.commit()
            time.sleep(1.5)

        print(f"\n--- Titan Queue Complete. Refresh your dashboard to see new X-Ray data. ---")

    except Exception as e:
        print(f"CRITICAL ERROR: {e}")
        session.rollback()
    finally:
        session.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Hephaestus Supply Chain Discovery Engine")
    parser.add_argument("--limit", type=int, default=5, help="Number of companies to research")
    parser.add_argument("--sectors", nargs='*', default=None, help="Optional: Specific sectors to target (e.g. Technology Industrials)")
    args = parser.parse_args()

    auto_discover_supply_chain(limit=args.limit, target_sectors=args.sectors)
