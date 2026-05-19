import json
import ollama
from pydantic import BaseModel, Field
from typing import List, Optional

class Dependency(BaseModel):
    source_company: str = Field(description="The name of the supplier or provider.")
    target_company: str = Field(description="The name of the customer or receiver.")
    source_ticker: Optional[str] = Field(default=None, description="The supplier ticker if explicitly known.")
    target_ticker: Optional[str] = Field(default=None, description="The customer ticker if explicitly known.")
    dependency_type: str = Field(description="E.g., Semiconductors, Raw Materials, Logistics, Cloud Services.")
    product: str = Field(description="The specific product, service, or material provided.")
    confidence_score: float = Field(description="Confidence from 0.0 to 1.0.")

class ExtractionResult(BaseModel):
    dependencies: List[Dependency]

def extract_dependencies(text: str, target_name: str = "the target company", target_ticker: str = "", model_name: str = "llama3.1:8b-instruct-q8_0") -> dict:
    """
    Passes raw scraped text to the local Ollama model.
    Uses strict ego-centric instructions when a target is provided.
    """

    has_target = target_name and target_name != "the target company"
    target_label = f"{target_name} ({target_ticker})" if target_ticker else target_name

    target_rule = (
        f"At least ONE of the companies in the relationship MUST be {target_name} or {target_ticker}. "
        "If a relationship does not directly involve them, IGNORE IT."
        if has_target
        else "Extract only clearly stated B2B supply chain links. Ignore tangential company mentions."
    )
    
    SYSTEM_PROMPT = f"""
    You are a Wall Street Equity Analyst researching {target_label}. 
    Your job is to extract modern B2B supply chain links from the provided source text.

    STRICT RULES:
    1. {target_rule}
    2. Extract relationships between two SEPARATE companies or institutions.
    3. IGNORE internal brands or subsidiaries.
    4. IGNORE venture capital, funding rounds, or acquisitions.
    5. FOCUS ON CORE OPERATIONS: Extract physical suppliers, raw material providers, manufacturing partners, logistics partners, and critical enterprise software/infrastructure.
    6. The source_company MUST be the supplier/provider. The target_company MUST be the customer/receiver.
    7. dependency_type must describe what is supplied, not a role label. Use "Advanced Silicon Fabrication" instead of "Customer".
    8. If the supplier is a private company, still extract it only when the relationship is explicit.

    Output JSON:
    {{"dependencies": [{{"source_company": "Supplier Name", "source_ticker": "SUP", "target_company": "Customer Name", "target_ticker": "CUS", "dependency_type": "Raw Materials", "product": "Lithium", "confidence_score": 0.9}}]}}
    """

    user_prompt = f"""
    Analyze the following text for supply chain, logistics, manufacturing, and operational dependencies.

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
