import json
import ollama
from pydantic import BaseModel, Field
from typing import List

# 1. Define the exact JSON structure we want the LLM to output
class Dependency(BaseModel):
    source_company: str = Field(description="The supplier or vendor company")
    target_company: str = Field(description="The buyer or dependent company")
    dependency_type: str = Field(description="Category of dependency (e.g., 'Memory', 'Advanced Packaging', 'Cooling')")
    product: str = Field(description="Specific product name if mentioned (e.g., 'HBM3e', 'CDU')")
    confidence_score: float = Field(description="Score from 0.0 to 1.0 indicating how explicitly the text states this link")

class ExtractionResult(BaseModel):
    dependencies: List[Dependency]

# 2. The function to route text to your local GPU
def extract_dependencies(text: str, model_name: str = "llama3") -> dict:
    """
    Passes raw scraped text to the local Ollama model to extract supply chain edges.
    """
    prompt = f"""
    Analyze the following text and extract any supply chain dependencies between companies.
    Focus strictly on hardware, data center, semiconductor, and AI dependencies.
    
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
                    'content': 'You are a strict data extraction agent. You only output valid JSON based on the requested schema.'
                },
                {
                    'role': 'user',
                    'content': prompt
                }
            ],
            format=ExtractionResult.model_json_schema()
        )
        
        # Parse the JSON string returned by the model into a Python dictionary
        raw_json = response['message']['content']
        parsed_data = json.loads(raw_json)
        return parsed_data
        
    except Exception as e:
        print(f"Error during LLM extraction: {e}")
        return {"dependencies": []}

# Test the local extraction pipeline
if __name__ == "__main__":
    # A sample snippet you might pull from financial news
    sample_text = "Due to the massive demand for the Instinct MI300X, AMD has secured additional HBM3e supply exclusively from SK Hynix for Q4."
    
    print("Sending text to local Ollama instance...\n")
    results = extract_dependencies(sample_text)
    print(json.dumps(results, indent=2))
