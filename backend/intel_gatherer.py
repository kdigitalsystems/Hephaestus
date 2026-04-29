import os
import requests
from yahooquery import Ticker
from sec_api import ExtractorApi

# Initialize APIs (Get a free key at sec-api.io)
SEC_API_KEY = "b35c078f27db530c7a5b68179a5f73345f112c3edd8948175a6fbc1e9fdfd212"
extractor = ExtractorApi(SEC_API_KEY)

class IntelGatherer:
    @staticmethod
    def get_yahoo_news(ticker):
        """Fetches recent news headlines and summaries."""
        print(f"  [*] Fetching Yahoo News for {ticker}...")
        t = Ticker(ticker)
        news = t.news(count=5)
        # Combine titles and summaries into one block for the LLM
        blob = ""
        for article in news:
            blob += f"Title: {article.get('title')}\nSummary: {article.get('summary')}\n---\n"
        return blob

    @staticmethod
    def get_sec_risk_factors(ticker):
        """Pulls 'Item 1A: Risk Factors' from the latest 10-K."""
        print(f"  [*] Pulling SEC Risk Factors for {ticker}...")
        try:
            # First, find the latest 10-K URL (Simplified logic)
            # In production, use the SEC 'QueryApi' to find the htm_url
            # For this example, we assume you have the filing URL
            filing_url = f"https://www.sec.gov/Archives/edgar/data/..." 
            
            # Extract only Section 1A (Risk Factors)
            section_text = extractor.get_section(filing_url, "1A", "text")
            return section_text[:5000] # Cap it for the LLM context
        except Exception:
            return ""

    @staticmethod
    def get_earnings_snippet(ticker):
        """Uses a free tier of Finnhub or similar for transcripts."""
        # Note: Transcripts are the hardest to get for free.
        # Most devs scrape the 'text' version from AlphaStreet or Motley Fool.
        return "CEO mentioned ramp up with new substrate vendor in Taiwan..."
