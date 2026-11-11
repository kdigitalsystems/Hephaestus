import json
import os
import re
import ollama
from pydantic import BaseModel, Field, ValidationError, field_validator
from typing import List, Optional

# Validated in Python, not via a JSON-schema `pattern`: Ollama compiles the schema
# into a llama.cpp grammar, and its regex subset rejected `\S`, so every request
# failed with "Failed to initialize samplers: failed to parse grammar".
SOURCE_URL_PATTERN = re.compile(r"^https?://\S+$")

# Must be a model the runner actually has; the workflow pulls it alongside the
# review models. The previous hard-coded tag did not exist, and every extraction
# failed with "model not found" while the pipeline kept publishing.
DEFAULT_EXTRACTION_MODEL = os.environ.get("HEPHAESTUS_EXTRACTION_MODEL", "llama3.1:8b")
# Grammar-constrained output can loop forever while staying valid JSON; on a
# 32k-context server one company generated for an hour without returning. A
# dozen dependencies fit comfortably in 2048 tokens, and no company is worth
# more than a few minutes of GPU time.
EXTRACTION_MAX_TOKENS = int(os.environ.get("HEPHAESTUS_EXTRACTION_MAX_TOKENS", "2048"))
EXTRACTION_TIMEOUT_SECONDS = float(os.environ.get("HEPHAESTUS_EXTRACTION_TIMEOUT_SECONDS", "300"))
# Small models sometimes write one string that never ends (a 7,000-character
# evidence_excerpt), so the token cap cuts the JSON off and nothing parses. Sampling is
# random, so asking again usually works: two nights running the same company failed this
# way, and a failed company stays in the queue. Only unreadable output is re-asked; a
# broken server or a missing model fails at once.
EXTRACTION_ATTEMPTS = max(1, int(os.environ.get("HEPHAESTUS_EXTRACTION_ATTEMPTS", "2")))


def ollama_chat(**kwargs):
    """`ollama.chat` has no timeout; a hung request would stall the whole queue."""
    return ollama.Client(timeout=EXTRACTION_TIMEOUT_SECONDS).chat(**kwargs)

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
        description="The exact HTTP or HTTPS URL from the SOURCE header supporting the excerpt, or null when unavailable.",
    )
    confidence_score: float = Field(ge=0.0, le=1.0, description="Confidence from 0.0 to 1.0.")

    @field_validator("evidence_source_url")
    @classmethod
    def source_url_must_be_http(cls, value):
        if value is None:
            return None
        if not SOURCE_URL_PATTERN.match(value):
            raise ValueError("evidence_source_url must be a single http(s) URL")
        return value

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

DEPENDENCIES_KEY = re.compile(r'"dependencies"\s*:\s*\[')


def salvage_dependencies(raw) -> list:
    """Complete dependency objects from model output that does not parse as a whole.

    The token cap cuts a long answer off mid-string, and a small model sometimes drops a
    comma. Either way the objects written before the damage are intact; discarding them
    made one bad tail cost the whole company. The damaged object and everything after it
    are left out, repeats are collapsed, and the survivors still go through the same
    validation as a clean answer.
    """
    if not isinstance(raw, str):
        return []
    start = DEPENDENCIES_KEY.search(raw)
    if not start:
        return []
    decoder = json.JSONDecoder()
    position = start.end()
    found, seen = [], set()
    while position < len(raw):
        while position < len(raw) and raw[position] in " \t\r\n,":
            position += 1
        if position >= len(raw) or raw[position] != "{":
            break
        try:
            item, position = decoder.raw_decode(raw, position)
        except ValueError:
            break
        fingerprint = json.dumps(item, sort_keys=True)
        if fingerprint not in seen:
            seen.add(fingerprint)
            found.append(item)
    return found


def extract_dependencies(text: str, target_name: str = "the target company", target_ticker: str = "", model_name: str = None) -> dict:
    """
    Passes raw scraped text to the local Ollama model.
    Uses strict ego-centric instructions when a target is provided.
    Returns {"dependencies": [...]} and, when the model call itself failed,
    an additional "error" message so callers can tell "nothing found" from "broken".
    """
    model_name = model_name or DEFAULT_EXTRACTION_MODEL

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

    parsed_data = None
    problem = ""
    for attempt in range(1, EXTRACTION_ATTEMPTS + 1):
        try:
            response = ollama_chat(
                model=model_name,
                messages=[
                    {'role': 'system', 'content': SYSTEM_PROMPT},
                    {'role': 'user', 'content': user_prompt}
                ],
                format=ExtractionResult.model_json_schema(),
                options={"num_predict": EXTRACTION_MAX_TOKENS},
            )
            raw_output = response['message']['content']
        except Exception as e:
            print(f"Error during LLM extraction: {e}")
            return {"dependencies": [], "error": str(e)}

        try:
            parsed_data = json.loads(raw_output)
            break
        except (TypeError, ValueError) as e:
            recovered = salvage_dependencies(raw_output)
            if recovered:
                print(
                    f"  [!] Model output was cut off or malformed ({e}); "
                    f"kept the {len(recovered)} complete dependenc{'y' if len(recovered) == 1 else 'ies'} before the damage."
                )
                parsed_data = {"dependencies": recovered}
                break
            problem = str(e)
            retry = f"; asking again (attempt {attempt} of {EXTRACTION_ATTEMPTS})" if attempt < EXTRACTION_ATTEMPTS else ""
            print(f"Error during LLM extraction: {e}{retry}")

    if parsed_data is None:
        return {"dependencies": [], "error": problem}

    items = parsed_data.get("dependencies") if isinstance(parsed_data, dict) else None
    if not isinstance(items, list):
        print("Error during LLM extraction: model output did not contain a dependencies list.")
        return {"dependencies": [], "error": "model output did not contain a dependencies list"}

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
