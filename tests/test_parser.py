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
