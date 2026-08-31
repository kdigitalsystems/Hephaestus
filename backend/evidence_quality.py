"""Shared evidence-quality rules for AI-discovered relationships."""

from __future__ import annotations


EVIDENCE_PLACEHOLDERS = (
    "not found in source text",
    "no evidence",
    "not explicitly stated",
    "source text does not",
    "could not find",
    "unable to find",
)


def is_ai_source(source: object) -> bool:
    label = str(source or "").strip().lower()
    return "ai" in label and "manual" not in label


def requires_source_evidence(source: object) -> bool:
    return "manual" not in str(source or "").strip().lower()


def has_usable_evidence(value: object, minimum_length: int = 20) -> bool:
    evidence = " ".join(str(value or "").split())
    lowered = evidence.lower()
    return len(evidence) >= minimum_length and not any(
        placeholder in lowered for placeholder in EVIDENCE_PLACEHOLDERS
    )


def unsupported_ai_evidence(source: object, evidence: object) -> bool:
    return requires_source_evidence(source) and not has_usable_evidence(evidence)
