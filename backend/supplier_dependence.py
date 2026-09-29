"""Read a filer's own statements about the suppliers it depends on.

Annual reports name critical suppliers: "We rely on TSMC for the production of all
wafers", "We primarily rely on Amazon Web Services to host our cloud computing", "We
purchase memory from SK Hynix Inc., Micron Technology, Inc., and Samsung". The filer
writes about its own suppliers, in the first person, so it is the customer by
construction: each statement is a supplier -> filer link whose direction the filing
fixes, found without a language model.

Precision over recall. A sentence must speak for the filer ("we", "our", "us") and carry
a dependence cue, and the supplier must be a listed company named in full where the cue
puts the supplier. Negations, former suppliers, and dependence on customers, retailers,
distributors or markets are skipped.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from customer_concentration import MAX_SENTENCE_LENGTH, split_sentences

FIRST_PERSON = re.compile(r"\b(?:we|our|us)\b", re.IGNORECASE)
SKIP_SENTENCE = re.compile(
    r"\b(?:do|does|did)\s+not\s+(?:\w+\s+)?(?:rely|depend)|\bnot\s+(?:be\s+)?(?:dependent|reliant)\b"
    r"|\bno\s+(?:single|one|individual)\s+(?:supplier|source|vendor|manufacturer)\b|\bnone\s+of\s+our\s+(?:suppliers|vendors)\b"
    r"|\bno\s+longer\b|\bpreviously\b|\bformerly\b|\bceased\b|\btransition(?:ed|ing)?\s+away\s+from\b"
    r"|\bmultiple\s+(?:sources|suppliers)\b|\balternative\s+(?:sources|suppliers)\s+(?:are|is)\s+(?:readily\s+)?available\b"
    # The stock-performance graph: "Underlying data provided by Nasdaq Global Indexes".
    r"|\bstock\s+performance\b|\btotal\s+(?:shareholder|stockholder)\s+return\b|\bcumulative\s+(?:total\s+)?return\b"
    r"|\b(?:listed|traded|quoted)\s+on\b"
    # Cash-flow tables: "Net cash provided by operating activities $ 162,623 ... 271,111".
    r"|\bnet\s+cash\s+(?:provided|used)\b"
    # Listing rules: "rely on available Nasdaq exemptions" is not a supplier.
    r"|\bexemptions?\b|\bcorporate\s+governance\b|\blisting\s+(?:rules|standards|requirements)\b|\bforeign\s+private\s+issuer\b"
    # Where revenue comes from: "We derive a significant portion of our revenue from ...
    # platforms manufactured by third parties, such as Sony's PlayStation".
    r"|\bderive[sd]?\s+(?:\w+\s+){0,4}?(?:revenues?|sales)\b|\b(?:portion|percent|%)\s+of\s+our\s+(?:net\s+|total\s+)?(?:revenues?|sales)\b",
    re.IGNORECASE,
)

# The supplier follows these cues.
OBJECT_AFTER = re.compile(
    "|".join((
        r"\b(?:rel(?:y|ies|ied|ying)|depend(?:s|ed)?|dependent|reliant)\s+(?:\w+\s+){0,3}?(?:on|upon)\b",
        # Present tense: "we purchased acumapimod ... from Novartis" in 2015 bought an asset.
        r"\bwe\s+(?:\w+\s+){0,3}?(?:purchases?|sources?|obtains?|procures?|buys?|licenses?)\b[^.;]{0,120}?\bfrom\b",
        r"\b(?:are|is)\s+(?:\w+\s+){0,2}?(?:purchased|sourced|obtained|procured|licensed)\s+(?:\w+\s+){0,3}?from\b",
        # An in-licence, whenever it was signed: the licensor supplies the rights.
        r"\bwe\s+(?:\w+\s+){0,3}?(?:in-?)?licensed\b[^.;]{0,120}?\bfrom\b",
        r"\bwe\s+(?:\w+\s+){0,3}?obtained\s+from\b(?=[^.;]{0,160}\blicen[sc]e\b)",
        r"\b(?:manufactured|fabricated|produced|assembled|packaged|tested|supplied|provided|built)\s+(?:for\s+us\s+)?(?:\w+ly\s+)?by\b",
        r"\bhosted\s+(?:\w+ly\s+)?(?:on|by|with|in)\b",
        r"\b(?:use|uses|utilize|utilizes|engage|engages|contract\s+with|outsource\s+\w+\s+to)\b[^.;]{0,80}?"
        r"\b(?:foundr(?:y|ies)|manufacturers?|subcontractors?|suppliers?|vendors?|(?:cloud\s+)?(?:service\s+)?providers?)\b[^.;]{0,40}?"
        r"\b(?:such\s+as|including|like)\b",
        r"\b(?:sole|single|only|primary|principal|exclusive|main|key)[\s-]+(?:source|supplier|manufacturer|foundry|foundries|provider|vendor)s?\b",
    )),
    re.IGNORECASE,
)
# The supplier precedes these: "TSMC is our sole foundry", "Boeing, our only supplier of".
OBJECT_BEFORE = re.compile(
    r"(?:\b(?:is|are|was|remains?)|,)\s+(?:currently\s+)?(?:our|the)\s+(?:sole|single|only|primary|principal|main|largest|exclusive|key)\s+"
    r"(?:source|supplier|manufacturer|foundry|provider|vendor)\b",
    re.IGNORECASE,
)
# Between the cue and the name: the name is a customer, a sales channel or a market
# ("rely on third-party retailers, including Amazon", "digital delivery platforms, such
# as Microsoft's Xbox Live"), or the dependence is on an ability ("depends on our
# ability to ... Taiwan") or on another company's own success.
NOT_A_SUPPLIER = re.compile(
    r"\b(?:customers?|clients?|distributors?|distribution\s+(?:channels?|systems?|partners?)|channels?|resellers?|retailers?|"
    r"OEMs?|ODMs?|original\s+(?:equipment|design)\s+manufacturers?|licensees?|competitors?|rivals?|advertis\w*|"
    r"users?|subscribers?|members?|sales|revenues?|markets?|ability|abilities|employees?|personnel|management|officers?|"
    r"founders?|safe\s+harbors?|laws?|regulations?|acquisitions?|financing|capital|liquidity|tenants?|lessees?|"
    r"franchisees?|borrowers?|lenders?|underwriters?|collaborat\w*|co-?promot\w*|joint\s+ventures?|partnerships?|approvals?|"
    r"success|efforts?|performance|reputation|brands?|(?:delivery|distribution)\s+platforms?|storefronts?|app\s+stores?|marketplaces?)\b",
    re.IGNORECASE,
)
# "depends partially on Microsoft designing its operating systems to run on our
# products", "on Microsoft to design and develop its operating system", "dependent on
# Novartis, AstraZeneca and Mereo BioPharma 5 having conducted their research": the
# company named is doing (or did) its own work, not supplying the filer.
OWN_ACTION_AFTER = re.compile(
    r"^\s+(?:(?!(?:during|including|according|regarding|following|concerning)\b)[a-z]+ing\b|to\s+(?:\w+\s+){1,4}?(?:its|their)\b)"
    r"|^[^.;]{0,80}?\bhaving\s+(?:\w+ly\s+)?\w+(?:ed|en)\b"
)
# After the name: "We depend on Walmart for a significant portion of our net sales",
# "on Amazon.com to sell our products", "Walmart, our largest customer", "dependent on
# Westlake for our cash flows", a licensee commercialising the filer's product.
DEMAND_AFTER = re.compile(
    r"^[^.;]{0,80}?\b(?:(?:our|a|the|its)\s+(?:\w+\s+){0,2}?customers?\b|(?:portion|percent|share|%)\s+of\s+(?:our\s+)?"
    r"(?:total\s+|net\s+|consolidated\s+)?(?:sales|revenues?)\b|to\s+(?:sell|resell|distribute|market)\b|as\s+a\s+customer\b"
    r"|for\s+(?:\w+\s+){0,4}?(?:cash\s+flows?|revenues?|sales|income|earnings|profits?)\b"
    # A licensee that develops or sells the filer's drug: "we depend on AbbVie for the
    # manufacture and commercialization of ORILISSA".
    r"|for\s+(?:\w+\s+){0,5}?(?:co-?)?commerciali[sz]\w*)",
    re.IGNORECASE,
)
# "plan to rely on", "We plan to source a portion of our initial capacity from": not a
# supplier yet. "will continue to rely on" is a current one.
PLANNED = re.compile(
    r"\b(?:(?:plans?|planned|intends?|expects?|anticipates?|seeks?)\s+to|may|might|could|would|will)\s+(?!continue\b)(?:\w+\s+)?"
    r"(?:rel(?:y|ies)|depend|use|utilize|engage|purchase|source|obtain|procure|buy|license|contract|outsource)\b",
    re.IGNORECASE,
)
OBJECT_WINDOW = 200
SUBJECT_WINDOW = 80
MAX_SUPPLIERS_PER_SENTENCE = 4

CLOUD = re.compile(r"\bcloud|\bhost(?:ed|ing)\b|\bdata\s+cent(?:er|re)s?\b|\bAWS\b|\bAzure\b|\bGCP\b", re.IGNORECASE)
FOUNDRY = re.compile(r"\bfoundr(?:y|ies)|\bwafers?\b|\bfabricat", re.IGNORECASE)
CONTRACT_MANUFACTURING = re.compile(
    r"\bcontract\s+manufactur|\bsubcontract|\bassembl|\bpackag|\bOSATs?\b|\belectronics\s+manufacturing\s+services\b", re.IGNORECASE
)

# Names a filing uses for a listed company that are not the company's own name. Product
# names ("iPhone", "Epyc") are left out: a supplier is a company, and evidence_quality's
# KNOWN_ALIASES carries the same entries so the review step sees the name too.
SUPPLIER_ALIASES = {
    "aws": "amazon com",
    "amazon web services": "amazon com",
    "microsoft azure": "microsoft",
    "azure": "microsoft",
    "google cloud": "alphabet",
    "google cloud platform": "alphabet",
    "tsmc": "taiwan semiconductor manufacturing",
    "amd": "advanced micro devices",
    "ibm": "international business machines",
    "foxconn": "hon hai precision industry",
    "supermicro": "super micro computer",
    "onsemi": "on semiconductor",
}
# Single words that name a company only in a longer phrase ("Target market", "Block").
UNSAFE_SINGLE_WORDS = {
    "target", "gap", "shell", "apple", "visa", "oracle", "amazon", "alphabet", "block", "match", "coach", "ball",
    "snap", "box", "fox", "first", "united", "general", "national", "american", "standard", "universal", "global",
    "advance", "total", "gravity", "commerce", "enterprise", "fortune", "unity", "compass", "progress", "insight",
    "service", "services", "energy", "power", "resources", "solutions", "systems", "technologies", "partners",
}

# Legal forms the name cleaner leaves on foreign listings ("Embraer S.A.").
LEGAL_SUFFIXES = (("s", "a"), ("n", "v"), ("a", "g"), ("s", "e"), ("s", "p", "a"), ("sa",), ("nv",), ("ag",), ("se",),
                  ("spa",), ("plc",), ("ltd",), ("limited",), ("co",), ("inc",), ("corp",), ("llc",), ("ab",), ("asa",), ("oyj",))
# A one-word name followed by another capitalised word is part of a longer proper noun
# ("Joint Venture", "Korea Electric") unless that word is a legal form, and one after a
# possessive is a common noun ("our Founder and CEO").
POSSESSIVES = {"our", "its", "their", "his", "her", "your", "a", "an", "this", "that"}
CORPORATE_WORDS = {
    "inc", "corp", "corporation", "company", "co", "ltd", "limited", "llc", "plc", "group", "holdings", "holding",
    "incorporated", "sa", "nv", "ag", "se", "technology", "technologies",
}

TOKEN = re.compile(r"[A-Za-z0-9&]+")
URL_OR_ADDRESS = re.compile(r"\S*[./@]\S*")


def normalize(text):
    return " ".join(TOKEN.findall(str(text or "").lower()))


def without_legal_suffix(words):
    words = tuple(words)
    trimmed = True
    while trimmed:
        trimmed = False
        for suffix in LEGAL_SUFFIXES:
            if len(words) > len(suffix) and words[-len(suffix):] == suffix:
                words, trimmed = words[:-len(suffix)], True
    return words


def lowercase_words(text):
    """Words the document uses in lower case: common nouns, not company names.

    Web and email addresses are dropped first, or "investor.nvidia.com" would make
    "nvidia" look like an ordinary word.
    """
    return set(re.findall(r"(?<![A-Za-z0-9&])[a-z][a-z0-9&]*", URL_OR_ADDRESS.sub(" ", str(text or ""))))


@dataclass
class SupplierDependence:
    supplier_name: str
    sentence: str
    dependency_type: str


class NameIndex:
    """Listed company names keyed by their word sequence: one dictionary probe per n-gram.

    Matching each sentence against thousands of name patterns took minutes per filing.
    Only whole names count; a first word alone ("Korea", "Research", "Enterprise") names
    a place or a common noun far more often than the company.
    """

    MAX_WORDS = 8

    def __init__(self, known_names):
        by_key, self.spelling = {}, {}
        for display, cleaned in known_names.items():
            key = tuple(normalize(cleaned).split())
            if key:
                by_key.setdefault(key, display)
                self.spelling.setdefault(key, str(cleaned).strip())
        for key, display in list(by_key.items()):
            by_key.setdefault(without_legal_suffix(key), display)
        self.index = {
            key: display
            for key, display in by_key.items()
            if len(" ".join(key)) >= 3 and not (len(key) == 1 and key[0] in UNSAFE_SINGLE_WORDS)
        }
        for alias, cleaned in SUPPLIER_ALIASES.items():
            display = by_key.get(tuple(cleaned.split()))
            if display:
                self.index.setdefault(tuple(alias.split()), display)

    def find(self, sentence, common_words=frozenset()):
        """(start, end, display name) for each company named, longest name first.

        `common_words` are the words the document also uses in lower case; a one-word
        name among them ("Joint", "Gravity") is an ordinary word in this filing.
        """
        tokens = [(match.group(0), match.start(), match.end()) for match in TOKEN.finditer(sentence)]
        found, i = [], 0
        while i < len(tokens):
            for length in range(min(self.MAX_WORDS, len(tokens) - i), 0, -1):
                display = self.index.get(tuple(token.lower() for token, _, _ in tokens[i:i + length]))
                if not display or not (tokens[i][0][0].isupper() or tokens[i][0][0].isdigit()):
                    continue  # a proper noun: "Micron", not "micron-scale"
                end = tokens[i + length - 1][2]
                if sentence[end:end + 1] == "-" and sentence[end + 1:end + 2].isdigit():
                    continue  # a product code: "ATI-052" is a drug candidate, not ATI Inc.
                if length == 1 and not self.single_word_name(tokens, i, common_words):
                    continue
                found.append((tokens[i][1], end, display))
                i += length
                break
            else:
                i += 1
        return found

    def single_word_name(self, tokens, i, common_words):
        word = tokens[i][0]
        if word.lower() in common_words or (i and tokens[i - 1][0].lower() in POSSESSIVES):
            return False
        following = tokens[i + 1][0] if i + 1 < len(tokens) else ""
        if word.isdigit():
            return following.lower() in CORPORATE_WORDS  # "111, Inc.", not "111 million"
        # A short all-caps word is an acronym ("API" is not APi Group) unless the
        # company's own name is written that way ("AMD", "IBM").
        if word.isupper() and len(word) <= 5 and not self.spelling.get((word.lower(),), word).isupper():
            return False
        return not (following[:1].isupper() and following[1:].islower() and following.lower() not in CORPORATE_WORDS)


def classify(sentence):
    if CLOUD.search(sentence):
        return "Cloud Infrastructure Provider"
    if FOUNDRY.search(sentence):
        return "Foundry Services"
    if CONTRACT_MANUFACTURING.search(sentence):
        return "Contract Manufacturing"
    return "Supply Relationship"


def placed_as_supplier(sentence, start, end, cues_after, cues_before):
    if OWN_ACTION_AFTER.match(sentence[end:]) or DEMAND_AFTER.match(sentence[end:]):
        return False
    # The nearest cue before the name governs it: in "We currently rely on one major
    # supplier, Nvidia, ... and plan to rely on another major supplier, AMD", AMD belongs
    # to the planned dependence, not the current one.
    governing = max((cue for cue in cues_after if cue.end() <= start), key=lambda cue: cue.end(), default=None)
    if (
        governing is not None
        and start <= governing.end() + OBJECT_WINDOW
        and not PLANNED.search(sentence[max(0, governing.start() - 40):governing.end()])
        and not NOT_A_SUPPLIER.search(sentence[governing.end():start])
    ):
        return True
    for cue in cues_before:
        if end <= cue.start() and cue.start() - end <= SUBJECT_WINDOW and not NOT_A_SUPPLIER.search(sentence[end:cue.start()]):
            return True
    return False


def extract_supplier_dependencies(text, index, filer_names=()):
    """Suppliers the filer says it depends on, one per supplier, in filing order.

    `index` is a NameIndex over the listed companies; `filer_names` are excluded so the
    filer never becomes its own supplier.
    """
    filer_keys = {normalize(name) for name in filer_names if normalize(name)}
    common_words = lowercase_words(text)
    results, seen = [], set()
    for sentence in split_sentences(text):
        if len(sentence) > MAX_SENTENCE_LENGTH or not FIRST_PERSON.search(sentence):
            continue
        cues_after = list(OBJECT_AFTER.finditer(sentence))
        cues_before = list(OBJECT_BEFORE.finditer(sentence))
        if not (cues_after or cues_before) or SKIP_SENTENCE.search(sentence):
            continue
        named = index.find(sentence, common_words)
        suppliers = list(dict.fromkeys(
            display
            for start, end, display in named
            if normalize(display) not in filer_keys
            and placed_as_supplier(sentence, start, end, cues_after, cues_before)
        ))
        if not suppliers or len(suppliers) > MAX_SUPPLIERS_PER_SENTENCE:
            continue
        for supplier in suppliers:
            if supplier.lower() not in seen:
                seen.add(supplier.lower())
                results.append(SupplierDependence(supplier, sentence, classify(sentence)))
    return results
