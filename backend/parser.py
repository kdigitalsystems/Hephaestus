import json
import re
import ollama
from pydantic import BaseModel, Field, ValidationError
from typing import List, Optional

class Dependency(BaseModel):
    source_company: str = Field(min_length=2, description="The name of the supplier or provider.")
    target_company: str = Field(min_length=2, description="The name of the customer or receiver.")
    source_ticker: Optional[str] = Field(default=None, description="The supplier ticker if explicitly known.")
    target_ticker: Optional[str] = Field(default=None, description="The customer ticker if explicitly known.")
    dependency_type: str = Field(min_length=2, description="E.g., Semiconductors, Raw Materials, Logistics, Cloud Services.")
    product: str = Field(min_length=2, description="The specific product, service, or material provided.")
    evidence_excerpt: str = Field(min_length=20, description="A short verbatim supporting excerpt from the source text.")
    evidence_source_url: Optional[str] = Field(
        default=None,
        pattern=r"^https?://\S+$",
        description="The exact URL from the SOURCE header supporting the excerpt, or null when unavailable.",
    )
    confidence_score: float = Field(ge=0.0, le=1.0, description="Confidence from 0.0 to 1.0.")

class ExtractionResult(BaseModel):
    dependencies: List[Dependency]


TEXT_FIELDS = ("source_company", "target_company", "dependency_type", "product", "evidence_excerpt")


def clean_model_text(value):
    """Undo the double escaping small models emit inside JSON strings.

    A model that writes "Apple\\u2019s" or "GM\\'s" inside an already-JSON-encoded
    string leaves literal backslash sequences in the parsed value, which were then
    published verbatim.
    """
    if not isinstance(value, str):
        return value
    text = re.sub(r"\\u([0-9a-fA-F]{4})", lambda match: chr(int(match.group(1), 16)), value)
    text = text.replace("\\'", "'").replace('\\"', '"').replace("\\n", " ").replace("\\t", " ")
    return " ".join(text.split())

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
    9. evidence_excerpt must quote the provided source text. evidence_source_url must copy the exact HTTP URL from that SOURCE header, or be null if the header has no URL.
    10. source_ticker and target_ticker must be null unless that exact ticker symbol appears in the source text. Never guess a ticker from a company name.

    Output JSON:
    {{"dependencies": [{{"source_company": "Supplier Name", "source_ticker": null, "target_company": "Customer Name", "target_ticker": null, "dependency_type": "Raw Materials", "product": "Lithium", "evidence_excerpt": "Supplier Name provides lithium to Customer Name.", "evidence_source_url": "https://example.com/source", "confidence_score": 0.9}}]}}
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
    except Exception as e:
        print(f"Error during LLM extraction: {e}")
        return {"dependencies": []}

    items = parsed_data.get("dependencies") if isinstance(parsed_data, dict) else None
    if not isinstance(items, list):
        print("Error during LLM extraction: model output did not contain a dependencies list.")
        return {"dependencies": []}

    # Validate item by item so one malformed relationship does not discard the rest.
    dependencies = []
    rejected = 0
    for item in items:
        if isinstance(item, dict):
            item = {key: clean_model_text(value) if key in TEXT_FIELDS else value for key, value in item.items()}
        try:
            dependencies.append(Dependency.model_validate(item).model_dump())
        except ValidationError:
            rejected += 1
    if rejected:
        print(f"  [!] Dropped {rejected} malformed dependenc{'y' if rejected == 1 else 'ies'} from model output.")
    return {"dependencies": dependencies}
