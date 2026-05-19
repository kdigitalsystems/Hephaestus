import os

# Market Data Clients
from alpaca.trading.client import TradingClient
from alpaca.data.historical import StockHistoricalDataClient, NewsClient
from alpaca.data.requests import StockSnapshotRequest, NewsRequest
from yahooquery import Ticker

def get_alpaca_credentials():
    api_key = os.environ.get("ALPACA_API_KEY")
    secret_key = os.environ.get("ALPACA_SECRET_KEY")

    key_path = os.path.expanduser("~/.ssh/alpaca_paper_keys")
    if (not api_key or not secret_key) and os.path.exists(key_path):
        with open(key_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("Key:"):
                    api_key = line.split(":", 1)[1].strip()
                elif line.startswith("Secret_Key:"):
                    secret_key = line.split(":", 1)[1].strip()

    if not api_key or not secret_key:
        raise ValueError("Missing Alpaca API credentials. Set env vars or ~/.ssh/alpaca_paper_keys.")

    return api_key, secret_key

def fetch_yfinance_data(symbol: str):
    """Deep fundamental data extraction via yahooquery."""
    print(f"\n{'='*80}\n[{symbol}] HEPHAESTUS MASTER DATA DUMP\n{'='*80}")
    try:
        ticker = Ticker(symbol)
        modules = ['summaryDetail', 'assetProfile', 'financialData', 
                   'defaultKeyStatistics', 'secFilings']
        data = ticker.get_modules(modules).get(symbol, {})

        if isinstance(data, str):
            print(f"[!] Error: {data}")
            return

        # Corporate Stats
        stats = data.get('defaultKeyStatistics', {})
        print(f"\n[VALUATION & FLOAT]")
        print(f"Market Cap: ${data.get('summaryDetail', {}).get('marketCap', 0):,}")
        print(f"Inst. Ownership: {stats.get('heldPercentInstitutions', 0) * 100:.2f}%")

        # SEC Filings for the LLM
        filings = data.get('secFilings', {}).get('filings', [])
        if filings:
            print("\n[RECENT FILINGS]")
            for f in filings[:3]:
                print(f"- {f.get('date')} | {f.get('type')}: {f.get('url')}")

    except Exception as e:
        print(f"[!] Critical Error: {e}")

def fetch_alpaca_data(symbol: str):
    """Live snapshot and news from Alpaca."""
    api_key, secret_key = get_alpaca_credentials()
    trading_client = TradingClient(api_key, secret_key, paper=True)
    data_client = StockHistoricalDataClient(api_key, secret_key)
    news_client = NewsClient(api_key, secret_key)

    print(f"\n{'='*60}\n[{symbol}] ALPACA LIVE PROFILE\n{'='*60}")
    try:
        # Market Snapshot
        snap = data_client.get_stock_snapshot(StockSnapshotRequest(symbol_or_symbols=symbol))[symbol]
        print(f"Latest Trade: ${snap.latest_trade.price if snap.latest_trade else 'N/A'}")
        
        # News for Sentiment
        news = news_client.get_news(NewsRequest(symbols=symbol, limit=3))
        articles = getattr(news, 'news', [])
        print("\n[RECENT NEWS]")
        for art in articles:
            print(f"- {art.created_at.strftime('%Y-%m-%d')}: {art.headline}")

    except Exception as e:
        print(f"[!] Alpaca Error: {e}")

if __name__ == "__main__":
    fetch_alpaca_data("AMD")
    fetch_yfinance_data("AMD")
