import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import parser


def test_extract_dependencies_validates_model_payload(monkeypatch):
    monkeypatch.setattr(parser.ollama, "chat", lambda **_kwargs: {
        "message": {"content": """{
            "dependencies": [{
                "source_company": "Supplier Inc.",
                "target_company": "Customer Inc.",
                "source_ticker": "SUP",
                "target_ticker": "CUS",
                "dependency_type": "Components",
                "product": "Control modules",
                "evidence_excerpt": "Supplier Inc. provides control modules to Customer Inc.",
                "evidence_source_url": "https://example.com/filing",
                "confidence_score": 0.9
            }]
        }"""}
    })

    result = parser.extract_dependencies("source text")

    assert result["dependencies"][0]["confidence_score"] == 0.9
    assert result["dependencies"][0]["evidence_source_url"] == "https://example.com/filing"


def test_extraction_model_comes_from_the_environment_and_failures_are_reported(monkeypatch):
    calls = []

    def failing_chat(**kwargs):
        calls.append(kwargs["model"])
        raise RuntimeError("model 'x' not found (status code: 404)")

    monkeypatch.setattr(parser.ollama, "chat", failing_chat)
    monkeypatch.setattr(parser, "DEFAULT_EXTRACTION_MODEL", "llama3.1:8b")

    result = parser.extract_dependencies("source text")

    assert calls == ["llama3.1:8b"]
    assert result["dependencies"] == []
    assert "not found" in result["error"]


def test_extract_dependencies_rejects_unbounded_or_missing_evidence(monkeypatch):
    monkeypatch.setattr(parser.ollama, "chat", lambda **_kwargs: {
        "message": {"content": """{
            "dependencies": [{
                "source_company": "Supplier Inc.",
                "target_company": "Customer Inc.",
                "dependency_type": "Components",
                "product": "Control modules",
                "evidence_excerpt": "",
                "confidence_score": 9
            }]
        }"""}
    })

    assert parser.extract_dependencies("source text") == {"dependencies": []}


def test_extract_dependencies_keeps_valid_items_when_one_is_malformed(monkeypatch):
    monkeypatch.setattr(parser.ollama, "chat", lambda **_kwargs: {
        "message": {"content": """{
            "dependencies": [
                {
                    "source_company": "Supplier Inc.",
                    "target_company": "Customer Inc.",
                    "dependency_type": "Components",
                    "product": "Control modules",
                    "evidence_excerpt": "Supplier Inc. provides control modules to Customer Inc.",
                    "confidence_score": 0.9
                },
                {
                    "source_company": "Other Supplier",
                    "target_company": "Customer Inc.",
                    "dependency_type": "Logistics",
                    "product": "Freight",
                    "evidence_excerpt": "Other Supplier ships freight for Customer Inc. every week.",
                    "confidence_score": 9
                }
            ]
        }"""}
    })

    result = parser.extract_dependencies("source text")

    assert [item["source_company"] for item in result["dependencies"]] == ["Supplier Inc."]


def test_extract_dependencies_unescapes_double_encoded_model_text(monkeypatch):
    monkeypatch.setattr(parser.ollama, "chat", lambda **_kwargs: {
        "message": {"content": r"""{
            "dependencies": [{
                "source_company": "LG Display",
                "target_company": "Apple Inc.",
                "dependency_type": "Display Panels",
                "product": "27-inch panels",
                "evidence_excerpt": "LG panels are used in Apple\\u2019s 2009 27-inch iMac and GM\\'s\\nnewer displays.",
                "confidence_score": 0.8
            }]
        }"""}
    })

    result = parser.extract_dependencies("source text")

    assert result["dependencies"][0]["evidence_excerpt"] == "LG panels are used in Apple’s 2009 27-inch iMac and GM's newer displays."


def test_extract_dependencies_rejects_url_with_trailing_payload(monkeypatch):
    monkeypatch.setattr(parser.ollama, "chat", lambda **_kwargs: {
        "message": {"content": """{
            "dependencies": [{
                "source_company": "Supplier Inc.",
                "target_company": "Customer Inc.",
                "dependency_type": "Components",
                "product": "Control modules",
                "evidence_excerpt": "Supplier Inc. provides control modules to Customer Inc.",
                "evidence_source_url": "https://x.com\\" onload=alert(1)",
                "confidence_score": 0.9
            }]
        }"""}
    })

    assert parser.extract_dependencies("source text") == {"dependencies": []}


def test_extraction_schema_stays_grammar_safe():
    """Ollama compiles the schema to a llama.cpp grammar with a tiny regex subset.

    A `pattern` on the URL field made every extraction fail with "failed to parse
    grammar"; URL validation lives in a Python validator instead.
    """
    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                assert key != "pattern", "regex patterns must not reach the Ollama grammar"
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(parser.ExtractionResult.model_json_schema())


def test_extract_dependencies_rejects_malformed_source_url(monkeypatch):
    monkeypatch.setattr(parser.ollama, "chat", lambda **_kwargs: {
        "message": {"content": """{
            "dependencies": [{
                "source_company": "Supplier Inc.",
                "target_company": "Customer Inc.",
                "dependency_type": "Components",
                "product": "Control modules",
                "evidence_excerpt": "Supplier Inc. provides control modules to Customer Inc.",
                "evidence_source_url": "javascript:alert(1)",
                "confidence_score": 0.9
            }]
        }"""}
    })

    assert parser.extract_dependencies("source text") == {"dependencies": []}
