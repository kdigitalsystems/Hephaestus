"""Shared evidence-quality rules for AI-discovered relationships."""

from __future__ import annotations

import re


# Phrases that mark an excerpt as model commentary rather than a quote from the
# collected source text.
EVIDENCE_PLACEHOLDERS = (
    "not found in source text",
    "no evidence",
    "not explicitly stated",
    "not explicitly mentioned",
    "not directly stated",
    "not directly mentioned",
    "not mentioned in the text",
    "not stated in the text",
    "no direct mention",
    "no specific mention",
    "source text does not",
    "the text does not",
    "the provided text",
    "based on the provided",
    "the text states",
    "the text mentions",
    "could not find",
    "unable to find",
    "well-known supplier",
    "well known supplier",
    "well-known customer",
    "well known customer",
    "is known to supply",
    "general knowledge",
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

# Multi-word phrases that make a relationship label non-supply wherever they appear
# in the label ("Historical Sale of Assets", "Equity Stake in ...").
NON_SUPPLY_LABEL_PHRASES = NON_SUPPLY_EVIDENCE_MARKERS + (
    "asset purchase",
    "asset sale",
    "brand license",
    "business sale",
    "business unit purchase",
    "co-commercialization",
    "competitor",
    "division sale",
    "equity investment",
    "equity stake",
    "facility sale",
    "formation of",
    "franchise agreement",
    "historical acquisition",
    "joint exploration agreement",
    "joint vaccine",
    "joint venture",
    "landlord",
    "license agreement",
    "licensing agreement",
    "merged company",
    "merger",
    "option deal",
    "parent company of",
    "patent dispute",
    "patent license",
    "patent licensing",
    "property owner",
    "royalty agreement",
    "royalty stream",
    "sale of",
    "sale_of_assets",
    "shareholder",
    "sold its subsidiary",
    "spin-off",
    "spinoff",
    "spun off",
    "spun-off",
    "trademark license",
    "transfer of rights",
    "zero emission vehicle credit",
)

# Single words that describe a non-supply relationship only when they ARE the label
# (after generic qualifiers are removed). "Partnership" is not a supply relationship;
# "Manufacturing Partnership" is, so these are never matched as substrings.
NON_SUPPLY_EXACT_LABELS = frozenset({
    "acquisition",
    "acquisitions",
    "acquired",
    "acquires",
    "alliance",
    "banned",
    "collaboration",
    "collaborations",
    "competition",
    "funding",
    "historical",
    "investment",
    "investments",
    "investor",
    "investors",
    "news",
    "ownership",
    "partner",
    "partners",
    "partnership",
    "partnerships",
    "patent",
    "patents",
    "prohibited",
    "royalty",
    "royalties",
    "settlement",
    "strategic goal",
    "unclear",
    "unknown",
})

# Leading words that do not change what a label means ("Strategic Partnership",
# "Key Customer", "Tier 1 Supplier").
LABEL_QUALIFIERS = frozenset({
    "a", "an", "the", "key", "major", "primary", "main", "largest", "strategic", "significant",
    "direct", "important", "top", "core", "critical", "long-term", "long", "term", "historical",
    "former", "past", "previous", "current", "ongoing", "potential", "commercial", "business",
    "corporate", "technology", "global", "preferred", "exclusive", "tier",
})

# Bare role labels mean the extractor described who the counterparty is instead of
# what is supplied. Customer-side labels usually mean the edge was emitted
# customer -> supplier and should be swapped. Supplier-side labels are just as
# unreliable but in either direction, so they are only flagged for review.
# Descriptive labels that merely contain a role word ("Customer Support Outsourcing",
# "GPU Supplier") name a real product or service and are not role labels.
CUSTOMER_ROLE_LABELS = frozenset({
    "buyer",
    "buyers",
    "client",
    "clients",
    "client relationship",
    "client relationships",
    "customer",
    "customers",
    "customer base",
    "customer relationship",
    "customer relationships",
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
SUPPLIER_ROLE_LABELS = frozenset({
    "supplier",
    "suppliers",
    "provider",
    "providers",
    "vendor",
    "vendors",
    "service provider",
    "service providers",
    "solution provider",
    "solutions provider",
    "material supplier",
    "materials supplier",
    "component supplier",
    "components supplier",
    "parts supplier",
    "sole supplier",
    "licensee",
    "licensor",
    "ip licensee",
    "ip licensor",
})
ROLE_LABEL_OF_PATTERN = re.compile(r"^(?:customer|client|buyer|purchaser|supplier|vendor|provider)s?\s+(?:of|for|to)\s+\S.*$")

AUTOMATED_NOTE_PREFIXES = ("automated cleanup:", "automated evidence cleanup:")


def _compile(markers):
    return tuple(re.compile(r"(?<![a-z0-9])" + re.escape(marker)) for marker in markers)


_EVIDENCE_PATTERNS = _compile(NON_SUPPLY_EVIDENCE_MARKERS)
_LABEL_PATTERNS = _compile(NON_SUPPLY_LABEL_PHRASES)


def normalize_text(value: object) -> str:
    return " ".join(str(value or "").lower().split())


def normalize_label(value: object) -> str:
    label = normalize_text(value).replace("_", " ")
    return re.sub(r"[^a-z0-9\s/&-]", "", label).strip()


def strip_label_qualifiers(label: str) -> str:
    words = label.split()
    while words and (words[0] in LABEL_QUALIFIERS or re.fullmatch(r"\d+", words[0])):
        words.pop(0)
    return " ".join(words)


def _matches_any(text: str, patterns) -> bool:
    return bool(text) and any(pattern.search(text) for pattern in patterns)


def is_ai_source(source: object) -> bool:
    label = str(source or "").strip().lower()
    return "ai" in label and "manual" not in label


def requires_source_evidence(source: object) -> bool:
    """Only curated manual provenance labels are exempt; a URL that happens to contain
    the word "manual" (…/service-manual.pdf) is still AI-derived evidence."""
    label = str(source or "").strip().lower()
    if label.startswith(("http://", "https://")):
        return True
    return "manual" not in label


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


def is_non_supply_label(dependency_type: object) -> bool:
    label = normalize_label(dependency_type)
    if not label:
        return False
    if _matches_any(label, _LABEL_PATTERNS):
        return True
    # Check the raw label too: "Historical" is both a qualifier and, alone, a non-supply label.
    return label in NON_SUPPLY_EXACT_LABELS or strip_label_qualifiers(label) in NON_SUPPLY_EXACT_LABELS


def has_non_supply_relationship(dependency_type=None, product=None, evidence=None, note=None) -> bool:
    """True when the relationship label or its supporting text describes a non-supply relationship."""
    if is_non_supply_label(dependency_type):
        return True
    supporting = " ".join(
        normalize_text(value)
        for value in (product, evidence, note)
        if value and not is_automated_note(value)
    )
    return _matches_any(supporting, _EVIDENCE_PATTERNS)


def _role_label(dependency_type: object, roles) -> bool:
    label = normalize_label(dependency_type)
    if not label:
        return False
    stripped = strip_label_qualifiers(label)
    if stripped in roles:
        return True
    match = ROLE_LABEL_OF_PATTERN.match(stripped)
    if not match:
        return False
    role = stripped.split()[0].rstrip("s")
    return any(candidate.startswith(role) for candidate in roles)


def is_customer_role_label(dependency_type: object) -> bool:
    """True for labels such as "Customer" or "Major Buyer" that name the customer role."""
    return _role_label(dependency_type, CUSTOMER_ROLE_LABELS)


def is_supplier_role_label(dependency_type: object) -> bool:
    """True for labels such as "Supplier" or "Service Provider" that name the supplier role."""
    return _role_label(dependency_type, SUPPLIER_ROLE_LABELS)


def is_role_label(dependency_type: object) -> bool:
    """True when a dependency label is just a counterparty role instead of what is supplied."""
    return is_customer_role_label(dependency_type) or is_supplier_role_label(dependency_type)
