import json
import ollama
from pydantic import BaseModel, Field
from typing import List

class Dependency(BaseModel):
    source_company: str = Field(description="The name of the supplier or provider.")
    target_company: str = Field(description="The name of the customer or receiver.")
    dependency_type: str = Field(description="E.g., Semiconductors, Raw Materials, Logistics, Cloud Services.")
    product: str = Field(description="The specific product, service, or material provided.")
    confidence_score: float = Field(description="Confidence from 0.0 to 1.0.")

class ExtractionResult(BaseModel):
    dependencies: List[Dependency]

def extract_dependencies(text: str, target_name: str = "the target company", target_ticker: str = "", model_name: str = "llama3.1:8b-instruct-q8_0") -> dict:
    """
    Passes raw scraped text to the local Ollama model.
    Uses strict Ego-Centric instructions to prevent tangential data extraction.
    """
    
    SYSTEM_PROMPT = f"""
    You are a Wall Street Equity Analyst researching {target_name} ({target_ticker}). 
    Your job is to extract modern B2B supply chain links ONLY for this specific company.

    STRICT RULES:
    1. EGO-CENTRIC EXTRACTION: At least ONE of the companies in the relationship MUST be {target_name} or {target_ticker}. If a relationship does not directly involve them, IGNORE IT.
    2. ONLY extract relationships between two SEPARATE public companies.
    3. IGNORE internal brands or subsidiaries.
    4. IGNORE venture capital, funding rounds, or acquisitions.
    5. FOCUS ON CORE OPERATIONS: Extract physical suppliers, raw material providers, manufacturing partners, logistics partners, and critical enterprise software/infrastructure.
    6. If the supplier is a private company, still extract it, but it must be directly linked to {target_name}.

    Output JSON:
    {{"dependencies": [{{"source_company": "Supplier Name", "target_company": "{target_name}", "dependency_type": "Raw Materials", "product": "Lithium", "confidence_score": 0.9}}]}}
    """

    user_prompt = f"""
    Analyze the following text for supply chain, logistics, manufacturing, and operational dependencies involving {target_name} ({target_ticker}).

    Text to analyze:
    {text}
    """

    try:
        response = ollama.chat(
            model=model_name,
            messages=[
                {'role': 'system', 'content': SYSTEM_PROMPT},
                {'role': 'user', 'content': user_prompt}
            ],
            format=ExtractionResult.model_json_schema()
        )
        
        raw_json = response['message']['content']
        parsed_data = json.loads(raw_json)
        return parsed_data
        
    except Exception as e:
        print(f"Error during LLM extraction: {e}")
        return {"dependencies": []}
