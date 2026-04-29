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
#   Available models include llama3, mistral-nemo, qwen3:14b, deepseek-r1:14b, gemma4
# Update the function signature to accept the target company and ticker
def extract_dependencies(text: str, target_name: str = "the target company", target_ticker: str = "", model_name: str = "llama3") -> dict:
    """
    Passes raw scraped text to the local Ollama model.
    Uses strict Ego-Centric instructions to prevent tangential data extraction.
    """
    
    # Inject the specific company into the prompt rules
    SYSTEM_PROMPT = f"""
    You are a Wall Street Equity Analyst researching {target_name} ({target_ticker}). 
    Your job is to extract modern hardware supply chain links ONLY for this specific company.

    STRICT RULES:
    1. EGO-CENTRIC EXTRACTION: At least ONE of the companies in the relationship MUST be {target_name} or {target_ticker}. If a relationship does not directly involve them, IGNORE IT.
    2. ONLY extract relationships between two SEPARATE public companies.
    3. IGNORE internal brands or subsidiaries (e.g., do NOT extract 'YouTube' as a supplier to 'Alphabet').
    4. IGNORE venture capital, funding rounds, or acquisitions.
    5. FOCUS on: Silicon, Manufacturing, Data Center Hardware, Infrastructure, and Energy.
    6. If the supplier is a private company, still extract it, but it must be directly linked to {target_name}.

    Output JSON:
    {{"dependencies": [{{"source_company": "Broadcom", "target_company": "Alphabet", "dependency_type": "Semiconductors", "product": "TPU AI Chips", "confidence_score": 0.9}}]}}
    """

    user_prompt = f"""
    Analyze the following text for supply chain dependencies involving {target_name} ({target_ticker}). 
    Focus strictly on tech, hardware, and semiconductor links.

    Text to analyze:
    {text}
    """

    try:
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
