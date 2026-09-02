import time
import argparse
from database import SessionLocal
from models import Node
from yahooquery import Ticker


def module_payload(ticker_data, name):
    """Yahoo returns an error string instead of a dict when a module is unavailable."""
    value = ticker_data.get(name)
    return value if isinstance(value, dict) else {}


def apply_ticker_modules(node, ticker_data):
    """Update only the fields whose Yahoo module was actually returned.

    A missing module must leave the previously stored values in place; otherwise a
    partial response wipes sector/industry/financials for a whole batch.
    """
    p = module_payload(ticker_data, 'price')
    s = module_payload(ticker_data, 'summaryDetail')
    prof = module_payload(ticker_data, 'assetProfile')
    fin = module_payload(ticker_data, 'financialData')
    stat = module_payload(ticker_data, 'defaultKeyStatistics')

    if 'regularMarketPrice' in p:
        node.current_price = p.get('regularMarketPrice')
        node.market_cap = p.get('marketCap')
        open_price = p.get('regularMarketOpen', 1)
        current = p.get('regularMarketPrice', 0)
        if open_price and current:
            node.percent_change = ((current - open_price) / open_price) * 100

    if s:
        node.dividend_yield = f"{s.get('dividendYield', 0) * 100:.2f}%" if s.get('dividendYield') else "N/A"
        node.trailing_pe = s.get('trailingPE')
        node.forward_pe = s.get('forwardPE')
        node.fifty_two_week_high = s.get('fiftyTwoWeekHigh')
        node.fifty_two_week_low = s.get('fiftyTwoWeekLow')

    if stat:
        node.enterprise_value = stat.get('enterpriseValue')
        node.price_to_book = stat.get('priceToBook')

    if fin:
        node.total_revenue = fin.get('totalRevenue')
        node.gross_margin = fin.get('grossMargins')
        node.target_price = fin.get('targetMeanPrice')
        if fin.get('recommendationKey'):
            node.recommendation = str(fin.get('recommendationKey')).replace('_', ' ').title()

    if prof:
        node.sector = prof.get('sector') or 'Uncategorized'
        node.industry = prof.get('industry') or 'Uncategorized'
        node.employees = prof.get('fullTimeEmployees')
        node.business_summary = prof.get('longBusinessSummary')
        officers = prof.get('companyOfficers') or []
        if officers and isinstance(officers[0], dict):
            node.ceo_name = officers[0].get('name', 'N/A')


def update_financial_metrics(limit=None):
    print("--- Starting Bulk Deep Financial Metrics Update ---")
    session = SessionLocal()
    
    try:
        query = session.query(Node).filter(Node.ticker.is_not(None))
        
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
                    if not isinstance(ticker_data, dict):
                        continue
                    try:
                        apply_ticker_modules(node, ticker_data)
                    except Exception as e:
                        # One malformed symbol must not discard the rest of the batch.
                        print(f"  [-] Skipping {node.ticker}: {e}")

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
