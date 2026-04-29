import json
import ollama
from pydantic import BaseModel, Field
from typing import List

# 1. Define the exact JSON structure we want the LLM to output
class Dependency(BaseModel):
    source_company: str = Field(description="Name or Ticker of the supplier")
    source_ticker: str = Field(None, description="The stock ticker of the supplier if known (e.g. NVDA)")
    target_company: str = Field(description="Name or Ticker of the buyer")
    target_ticker: str = Field(None, description="The stock ticker of the buyer if known (e.g. AAPL)")
    dependency_type: str = Field(description="Category (e.g., 'Semiconductor')")
    product: str = Field(description="Specific item (e.g., 'H100 GPUs')")
    confidence_score: float = Field(description="Score 0.0-1.0")

class ExtractionResult(BaseModel):
    dependencies: List[Dependency]

# 2. The function to route text to your local GPU
def extract_dependencies(text: str, model_name: str = "llama3") -> dict:
    """
    Passes raw scraped text to the local Ollama model.
    Uses strict system instructions to prevent 'History Hallucinations'.
    """
    
    # This is the "Audit Firewall" that prevents the AI from getting sidetracked by history.
    SYSTEM_PROMPT = """
    You are a Wall Street Equity Analyst. Your job is to extract modern hardware supply chain links.

    STRICT RULES:
    1. ONLY extract relationships between two SEPARATE public companies (e.g., Apple and TSMC).
    2. IGNORE internal brands or subsidiaries (e.g., do NOT extract 'YouTube' as a supplier to 'Google').
    3. IGNORE venture capital, funding rounds, or acquisitions (e.g., skip 'Series C funding').
    4. IGNORE market research or historic events from over 5 years ago.
    5. FOCUS on: Silicon, Manufacturing, Data Center Hardware, Infrastructure, and Energy.
    6. If the supplier is a private company (like SpaceX), still extract it, but it will be filtered later.

    Output JSON:
    {"dependencies": [{"source_company": "Broadcom", "target_company": "Alphabet", "dependency_type": "Semiconductors", "product": "TPU AI Chips", "confidence_score": 0.9}]}
    """

    user_prompt = f"""
    Analyze the following text for supply chain dependencies. 
    Focus strictly on tech, hardware, and semiconductor links relevant to the stock market.

    Text to analyze:
    {text}
    """

    try:
        # Pinging your local Ollama server and enforcing the JSON schema
        response = ollama.chat(
            model=model_name,
            messages=[
                {
                    'role': 'system',
                    'content': SYSTEM_PROMPT
                },
                {
                    'role': 'user',
                    'content': user_prompt
                }
            ],
            format=ExtractionResult.model_json_schema()
        )
        
        # Parse the JSON string returned by the model
        raw_json = response['message']['content']
        parsed_data = json.loads(raw_json)
        return parsed_data
        
    except Exception as e:
        print(f"Error during LLM extraction: {e}")
        return {"dependencies": []}

# Test the refined audit pipeline
if __name__ == "__main__":
    # Test with a 'Trap' sentence that usually causes history hallucinations
    sample_text = """
    While Alphabet Inc. traces its naming roots back to the Phoenician alphabet, 
    the modern company relies heavily on Nvidia's H100 GPUs to power its Gemini AI models. 
    Tesla Inc., named after Nikola Tesla, recently secured a deal with Panasonic for battery cells.
    """
    
    print("Sending text to local Ollama instance (Audit Mode)...\n")
    results = extract_dependencies(sample_text)
    print(json.dumps(results, indent=2))
