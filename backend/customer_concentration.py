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
# Revenue only: a customer's share of accounts receivable is a different (and usually
# larger) number, and publishing it as "24% of revenue" would be wrong.
REVENUE_PATTERN = re.compile(
    r"\b(?:net\s+sales|net\s+revenues?|revenues?|sales|total\s+revenues?|consolidated\s+revenues?)\b",
    re.IGNORECASE,
)
RECEIVABLES_PATTERN = re.compile(r"\b(?:accounts?\s+receivable|receivables?|billings)\b", re.IGNORECASE)
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
AMBIGUOUS_NAMES = {"target", "gap", "shell", "apple", "visa", "oracle", "amazon", "alphabet", "block", "match", "coach", "ball", "snap", "box", "fox", "first", "united", "general", "national", "american", "standard", "universal", "global", "advance", "total", "pool", "city", "hello", "strategy", "southern", "progressive"}
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
# "Amazon, Best Buy and Walmart collectively accounted for 81%" is a group figure;
# it belongs to none of the names individually (Roku's retailers were each published at 81%).
COLLECTIVE_CUE = re.compile(
    r"\b(?:collectively|combined|together|jointly|in\s+(?:the\s+)?aggregate|an\s+aggregate\s+of|as\s+a\s+group|in\s+total)\b",
    re.IGNORECASE,
)
# Vendor concentration is the opposite relationship: TD SYNNEX's 10-K table of revenue
# "generated from products purchased from vendors" named Apple at 12%, and Apple was
# published as a customer of its own distributor.
VENDOR_CONCENTRATION = re.compile(
    r"\b(?:purchased|purchases|sourced|procured|bought)\s+from\b"
    r"|\b(?:vendors?|suppliers?)\s+(?:that|which|who)\b"
    r"|\bfrom\s+(?:(?:our|its|this|these|such|a|one|two|three)\s+)?(?:(?:largest|principal|major|key)\s+)?(?:vendors?|suppliers?)\b"
    r"|\b(?:vendor|supplier)\s+concentration\b",
    re.IGNORECASE,
)
# A rating agency named next to "rating" is not a customer (Sabesp -> S&P Global 50%).
RATING_CONTEXT = re.compile(r"\b(?:credit\s+)?ratings?\b|\brated\b|\bmoody|\bfitch\b", re.IGNORECASE)
# Shares outside these bounds are usually a mispaired percentage and need a human look:
# issuers disclose customers at 10% or more, and a mega-cap customer above 40% of a
# supplier's revenue is rare enough (Symbotic -> Walmart) to confirm by hand.
MIN_PLAUSIBLE_SHARE = 9.5
MEGACAP_MARKET_CAP = 1e11
MAX_PLAUSIBLE_MEGACAP_SHARE = 40.0

# Why a named customer has no share of its own. The first four mean the filing does name it
# as a customer and the figure is simply not its own (a joint, aggregate or range figure, a
# table cell that is blank this year): the link can stand without the number. The last three
# mean the sentence does not make it a customer with a share at all.
GROUP_FIGURE = "group"
RANGE_FIGURE = "range"
PRIOR_YEAR_ONLY = "prior_year"
UNMAPPED = "unmapped"
NEGATED = "negated"
NOT_A_CUSTOMER = "not_a_customer"
NO_FIGURE = "no_figure"
KEEPS_THE_LINK = frozenset({GROUP_FIGURE, RANGE_FIGURE, PRIOR_YEAR_ONLY, UNMAPPED})

# A figure that is not a customer's share of revenue: a ceiling ("less than 10%"), a
# condition ("if 60% or more of its revenue ... is derived from"), or a rate of change.
UPPER_BOUND = re.compile(
    r"(?:\b(?:less|fewer|lower|smaller)\s+than|\bunder|\bbelow|\bup\s+to|\bat\s+most|\bno\s+(?:more|greater)\s+than"
    r"|\bnot\s+(?:more|greater|over|exceeding)(?:\s+than)?|<)\s*(?:approximately\s+|about\s+|roughly\s+|around\s+|nearly\s+)?$",
    re.IGNORECASE,
)
CONDITION = re.compile(r"\b(?:if|unless|provided\s+that|in\s+the\s+event\s+that)\b[^.;:]{0,70}$", re.IGNORECASE)
RATE_OF_CHANGE = re.compile(
    r"(?:\b(?:increase|decrease|decline|growth|reduction|rise|drop|change|improvement)s?\s+(?:of|by|in)\s+(?:approximately\s+|about\s+|nearly\s+)?"
    r"|\b(?:increased|decreased|declined|grew|fell|rose|dropped|improved)\s+(?:by\s+)?(?:approximately\s+|about\s+|nearly\s+)?)$",
    re.IGNORECASE,
)
RATE_OF_CHANGE_AFTER = re.compile(r"^\s?(?:%|percent)?\s*(?:increase|decrease|decline|growth|reduction)\b", re.IGNORECASE)
# "between 14% to 28%": one range stated for several customers, not a share for any of them. A dash
# with spaces round it is a table separator ("12 % - 11 %"), not a range.
RANGE = re.compile(
    r"\b(?:between|from|ranging\s+from|ranged\s+from)\s+\d{1,2}(?:\.\d+)?\s?(?:%|percent)?\s+(?:to|and|through)\s+\d{1,2}(?:\.\d+)?\s?(?:%|percent)"
    r"|\d{1,2}(?:\.\d+)?\s?(?:%|percent)?(?:[-\u2013\u2014]|\s+(?:to|through)\s+)\d{1,2}(?:\.\d+)?\s?(?:%|percent)",
    re.IGNORECASE,
)
# A table cell with nothing to report this year: "*" (below the 10% threshold), "n/a", "< 10 %".
TABLE_BLANK = re.compile(r"(?<!\S)(?:\*+|n/?a|NM)(?!\S)|<\s*\d{1,2}(?:\.\d+)?\s?(?:%|percent)", re.IGNORECASE)

# Company names used as modifiers of the filer's own business ("the Pool business", "Research
# Solutions products", "the Southern Region") are not the company.
DESCRIPTOR_AFTER = re.compile(
    r"^\s+(?:business(?:es)?|segments?|divisions?|regions?|units?|markets?|operations|products?|services?|brands?|platforms?|channels?"
    r"|customers?)\b|^\s+(?:Regions?|Segments?|Divisions?|Revenues?|Leasing|Business)\b"
)
FOLLOWING_WORD = re.compile(r"^\s+(of\s+)?([A-Z][\w&\u2019'.-]*)")
PRECEDING_WORD = re.compile(r"([A-Z][a-z]{2,}[\w\u2019'-]*)\s+$")
# "sales of new Brunswick boats": the name is the product sold, not the buyer.
PRODUCT_SOLD = re.compile(r"\b(?:sales|revenues?|shipments)\s+of\s+(?:(?:new|used|the|our|its|certain)\s+){0,2}$", re.IGNORECASE)
LEGAL_WORDS = {
    "inc", "corp", "corporation", "co", "company", "ltd", "limited", "llc", "plc", "lp", "llp", "sa", "nv", "ag", "se", "ab", "asa",
    "oyj", "spa", "bv", "group", "holdings", "holding", "incorporated", "class", "common", "ordinary", "stock", "shares",
    "us", "usa", "uk", "eu", "technologies",
}
# Words that are capitalised only for their place in a sentence or a table heading, so that
# "Customers, Apple and Samsung" is not read as three names and "Customers Walmart" is Walmart.
NON_NAME_WORDS = {
    "the", "a", "an", "our", "we", "in", "on", "at", "of", "for", "from", "by", "with", "to", "and", "or", "as", "if", "while",
    "also", "additionally", "however", "currently", "historically", "such", "these", "those", "this", "that", "some", "all",
    "most", "many", "each", "both", "other", "certain", "during", "since", "after", "before", "overall", "similarly",
    "specifically", "notably", "furthermore", "moreover", "accordingly", "therefore", "thus", "although", "though", "customer",
    "customers", "distributor", "distributors", "client", "clients", "retailer", "retailers", "reseller", "resellers", "sales",
    "revenue", "revenues", "net", "total", "major", "significant", "concentration", "concentrations", "risk", "risks", "note",
    "notes", "item", "part", "table", "contents", "information", "fiscal", "year", "years", "quarter", "top", "largest", "one",
    "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "first", "second", "third", "fourth", "fifth",
    "january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december",
    "approximately", "about", "there", "it", "its", "their", "which", "who", "where", "when", "following", "percentage",
    "percent", "direct", "indirect", "product", "products", "segment", "segments", "business", "see", "no", "not",
}
# How far a concentration verb may sit from the names it governs (an appositive can fill 250
# characters: "Apple, through sales to multiple distributors, contract manufacturers ... in the
# aggregate accounted for 67%"), and how far its first figure from the verb. Beyond that the
# verb belongs to another clause.
VERB_WINDOW = 320
FIGURE_WINDOW = 120


@dataclass
class ConcentrationDisclosure:
    customer_name: str
    share_pct: float | None
    sentence: str
    fiscal_year: str | None = None
    candidates: list[str] = field(default_factory=list)
    reason: str | None = None  # why share_pct is None, when the sentence is read but not attributable


ABBREVIATION_END = re.compile(r"(?:^|\s)(?:[A-Z]|S\.A|N\.V|A\.G|S\.E|U\.S|U\.K|St|No|Mr|Ms|Dr|vs)\.$")


def split_sentences(text):
    normalized = " ".join(str(text or "").split())
    merged = []
    for part in SENTENCE_SPLIT.split(normalized):
        # "Petroleo Brasileiro S.A. (together with...)" is one sentence, and splitting it
        # left the disclosure without the customer that owns the percentage.
        if merged and ABBREVIATION_END.search(merged[-1]):
            merged[-1] = f"{merged[-1]} {part}"
        else:
            merged.append(part)
    return [sentence.strip() for sentence in merged if sentence.strip()]


ZERO_WIDTH = re.compile(r"[\u200b-\u200d\u2060\ufeff]")
# Page furniture the text conversion leaves inside sentences: "... group revenues. 22 Table of
# Contents Our Growth Strategy ..." joined a customer sentence to the next section.
PAGE_FOOTER = re.compile(r"\b(?:\d{1,3}\s+)?Table of Contents\b(?:\s+Index to (?:Consolidated )?Financial Statements\b)?")
# "Prevail Therapeutics Inc. ("Prevail") and TG Therapeutics ..." was split after "Inc.", and the
# stored sentence began with the defined term. A bracket right after a legal abbreviation continues
# the company's name ("Booking Holdings Inc. (and its subsidiaries)").
BRACKET_START = re.compile(r"^\(")
LEGAL_ABBREVIATION_END = re.compile(r"\b(?:Inc|Corp|Co|Ltd|Cos|LLC|L\.P|S\.A|N\.V|plc)\.$")


def disclosure_sentences(text):
    """The sentences of a filing as the concentration reader sees them."""
    cleaned = PAGE_FOOTER.sub(" ", ZERO_WIDTH.sub(" ", str(text or "")))
    merged = []
    for sentence in split_sentences(cleaned):
        if merged and BRACKET_START.match(sentence) and LEGAL_ABBREVIATION_END.search(merged[-1]):
            merged[-1] = f"{merged[-1]} {sentence}"
        else:
            merged.append(sentence)
    return merged


def starts_mid_sentence(sentence):
    """A stored sentence that begins with the tail of another: a defined term in brackets, a legal suffix.

    What came before may have named more customers, so the pairing cannot be trusted either way.
    """
    first = str(sentence or "").lstrip()[:1]
    return not first or first == "(" or first.islower() or first in ")],;:\u201d\u2019\"'" or bool(
        re.match(r"(?:Inc|Corp|Co|Ltd|LLC|plc)\b\.?,", str(sentence or "").lstrip())
    )


def is_concentration_sentence(sentence):
    if len(sentence) > MAX_SENTENCE_LENGTH:
        return False
    if not figures(sentence):
        # Every percentage belonged to a receivables clause, or was a ceiling or a rate.
        return False
    if VENDOR_CONCENTRATION.search(sentence):
        return False
    return bool(REVENUE_PATTERN.search(sentence) and CONCENTRATION_PATTERN.search(sentence))


@dataclass(frozen=True)
class Figure:
    start: int
    end: int
    value: float
    in_range: bool = False  # one end of "between 14% to 28%"


def figures(sentence):
    """Every percentage that could be a customer's share of revenue, in order.

    Dropped on their own, without disqualifying the sentence around them: a receivables
    percentage, a ceiling ("less than 10%"), a condition ("if 60% or more of its revenue")
    and a rate of change ("an increase of 16%"). "More than 10%" and "10% or more" stay: SEC
    filers state the disclosure threshold that way ("each accounted for more than 10%").
    """
    ranged = [(m.start(), m.end()) for m in RANGE.finditer(sentence)]
    found = []
    for match in PERCENT_PATTERN.finditer(sentence):
        try:
            value = float(match.group(1))
        except ValueError:
            continue
        if not 0 < value < 100:
            continue
        after = sentence[match.end():match.end() + 45]
        before = sentence[max(0, match.start() - 60):match.start()]
        if (
            RECEIVABLES_PATTERN.search(after)
            or UPPER_BOUND.search(before)
            or CONDITION.search(before)
            or RATE_OF_CHANGE.search(before)
            or RATE_OF_CHANGE_AFTER.match(after)
        ):
            continue
        in_range = any(start <= match.start() and match.end() <= end for start, end in ranged)
        found.append(Figure(match.start(), match.end(), value, in_range))
    return found


# Discovery stores the disclosure as "{filer} ({ticker}) {form} filed {date}: {sentence}".
EVIDENCE_PREFIX = re.compile(r"^.*? filed \d{4}-\d{2}-\d{2}:\s*")
FILER_EVIDENCE = re.compile(r"^.{1,200}?\(([A-Z0-9][A-Z0-9.\-]{0,9})\)\s+(?:10-K|10-KT|20-F|40-F)(?:/A)?\s+filed\s+\d{4}-\d{2}-\d{2}:")
DISCLOSURE_TITLE_MARKER = "customer-concentration disclosure"
# supplier_dependence.py's links: the filer names its own supplier, so it is the customer.
SUPPLIER_TITLE_MARKER = "supplier-dependence disclosure"


def filer_documented_direction(edge):
    """True when a party's own filing states this link, which fixes the direction.

    A 10-K customer disclosure is written by the supplier about its customers, so the
    filer is the supplier by construction; a supplier-dependence statement ("We rely on
    TSMC for all wafers") is written by the customer, so the filer is the customer. The
    review models still vote "backwards" on these (P&G -> Walmart, Tyson -> Walmart), and
    that vote must not reverse or hold them.
    """
    title = str(getattr(edge, "source_title", "") or "")
    match = FILER_EVIDENCE.match(str(getattr(edge, "evidence_excerpt", "") or ""))
    if not match:
        return False
    if DISCLOSURE_TITLE_MARKER in title:
        # Whether or not the figure is the customer's own: a joint figure keeps the link and
        # loses the share (see share_statement_problem), and the filing still names the customer.
        filer = getattr(edge, "source_node", None)
    elif SUPPLIER_TITLE_MARKER in title:
        filer = getattr(edge, "target_node", None)
    else:
        return False
    ticker = str(getattr(filer, "ticker", "") or "").upper()
    return bool(ticker) and match.group(1).upper() == ticker


def disclosure_sentence(evidence_excerpt):
    """The filing sentence behind a stored concentration edge, without the provenance prefix."""
    return EVIDENCE_PREFIX.sub("", str(evidence_excerpt or ""), count=1).strip()


def fiscal_year(sentence):
    years = FISCAL_YEAR_PATTERN.findall(sentence)
    return max(years) if years else None


def name_pattern(name):
    return re.compile(r"(?<![A-Za-z0-9])" + re.escape(name) + r"(?![A-Za-z0-9])")


def is_part_of_a_longer_name(sentence, start, end, cleaned):
    """The match is a word of something else: a modifier ("the Pool business", "Research Solutions
    products", "Southern Region"), a longer proper noun ("Commercial Vehicle OEM", "the Helios
    Pool", "City of New York"), or a product sold ("sales of new Brunswick boats")."""
    after = sentence[end:end + 40]
    if DESCRIPTOR_AFTER.match(after) or PRODUCT_SOLD.search(sentence[max(0, start - 40):start]):
        return True
    follower = FOLLOWING_WORD.match(after)
    if follower:
        word = follower.group(2).rstrip(".,")
        # A short all-caps word after a name ("Commercial Vehicle OEM") is a category, not a company form.
        if word.isupper() and 2 <= len(word) <= 5 and word.lower() not in NON_NAME_WORDS | LEGAL_WORDS:
            return True
        if follower.group(1) and cleaned.lower() in AMBIGUOUS_NAMES:
            return True  # "City of New York"
    if cleaned.lower() in AMBIGUOUS_NAMES:
        # An everyday word that is also a company name is the company only when nothing
        # capitalised leads into it: "the Helios Pool" and "Windows Hello" are other things.
        preceding = PRECEDING_WORD.search(sentence[max(0, start - 30):start])
        if preceding and preceding.group(1).lower() not in NON_NAME_WORDS:
            return True
    return False


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
            if RATING_CONTEXT.search(sentence[max(0, match.start() - 60):match.end() + 60]):
                continue
            if cleaned.lower() in AMBIGUOUS_NAMES:
                before = sentence[max(0, match.start() - 24):match.start()]
                after = sentence[match.end():match.end() + 24]
                if not (CONTEXT_AFTER.search(after) or CONTEXT_BEFORE.search(before)):
                    continue
            if is_part_of_a_longer_name(sentence, match.start(), match.end(), cleaned):
                continue
            found.append((match.start(), display_name, cleaned))
            break
    found.sort()
    # Prefer the longest match when one cleaned name contains another ("Coca-Cola" vs
    # "Coca-Cola Consolidated"). Comparing display names instead dropped both, because
    # neither "The Coca-Cola Company" nor "Coca-Cola Consolidated, Inc." contains the other.
    ordered = []
    for position, name, cleaned in found:
        if any(cleaned.lower() != other.lower() and cleaned.lower() in other.lower() for _, _, other in found):
            continue
        ordered.append((position, name))
    return ordered


# --- lists of names ---------------------------------------------------------------------------

NAME_WORD = r"(?:[A-Z][\w&.\u2019'\u00ae\u2122/-]*|\d+[A-Z][\w&.\u2019'\u00ae\u2122/-]*)"
NAME_CHUNK = re.compile(
    rf"(?<![\w\u2019'])(?:the\s+)?{NAME_WORD}"
    rf"(?:\s+(?:&\s+)?{NAME_WORD}|\s+(?:of|de|du|la|van|von|der|den|do|da|dos|das|del|di|el)\s+{NAME_WORD}|\s*\([^()0-9%]{{1,40}}\)\s+{NAME_WORD})*"
    r"(?:,\s*(?:Inc|Corp|Co|Ltd|LLC|LP|PLC|Plc|Limited|Incorporated)\b\.?)?"
    r"(?:\s+(?:plc|ltd|inc|corp|co|llc|nv|ag|sa|se|spa|oyj|asa|ab|bv|gmbh|limited)\b\.?)*"  # "Shell plc"
)
# What hangs on a name without being another: "(Walmart)", "(and its subsidiaries)", "and its affiliates".
NAME_ATTACHMENT = re.compile(
    r"\s*\([^()0-9%]{1,90}\)|\s+and\s+(?:its|their)\s+(?:subsidiar(?:y|ies)|affiliates?|related\s+entities)|\s+and\s+subsidiar(?:y|ies)|[\u00ae\u2122]",
    re.IGNORECASE,
)
LIST_JOINT = re.compile(r"\s*(?:(?:,|;)\s*)?(?:(?:and|or|&)\s+)?(?:\((?:[ivx]{1,4}|[a-d]|\d{1,2})\)\s*)?")
LIST_CONNECTOR = re.compile(r",|;|\band\b|\bor\b|&")
FIGURE_JOINT = re.compile(r"\s*(?:(?:,|;)\s*)?(?:(?:and|or|&)\s+)?(?:(?:approximately|about|roughly|around|nearly)\s+)?")


def is_a_name(text):
    words = [re.sub(r"['\u2019]s$", "", word.strip(".,&'\u2019").lower()) for word in re.findall(r"[A-Za-z][A-Za-z\u2019'&.-]*", text)]
    words = [word for word in words if word]
    if not words or all(word in NON_NAME_WORDS or word in LEGAL_WORDS for word in words):
        return False
    return not (len(words) == 1 and len(words[0]) >= 7 and words[0].endswith("ly"))  # "Additionally,"


def name_chunks(sentence):
    """(start, end) of each capitalised name in the sentence, with what hangs on it."""
    chunks, position = [], 0
    while True:
        match = NAME_CHUNK.search(sentence, position)
        if not match:
            return chunks
        end = match.end()
        while True:
            attached = NAME_ATTACHMENT.match(sentence, end)
            if not attached:
                break
            end = attached.end()
        if is_a_name(sentence[match.start():match.end()]):
            chunks.append((match.start(), end))
        position = max(end, match.end())


def name_runs(sentence):
    """Names joined by list punctuation ("A, B and C"), as lists of (start, end) spans.

    Every name counts, listed company or not: the third of "Daimler, PACCAR and Traton" is the
    third whether or not Traton is one of the companies Hephaestus tracks.
    """
    runs = []
    for chunk in name_chunks(sentence):
        if runs:
            gap = sentence[runs[-1][-1][1]:chunk[0]]
            if LIST_JOINT.fullmatch(gap) and LIST_CONNECTOR.search(gap):
                previous = sentence[runs[-1][-1][0]:runs[-1][-1][1]].lower()
                if re.fullmatch(r",\s*or\s+", gap) and sentence[chunk[0]:chunk[1]].lower().strip(" ,.") in previous:
                    # "Westcon Group, or Westcon,": the short name the filing defines, not another company
                    runs[-1][-1] = (runs[-1][-1][0], chunk[1])
                else:
                    runs[-1].append(chunk)
                continue
        runs.append([chunk])
    return runs


def run_of(runs, start, end):
    for run in runs:
        for index, (chunk_start, chunk_end) in enumerate(run):
            if chunk_start <= start < chunk_end:
                return run, index
    return [(start, end)], 0


# "35%, 23% and 12% of our revenues were attributable to Shell, Exxon and Chevron, respectively":
# the figures come first and the names follow the cue.
PASSIVE_CUE = re.compile(r"(?:\b(?:attributable|due)\s+to|\bderived\s+from|\bgenerated\s+(?:by|from))\s+(?:the\s+)?$", re.IGNORECASE)


def passive_cue_before(sentence, position):
    return PASSIVE_CUE.search(sentence, max(0, position - 40), position)


def subject_names(sentence, positioned_names, runs=None):
    """Keep the names that are the subject of the disclosure.

    A customer is named before the concentration verb ("Apple accounted for 24%")
    or right after a sales cue ("sales to Walmart represented 14%"), or follows the cue of a
    passive that has the figures first. A company mentioned after the verb ("...ahead of
    Coca-Cola") is context, not a customer.
    """
    runs = name_runs(sentence) if runs is None else runs
    kept = []
    for position, name in positioned_names:
        before = sentence[max(0, position - 24):position]
        # A customer is the subject of a concentration verb that follows it ("Apple
        # accounted for 24%"), or the object of a sales cue ("sales to Walmart were").
        # A name trailing the verb is context: a peer list ("comprised A, B and Walmart,
        # whose revenues exceeded 30%") or a comparison ("ahead of Target").
        run, _ = run_of(runs, position, position)
        if (
            CONCENTRATION_PATTERN.search(sentence, position)
            or SALES_TO_CUE.search(before)
            or passive_cue_before(sentence, run[0][0])
        ):
            kept.append((position, name))
    return kept


# --- which figure belongs to which name --------------------------------------------------------

NEGATION = re.compile(r"\b(?:no|none|neither|nor|not|never|without)\b|n't\b", re.IGNORECASE)
EACH = re.compile(r"\b(?:each|both|individually)\b", re.IGNORECASE)
EACH_BEFORE = re.compile(r"\b(?:each|both)\s+of\s+(?:the\s+)?$", re.IGNORECASE)
# "Sales to our ten largest customers accounted for 53%", "SEALSQ's ten largest customers accounted
# for 60%": the verb's subject is a group of customers, not the company named just before it.
AGGREGATE_SUBJECT = re.compile(
    r"\b(?:(?:largest|top|biggest|major|significant)\s+(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten|twelve|fifteen|twenty)"
    r"|(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten|twelve|fifteen|twenty)\s+(?:largest|top|biggest|major|significant))\s+"
    r"(?:\w+\s+)?(?:customers?|distributors?|resellers?|clients?|retailers?|wholesalers?)\b",
    re.IGNORECASE,
)
# "Walmart and Target accounted for 20% and 15% in 2025 and 2024, respectively": the last "respectively"
# can pair the two figures with the two years (the pair together) as well as with the two names.
YEARS_THEN_RESPECTIVELY = re.compile(
    r"^[^.;]{0,60}?\b(?:fiscal\s+)?(?:19|20)\d\d\b(?:\s*,\s*(?:and\s+)?|\s+and\s+)(?:fiscal\s+)?(?:19|20)\d\d\b\W{0,3}respectively",
    re.IGNORECASE,
)
YEAR_LIST = re.compile(r"\b(?:fiscal\s+)?(?:19|20)\d\d(?:(?:\s*,\s*(?:and\s+)?|\s+and\s+)(?:fiscal\s+)?(?:19|20)\d\d)+", re.IGNORECASE)


def is_negated(sentence, start, end, stop):
    """"No single customer, including Walmart, accounted ..." names Walmart to say it is not one."""
    before = sentence[max(0, start - 60):start]
    cut = max((m.end() for m in CONCENTRATION_PATTERN.finditer(before)), default=0)
    cut = max(cut, before.rfind(";") + 1, before.rfind(":") + 1)
    return bool(NEGATION.search(before[cut:]) or NEGATION.search(sentence[end:stop]))


def figures_after_verb(sentence, figs, verb):
    """The figures a concentration verb introduces: the first one, then those joined to it by
    list punctuation ("18%, 11% and 10%"). Figures further on belong to other years or clauses."""
    following = [f for f in figs if f.start >= verb.end()]
    if not following or following[0].start - verb.end() > FIGURE_WINDOW:
        return []
    if CONCENTRATION_PATTERN.search(sentence, verb.end(), following[0].start):
        return []
    return figure_chain(sentence, figs, following[0])


def figure_chain(sentence, figs, first):
    run = [first]
    for figure in figs[figs.index(first) + 1:]:
        if not FIGURE_JOINT.fullmatch(sentence[run[-1].end:figure.start]):
            break
        run.append(figure)
    return run


def figures_before_cue(sentence, figs, cue_start):
    """The run of figures that ends right before a passive cue."""
    preceding = [f for f in figs if f.end <= cue_start]
    if not preceding or cue_start - preceding[-1].end > FIGURE_WINDOW:
        return []
    run = [preceding[-1]]
    for figure in reversed(preceding[:-1]):
        if not FIGURE_JOINT.fullmatch(sentence[figure.end:run[0].start]):
            break
        run.insert(0, figure)
    return run


def current_year_figure(sentence, run):
    """The figure for the latest year. Filings list the current year first, except a few that
    count up ("In 2023, 2024, and 2025, ... represented 13.8%, 10.1%, and 13.7%")."""
    if len(run) > 1:
        for listed in YEAR_LIST.finditer(sentence):
            years = [int(year) for year in re.findall(r"\d{4}", listed.group(0))]
            if len(years) == len(run) and years == sorted(set(years)):
                return run[-1]
    return run[0]


# "Our five largest customers, including Walmart, accounted for 60%": Walmart is a member of the
# group whose figure this is - unless a relative clause makes the figure its own ("Walmart, which
# accounted for 20%").
MEMBER_CUE = re.compile(r"\b(?:including|includes|such\s+as|like|namely|e\.g\.,?|led\s+by|headed\s+by|among\s+them)\s+(?:the\s+)?$", re.IGNORECASE)
RELATIVE_CLAUSE = re.compile(r"^\s*,?\s*(?:which|who|that)\b", re.IGNORECASE)
LIST_TAIL = re.compile(r"(?:\band|&|\bor|\b(?:plc|ltd|inc|corp|co|llc|nv|ag|sa|se),)\s+$", re.IGNORECASE)
TOGETHER_WITH = re.compile(r"\b(?i:together|along)\s+with\s+(?:[Tt]he\s+)?[A-Z]")


def group_share(sentence, run, index, count, figure_run, joint_region, each_region, member=False):
    """(share, reason) for the name at `index` of a list of `count` names, given the figures
    the list's verb introduces. `member`: the list is introduced as part of a larger group."""
    first = figure_run[0]
    if any(figure.in_range for figure in figure_run):
        return None, RANGE_FIGURE
    each = bool(EACH.search(sentence, *each_region) or EACH_BEFORE.search(sentence, max(0, run[0][0] - 14), run[0][0]))
    if member and len(figure_run) == 1 and not each:
        return None, GROUP_FIGURE
    if count == 1:
        if TOGETHER_WITH.search(sentence, *joint_region):
            return None, GROUP_FIGURE  # "..., and together with Expedia, accounted for 21% and 22%"
        if len(figure_run) > 1 and LIST_TAIL.search(sentence[:run[0][0]]) and len(set(re.findall(r"\b(?:19|20)\d\d\b", sentence))) < len(figure_run):
            # A name that follows "and" is the last of a list. Its several figures should be its
            # years; if they are not, a name before it was not read as one ("vivo, Samsung and
            # Apple") and the first figure would be someone else's.
            return None, UNMAPPED
        return current_year_figure(sentence, figure_run).value, None
    if COLLECTIVE_CUE.search(sentence, *joint_region):
        return None, GROUP_FIGURE
    if each:
        return first.value, None
    if len(figure_run) == count and not YEARS_THEN_RESPECTIVELY.match(sentence[figure_run[-1].end:]):
        return figure_run[index].value, None
    # One figure for several names is theirs together; a different count (three years for two
    # names, five names for four figures), or figures that may be the years' (see above), cannot
    # be mapped without guessing.
    return None, GROUP_FIGURE if len(figure_run) == 1 else UNMAPPED


def read_one(sentence, figs, runs, chunk_starts, other_names, start, end):
    """(share, reason) for the listed company named at sentence[start:end]."""
    run, index = run_of(runs, start, end)
    count = len(run)
    run_start, run_end = run[0][0], run[-1][1]

    # 1. A passive with the figures first.
    cue = passive_cue_before(sentence, run_start)
    if cue:
        before = figures_before_cue(sentence, figs, cue.start())
        if before:
            return group_share(sentence, run, index, count, before, (cue.start(), run_end + 30), (cue.start(), run_end + 30))

    # 2. A table row or a bullet: the name, then its own figures, with nothing but names and
    #    punctuation in between ("Humana Insurance Company * 19.3% 12.0%").
    if count == 1:
        limit = min((s for s in chunk_starts if s >= run_end), default=len(sentence))
        cells = [f for f in figs if run_end <= f.start < limit]
        if cells and is_table_gap(sentence[run_end:cells[0].start]):
            if TABLE_BLANK.search(sentence[run_end:cells[0].start]):
                return None, PRIOR_YEAR_ONLY
            return cells[0].value, None

    # 3. The names' figures: after a concentration verb ("accounted for 24%"), or right after
    #    the names when another verb introduces them ("Walmart ... amounted to approximately 7%").
    verb = CONCENTRATION_PATTERN.search(sentence, run_end)
    first = next((f for f in figs if f.start >= run_end), None)
    if first is not None:
        if verb is not None and verb.end() <= first.start:
            stop = verb.start()
            figure_run = figures_after_verb(sentence, figs, verb) if stop - run_end <= VERB_WINDOW else []
        else:
            stop = first.start
            figure_run = figure_chain(sentence, figs, first) if stop - run_end <= VERB_WINDOW and is_revenue_figure(sentence, first, figs) else []
        if figure_run:
            if is_negated(sentence, start, end, stop):
                return None, NEGATED
            if AGGREGATE_SUBJECT.search(sentence, end, stop):
                return None, NOT_A_CUSTOMER
            lead_start = max((f.end for f in figs if f.end <= run_start), default=0)
            if any(lead_start <= other < stop for other in other_names if not run_start <= other < run_end):
                return None, UNMAPPED  # two lists share one verb: which figure is whose is a guess
            member = bool(MEMBER_CUE.search(sentence, max(0, run_start - 25), run_start)) and not RELATIVE_CLAUSE.match(sentence[run_end:stop])
            return group_share(
                sentence, run, index, count, figure_run, (lead_start, figure_run[0].start), (run_end, figure_run[0].start), member
            )

    # 4. A lone figure and a lone name, whichever comes first ("generated 29.7% of its revenue
    #    from Amazon"); the figure must still read as revenue.
    if len(figs) == 1 and is_revenue_figure(sentence, figs[0], figs):
        if is_negated(sentence, start, end, max(end, figs[0].start)):
            return None, NEGATED
        around = (max(0, min(run_start, figs[0].start) - 80), max(run_end, figs[0].end))
        return group_share(sentence, run, index, count, figs, around, around)
    return None, NO_FIGURE


def is_table_gap(gap):
    """Only names, punctuation and blank cells lie between a name and its figures."""
    stripped = TABLE_BLANK.sub(" ", re.sub(r"\([^()]*\)", " ", gap))
    words = re.findall(r"[A-Za-z][A-Za-z\u2019'&.-]*", stripped)
    return all(word[0].isupper() or word.lower() in {"of", "and", "the", "de"} for word in words)


def is_revenue_figure(sentence, figure, figs=()):
    """A concentration verb shortly before the figure, or a revenue word shortly after it, in its
    own clause: "owns 40% of our shares, and its purchases accounted for 12% of our revenue" has
    a revenue figure, but it is not the 40%."""
    after = sentence[figure.end:figure.end + 80]
    cuts = [len(after)]
    cuts += [after.find(";")] if ";" in after else []
    verb = CONCENTRATION_PATTERN.search(after)
    if verb:
        cuts.append(verb.start())
    before = sentence[max(figure.start - 60, max((f.end for f in figs if f.end <= figure.start), default=0)):figure.start]
    return bool(CONCENTRATION_PATTERN.search(before) or REVENUE_PATTERN.search(after[:min(cuts)]))


def read_shares(sentence, positioned_names, known_names):
    """[(name, share, reason)] for each customer named in a concentration sentence.

    A percentage belongs to a name only when the sentence's structure says so. A table row
    gives a name its own figures ("Walmart 13% 16% 17%": the first is this year's, and a "*"
    there means below 10% this year). "A, B and C accounted for 18%, 11% and 10%,
    respectively" maps by position, counting every name in the list, listed company or not,
    and gives nothing when the counts differ. "A and B together accounted for 30%", "the five
    largest customers ... in the aggregate 70%", a range ("between 14% to 28%") and a ceiling
    are no customer's share. "No single customer, including Walmart, accounted for more than
    10%" names Walmart to deny it.
    """
    figs = figures(sentence)
    runs = name_runs(sentence)
    chunk_starts = [start for start, _ in name_chunks(sentence)]
    other_names = [position for position, _ in positioned_names]
    readings = []
    for position, name in sorted(positioned_names):
        end = position + len(str(known_names[name]).strip())
        share, reason = read_one(sentence, figs, runs, chunk_starts, other_names, position, end)
        readings.append((name, share, reason))
    return readings


def read_disclosures(text, known_names, filer_names=(), require_customer_cue=True):
    """Every customer named in filing text, with its share or the reason it has none.

    `known_names` maps display names to cleaned names for the companies that can be
    counterparties (the public-company universe). `filer_names` are excluded so the
    filer's own name is never read as a customer.
    """
    disclosures = []
    seen = set()
    previous = ""
    for sentence in disclosure_sentences(text):
        context = f"{previous} {sentence}"
        previous = sentence
        if not is_concentration_sentence(sentence):
            continue
        if require_customer_cue and not CUSTOMER_CUE.search(context):
            continue
        positioned = mentioned_names(sentence, known_names, filer_names)
        if not positioned or len(positioned) > MAX_NAMES_PER_SENTENCE:
            continue
        names = subject_names(sentence, positioned)
        if not names:
            continue
        year = fiscal_year(sentence)
        candidates = [name for _, name in names]
        for name, share, reason in read_shares(sentence, names, known_names):
            key = (name.lower(), share, reason)
            if key in seen:
                continue
            seen.add(key)
            disclosures.append(ConcentrationDisclosure(name, share, sentence, year, candidates, reason))
    return disclosures


def extract_disclosures(text, known_names, filer_names=(), require_customer_cue=True):
    """Return the customer-concentration disclosures found in filing text.

    A named company without a share of its own is usually a list or a comparison, not a
    disclosure; magnitude is the whole point here, so it is left out (see read_disclosures).
    """
    return [d for d in read_disclosures(text, known_names, filer_names, require_customer_cue) if d.share_pct is not None]


SHARE_PROBLEM_TEXT = {
    GROUP_FIGURE: "the sentence gives one figure for several customers together",
    RANGE_FIGURE: "the sentence gives a range for several customers, not a share for each",
    PRIOR_YEAR_ONLY: "this year's cell is blank; the stored figure is a prior year's",
    UNMAPPED: "the sentence's list of customers and its figures cannot be matched one to one",
    NEGATED: "the sentence says no customer reached the threshold",
    NOT_A_CUSTOMER: "the sentence's figure belongs to a group of customers",
    NO_FIGURE: "the sentence gives this company no share of revenue",
}


@dataclass(frozen=True)
class ShareProblem:
    """What a stored customer disclosure needs: its share corrected, cleared, or a human."""

    action: str  # "correct" | "clear" | "hold"
    share: float | None
    reason: str


def share_statement_problem(edge, known_names, clean_name):
    """Why a stored customer share no longer stands under the current reader, or None.

    The stored sentence is read again, as discovery reads a filing (`clean_name` is the name
    cleaner `known_names` was built with). Three outcomes:
    "correct": the sentence gives this customer another share (PACCAR was published at the
    first customer's 18%, the sentence says 11%). "clear": the sentence does name it a customer
    but the figure is not its own (a joint, aggregate or range figure, a blank cell this year),
    so the link stands and the number goes; the filing still fixes the direction, so the link
    keeps its protection from the review models' "backwards" vote. "hold": the sentence does not
    make this company a customer with a share (a name that is really "Southern Region", a
    ceiling, a figure for the customers of someone else), which only a person can settle.
    A stored sentence that starts mid-sentence is never judged: what was cut off may name
    more customers.
    """
    filer, customer = edge.source_node, edge.target_node
    if filer is None or customer is None:
        return None
    sentence = disclosure_sentence(edge.evidence_excerpt)
    if starts_mid_sentence(sentence):
        return None
    # The stored excerpt is the disclosure sentence alone, but discovery may have taken the customer
    # cue from the sentence before it; requiring the cue again would demote a valid edge every run.
    readings = [
        d for d in read_disclosures(sentence, known_names, (filer.name, clean_name(filer.name or "")), require_customer_cue=False)
        if d.customer_name == customer.name
    ]
    shares = [d.share_pct for d in readings if d.share_pct is not None]
    if shares:
        share = max(shares)
        if edge.revenue_share == share:
            return None
        return ShareProblem("correct", share, f"the filing sentence gives {customer.ticker} {share:g}%")
    reasons = {d.reason for d in readings}
    if reasons and reasons <= KEEPS_THE_LINK:
        if edge.revenue_share is None:
            return None
        return ShareProblem("clear", None, SHARE_PROBLEM_TEXT[sorted(reasons)[0]])
    if reasons:
        return ShareProblem("hold", None, SHARE_PROBLEM_TEXT[sorted(reasons)[0]])
    return ShareProblem("hold", None, "the filing sentence no longer yields a revenue share for this customer")


def implausible_share(share_pct, customer_market_cap=None):
    """Why a disclosed share needs human confirmation, or None when it looks right."""
    if share_pct is None:
        return None
    if share_pct < MIN_PLAUSIBLE_SHARE:
        return f"{share_pct:g}% is below the 10% disclosure threshold, which usually means a mispaired percentage"
    if customer_market_cap and customer_market_cap >= MEGACAP_MARKET_CAP and share_pct > MAX_PLAUSIBLE_MEGACAP_SHARE:
        return f"{share_pct:g}% of revenue from a mega-cap customer is unusual and needs confirmation"
    return None


def describe_share(share_pct, filer_ticker=None):
    if share_pct is None:
        return f"10%+ of {filer_ticker} revenue" if filer_ticker else "10%+ of revenue"
    share = f"{share_pct:g}%"
    return f"{share} of {filer_ticker} revenue" if filer_ticker else f"{share} of revenue"
