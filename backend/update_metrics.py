import os
import time
import argparse
from database import SessionLocal
from models import Node
from yahooquery import Ticker

def update_financial_metrics(limit=None):
    print("--- Starting Bulk Deep Financial Metrics Update ---")
    session = SessionLocal()
    
    try:
        query = session.query(Node).filter(Node.ticker != None)
        
        # Apply the development limit if provided
        if limit:
            query = query.limit(limit)
            
        nodes = query.all()
        total_nodes = len(nodes)
        
        print(f"Found {total_nodes} companies to update" + (" (DEV LIMIT ACTIVE)." if limit else "."))
        
        chunk_size = 100 
        
        for i in range(0, total_nodes, chunk_size):
            batch = nodes[i:i + chunk_size]
            tickers = [node.ticker for node in batch]
            
            print(f"Processing batch {i} to {i + len(batch)}...")
            
            try:
                t = Ticker(tickers, asynchronous=True)
                
                modules = ['price', 'summaryDetail', 'assetProfile', 'financialData', 'defaultKeyStatistics']
                dict_data = t.get_modules(modules)
                
                for node in batch:
                    ticker_data = dict_data.get(node.ticker, {})
                    if isinstance(ticker_data, str) or not isinstance(ticker_data, dict):
                        continue
                        
                    p = ticker_data.get('price', {})
                    s = ticker_data.get('summaryDetail', {})
                    prof = ticker_data.get('assetProfile', {})
                    fin = ticker_data.get('financialData', {})
                    stat = ticker_data.get('defaultKeyStatistics', {})
                    
                    if 'regularMarketPrice' in p:
                        node.current_price = p.get('regularMarketPrice')
                        node.market_cap = p.get('marketCap')
                        open_price = p.get('regularMarketOpen', 1)
                        current = p.get('regularMarketPrice', 0)
                        if open_price and current:
                            node.percent_change = ((current - open_price) / open_price) * 100
                            
                    node.dividend_yield = f"{s.get('dividendYield', 0) * 100:.2f}%" if s.get('dividendYield') else "N/A"
                    node.trailing_pe = s.get('trailingPE')
                    node.forward_pe = s.get('forwardPE')
                    node.fifty_two_week_high = s.get('fiftyTwoWeekHigh')
                    node.fifty_two_week_low = s.get('fiftyTwoWeekLow')
                        
                    node.enterprise_value = stat.get('enterpriseValue')
                    node.price_to_book = stat.get('priceToBook')
                        
                    node.total_revenue = fin.get('totalRevenue')
                    node.gross_margin = fin.get('grossMargins')
                    node.target_price = fin.get('targetMeanPrice')
                    if fin.get('recommendationKey'):
                        node.recommendation = fin.get('recommendationKey').replace('_', ' ').title()
                            
                    node.sector = prof.get('sector', 'Uncategorized')
                    node.industry = prof.get('industry', 'Uncategorized')
                    node.employees = prof.get('fullTimeEmployees')
                    node.business_summary = prof.get('longBusinessSummary')
                    
                    officers = prof.get('companyOfficers', [])
                    if officers:
                        node.ceo_name = officers[0].get('name', 'N/A')
                                
                session.commit()
                time.sleep(1) 
                
            except Exception as e:
                print(f"  [-] Error processing batch: {e}")
                session.rollback()

        print("\n--- Bulk Financial Metrics Update Complete ---")
        
    except Exception as e:
        session.rollback()
        print(f"Database error: {e}")
    finally:
        session.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None, help="Limit the number of stocks to process")
    args = parser.parse_args()
    
    update_financial_metrics(limit=args.limit)
