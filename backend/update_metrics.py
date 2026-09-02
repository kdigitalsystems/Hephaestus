import os
import time
import argparse
from database import SessionLocal
from models import Edge, Node
from yahooquery import Ticker


def module_payload(ticker_data, name):
    """Yahoo returns an error string instead of a dict when a module is unavailable."""
    value = ticker_data.get(name)
    return value if isinstance(value, dict) else {}


NO_RECOMMENDATION_VALUES = {"", "none", "null", "n/a"}


def ticker_updates(ticker_data):
    """Compute the fields to update from the Yahoo modules that were actually returned.

    A missing module must leave the previously stored values in place; otherwise a
    partial response wipes sector/industry/financials for a whole batch. Values are
    collected first and assigned together so a failure never persists a half-updated
    company.
    """
    p = module_payload(ticker_data, 'price')
    s = module_payload(ticker_data, 'summaryDetail')
    prof = module_payload(ticker_data, 'assetProfile')
    fin = module_payload(ticker_data, 'financialData')
    stat = module_payload(ticker_data, 'defaultKeyStatistics')
    updates = {}

    if 'regularMarketPrice' in p:
        updates['current_price'] = p.get('regularMarketPrice')
        updates['market_cap'] = p.get('marketCap')
        open_price = p.get('regularMarketOpen', 1)
        current = p.get('regularMarketPrice', 0)
        if open_price and current:
            updates['percent_change'] = ((current - open_price) / open_price) * 100

    if s:
        updates['dividend_yield'] = f"{s.get('dividendYield', 0) * 100:.2f}%" if s.get('dividendYield') else "N/A"
        updates['trailing_pe'] = s.get('trailingPE')
        updates['forward_pe'] = s.get('forwardPE')
        updates['fifty_two_week_high'] = s.get('fiftyTwoWeekHigh')
        updates['fifty_two_week_low'] = s.get('fiftyTwoWeekLow')

    if stat:
        updates['enterprise_value'] = stat.get('enterpriseValue')
        updates['price_to_book'] = stat.get('priceToBook')

    if fin:
        # Revenue is reported in the filing currency while market cap is in USD;
        # storing KRW or ARS revenue next to a USD cap makes every ratio meaningless.
        financial_currency = str(fin.get('financialCurrency') or 'USD').upper()
        updates['total_revenue'] = fin.get('totalRevenue') if financial_currency == 'USD' else None
        updates['gross_margin'] = fin.get('grossMargins')
        updates['target_price'] = fin.get('targetMeanPrice')
        recommendation = str(fin.get('recommendationKey') or '').strip()
        # Yahoo returns the literal string "none" for uncovered names.
        if recommendation.lower() not in NO_RECOMMENDATION_VALUES:
            updates['recommendation'] = recommendation.replace('_', ' ').title()
        else:
            updates['recommendation'] = None

    if prof:
        updates['sector'] = prof.get('sector') or 'Uncategorized'
        updates['industry'] = prof.get('industry') or 'Uncategorized'
        updates['employees'] = prof.get('fullTimeEmployees')
        updates['business_summary'] = prof.get('longBusinessSummary')
        officers = prof.get('companyOfficers') or []
        if officers and isinstance(officers[0], dict):
            updates['ceo_name'] = officers[0].get('name', 'N/A')
    return updates


def apply_ticker_modules(node, ticker_data):
    """Apply the computed updates and return how many fields changed."""
    updates = ticker_updates(ticker_data)
    for field, value in updates.items():
        setattr(node, field, value)
    return len(updates)


MODULES = ['price', 'summaryDetail', 'assetProfile', 'financialData', 'defaultKeyStatistics']
# When most of a batch comes back as error strings Yahoo is throttling us; one bad
# batch is normal, a run full of them silently empties the dashboard.
THROTTLE_RATIO = 0.5
THROTTLE_PAUSE_SECONDS = float(os.environ.get("HEPHAESTUS_YAHOO_THROTTLE_PAUSE", "20"))


def fetch_batch(tickers):
    return Ticker(tickers, asynchronous=True).get_modules(MODULES)


def looks_throttled(dict_data, tickers):
    if not tickers:
        return False
    missing = sum(1 for ticker in tickers if not isinstance(dict_data.get(ticker), dict))
    return missing / len(tickers) >= THROTTLE_RATIO


def linked_node_ids(session):
    """Ids of every company that appears on a supply-chain edge."""
    ids = set()
    for source_id, target_id in session.query(Edge.source_id, Edge.target_id).all():
        ids.add(source_id)
        ids.add(target_id)
    return ids


def prioritized_nodes(session, limit=None):
    """Companies with supply-chain links come first.

    Yahoo throttles long crawls, and the tail of the crawl is what goes without
    data. The companies people actually open - the linked ones - must be at the
    head so a throttled run degrades the long tail, not TSM and AMD.
    """
    linked = linked_node_ids(session)
    nodes = session.query(Node).filter(Node.ticker.is_not(None)).order_by(Node.id.asc()).all()
    nodes.sort(key=lambda node: (node.id not in linked, node.id))
    return nodes[:limit] if limit else nodes


def update_financial_metrics(limit=None):
    print("--- Starting Bulk Deep Financial Metrics Update ---")
    session = SessionLocal()
    updated = 0
    no_data = 0
    sample_errors = {}

    try:
        nodes = prioritized_nodes(session, limit)
        total_nodes = len(nodes)

        print(f"Found {total_nodes} companies to update" + (" (DEV LIMIT ACTIVE)." if limit else "."))

        chunk_size = 100

        for i in range(0, total_nodes, chunk_size):
            batch = nodes[i:i + chunk_size]
            tickers = [node.ticker for node in batch]

            print(f"Processing batch {i} to {i + len(batch)}...")

            try:
                dict_data = fetch_batch(tickers)
                if looks_throttled(dict_data, tickers):
                    print(f"  [!] Most of this batch returned no data; pausing {THROTTLE_PAUSE_SECONDS:.0f}s and retrying once.")
                    time.sleep(THROTTLE_PAUSE_SECONDS)
                    dict_data = fetch_batch(tickers)

                for node in batch:
                    ticker_data = dict_data.get(node.ticker, {})
                    if not isinstance(ticker_data, dict):
                        # Yahoo returns an error string ("Quote not found", a throttling
                        # message) instead of a dict; count it instead of hiding it.
                        no_data += 1
                        message = str(ticker_data)[:80]
                        sample_errors.setdefault(message, node.ticker)
                        continue
                    try:
                        if apply_ticker_modules(node, ticker_data):
                            updated += 1
                        else:
                            no_data += 1
                    except Exception as e:
                        # One malformed symbol must not discard the rest of the batch.
                        print(f"  [-] Skipping {node.ticker}: {e}")

                session.commit()
                time.sleep(1)

            except Exception as e:
                print(f"  [-] Error processing batch: {e}")
                session.rollback()

        print(f"\n--- Bulk Financial Metrics Update Complete: {updated} updated, {no_data} returned no data ---")
        for message, ticker in list(sample_errors.items())[:5]:
            print(f"  [-] e.g. {ticker}: {message}")
        if total_nodes and no_data / total_nodes >= THROTTLE_RATIO:
            print("  [!] Most companies received no market data; the export will shrink until the provider recovers.")
        
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
