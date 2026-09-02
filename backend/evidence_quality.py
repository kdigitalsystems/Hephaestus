"""Shared evidence-quality rules for AI-discovered relationships."""

from __future__ import annotations

import re


EVIDENCE_PLACEHOLDERS = (
    "not found in source text",
    "no evidence",
    "not explicitly stated",
    "source text does not",
    "could not find",
    "unable to find",
)

# Phrases that signal a non-supply relationship wherever they appear: relationship
# labels, product descriptions, evidence excerpts, and reviewer rationale.
NON_SUPPLY_EVIDENCE_MARKERS = (
    "alleged liability",
    "breach incident",
    "data breach",
    "generic substitutes",
    "intellectual property theft",
    "lawsuit",
    "legal dispute",
    "neither supply chain",
    "neither supply-chain",
    "news report",
    "not a supply chain relationship",
    "not a supply-chain relationship",
    "not an operational supply chain",
    "not an operational supply-chain",
    "not current supply chain",
    "not known to be a customer",
    "not to purchase",
    "stolen",
    "suing",
    "theft",
    "trade secret",
    "unknown operational supply chain",
)

# Relationship labels that describe something other than an operational supply
# relationship. Many of these words are ordinary in product names ("Collaboration
# software") and in filing prose ("we acquired components from"), so they are only
# matched against the relationship label itself, never against products, evidence,
# or reviewer rationale.
NON_SUPPLY_LABEL_MARKERS = NON_SUPPLY_EVIDENCE_MARKERS + (
    "acquisition",
    "acquired",
    "acquires",
    "asset purchase",
    "asset sale",
    "banned",
    "business unit purchase",
    "co-commercialization",
    "collaboration",
    "competition",
    "competitor",
    "equity stake",
    "funding",
    "historical acquisition",
    "investment",
    "investor",
    "joint exploration agreement",
    "joint vaccine",
    "joint venture",
    "license agreement",
    "licensing agreement",
    "merged company",
    "merger",
    "option deal",
    "ownership",
    "parent company of",
    "partnership",
    "patent",
    "prohibited",
    "rights to",
    "royalty",
    "sale_of_assets",
    "settlement",
    "shareholder",
    "sold its subsidiary",
    "spin-off",
    "spinoff",
    "spun off",
    "spun-off",
    "transfer of rights",
    "zero emission vehicle credit",
)

# Bare role labels mean the extractor described who the counterparty is instead of
# what is supplied. They usually indicate a customer -> supplier direction error.
# Descriptive labels that merely contain a role word ("Customer Support Outsourcing",
# "End-User Hardware") describe a real product or service and are not role labels.
ROLE_LABELS = frozenset({
    "buyer",
    "buyers",
    "client",
    "clients",
    "client relationship",
    "customer",
    "customers",
    "customer base",
    "customer relationship",
    "end user",
    "end users",
    "end-user",
    "end-users",
    "key account",
    "key accounts",
    "outsourcing partner",
    "purchaser",
    "purchasers",
})
ROLE_LABEL_QUALIFIERS = frozenset({
    "a", "the", "key", "major", "primary", "main", "largest", "strategic", "significant", "direct", "important", "top",
})
ROLE_LABEL_OF_PATTERN = re.compile(r"^(?:customer|client|buyer|purchaser)s?\s+(?:of|for)\s+\S.*$")

AUTOMATED_NOTE_PREFIXES = ("automated cleanup:", "automated evidence cleanup:")


def _compile(markers):
    return tuple(re.compile(r"(?<![a-z0-9])" + re.escape(marker)) for marker in markers)


_EVIDENCE_PATTERNS = _compile(NON_SUPPLY_EVIDENCE_MARKERS)
_LABEL_PATTERNS = _compile(NON_SUPPLY_LABEL_MARKERS)


def normalize_text(value: object) -> str:
    return " ".join(str(value or "").lower().split())


def _matches_any(text: str, patterns) -> bool:
    return bool(text) and any(pattern.search(text) for pattern in patterns)


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


def is_automated_note(note: object) -> bool:
    """Notes written by the cleanup tooling must not feed back into the cleanup rules."""
    return normalize_text(note).startswith(AUTOMATED_NOTE_PREFIXES)


def has_non_supply_relationship(dependency_type=None, product=None, evidence=None, note=None) -> bool:
    """True when the relationship label or its supporting text describes a non-supply relationship."""
    if _matches_any(normalize_text(dependency_type), _LABEL_PATTERNS):
        return True
    supporting = " ".join(
        normalize_text(value)
        for value in (product, evidence, note)
        if value and not is_automated_note(value)
    )
    return _matches_any(supporting, _EVIDENCE_PATTERNS)


def is_role_label(dependency_type: object) -> bool:
    """True when a dependency label is just a counterparty role such as "Customer"."""
    label = normalize_text(dependency_type).replace("_", " ")
    label = re.sub(r"[^a-z0-9\s/-]", "", label).strip()
    if not label:
        return False
    if ROLE_LABEL_OF_PATTERN.match(label):
        return True
    words = label.split()
    while words and words[0] in ROLE_LABEL_QUALIFIERS:
        words.pop(0)
    return " ".join(words) in ROLE_LABELS
