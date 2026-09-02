import os
import requests
from bs4 import BeautifulSoup
import re

# Identify the project honestly instead of impersonating a desktop browser; the
# source policy in docs/DATA_SOURCES.md rules out scraping pages that would block us.
DEFAULT_USER_AGENT = os.environ.get(
    "HEPHAESTUS_SOURCE_USER_AGENT",
    "HephaestusTerminal/1.0 research@saqibdesktop.local",
)
MAX_ARTICLE_BYTES = 4 * 1024 * 1024

def scrape_article(url: str) -> str:
    """
    Fetches a web page and extracts the core text paragraphs,
    stripping away the HTML boilerplate.
    """
    headers = {"User-Agent": DEFAULT_USER_AGENT}

    try:
        print(f"Scraping: {url}")
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()

        # Let BeautifulSoup sniff <meta charset>; response.text defaults to
        # ISO-8859-1 when the server omits a charset and garbles UTF-8 names.
        soup = BeautifulSoup(response.content[:MAX_ARTICLE_BYTES], 'html.parser')
        
        # Target paragraph tags which usually contain the meat of financial articles
        paragraphs = soup.find_all('p')
        text_content = " ".join([p.get_text() for p in paragraphs])
        
        # Clean up excess whitespace
        clean_text = re.sub(r'\s+', ' ', text_content).strip()
        
        return clean_text
        
    except requests.exceptions.RequestException as e:
        print(f"Failed to scrape {url}: {e}")
        return ""

if __name__ == "__main__":
    # Example test: You can swap this with a real press release or news article
    test_url = "https://finance.yahoo.com/news/advanced-micro-devices-amd-stock-161513222.html"
    article_text = scrape_article(test_url)
    
    print(f"Extracted {len(article_text)} characters.")
    print("Preview:", article_text[:200], "...")
    
    # In a fully integrated pipeline, you would pass 'article_text' 
    # directly to parser.extract_dependencies(article_text) here.
