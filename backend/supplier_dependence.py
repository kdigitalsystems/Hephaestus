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

from customer_concentration import MAX_SENTENCE_LENGTH, SUPPLIER_TITLE_MARKER, disclosure_sentence, split_sentences

FIRST_PERSON = re.compile(r"\b(?:we|our|us)\b", re.IGNORECASE)
# What may sit between "we" and the verb: "we currently rely", "we are required to buy",
# "we predominantly use". Anything else ("we received significant purchase orders") is a
# different sentence.
ADVERBS = (
    r"(?:(?:currently|also|primarily|generally|typically|only|may|will|do|have|historically|predominantly|mainly|principally|"
    r"exclusively|solely|substantially|often|continue\s+to|are\s+required\s+to|must|need\s+to)\s+)*"
)
SKIP_SENTENCE = re.compile(
    r"\b(?:do|does|did)\s+not\s+(?:\w+\s+)?(?:rely|depend)|\bnot\s+(?:\w+ly\s+)?(?:be\s+)?(?:dependent|reliant)\b"
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
    r"|\bderive[sd]?\s+(?:\w+\s+){0,4}?(?:revenues?|sales)\b|\b(?:portion|percent|%)\s+of\s+our\s+(?:net\s+|total\s+)?(?:revenues?|sales)\b"
    # The auditor is not a supplier: "services provided by CBIZ CPAs P.C. and Marcum LLP".
    r"|\b(?:independent\s+registered\s+public\s+accounting|accounting\s+firm|audit\s+(?:committee|services|fees)|auditors?)\b"
    # A source cited for a market size, not a supplier: "forecasts provided by Gartner".
    r"|\bmarket\s+(?:sizing|intelligence)\b|\bindustry\s+(?:publications|reports|surveys)\b|\bthird-party\s+sources\b"
    r"|\bderived\s+(?:and\s+extrapolated\s+)?from\b|\bestimates\s+and\s+forecasts\b"
    # Money in, not goods: financings, the parent's cash, loans and floor-plan lenders.
    r"|\b(?:gross|net)\s+proceeds\b|\bcompleted\s+a\s+(?:\w+\s+)?financing\b|\bprivate\s+placement\b|\bsources?\s+of\s+(?:funds?|funding|cash|liquidity)\b"
    r"|\bcredit\s+(?:agreements?|facilit(?:y|ies))\b|\brevolving\b|\bfloor\s*plan\b|\bmortgage\b|\bloans?\b"
    # Legal paperwork: "The Tax Opinion relied on certain facts, representations ... from us and Crane Company".
    r"|\btax\s+opinion\b|\blegal\s+opinion\b|\brepresentations\s+(?:and|or)\s+(?:warranties|undertakings)\b"
    # Rivals' products, and what the filer's own product is built to work with.
    r"|\bcompete\b[^.;]{0,60}\bagainst\b|\bcompete\s+with\b|\bsources?\s+of\s+competition\b|\badvantages\s+over\b"
    r"|\bdesigned\s+to\s+(?:support|work\s+with|be\s+compatible)\b|\bcompatible\s+with\b"
    # Courts, tax rulings and pay consultants: "relying on the reasoning in Amgen", "the IRS Ruling relied on
    # representations from us and MasterBrand", "the compensation analysis provided by Meridian".
    r"|\bindemnit\w+|\bthe\s+competition\b|\bindebtedness\b|\bscheduled\s+payments\b|\bdebt\s+service\b|\bletter\s+ruling\b|\bIRS\b|\btax\s+(?:counsel|advisors?)\b|\bfacts,\s+assumptions\b|\bcourt\s+decisions?\b|\bprecedential\b"
    r"|\bPFIC\b|\bcompensation\s+(?:committee|analysis|consultant)\b|\bnamed\s+executive\s+officers\b"
    r"|\bcommercially\s+available\b|\bside\s+effects\b"
    # The filer is the supplier: "We are a primary supplier of driveline components to GM",
    # "TSMC designates us as its preferred provider", "we receive a 15% royalty".
    r"|\bwe\s+(?:are|were|serve\s+as)\s+(?:a|an|the)\s+(?:\w+\s+){0,2}?(?:supplier|provider|manufacturer|vendor|source)\b"
    r"|\bus\s+as\s+(?:its|their|a|an|the)\s+(?:\w+[\s-]+){0,3}?(?:supplier|provider|manufacturer|vendor|source)\b"
    r"|\bwe\s+(?:receive|earn|are\s+entitled\s+to)\b[^.;]{0,40}\broyalt"
    # The filer's customers: "revenue will only come from one customer, Woodward".
    r"|\bone\s+customer\b|\bmajor\s+customers\b|\bour\s+(?:only|sole|largest|principal|primary|key|significant)\s+customers?\b"
    r"|\bcustomers?\s+(?:include|such\s+as)\b"
    # Deals and what was bought once: "a License Purchase Agreement to acquire spectrum".
    r"|\bcompleted\s+the\s+(?:purchase|acquisition)\b|\bto\s+acquire\s+(?:\w+\s+){0,3}?(?:spectrum|assets?|businesses?|operations|company|licenses)\b"

    # Other people's rules and past involvement.
    r"|\b(?:issued|granted)\s+(?:a|an)\s+(?:\w+\s+){0,3}?warrant\b|\bnet\s+(?:profit|loss|income)\b|\banticipated\s+benefits\b|\bright\s+to\s+supply\b"
    r"|\bcontinue\s+as\s+(?:a|an|the)?\s*(?:\w+\s+)?(?:supplier|provider|manufacturer)\b"
    # What the filer sells on someone's platform: "We offer our QCaaS on public clouds provided by AWS".
    r"|\boffer(?:s|ed)?\b[^.;]{0,60}\bon\s+(?:\w+\s+){0,2}?(?:clouds?|marketplaces?)\b"
    r"|\(located\s+in\s+[A-Z]|\bterms\s+of\s+use\b|\bupdates?\s+provided\s+by\b|\bwith\s+which\s+we\s+partner\b|\bearlier\b",
    re.IGNORECASE,
)

# The supplier follows these cues.
OBJECT_AFTER = re.compile(
    "|".join((
        r"\b(?:rel(?:y|ies|ied|ying)|depend(?:s|ed)?|dependent|reliant)\s+(?:\w+\s+){0,3}?(?:on|upon)\b",
        # Present tense: "we purchased acumapimod ... from Novartis" in 2015 bought an asset.
        # (Only adverbs may sit between "we" and the verb: "we received significant purchase
        # orders from Pfizer" is a customer.)
        r"\bwe\s+" + ADVERBS + r"(?:purchases?|sources?|obtains?|procures?|buys?|licenses?|buy\s+back)\b[^.;]{0,120}?\bfrom\b",
        r"\bwe\s+do\s+not\s+(?:currently\s+)?(?:source|purchase|obtain|buy)\b[^.;]{0,80}\bfrom\s+anyone\s+other\s+than\b",
        r"\b(?:are|is)\s+(?:\w+\s+){0,2}?(?:purchased|sourced|obtained|procured|licensed)\s+(?:\w+\s+){0,3}?from\b",
        # An in-licence, whenever it was signed: the licensor supplies the rights.
        r"\bwe\s+(?:\w+\s+){0,4}?(?:in-?)?licen[sc]e[sd]?\b[^.;]{0,120}?\bfrom\b",
        r"\b(?:obtain|acquire)\s+(?:\w+\s+){0,3}?rights\b[^.;]{0,80}?\bfrom\b",
        r"\bwe\s+(?:\w+\s+){0,3}?obtained\s+from\b(?=[^.;]{0,160}\blicen[sc]e\b)",
        r"\b(?:manufactured|fabricated|produced|assembled|packaged|tested|supplied|provided|built)\s+(?:for\s+us\s+)?(?:\w+ly\s+)?by\b",
        r"\bhosted\s+(?:\w+ly\s+)?(?:on|by|with|in)\b",
        # The filer is the one using them ("for ultimate use by ... manufacturers, including GM" is not).
        r"\b(?:we\s+" + ADVERBS + r"|our\s+(?:[^.;]{0,40}?\s)?)"
        r"(?:use|uses|utilize|utilizes|engage|engages|contract\s+with|outsource\s+\w+\s+to)\b[^.;]{0,80}?"
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
    r"success|efforts?|performance|reputation|brands?|(?:delivery|distribution)\s+platforms?|storefronts?|app\s+stores?|marketplaces?|"
    # Sales and marketing channels: "social media platforms such as Instagram and Pinterest",
    # "third-party ecommerce platforms, such as Amazon", "visits from Pinterest".
    r"search\s+engines?|social\s+media|e-?commerce|visits|traffic|marketing|sell(?:s|ing)?|"
    # Money, partners and obligations: "primary source of funding was through Agenus".
    r"funding|funds|cash|proceeds|dividends?|involvement|compliance|obligations?|"
    # Customers that go by other names, investors, and things that are not companies.
    r"merchants|investors?|reasoning|actions?|decisions?|application\s+of|authorit\w+|agenc\w+|distributions?|our\s+(?:wholly[\s-]owned\s+)?subsidiar\w+|"
    r"commerciali[sz]\w*)\b",
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
# Westlake for our cash flows". And a partner doing the filer's own commercial work:
# "we depend on AbbVie for the manufacture and commercialization of ORILISSA", "reliant
# on Meridian to collect payments", "dependent on J&J's ability to develop and commercialize".
DEMAND_AFTER = re.compile(
    r"^[^.;]{0,80}?\b(?:(?:our|a|the|its|such)\s+(?:\w+\s+){0,2}?customers?\b|(?:portion|percent|share|%)\s+of\s+(?:our\s+)?"
    r"(?:total\s+|net\s+|consolidated\s+)?(?:sales|revenues?)\b|as\s+a\s+customer\b"
    r"|to\s+(?:[a-z]+,?\s+(?:and\s+)?){0,3}?(?:sell|resell|distribute|market|prosecute|remit|collect\s+payments)\b"
    r"|for\s+(?:\w+\s+){0,4}?(?:cash\s+flows?|revenues?|sales|income|earnings|profits?)\b"
    r"|for\s+(?:the\s+)?(?:joint\s+)?(?:development|funding|submi\w+)\b"
    r"|(?:other\s+)?strategic\s+(?:investors?|partners?)\b|(?:other\s+)?(?:key\s+)?merchants\b"
    r"|[\u2019']s\s+(?:ability|efforts?|success|performance|compliance|obligations?|involvement|participation)\b"
    r"|for\s+(?:the\s+)?(?:manufacture|development|supply)\s+and\s+(?:co-?)?commerciali[sz]\w*)",
    re.IGNORECASE,
)
# The same, only where the sentence says the filer depends on the name: "we are relying
# on Biogen to obtain regulatory approvals ... and commercialize". (An in-licence "to
# develop and commercialize" says what the filer does with the licence, not who sells it.)
PARTNER_AFTER = re.compile(
    r"^[^.;]{0,200}?\b(?:to\s+(?:[a-z]+\s+){0,6}?commerciali[sz]e\b|(?:is|are|will\s+be)\s+commerciali[sz]ing\b"
    r"|(?:drive|assist\s+with)\s+(?:\w+\s+){0,3}?commerciali[sz]ation\b|development\s+and\s+commerciali[sz]ation\b)"
    r"|^[^.;]{0,60}?\bregulatory\s+approvals?\b",
    re.IGNORECASE,
)
DEPENDENCE_CUE = re.compile(r"^(?:rel(?:y|ies|ied|ying)|depend|reliant)", re.IGNORECASE)
# "plan to rely on", "We plan to source a portion of our initial capacity from": not a
# supplier yet. "will continue to rely on" is a current one.
PLANNED = re.compile(
    r"\b(?:(?:plans?|planned|intends?|expects?|anticipates?|seeks?)\s+to|may|might|could|would|will)\s+(?!continue\b)(?:\w+\s+)?"
    r"(?:rel(?:y|ies)|depend|use|utilize|engage|purchase|source|obtain|procure|buy|license|contract|outsource)\b",
    re.IGNORECASE,
)
# "Our aircraft were manufactured by Boeing" names a supplier; "vehicles ... produced by
# Ferrari" names a customer. The passive needs the filer ("we", "our", "us") just before it.
PASSIVE_BY = re.compile(
    r"^(?:manufactured|fabricated|produced|assembled|packaged|tested|supplied|provided|built)\b", re.IGNORECASE
)
PASSIVE_AFTER_WINDOW = 240
# "including those produced by Ferrari": other people's products.
OTHER_PRODUCTS = re.compile(
    r"\bincluding\s+(?:those|vehicles|products|brands)\b"
    # "an option to gather and compress natural gas produced by Antero Resources": the filer serves the producer.
    r"|\bgather(?:s|ing)?\b",
    re.IGNORECASE,
)
SOLE_CUE = re.compile(r"^(?:sole|single|only|primary|principal|exclusive|main|key)[\s-]", re.IGNORECASE)
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

# "-052", "-MS", "-based": the tail of a drug code or an adjective, not "Sanmina-SCI".
PRODUCT_CODE_TAIL = re.compile(r"^-(?:\d+|[A-Za-z0-9]{1,2}\b|[a-z]\w*)")
PART_NUMBER_TAIL = re.compile(r"^-\d")
# "Nov. 3, 2006" is a date, not NOV Inc.; the company is written in capitals.
CALENDAR_WORDS = {"jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec"}
# "API technology" is not "API Technology Corp": a common noun after the name is not a legal form.
LEGAL_FORMS = {"inc", "corp", "corporation", "company", "co", "ltd", "limited", "llc", "plc", "group", "holdings", "holding",
               "incorporated", "sa", "nv", "ag", "se"}
ACRONYM_MIN_MARKET_CAP = 5e9
ACRONYM_MIXED_MIN_MARKET_CAP = 5e9
# Capitalised only because they start a sentence or a clause.
SENTENCE_STARTERS = {
    "the", "a", "an", "our", "we", "in", "on", "at", "of", "for", "from", "by", "with", "to", "and", "or", "as", "if", "while",
    "also", "additionally", "however", "currently", "historically", "such", "these", "those", "this", "that", "some", "all",
    "most", "many", "each", "both", "other", "certain", "third",
}
NUMBER = re.compile(r"(?<![A-Za-z])\d[\d,.]*(?![A-Za-z])")
TABLE_NUMBERS = 10
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


ACRONYM_DEFINITION = re.compile(r"\(\s*[\u201c\"'\u2018]?([A-Za-z][A-Za-z0-9&]{1,7})[\u201d\"'\u2019]?\s*\)")
EXPANSION_SKIP = {"of", "and", "the", "for", "in", "to", "a", "an", "on"}


def defined_acronyms(text):
    """Acronym (upper case) -> the words the filing says it stands for: "age-related macular
    degeneration (AMD)", "Integrated DNA Technologies (IDT)", "Department of War (DoW)".

    A filing that defines an acronym as something else uses it that way; it is not the
    listed company that happens to share the letters.
    """
    found = {}
    for match in ACRONYM_DEFINITION.finditer(text):
        acronym = match.group(1)
        words = re.findall(r"[A-Za-z0-9]+", text[max(0, match.start() - 90):match.start()])
        for count in range(1, len(words) + 1):
            suffix = words[-count:]
            if "".join(word[0] for word in suffix if word.lower() not in EXPANSION_SKIP).lower() == acronym.lower():
                found.setdefault(acronym.upper(), " ".join(suffix).lower())
                break
    return found


def lowercase_words(text):
    """Words the document uses in lower case: common nouns, not company names.

    Web and email addresses are dropped first, or "investor.nvidia.com" would make
    "nvidia" look like an ordinary word.
    """
    return set(re.findall(r"(?<![A-Za-z0-9&])[a-z][a-z0-9&]*", URL_OR_ADDRESS.sub(" ", str(text or ""))))


# A warrant, a preferred line or a right is a security of a company, not a company: the
# sweep read their parent's filing again and linked the parent's suppliers to them.
NON_COMMON_SECURITY = re.compile(r"\b(?:warrants?|preferred|rights|subordinated|cumulative)\b|\bnotes?\s+due\b", re.IGNORECASE)
SECURITY_NOISE = re.compile(
    r"\b(?:warrants?|rights?|preferred|depositary|subordinated|cumulative|perpetual|mandatory|convertible|series\s+[a-z0-9]+|"
    r"class\s+[a-z0-9]+|common|ordinary|capital|shares?|stock|american|units?|representing|each|interest|a|in|of|the)\b",
    re.IGNORECASE,
)


def is_non_common_security(name):
    """"Cingulate Inc. Warrants", "LifeMD ... Series A Cumulative Perpetual Preferred Stock"."""
    return bool(NON_COMMON_SECURITY.search(re.sub(r"\([^)]*\)", " ", str(name or ""))))


def company_key(name):
    """One key for every listing of a company: "Alphabet Inc. Class A Common Stock" and
    "Alphabet Inc. Class C Capital Stock", or "Webull ... Ordinary Shares" and "Webull ...
    Warrants". The legal form stays in the key, so Toro Company and Toro Corp. are two."""
    text = re.sub(r"\([^)]*\)", " ", str(name or ""))
    text = re.sub(r"\d+(?:\.\d+)?%", " ", text)
    return " ".join(TOKEN.findall(SECURITY_NOISE.sub(" ", text).lower()))


def same_company(first_name, second_name):
    key = company_key(first_name)
    return bool(key) and key == company_key(second_name)


@dataclass
class SupplierDependence:
    supplier_name: str
    sentence: str
    dependency_type: str


class NameIndex:
    """Listed company names keyed by their word sequence: one dictionary probe per n-gram.

    Matching each sentence against thousands of name patterns took minutes per filing.
    Only whole names count; a first word alone ("Korea", "Research", "Enterprise") names
    a place or a common noun far more often than the company, and a name two listed
    companies share ("Toro") names neither.
    """

    MAX_WORDS = 8

    def __init__(self, known_names, market_caps=None):
        """`market_caps` maps a display name to its market cap; it lets a short all-caps
        name ("AT&T", "IQVIA") through when the company is large enough that the acronym
        is surely it, and holds back the small ones ("CSP", "IDT") that share it with a
        process or a private firm."""
        by_key, self.spelling, self.cap, owners = {}, {}, {}, {}
        self.aliases = {tuple(alias.split()) for alias in SUPPLIER_ALIASES}
        market_caps = market_caps or {}

        def add(key, display, cleaned, cap):
            by_key.setdefault(key, display)
            self.spelling.setdefault(key, str(cleaned).strip())
            self.cap.setdefault(key, cap)
            owners.setdefault(key, set()).add(company_key(display))

        for display, cleaned in known_names.items():
            key = tuple(normalize(cleaned).split())
            if key:
                add(key, display, cleaned, market_caps.get(display) or 0)
                trimmed = without_legal_suffix(key)
                if trimmed != key:
                    add(trimmed, display, " ".join(trimmed), market_caps.get(display) or 0)
        self.index = {
            key: display
            for key, display in by_key.items()
            if sum(len(word) for word in key) >= 3  # not "V.F." (the "C. V. F-36" of a Mexican company form)
            and not (len(key) == 1 and key[0] in UNSAFE_SINGLE_WORDS)
            and len(owners[key]) == 1  # not a name two companies share
        }
        # Words of each company's own name: an acronym the filing defines as something that
        # shares none of them is not this company.
        self.company_words = {key: set(key) for key in self.index}
        for alias, cleaned in SUPPLIER_ALIASES.items():
            company = tuple(cleaned.split())
            display = by_key.get(company)
            if display:
                self.index.setdefault(tuple(alias.split()), display)
                self.company_words[tuple(alias.split())] = set(company) | set(normalize(display).split())

    def find(self, sentence, common_words=frozenset(), defined=None):
        """(start, end, display name) for each company named, longest name first.

        `common_words` are the words the document also uses in lower case; a one-word
        name among them ("Joint", "Gravity") is an ordinary word in this filing. `defined`
        maps acronyms the document defines to their expansions (see defined_acronyms).
        """
        tokens = [(match.group(0), match.start(), match.end()) for match in TOKEN.finditer(sentence)]
        found, i = [], 0
        while i < len(tokens):
            for length in range(min(self.MAX_WORDS, len(tokens) - i), 0, -1):
                key = tuple(token.lower() for token, _, _ in tokens[i:i + length])
                display = self.index.get(key)
                if not display or not (tokens[i][0][0].isupper() or tokens[i][0][0].isdigit()):
                    continue  # a proper noun: "Micron", not "micron-scale"
                end = tokens[i + length - 1][2]
                if tokens[i][1] and sentence[tokens[i][1] - 1] == "-":
                    continue  # "Tier-1 automotive" is not the company "1 Automotive"
                if tokens[i][0].isdigit() and self.after_a_label(sentence, tokens, i):
                    continue  # "Tier 1 automotive": a grade or a part number, not "1 Automotive"
                tail = sentence[end:]
                if (length == 1 and PRODUCT_CODE_TAIL.match(tail)) or PART_NUMBER_TAIL.match(tail):
                    continue  # a product code: "ATI-052", "Lucid-MS" are drugs, not ATI Inc. or Lucid; "F-36"
                if length > 1 and not self.written_as_a_name(tokens[i:i + length], key):
                    continue  # "Commercial vehicle ADAS" is not the company "Commercial Vehicle Group"
                if self.other_acronym(tokens[i][0], key, defined):
                    continue  # the filing says "AMD" is a disease, "IDT" a DNA firm
                if length == 1 and not self.single_word_name(sentence, tokens, i, common_words):
                    continue
                found.append((tokens[i][1], end, display))
                i += length
                break
            else:
                i += 1
        return found

    @staticmethod
    def after_a_label(sentence, tokens, i):
        """A capitalised word, with nothing but space between, comes right before token i."""
        previous = tokens[i - 1][0] if i else ""
        if previous == "&" and i > 1 and not tokens[i - 2][0].isupper():
            previous = tokens[i - 2][0]  # "Barnes & Noble": the "Barnes" before the ampersand ("GCP & Azure" is a list)
            gap = ""
        else:
            gap = sentence[tokens[i - 1][2]:tokens[i][1]].strip() if i else "x"
        return (
            len(previous) >= 2  # a stray "I" from "PART I Microsoft" is a page header
            and previous[:1].isupper()
            and not gap
            and previous.lower() not in CORPORATE_WORDS
            and previous.lower() not in SENTENCE_STARTERS
        )

    def written_as_a_name(self, tokens, key):
        """Every word of a multi-word name is capitalised, unless the company itself writes it
        in lower case ("SK hynix", "iRobot")."""
        spelling = TOKEN.findall(self.spelling.get(key, ""))
        for position, (word, _, _) in enumerate(tokens):
            if word[:1].isupper() or word[:1].isdigit() or word.lower() in EXPANSION_SKIP or word == "&":
                continue
            own = spelling[position] if position < len(spelling) else ""
            if not own[:1].islower():
                return False
        return True

    def other_acronym(self, word, key, defined):
        """The filing defines this word as the initials of something that is not the company."""
        expansion = defined.get(word.upper()) if defined and word.isalnum() else None
        return bool(expansion) and not (set(expansion.split()) & self.company_words.get(key, set()))

    def single_word_name(self, sentence, tokens, i, common_words):
        word = tokens[i][0]
        key = (word.lower(),)
        if key in self.aliases:
            # "AWS", "TSMC": a name only a company uses, but "Wet AMD" is a disease.
            return not self.after_a_label(sentence, tokens, i) or tokens[i - 1][0].lower() in self.company_words.get(key, set())
        if word.lower() in common_words or (i and tokens[i - 1][0].lower() in POSSESSIVES):
            return False
        following = tokens[i + 1][0] if i + 1 < len(tokens) else ""
        if word.isdigit():
            return following.lower() in CORPORATE_WORDS  # "111, Inc.", not "111 million"
        if word.lower() in CALENDAR_WORDS and not word.isupper():
            return False
        spelling = self.spelling.get(key, word)
        # The company's own spelling, or capitals, or a long brand in camel case ("SportRadar",
        # "NeuroPace"); the listing may itself be in capitals ("ABBVIE INC."). A short word
        # with a stray capital is something else: "DoW" (the Department of War) is not Dow.
        if (
            word not in {spelling, spelling.upper(), spelling.capitalize(), spelling.title()}
            and not spelling.isupper()
            and len(word) < 6
        ):
            return False
        # A short all-caps word is an acronym ("API" is not APi Group, "CSP" is chip-scale
        # packaging, "IDT" is a DNA company) unless a legal form follows it or the company is
        # big enough that the acronym is surely it ("AT&T", "IQVIA").
        if word.isupper() and len(word) <= 5 and following.lower() not in LEGAL_FORMS:
            if spelling.isupper():
                if self.cap.get(key, 0) < ACRONYM_MIN_MARKET_CAP:
                    return False
            elif len(word) <= 3 or self.cap.get(key, 0) < ACRONYM_MIXED_MIN_MARKET_CAP:
                return False  # "API" for APi Group, "OPERA" for Opera: capitals of a mixed-case name need a big company
        # A capitalised word right before the name makes it part of a product or brand:
        # "MiniMed Flex pumps", "launch vehicle VMS Eve". A comma or "and" in between
        # ("Huawei, Ericsson") does not.
        if self.after_a_label(sentence, tokens, i):
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


def cue_places_supplier(sentence, cue, start):
    """Do the words between this cue and the name leave the name a supplier?"""
    between = sentence[cue.end():start]
    return (
        start <= cue.end() + OBJECT_WINDOW
        and not NOT_A_SUPPLIER.search(between)
        # "the sole supplier of components to General Motors": a name right after "for" or
        # "to" is the customer.
        and not (SOLE_CUE.match(cue.group(0)) and re.search(r"\b(?:for|to)\s+(?:the\s+)?$", between, re.IGNORECASE))
    )


def passive_is_ours(sentence, cue, start, end):
    """"Our aircraft were manufactured by Boeing" names a supplier; "vehicles ... produced by
    Ferrari" names a customer. The filer must be in the sentence before the passive, or
    just around the name ("by our U.S. facility, Novartis", "... of our total purchases")."""
    before = sentence[:cue.start()]
    if OTHER_PRODUCTS.search(before):
        return False
    return bool(
        FIRST_PERSON.search(before)
        or FIRST_PERSON.search(sentence[cue.end():start])
        or FIRST_PERSON.search(sentence[end:end + PASSIVE_AFTER_WINDOW])
    )


def placed_as_supplier(sentence, start, end, cues_after, cues_before):
    if OWN_ACTION_AFTER.match(sentence[end:]) or DEMAND_AFTER.match(sentence[end:]):
        return False
    # The nearest cue before the name governs it: in "We currently rely on one major
    # supplier, Nvidia, ... and plan to rely on another major supplier, AMD", AMD belongs
    # to the planned dependence, not the current one. A passive ("provided by") that is
    # not about the filer's own goods defers to the cue before it ("depends on").
    for cue in sorted((cue for cue in cues_after if cue.end() <= start), key=lambda cue: cue.end(), reverse=True):
        if PASSIVE_BY.match(cue.group(0)) and not passive_is_ours(sentence, cue, start, end):
            continue
        if PLANNED.search(sentence[max(0, cue.start() - 40):cue.end()]):
            break
        if DEPENDENCE_CUE.match(cue.group(0)) and PARTNER_AFTER.match(sentence[end:]):
            break
        if cue_places_supplier(sentence, cue, start):
            return True
        break
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
    defined = defined_acronyms(text)
    results, seen = [], set()
    for sentence in split_sentences(text):
        if len(sentence) > MAX_SENTENCE_LENGTH or not FIRST_PERSON.search(sentence):
            continue
        if len(NUMBER.findall(sentence)) >= TABLE_NUMBERS:
            continue  # a table or a page of figures, not a statement
        cues_after = list(OBJECT_AFTER.finditer(sentence))
        cues_before = list(OBJECT_BEFORE.finditer(sentence))
        if not (cues_after or cues_before) or SKIP_SENTENCE.search(sentence):
            continue
        named = index.find(sentence, common_words, defined)
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


def statement_names_supplier(sentence, supplier_name, index, filer_names=()):
    """Does the reader, run on this stored sentence, still name this supplier?

    Used to re-check links created under older rules: a link whose sentence no longer
    passes is dropped without anyone clicking through it.
    """
    key = company_key(supplier_name)
    return any(company_key(found.supplier_name) == key for found in extract_supplier_dependencies(sentence, index, filer_names))


def supplier_statement_problem(edge, index, clean_name):
    """Why a supplier-statement edge no longer stands under the current rules, or None.

    The stored sentence is read again, as the sweep reads a filing: links made under older
    rules (a drug partner, a sales channel, "DoW" read as Dow) fall out of it. `clean_name`
    is the name cleaner used to build `index`.
    """
    if SUPPLIER_TITLE_MARKER not in str(getattr(edge, "source_title", "") or ""):
        return None
    supplier, filer = edge.source_node, edge.target_node
    if supplier is None or filer is None:
        return None
    filer_names = (filer.name, clean_name(filer.name or ""), filer.ticker)
    if statement_names_supplier(disclosure_sentence(edge.evidence_excerpt), supplier.name, index, filer_names):
        return None
    return "the filing sentence does not read as a supplier statement under the current rules"
