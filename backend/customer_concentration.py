"""Extract customer-concentration disclosures from annual filings.

Issuers must disclose customers that account for 10% or more of revenue, usually by
name: "Apple accounted for approximately 24% of our net sales". Those sentences are
the most precise supply-chain evidence available - a named counterparty, a known
direction (the filer supplies the customer), and a magnitude - so they are extracted
deterministically instead of being left to the language model.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


PERCENT_PATTERN = re.compile(r"(?<![\d.])(\d{1,2}(?:\.\d+)?)\s?(?:%|percent)", re.IGNORECASE)
REVENUE_PATTERN = re.compile(
    r"\b(?:net\s+sales|net\s+revenues?|revenues?|sales|total\s+revenues?|consolidated\s+revenues?|"
    r"accounts?\s+receivable|billings)\b",
    re.IGNORECASE,
)
CONCENTRATION_PATTERN = re.compile(
    r"\b(?:accounted\s+for|represented|represents|comprised|constituted|contributed|"
    r"made\s+up|generated|derived\s+from|attributable\s+to|concentrat)",
    re.IGNORECASE,
)
FISCAL_YEAR_PATTERN = re.compile(r"\b(?:fiscal\s+(?:year\s+)?)?(20\d{2})\b")
SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z(\"'])")
UNNAMED_CUSTOMER = re.compile(r"\b(?:one|two|three|four|five|a\s+single|no|our\s+(?:largest|top|major))\s+customers?\b", re.IGNORECASE)
GOVERNMENT_TERMS = ("government", "department of", "u.s. army", "u.s. navy", "air force", "ministry", "federal", "nasa", "medicare", "medicaid")
# Company names that are also ordinary words; they only count with corporate context.
AMBIGUOUS_NAMES = {"target", "gap", "shell", "apple", "visa", "oracle", "amazon", "alphabet", "block", "match", "coach", "ball", "snap", "box", "fox", "first", "united", "general", "national", "american", "standard", "universal", "global", "advance", "total"}
# Context that makes an ambiguous name a company: a corporate suffix, being the
# subject of a concentration verb, or following "sales to" / "customers such as".
CONTEXT_AFTER = re.compile(
    r"^(?:,?\s+(?:inc|corp|corporation|co|company|plc|ltd|limited|holdings|group|stores|technologies|systems)\b\.?"
    r"|,?\s+(?:accounted|represented|represents|comprised|constituted|contributed|and|which|who)\b)",
    re.IGNORECASE,
)
CONTEXT_BEFORE = re.compile(
    r"(?:\b(?:sales|revenue|revenues|shipments|billings|receivable)\s+(?:to|from)\s+$"
    r"|\b(?:customers?|distributors?|including|namely|such\s+as|and|with)\s+$"
    r"|,\s+$)",
    re.IGNORECASE,
)
MIN_NAME_LENGTH = 4
MAX_SENTENCE_LENGTH = 700
# A genuine disclosure names one to three customers; a longer list is a peer group,
# an index constituent list, or a remuneration benchmark.
MAX_NAMES_PER_SENTENCE = 3
# The sentence (or the one before it) must be about customers, distributors, or
# sales, not revenue in general - "Youdao accounted for 81.9% of revenues" is a
# segment, not a customer.
CUSTOMER_CUE = re.compile(
    r"\b(?:customers?|distributors?|wholesalers?|resellers?|sales\s+to|revenues?\s+from|shipments\s+to|billings\s+to)\b",
    re.IGNORECASE,
)
SALES_TO_CUE = re.compile(r"\b(?:sales|revenue|revenues|shipments|billings)\s+(?:to|from)\s*$", re.IGNORECASE)


@dataclass
class ConcentrationDisclosure:
    customer_name: str
    share_pct: float | None
    sentence: str
    fiscal_year: str | None = None
    candidates: list[str] = field(default_factory=list)


def split_sentences(text):
    normalized = " ".join(str(text or "").split())
    return [sentence.strip() for sentence in SENTENCE_SPLIT.split(normalized) if sentence.strip()]


def is_concentration_sentence(sentence):
    if len(sentence) > MAX_SENTENCE_LENGTH:
        return False
    return bool(PERCENT_PATTERN.search(sentence) and REVENUE_PATTERN.search(sentence) and CONCENTRATION_PATTERN.search(sentence))


def percentages(sentence):
    values = []
    for match in PERCENT_PATTERN.finditer(sentence):
        try:
            value = float(match.group(1))
        except ValueError:
            continue
        if 0 < value < 100:
            values.append(value)
    return values


def fiscal_year(sentence):
    years = FISCAL_YEAR_PATTERN.findall(sentence)
    return max(years) if years else None


def name_pattern(name):
    return re.compile(r"(?<![A-Za-z0-9])" + re.escape(name) + r"(?![A-Za-z0-9])")


def mentioned_names(sentence, known_names, exclude=()):
    """Known company names that appear in the sentence, in order of appearance.

    `known_names` maps a display name to its cleaned form; ambiguous names (Target,
    Apple, Shell) need corporate context so "our target market" is not a customer.
    """
    lowered_exclusions = {str(value or "").lower() for value in exclude}
    found = []
    for display_name, cleaned in known_names.items():
        cleaned = str(cleaned or "").strip()
        if len(cleaned) < MIN_NAME_LENGTH or cleaned.lower() in lowered_exclusions:
            continue
        for match in name_pattern(cleaned).finditer(sentence):
            if cleaned.lower() in AMBIGUOUS_NAMES:
                before = sentence[max(0, match.start() - 24):match.start()]
                after = sentence[match.end():match.end() + 24]
                if not (CONTEXT_AFTER.search(after) or CONTEXT_BEFORE.search(before)):
                    continue
            found.append((match.start(), display_name))
            break
    found.sort()
    # Prefer the longest name when one is a prefix of another ("Amazon" vs "Amazon Web Services").
    ordered = []
    for position, name in found:
        if any(name != other and name.lower() in other.lower() for _, other in found):
            continue
        ordered.append((position, name))
    return ordered


def subject_names(sentence, positioned_names):
    """Keep the names that are the subject of the disclosure.

    A customer is named before the concentration verb ("Apple accounted for 24%")
    or right after a sales cue ("sales to Walmart represented 14%"). A company
    mentioned after the verb ("...ahead of Coca-Cola") is context, not a customer.
    """
    verb = CONCENTRATION_PATTERN.search(sentence)
    verb_position = verb.start() if verb else len(sentence)
    kept = []
    for position, name in positioned_names:
        before = sentence[max(0, position - 24):position]
        if position < verb_position or SALES_TO_CUE.search(before):
            kept.append(name)
    return kept


def pair_names_with_shares(names, shares, sentence):
    """Match customers to percentages.

    Filings list the current year first ("24% in 2025 and 21% in 2024"), so with
    at least as many percentages as names the leading ones are taken in order.
    "X and Y each accounted for more than 10%" gives every name the same share.
    Anything else is a named 10% customer whose exact share is unknown.
    """
    lowered = f" {sentence.lower()} "
    if not names:
        return []
    if len(shares) >= len(names):
        return list(zip(names, shares[:len(names)]))
    if len(shares) == 1 and " each " in lowered:
        return [(name, shares[0]) for name in names]
    return [(name, None) for name in names]


def extract_disclosures(text, known_names, filer_names=()):
    """Return customer-concentration disclosures found in filing text.

    `known_names` maps display names to cleaned names for the companies that can be
    counterparties (the public-company universe). `filer_names` are excluded so the
    filer's own name is never read as a customer.
    """
    disclosures = []
    seen = set()
    previous = ""
    for sentence in split_sentences(text):
        context = f"{previous} {sentence}"
        previous = sentence
        if not is_concentration_sentence(sentence):
            continue
        if not CUSTOMER_CUE.search(context):
            continue
        positioned = mentioned_names(sentence, known_names, filer_names)
        if not positioned or len(positioned) > MAX_NAMES_PER_SENTENCE:
            continue
        names = subject_names(sentence, positioned)
        if not names:
            continue
        shares = percentages(sentence)
        year = fiscal_year(sentence)
        for name, share in pair_names_with_shares(names, shares, sentence):
            if share is None:
                # A named company without a pairable percentage is usually a list or
                # a comparison, not a disclosure; magnitude is the whole point here.
                continue
            key = (name.lower(), share)
            if key in seen:
                continue
            seen.add(key)
            disclosures.append(ConcentrationDisclosure(name, share, sentence, year, names))
    return disclosures


def describe_share(share_pct, filer_ticker=None):
    if share_pct is None:
        return f"10%+ of {filer_ticker} revenue" if filer_ticker else "10%+ of revenue"
    share = f"{share_pct:g}%"
    return f"{share} of {filer_ticker} revenue" if filer_ticker else f"{share} of revenue"
