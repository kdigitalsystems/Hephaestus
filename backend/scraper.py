import requests
from bs4 import BeautifulSoup
import re

def scrape_article(url: str) -> str:
    """
    Fetches a web page and extracts the core text paragraphs, 
    stripping away the HTML boilerplate.
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    
    try:
        print(f"Scraping: {url}")
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
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
