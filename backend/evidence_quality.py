"""Shared evidence-quality rules for AI-discovered relationships."""

from __future__ import annotations

import re
from types import SimpleNamespace
from datetime import datetime, timezone


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


# Sales of a business are transactions, not supply: "Integra acquired instrumentation
# lines from Medtronic", "Celestica acquired Hewlett-Packard's PCA operation" and
# "Agilent sold its 47% stake in Lumileds to Philips" were all published as supply links.
# Only the transferred asset types count, so "acquired raw materials from" still passes.
_DEAL_TOKENS = r"(?:[\w\-&.%$]+(?:'s|’s|')?\s+){0,6}?"
_DEAL_ASSETS = (
    r"(?:stake|interests?|operations?|business(?:es)?|divisions?|units?|product\s+lines|lines|"
    r"subsidiar(?:y|ies)|assets|plants?|facilit(?:y|ies)|refiner(?:y|ies)|mines?|brands?)\b"
)
BUSINESS_TRANSACTION_PATTERNS = tuple(re.compile(pattern) for pattern in (
    r"\bacquired\s+" + _DEAL_TOKENS + _DEAL_ASSETS,
    r"\bsold\s+(?:its|their|the|a|an|our|\d+)\s+" + _DEAL_TOKENS + _DEAL_ASSETS,
    r"\b(?:agreed|agreement|deal)\s+to\s+(?:acquire|purchase|buy|sell)\s+" + _DEAL_TOKENS + _DEAL_ASSETS,
    r"\bdivest(?:ed|iture\s+of)\s+" + _DEAL_TOKENS + _DEAL_ASSETS,
))

_EVIDENCE_PATTERNS = _compile(NON_SUPPLY_EVIDENCE_MARKERS) + BUSINESS_TRANSACTION_PATTERNS
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


ROLE_ARROW = re.compile(r"->|<->|→|←")


def is_role_label(dependency_type: object) -> bool:
    """True when a dependency label is just a counterparty role instead of what is supplied."""
    if ROLE_ARROW.search(str(dependency_type or "")):
        # "manufacturer -> logistics provider" is the panel's shorthand, not a product.
        return True
    return is_customer_role_label(dependency_type) or is_supplier_role_label(dependency_type)


# First words that name a category, a place, or a virtue rather than a company.
GENERIC_NAME_WORDS = {
    "general", "american", "united", "national", "international", "first", "global", "standard",
    "advanced", "universal", "allied", "consolidated", "digital", "energy", "capital", "financial",
    "north", "south", "west", "east", "royal", "pacific", "atlantic", "central", "western", "eastern",
    "southern", "northern", "alpha", "beta", "delta", "gamma", "omega", "apex", "summit", "pioneer",
    "liberty", "freedom", "great", "best", "premier", "prime", "super", "world", "texas", "california",
    "china", "japan", "taiwan", "boston", "chicago", "alaska", "hawaiian", "canadian", "new", "old", "mid", "trans", "inter",
}


# Trade names that never appear in the listed company name.
KNOWN_ALIASES = {
    "TSM": ("tsmc",),
    "GOOG": ("google",),
    "GOOGL": ("google",),
    "META": ("facebook",),
    "MMM": ("3m",),
    "HNHPF": ("foxconn",),
    # Trade names and trademarks unique to one company; excerpts use them instead of
    # the listed name ("Supermicro", "servers based on 2nd gen Epyc", "used in the iPhone 15").
    "SMCI": ("supermicro",),
    "AMD": ("epyc", "ryzen", "radeon"),
    "AAPL": ("iphone", "ipad", "macbook"),
    "ON": ("onsemi",),
    # "Elasticsearch technology is used by eBay, ..." names Elastic; a general prefix
    # rule would also read "Applebee's" as Apple, so product names are listed here.
    "ESTC": ("elasticsearch",),
}


def strong_aliases(node):
    """Aliases that identify a company unambiguously in prose.

    A ticker of three or more letters, the cleaned full name, its first two words,
    and known trade names. Single first words ("Boston", "Taiwan", "Alaska") are
    deliberately excluded: they are how place names get matched to companies.
    """
    if not node:
        return []
    aliases = []
    ticker = str(node.ticker or "").strip().lower()
    if len(ticker) >= 3:
        aliases.append(ticker)
    cleaned = cleaned_company_name(node)
    if cleaned:
        aliases.append(cleaned)
        words = cleaned.split()
        if len(words) >= 2:
            aliases.append(" ".join(words[:2]))
    aliases.extend(KNOWN_ALIASES.get(str(node.ticker or "").upper(), ()))
    return [alias for alias in dict.fromkeys(aliases) if len(alias) >= 2]


def cleaned_company_name(node):
    """Lowercase company name without share-class, legal-form and punctuation noise."""
    cleaned = re.sub(
        r"\b(common stock|ordinary shares|american depositary shares|class a|class b|incorporated|inc\.?|corporation|corp\.?|company|co\.?|limited|ltd\.?|plc|holdings?|group|n\.?v\.?|s\.?a\.?|a\.?g\.?|s\.?e\.?|the)\b",
        "",
        getattr(node, "name", None) or "",
        flags=re.I,
    )
    cleaned = re.sub(r"[^a-z0-9& ]+", " ", cleaned.lower())
    return re.sub(r"\s+", " ", cleaned).strip()


def normalize_for_alias_match(text):
    """Aliases are built with punctuation collapsed to spaces, so the text must be too.

    Without this, "Amazon.com" never matched the alias "amazon com", "Coca-Cola" never
    matched "coca cola", and "Lowe's" never matched "lowe s" - so the review step held
    the pipeline's best evidence (named customers in 10-K disclosures) forever.
    """
    lowered = re.sub(r"[^a-z0-9& ]+", " ", str(text or "").lower())
    return " ".join(lowered.split())


def mentions_company(text, node):
    lowered = normalize_for_alias_match(text)
    return any(
        re.search(r"(?<![a-z0-9])" + re.escape(alias) + r"(?![a-z0-9])", lowered)
        for alias in strong_aliases(node)
        if not owned_by_another(alias, node)
    )


def bound_by_generic_word(text: object, node: object) -> bool:
    """The company's only trace in the excerpt is a generic word from its name.

    Entity resolution bound "CPC Corporation, Taiwan" to TSMC (Cheniere -> TSMC) and
    "the Red Dog mine in Alaska" to Alaska Air Group; the excerpt names a place, not
    the company. A two-letter ticker ("GE supplies engines") still counts as naming it.
    """
    if not node or mentions_company(text, node):
        return False
    lowered = normalize_for_alias_match(text)
    ticker = str(getattr(node, "ticker", "") or "").strip().lower()
    if len(ticker) >= 2 and re.search(r"(?<![a-z0-9])" + re.escape(ticker) + r"(?![a-z0-9])", lowered):
        return False
    words = cleaned_company_name(node).split()
    if not words or words[0] not in GENERIC_NAME_WORDS:
        return False
    return bool(re.search(r"(?<![a-z0-9])" + re.escape(words[0]) + r"(?![a-z0-9])", lowered))


_LABEL_SUFFIXES = (" services", " products", " industry")


def comparable_label(value: object) -> str:
    """Lowercased label without a trailing "services"/"products"/"industry"."""
    text = " ".join(str(value or "").lower().split())
    for suffix in _LABEL_SUFFIXES:
        text = text.removesuffix(suffix)
    return text


def is_endpoint_label(product: object, *nodes: object) -> bool:
    """The product is just one endpoint's industry or sector ("Auto Parts", "Semiconductors")."""
    value = comparable_label(product)
    if not value:
        return False
    for node in nodes:
        for label in (getattr(node, "industry", None), getattr(node, "sector", None)):
            label = comparable_label(label)
            if label and label not in {"uncategorized", "unknown"} and value == label:
                return True
    return False



# --- Does an excerpt that names only one company still support the edge? -------------
#
# Excerpts cut from a company's own profile call it "the company" or "it": "It also
# reported its top customers as Apple, ... Microsoft" supports SiTime -> Microsoft
# without ever naming SiTime. Excerpts that never refer to a company at all ("The
# company's iron ore mines are primarily in Brazil" for Vale -> BHP) are junk.
ANAPHOR = re.compile(r"(?:^|[.;:]\s+|\(\s*)(?:the\s+company(?:'s)?|the\s+firm(?:'s)?|the\s+platform|the\s+service|the\s+airline|it|its|we|our)\b|\b(?:subsidiaries|segment|division)\s+of\s+the\s+company\b|\bthe\s+company(?:'s)?\b", re.IGNORECASE)
BOTH_ANAPHOR = re.compile(r"\bthe\s+(?:two\s+)?companies\s+(?:have|had|are|were|will|jointly|co-)", re.IGNORECASE)
# Sentences whose subject is left implicit: "Customers include AT&T, ...",
# "In 2020, 21.7% of revenues were from Shell."
# "Sales of gold dore accounted for 82% of revenue, with 16% sold to Bank of Montreal":
# a share of the (unnamed) filer's own sales, so the filer supplies the named buyer.
IMPLICIT_SUPPLIER_SHARE = re.compile(
    r"\d+(?:\.\d+)?\s*%\s+(?:of\s+(?:our\s+|its\s+)?\w+(?:\s+\w+)?\s+)?(?:was\s+|were\s+)?sold\s+to\b",
    re.IGNORECASE,
)
IMPLICIT_SUBJECT = re.compile(
    r"^(?:in\s+\d{4},?\s+)?(?:\d+(?:\.\d+)?\s*%\s+of\s+(?:its\s+|our\s+)?(?:revenues?|sales)\s+(?:were|was|came)\s+from"
    r"|(?:major\s+|key\s+|top\s+)?customers\s+(?:include|included|such\s+as))",
    re.IGNORECASE,
)
# The subject sells to the named company.
SUBJECT_SUPPLIES = re.compile(
    r"customers?\s+(?:as|include|included|including|such\s+as)|clients?\s+(?:like|such\s+as|including|include)"
    r"|serves\s+(?:\w+\s+){0,2}(?:customers|clients)|supplied\s+to|sold\s+to|suppliers?\s+(?:to|of)\b"
    r"|manufactur\w*\s+(?:[\w\-&.,']+\s+){0,8}?for\b|(?:were|was|are|is)\s+used\s+(?:on|in|by)\b"
    r"|consumed\s+by|added\s+to\s+the\s+(?:\w+\s+){0,2}supply\s+chain\s+of|%\s+of\s+(?:its\s+|our\s+)?(?:revenues?|sales)\s+(?:were|was|came)\s+from"
    r"|performed\s+(?:\w+\s+){0,3}work\s+(?:on|for)|sells?\s+(?:[\w\-&.,']+\s+){0,6}?to\b|provid\w+\s+(?:[\w\-&.,']+\s+){0,8}?(?:to|for)\b",
    re.IGNORECASE,
)
# The subject buys from, or runs on, the named company.
SUBJECT_BUYS = re.compile(
    r"infrastructure\s+such\s+as|(?:runs|built|hosted|deployed)\s+on\b|(?:uses|purchases|buys|sources)\s+(?:[\w\-&.,']+\s+){0,6}?from\b"
    r"|supplied\s+by|manufactured\s+by|customer\s+of",
    re.IGNORECASE,
)
SUPPLY_WORDS = re.compile(r"\bsuppl(?:y|ies|ied|ier|iers)\b|\bcustomers?\b|\bclients?\b|\bmanufactur|\bprovid|\bsells?\b|\bpurchas|\bcontract", re.IGNORECASE)
# Availability, integrations, ecosystems and rivals are not supply.
NON_SUPPLY_CUES = re.compile(
    r"\bavailable\s+on\b|\bintegrat\w*\s+(?:\w+\s+){0,3}with\b|\becosystem\b|\balong\s+with\b|\bcompetitors?\b|\bcompetes?\b|\brivals?\b"
    r"|\bin\s+collaboration\s+with\b|\bapps?\s+for\b"
    r"|\breplaced\s+[\w&.,' ]{1,60}(?:inc|corp|corporation|group|company)\b",
    re.IGNORECASE,
)
# A relationship evidenced only by events before 2010, or described as past, is not a
# current supply chain: "adopted IBM mainframes in 1979", "used to be the largest producer".
HISTORICAL_CUES = re.compile(r"\bused\s+to\b|\bformerly\b|\bformer\b|\bpreviously\b|\bat\s+the\s+time\b|\bonce\s+was\b|\bhistorically\b", re.IGNORECASE)
YEAR = re.compile(r"\b(1[89]\d\d|20\d\d)s?\b")
# Only a year that dates an event counts: "In 1984, Logitech won a contract", "throughout
# the 1980s". "Apple's 2009 27-inch iMac" names a product model, and the panel supplier
# still supplies Apple.
EVENT_YEAR = re.compile(
    r"\b(?:in|on|since|until|through|throughout|by|from|during|circa|as\s+of)\s+(?:the\s+)?(?:(?:early|late|mid)[\s-]+)?"
    r"(?:(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+(?:\d{1,2},?\s+)?|fall\s+of\s+|spring\s+of\s+|summer\s+of\s+|winter\s+of\s+)?"
    r"(1[89]\d\d|20\d\d)s?\b"
    r"|(?:^|[.;]\s+)(?:(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+(?:\d{1,2},?\s+)?)?(1[89]\d\d|20\d\d),",
    re.IGNORECASE,
)
# An end date in the past ("Until 2021, ...", "through December 2011") ends the relationship.
ENDED = re.compile(r"\b(?:until|through)\s+(?:(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+)?(\d{4})\b", re.IGNORECASE)
ONGOING = re.compile(r"\bsince\b|\bcurrently\b|\bcontinues?\b|\bcontinued\s+to\b|\bremains?\b|\bstill\b|\bnow\b|\btoday\b|\bongoing\b", re.IGNORECASE)
STALE_BEFORE_YEAR = 2010
# "companies such as AMD... are customers of TSMC": the excerpt was cut inside the list
# that probably named the missing company, so it cannot be judged either way.
TRUNCATED_LIST = re.compile(r"\b(?:such\s+as|including|include[sd]?)\s+[^.]{0,60}?(?:\.\.\.|…)", re.IGNORECASE)


def names_company(text: object, node: object) -> bool:
    """The excerpt names the company by a strong alias, a ticker, or a distinctive first word."""
    if not node:
        return False
    if mentions_company(text, node):
        return True
    lowered = normalize_for_alias_match(text)
    ticker = str(getattr(node, "ticker", "") or "").strip().lower()
    if len(ticker) >= 2 and not owned_by_another(ticker, node) and re.search(r"(?<![a-z0-9])" + re.escape(ticker) + r"(?![a-z0-9])", lowered):
        return True
    words = cleaned_company_name(node).split()
    return bool(words) and len(words[0]) >= 4 and words[0] not in GENERIC_NAME_WORDS and not owned_by_another(words[0], node) and bool(
        re.search(r"(?<![a-z0-9])" + re.escape(words[0]) + r"(?![a-z0-9])", lowered)
    )


def is_stale(text: str) -> bool:
    if HISTORICAL_CUES.search(text):
        return True
    this_year = datetime.now(timezone.utc).year
    if any(int(year) < this_year for year in ENDED.findall(text)):
        return True
    if ONGOING.search(text):
        return False
    years = [int(first or second) for first, second in EVENT_YEAR.findall(text)]
    return bool(years) and max(years) < STALE_BEFORE_YEAR


# --- Two companies named side by side is not a relationship between them -------------
#
# "Big Tech ... Alphabet (Google), Amazon, Apple, Meta (Facebook), Microsoft, and Nvidia"
# was published as Meta -> Nvidia; "Phillips 66, Kinder Morgan and HF Sinclair Announce
# ..." as Phillips 66 -> Kinder Morgan. Valid list sentences keep one company outside the
# list and a verb linking the list to it ("IDMs such as Intel, NXP ... outsource some of
# their production to TSMC"), so only companies that appear *solely* as list items count.
# A capitalised run of words; "In May 2024," and similar lead-ins are not list items.
_LEAD_IN = r"(?:In|On|At|As|By|For|From|Since|After|Before|During|Until|Of|With|Through|Following|Over|Under|When|While)\b"
_NAME = rf"(?!{_LEAD_IN})(?:[A-Z0-9][\w&'’.\-]*)(?:\s+(?:[A-Z0-9][\w&'’.\-]*|of|the|de|du|&))*(?:\s*\([^()]{{1,40}}\))?"
_GLUE = r"(?:\s*,\s*(?:and\s+|or\s+)?|\s+and\s+|\s+or\s+|\s*&\s*|\s*;\s*)"
ENUMERATION = re.compile(rf"{_NAME}(?:{_GLUE}{_NAME})+")
# Wording that relates the listed companies to each other; with it, a list can be a
# genuine relationship ("Dell and AMD signed a supply agreement").
PAIR_SUPPLY_WORDS = re.compile(
    r"\bsuppl\w*|\bcustomers?\b|\bclients?\b|\bmanufactur\w*|\bprovid(?:e|es|ed|ing)\b|\bsells?\b|\bsold\b|\bselling\b|\bpurchas\w*"
    r"|\bcontract\w*|\bvendors?\b|\blicens\w*|\boutsourc\w*|\buses?\b|\bused\b|\busing\b|\bpowered\s+by\b|\bdeliver\w*",
    re.IGNORECASE,
)


def company_mentions(text: str, node: object) -> list[tuple[int, int]]:
    """Character spans where the excerpt names the company."""
    spans = []
    words = cleaned_company_name(node).split()
    aliases = set(strong_aliases(node))
    if words and len(words[0]) >= 4 and words[0] not in GENERIC_NAME_WORDS:
        aliases.add(words[0])
    ticker = str(getattr(node, "ticker", "") or "").strip().lower()
    if len(ticker) >= 2:
        aliases.add(ticker)
    aliases = {alias for alias in aliases if not owned_by_another(alias, node)}
    for alias in aliases:
        # Aliases are punctuation-collapsed ("coca cola", "amazon com"); allow any
        # punctuation between their words in the original text.
        pattern = r"(?<![A-Za-z0-9])" + r"[\W_]+".join(re.escape(part) for part in alias.split()) + r"(?![A-Za-z0-9])"
        spans.extend(match.span() for match in re.finditer(pattern, text, re.IGNORECASE))
    return spans


# Dates are capitalised too: "In May 2024, Netflix announced ..." is not the list
# "May 2024, Netflix". They are blanked (keeping positions) before lists are found.
DATE_PHRASE = re.compile(
    r"\b(?:(?:in|on|as\s+of|by|since|during|until|from|through)\s+)?(?:(?:early|late|mid)[\s-]+)?"
    r"(?:(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+(?:\d{1,2},?\s+)?\d{4}|\d{4})\b,?",
    re.IGNORECASE,
)


def listed_only_together(text: str, source_node: object, target_node: object) -> bool:
    """Both companies appear only as items of name lists, with nothing relating them."""
    if PAIR_SUPPLY_WORDS.search(text):
        return False
    masked = DATE_PHRASE.sub(lambda match: " " * len(match.group(0)), text)
    lists = [match.span() for match in ENUMERATION.finditer(masked)]
    if not lists:
        return False
    for node in (source_node, target_node):
        mentions = company_mentions(text, node)
        if not mentions or not all(any(start <= a and b <= end for start, end in lists) for a, b in mentions):
            return False
    return True


# Events that are not a supply relationship, read from the excerpt only (never from a
# model's rationale, which may mention a spin-off while describing real supply).
_ASSET = (
    r"(?:stake|interests?|operations?|business(?:es)?|divisions?|units?|product\s+lines|lines|subsidiar(?:y|ies)|assets|"
    r"plants?|facilit(?:y|ies)|refiner(?:y|ies)|mines?|brands?|rights|segments?)\b"
)
_TOKENS = r"(?:[\w\-&.%$,]+(?:'s|’s|')?\s+){0,8}?"
# Noun phrases ("purchase of", "sale of") only count business-type assets: "Meta's purchase
# of power from Vistra's nuclear plants" is supply.
_BUSINESS = r"(?:stake|interests?|business(?:es)?|divisions?|units?|subsidiar(?:y|ies)|brands?|rights|segments?|operations?)\b"
_PROPER = r"(?-i:[A-Z])[\w\-&.'’]*(?:\s+(?-i:[A-Z])[\w\-&.'’]*){0,3}"
NON_SUPPLY_EVENTS = re.compile(
    "|".join((
        # Sales, mergers, spin-offs and joint ventures.
        rf"\b(?:purchased|bought|acquiring|acquires|sold(?:\s+off)?\s+(?:its|their|the|a|an|our|\d+))\s+{_TOKENS}{_ASSET}",
        rf"\b(?:purchase|acquisition|sale)\s+of\s+{_TOKENS}{_BUSINESS}",
        # "MKS purchased Granville-Phillips from Azenta for $87 million": a named business,
        # not "purchased 100 aircraft from Boeing for $12 billion".
        rf"\b(?:purchased|bought)\s+{_PROPER}\s+from\b[^.;]{{1,60}}?(?:\bfor\s+(?:about\s+|approximately\s+|over\s+|more\s+than\s+)?(?:US)?\$|\bin\s+a\s+deal\b)",
        rf"\bacquired\s+(?:{_PROPER}\s+)?from\s+(?-i:[A-Z])",
        rf"\bmerge\w*\s+(?:its|their|the)\s+{_TOKENS}{_ASSET}",
        r"\bto\s+form\s+(?:a|an)\s+(?:[\w\-]+\s+){0,5}?(?:joint\s+venture|venture|operation|company)\b",
        r"\bspun\s+(?:off|out)\b|\bspin-?off\b|\bspinning\s+(?:off|out)\b",
        r"\b(?:it|which|freight|business|segment|unit|division|plant|subsidiary)\s+was\s+sold\s+to\b",
        rf"\bacquisition\s+of\s+{_PROPER}\s+by\b",
        # Fines, hires, investigations, data sharing, brand licensing, speculation.
        r"\bfined\b|\bantitrust\b",
        # Ownership stakes: "It is approximately 45% owned by Brookfield Asset Management",
        # "Marathon owns a 20.4% interest in MPLX". Percentages only, so "a wholly owned
        # subsidiary of X supplies Y" still reads as supply.
        r"\b\d+(?:\.\d+)?\s*%\s+owned\s+by\b|\bowns\s+(?:a\s+|an\s+)?(?:approximately\s+|about\s+|roughly\s+)?\d+(?:\.\d+)?\s*%"
        r"|\bmajority[\s-]owner\b|\b\d+(?:\.\d+)?\s*%\s+(?:stake|interest)\s+in\b",
        r"\bhired\b[^.;]{0,60}\b(?:previously|formerly)\s+(?:of|at|with)\b|\bhired\s+a\s+team\s+from\b",
        r"\binvestigated\b",
        r"\bshares?\s+(?:\w+\s+){0,2}data\s+with\b",
        r"\bbrand\s+licens\w*|\bunder\s+license\s+with\b|\bout-licens\w*",
        r"\bsuggests?\s+that\b|\b(?:may|might|could)\s+(?:use|buy|purchase|source)\b",
        r"\bsupport\s+(?:was\s+|is\s+|has\s+been\s+)?(?:broadened|extended|expanded)\b",
    )),
    re.IGNORECASE,
)


# Scraped page furniture - a live quote widget beside a menu of customers ("... Lockheed
# Martin Northrop Grumman +Approved Primes NASDAQ: FEIM LIVE $69.74 +1.01%") - is not prose.
PAGE_CHROME = re.compile(
    r"\b(?:NASDAQ|NYSE|NYSE\s+American|OTC)\s*:\s*[A-Z.]{1,6}\s+(?:LIVE\s+)?\$\d"
    r"|\bOpen\s+link\s+menu\b|\bSkip\s+to\s+(?:main\s+)?content\b|\bToggle\s+navigation\b"
)


def evidence_support(evidence: object, source_node: object, target_node: object) -> tuple[str, str]:
    """How an excerpt supports source -> target, for excerpts that do not name both companies.

    Returns (verdict, reason) where verdict is one of:
      "named"       both companies are named; this check has nothing to add
      "supported"   one company is the excerpt's subject ("It ...", "The company's ...")
                    and a verb places it on the edge's side of the relationship
      "backwards"   the same, but the verb places it on the other side
      "unclear"     both are identified, but the excerpt does not say who supplies whom
      "unsupported" the excerpt never refers to one of the companies, describes a
                    past or non-supply relationship, or is otherwise not evidence for it
    """
    text = " ".join(str(evidence or "").split())
    if PAGE_CHROME.search(text):
        return "unsupported", "the excerpt is scraped page navigation, not a statement"
    named_source, named_target = names_company(text, source_node), names_company(text, target_node)
    event = NON_SUPPLY_EVENTS.search(text)
    if event:
        return "unsupported", f'the excerpt describes a sale, spin-off, fine, hire or similar event, not supply ("{event.group(0)[:60]}")'
    if named_source and named_target:
        # Judge only the sentences about these companies: a filing excerpt that says
        # "Moderna accounted for 22% ... We previously derived revenue from Natera" is
        # current for Moderna.
        relevant = " ".join(
            sentence for sentence in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"(])", text)
            if names_company(sentence, source_node) or names_company(sentence, target_node)
        )
        if is_stale(relevant or text):
            return "unsupported", "the excerpt describes a past relationship"
        if listed_only_together(text, source_node, target_node):
            return "unsupported", "the companies are only listed side by side; nothing relates them"
        if NON_SUPPLY_CUES.search(text) and not PAIR_SUPPLY_WORDS.search(text):
            return "unsupported", "the excerpt describes an integration, collaboration, availability or rivalry, not supply"
        return "named", ""
    if is_stale(text):
        return "unsupported", "the excerpt describes a past relationship"
    if NON_SUPPLY_CUES.search(text):
        return "unsupported", "the excerpt describes availability, an integration, an ecosystem or a rival, not supply"

    subject = None
    if BOTH_ANAPHOR.search(text) and not named_source and not named_target:
        subject = "both"
    elif named_source != named_target and (ANAPHOR.search(text) or IMPLICIT_SUBJECT.search(text) or IMPLICIT_SUPPLIER_SHARE.search(text)):
        subject = "source" if not named_source else "target"
    if subject is None:
        if TRUNCATED_LIST.search(text):
            return "unclear", "the excerpt was cut off inside the list that may name the company"
        missing = source_node if not named_source else target_node
        return "unsupported", f"the excerpt never refers to {getattr(missing, 'ticker', None) or 'one of the companies'}"

    if subject != "both":
        if SUBJECT_SUPPLIES.search(text):
            subject_role = "supplier"
        elif SUBJECT_BUYS.search(text):
            subject_role = "customer"
        else:
            subject_role = None
        if subject_role:
            expected = "supplier" if subject == "source" else "customer"
            if subject_role == expected:
                return "supported", "the company is the excerpt's subject and the excerpt says how it trades with the other"
            return "backwards", "the excerpt describes the opposite direction"
    if SUPPLY_WORDS.search(text):
        return "unclear", "the excerpt mentions supply but not who supplies whom"
    return "unsupported", "the excerpt does not describe a supply relationship between these companies"



# --- Which way does the excerpt say the supply runs? -------------------------------------
#
# 36 of 203 approved links naming both companies were published backwards with
# consistent model reasoning: "Ambarella chips were used in ... encoders from Harmonic"
# became HLIT -> AMBA, "Corning is one of the main suppliers to Apple" AAPL -> GLW. Each
# pattern below places {S} as the supplier and {C} as the customer; it is tried both
# ways round, and only an answer that holds one way and not the other is used.
_GAP = r"[^.;!?]{0,160}?"
_SHORT = r"[^.;!?]{0,45}?"
_SUPPLIER_VERBS = (
    r"(?:supplies|supplied|supply|supplying|provides|provided|provide|providing|sells|sold|sell|selling|delivers|delivered|"
    r"deliver|ships|shipped|licenses|licensed|manufactures|manufactured|manufacture|manufacturing|produces|produced|makes|made)"
)
# Inflected forms only ("purchase agreement" is a noun), and never passive: "Arm
# processors are used ... like the Apple iPod" does not mean Arm uses Apple.
_NOT_PASSIVE = r"(?<!\bis\s)(?<!\bare\s)(?<!\bwas\s)(?<!\bwere\s)(?<!\bbeen\s)(?<!\bbe\s)(?<!\bbeing\s)"
_CUSTOMER_VERBS = (
    rf"{_NOT_PASSIVE}(?:uses|used|using|(?<!\bthe\s)(?<!\ba\s)(?<!\bits\s)(?<!\btheir\s)(?<!\bto\s)(?<!\bfor\s)(?<!\bin\s)use(?!\s+of\b)|"
    r"utilizes|utilized|buys|bought|purchases|purchased|sources|sourced|sourcing|"
    r"leases|leased|reserved|ordered|relies\s+on|relied\s+on|depends\s+on|adopted|selected|chose|commissioned|contracted|hired|engaged)"
)
# (template, strength). When both directions match, a stronger, more explicit phrase
# decides; equal strength means the excerpt is ambiguous and gives no answer.
DIRECTION_TEMPLATES = (
    (rf"{{S}}{_GAP}\b{_SUPPLIER_VERBS}\b{_GAP}\b(?:to|for)\b{_SHORT}{{C}}", 2),
    (rf"{{C}}{_GAP}\b{_CUSTOMER_VERBS}\b{_GAP}{{S}}", 1),
    (rf"{{C}}{_GAP}\boutsourc\w*{_GAP}\bto\s+{{S}}", 2),
    (rf"{{S}}{_GAP}\b(?:used|utilized|featured|integrated|deployed|installed|incorporated|appeared|adopted)\s+(?:in|into|on|by|across)\b{_GAP}{{C}}", 2),
    (rf"{{C}}{_GAP}\b(?:powered\s+by|built\s+on|based\s+on|running\s+on|runs\s+on|featuring|equipped\s+with)\b{_SHORT}{{S}}", 2),
    (rf"\b(?:controlled|powered|supplied|manufactured|produced|designed)\s+by\s+{{S}}{_GAP}\b(?:include|includes|included|including)\b{_GAP}{{C}}", 2),
    (rf"{{S}}{_GAP}\b(?:customers|clients|licensees)\b{_GAP}\b(?:include|included|including|like|such\s+as)\b{_GAP}{{C}}", 3),
    (rf"\b(?:customers|clients|licensees)\s+of\s+{{S}}{_GAP}{{C}}", 3),
    (rf"{{C}}{_SHORT}\b(?:is|are|was|were|became|as)\b{_SHORT}\b(?:customer|client|licensee|distributor|dealer|reseller|retailer)s?\s+(?:of|for)\b{_SHORT}{{S}}", 3),
    (rf"{{S}}{_SHORT}\b(?:is|are|was|were|became|as)\b{_SHORT}\b(?:supplier|provider|manufacturer|vendor|licensor|maker)s?\b{_SHORT}\b(?:of|to|for)\b{_GAP}{{C}}", 3),
    (rf"{{S}}{_SHORT}\b(?:is|are|was|were|became)\b{_SHORT}\b(?:official|exclusive|preferred|recommended|approved)\b{_SHORT}\bfor\b{_SHORT}{{C}}", 3),
    (rf"{{S}}{_SHORT}\b(?:chosen|selected|hired|contracted|commissioned|awarded|picked|tapped)\s+by\b{_SHORT}{{C}}", 3),
    (rf"\b(?:made|manufactured|produced)\s+for\b{_GAP}{{C}}{_GAP}\bby\b{_SHORT}{{S}}", 3),
    (rf"{{C}}{_GAP}\b(?:sells|sold|selling|offers|offered|offering|carries|carried|stocks)\b{_GAP}(?:\bfrom\b{_SHORT}{{S}}|{{S}}(?:'s|’s))", 2),
    (rf"{{S}}{_GAP}\b(?:sold|available|distributed)\s+(?:at|in|by|through)\b{_GAP}{{C}}", 2),
)
_ABBREVIATION = re.compile(r"\b(?:Inc|Corp|Co|Ltd|Jr|Sr|St|No|U\.S|S\.A|N\.V|L\.P|p\.l\.c)\.", re.IGNORECASE)


def mention_pattern(node: object) -> str | None:
    """A regex alternation matching the company's mentions, possessive included."""
    words = cleaned_company_name(node).split()
    aliases = set(strong_aliases(node))
    if words and len(words[0]) >= 4 and words[0] not in GENERIC_NAME_WORDS:
        aliases.add(words[0])
    ticker = str(getattr(node, "ticker", "") or "").strip().lower()
    if len(ticker) >= 2:
        aliases.add(ticker)
    aliases = {alias for alias in aliases if not owned_by_another(alias, node)}
    parts = [r"[\W_]+".join(re.escape(word) for word in alias.split()) for alias in sorted(aliases, key=len, reverse=True)]
    if not parts:
        return None
    return r"(?<![A-Za-z0-9])(?:" + "|".join(parts) + r")(?:'s|’s)?(?![A-Za-z0-9])"


def evidence_direction(evidence: object, source_node: object, target_node: object) -> tuple[str | None, str]:
    """("forward" | "backward" | None, matched phrase) for an excerpt naming both companies."""
    text = _ABBREVIATION.sub(lambda match: match.group(0)[:-1], " ".join(str(evidence or "").split()))
    source, target = mention_pattern(source_node), mention_pattern(target_node)
    if not source or not target:
        return None, ""
    found = {}
    for direction, supplier, customer in (("forward", source, target), ("backward", target, source)):
        for template, strength in DIRECTION_TEMPLATES:
            match = re.search(template.replace("{S}", supplier).replace("{C}", customer), text, re.IGNORECASE)
            if match and strength > found.get(direction, (0, ""))[0]:
                found[direction] = (strength, match.group(0))
    if not found:
        return None, ""
    ranked = sorted(found.items(), key=lambda item: item[1][0], reverse=True)
    if len(ranked) == 2 and ranked[0][1][0] == ranked[1][1][0]:
        return None, ""
    direction, (_strength, phrase) = ranked[0]
    return direction, phrase



# --- A short alias that is another company's whole name belongs to that company ---------
#
# Helmerich & Payne trades as HP, so every "HP announced new notebooks ..." named the
# drilling company; Apple Hospitality REIT's first word is Apple. When the universe of
# listed names is registered, an alias equal to another company's full cleaned name
# stops counting as a mention of this one.
_NAME_OWNERS: dict[str, set[str]] = {}


def register_company_names(rows) -> None:
    """rows: (ticker, name) for every listed company."""
    _NAME_OWNERS.clear()
    for ticker, name in rows:
        cleaned = cleaned_company_name(SimpleNamespace(name=name, ticker=ticker))
        if cleaned:
            _NAME_OWNERS.setdefault(cleaned, set()).add(str(ticker or "").upper())


def owned_by_another(alias: str, node: object) -> bool:
    owners = _NAME_OWNERS.get(alias)
    ticker = str(getattr(node, "ticker", "") or "").upper()
    return bool(owners) and ticker not in owners and alias != cleaned_company_name(node)
