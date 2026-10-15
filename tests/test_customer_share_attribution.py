"""Which customer a disclosed revenue percentage belongs to (backend/customer_concentration.py).

Every sentence below is quoted from a real 10-K or 20-F, or is a minimal variant of one.
"""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import auto_discover_edges
from customer_concentration import (
    disclosure_sentences,
    extract_disclosures,
    filer_documented_direction,
    read_disclosures,
    starts_mid_sentence,
)
from models import Base, Edge, Node

WMT, AAPL, XIAOMI, COSTCO = "Walmart Inc. Common Stock", "Apple Inc. Common Stock", "Xiaomi Corporation", "Costco Wholesale Corporation"
HD, GARMIN, PACCAR, SOUTHERN, TEXTRON = "The Home Depot, Inc.", "Garmin Ltd", "PACCAR Inc. Common Stock", "The Southern Company", "Textron, Inc."
NAMES = [
    WMT, AAPL, XIAOMI, COSTCO, HD, GARMIN, PACCAR, SOUTHERN, TEXTRON, "Target Corporation", "Shell plc", "Equinor ASA", "Chevron Corporation",
    "Pool Corporation Common Stock", "Commercial Vehicle Group, Inc. Common Stock", "Brunswick Corporation", "Expedia Group, Inc. Common Stock",
    "Booking Holdings Inc. Common Stock", "Hello Group Inc. American Depositary Shares", "City Holding Company Common Stock",
]
KNOWN = {name: auto_discover_edges.clean_company_name(name) for name in NAMES}
FILER = ("Acme Corp", "Acme")


def shares(sentence):
    """{display name: share, or None when the sentence names the company but gives it no share}."""
    return {d.customer_name: d.share_pct for d in read_disclosures(sentence, KNOWN, FILER, require_customer_cue=False)}


def reasons(sentence):
    """{display name: why it has no share}; a company with a share is left out."""
    return {d.customer_name: d.reason for d in read_disclosures(sentence, KNOWN, FILER, require_customer_cue=False) if d.share_pct is None}


# --- the hand-labelled production links -----------------------------------------------------

LABELS = json.loads((ROOT / "tests" / "data" / "customer_share_labels.json").read_text(encoding="utf-8"))
# Right customer, but the sentence's list cannot be matched to its figures without a guess:
# 'Sturm, Ruger & Co.' holds a comma of its own, so five names meet four figures.
KNOWN_DROPS = {"PEW>SWBI"}


def labelled_known():
    return {item["name"]: auto_discover_edges.clean_company_name(item["name"]) for item in LABELS["universe"]}


def read_labelled(link):
    filer_names = (link["filer"], auto_discover_edges.clean_company_name(link["filer"]))
    found = extract_disclosures(link["sentence"], labelled_known(), filer_names, require_customer_cue=False)
    found = [d.share_pct for d in found if d.customer_name == link["customer"]]
    return max(found) if found else None


def test_the_reader_on_every_hand_labelled_link():
    """182 stored links read one by one. 40 of the 165 published ones carried a share that was not
    the customer's: another customer's figure from the same list, a joint, aggregate or range
    figure, last year's cell, a name that is really "Southern Region". Every wrong link must come
    out corrected or without a share, and every right one must keep its value."""
    wrong, lost, kept = [], [], 0
    expected_numbers = expected_none = 0
    for link in LABELS["links"]:
        if link["label"] == "unconfirmed":
            continue  # cut off at the start; see test_a_cut_off_excerpt_is_never_judged
        got = read_labelled(link)
        if link["label"] is None:
            expected_none += 1
            if got is not None:
                wrong.append((link["id"], link["kind"], got, link["label"]))
            continue
        expected_numbers += 1
        if got is None and link["id"] not in KNOWN_DROPS:
            lost.append((link["id"], link["label"]))
        elif got is not None and got != link["label"]:
            wrong.append((link["id"], link["kind"], got, link["label"]))
        kept += got == link["label"]
    assert not wrong, wrong
    assert not lost, lost
    assert expected_numbers >= 140 and expected_none >= 35, (expected_numbers, expected_none)
    assert kept == expected_numbers - len(KNOWN_DROPS)


def test_the_labelled_set_holds_the_wrong_rows_it_claims():
    """Guards the fixture itself: the stored values are what was published, the labels what the sentences say."""
    published = [link for link in LABELS["links"] if link["status"] == "approved" and link["label"] != "unconfirmed"]
    wrong_stored = [link for link in published if link["stored"] != link["label"]]
    assert len(published) >= 160 and len(wrong_stored) >= 38
    kinds = {link["kind"] for link in wrong_stored}
    assert {"wrong_value", "joint", "aggregate", "range", "prior_year", "collision", "not_a_share"} <= kinds
    assert {link["id"] for link in LABELS["links"] if link["label"] == "unconfirmed"} == {"DTIL>TGTX"}


# --- lists: every name counts, tracked or not -----------------------------------------------

def test_a_list_maps_by_position_counting_every_name():
    # Daimler AG and Traton SE are not tracked companies, but they are the first and third customers.
    assert shares("Our top three customers, Daimler AG, PACCAR Inc. and Traton SE, accounted for approximately 18%, 11% and 10%, respectively, of our net sales during 2025.") == {
        PACCAR: 11.0
    }
    # Two names, two figures; the second of five; a name holding "Exxon Mobil Corporation and its subsidiaries".
    assert shares("In 2025, our top five customers were Volvo, PACCAR, Traton, Daimler Truck and Ford, which comprised 18%, 15%, 11%, 7% and 6% of our net sales, respectively.") == {PACCAR: 15.0}
    assert shares("Royalties from properties operated by Exxon Mobil Corporation and its subsidiaries and Garmin Ltd represented approximately 16 % and 15 % of total revenues, respectively.") == {GARMIN: 15.0}
    # A semicolon list with enumerators, and a legal suffix after a comma.
    assert shares("Three major customers, (i) Intellino Tech Sdn Bhd; (ii) Textron, Inc.; and (iii) Teligent International Limited , accounted for 53.1%, 18.2% and 14.3%, respectively, of revenues.") == {
        TEXTRON: 18.2
    }
    # The next run of figures is the prior year's and does not shift the list.
    assert shares("In 2025, our three largest Tier 1 customers, which were ZF, Valeo, and Textron, accounted for 30%, 17%, and 15%, respectively, of our revenue, compared to 27%, 20%, and 14%, respectively, in 2024.") == {
        TEXTRON: 15.0
    }
    # Where every name is tracked it still maps one to one.
    assert shares("Our customers Walmart, Target Corporation and Xiaomi accounted for 25%, 15% and 12%, respectively, of revenue.") == {WMT: 25.0, "Target Corporation": 15.0, XIAOMI: 12.0}


def test_a_name_in_the_list_that_the_reader_cannot_see_is_never_guessed():
    # "vivo" is written in lower case, so it is not seen as a name; the figures then outnumber the
    # names, and the first would be handed to the wrong company.
    assert shares("Our customers vivo, Apple Inc. and Xiaomi accounted for 30%, 20% and 10%, respectively, of revenue.") == {AAPL: None, XIAOMI: None}
    assert reasons("Our customers vivo and Xiaomi accounted for 30% and 20%, respectively, of revenue.") == {XIAOMI: "unmapped"}
    # Five names meet four figures: not mapped.
    assert reasons("Products manufactured by Sturm, Ruger & Co., Garmin Ltd, Springfield Armory, and Sig Sauer represented approximately 10%, 8%, 7% and 5%, respectively, of our 2025 sales.") == {GARMIN: "unmapped"}
    # "Shell plc" is one name, not "Shell" and a stray "plc".
    assert shares("Our customers Petrobras, Shell plc and Equinor ASA represented 22 percent, 22 percent and 12 percent of revenues, respectively.") == {"Shell plc": 22.0, "Equinor ASA": 12.0}
    # "Walmart Inc., or Walmart," names one company twice.
    assert shares("Sales to Walmart Inc., or Walmart, our largest customer, accounted for approximately 13.8%, 13.3% and 16.3%, respectively, of our revenues in 2025, 2024 and 2023.") == {WMT: 13.8}


def test_figures_that_may_be_the_years_of_a_pair_are_not_handed_to_the_names():
    # "20% and 15% in 2025 and 2024, respectively" can be two names or two years of the pair together.
    sentence = "Revenue from Walmart and Xiaomi represented 20% and 15% of our net sales for 2025 and 2024, respectively."
    assert reasons(sentence) == {WMT: "unmapped", XIAOMI: "unmapped"}
    assert shares("In 2025 and 2024, Walmart and Xiaomi represented 20% and 15% of our net sales, respectively.") == {WMT: 20.0, XIAOMI: 15.0}
    assert shares("Walmart and Xiaomi each represented 20% and 15% of our net sales for 2025 and 2024, respectively.") == {WMT: 20.0, XIAOMI: 20.0}


def test_two_lists_that_share_one_verb_are_not_guessed():
    sentence = "Sales to The Home Depot and sales to Walmart accounted for 17% and 11% of net sales, respectively."
    assert reasons(sentence) == {HD: "unmapped", WMT: "unmapped"}


def test_figures_that_come_first_map_to_the_names_after_the_passive():
    sentence = ("For the year ended December 31, 2025, 35%, 23% and 12% of our oil, natural gas and NGL revenues were attributable to Shell Trading (US) Company, "
                "Exxon Mobil Corporation and Chevron Corporation, respectively, which are the customers that individually represented 10% or more of our oil revenues.")
    assert shares(sentence) == {"Chevron Corporation": 12.0}
    assert shares("Approximately 24% of our revenue was generated by Xiaomi.") == {XIAOMI: 24.0}
    assert shares("Approximately 20% of our revenue was derived from Walmart.") == {WMT: 20.0}


def test_several_figures_for_one_name_are_its_years():
    assert shares("In 2025, 2024 and 2023, Walmart accounted for 20%, 19% and 18% of our net sales, respectively.") == {WMT: 20.0}
    # Some filings count up.
    assert shares("In 2023, 2024, and 2025, revenues from Walmart represented 13.8%, 10.1%, and 13.7%, respectively, of our revenues.") == {WMT: 13.7}
    assert shares("In fiscal 2023, fiscal 2024, and fiscal 2025, revenues from Walmart represented 13.8%, 10.1%, and 13.7%, respectively, of our revenues.") == {WMT: 13.7}
    # Not years: the first figure of "Alcon and the other customers" is Alcon's.
    assert shares("Walmart and the other customers accounted for 44%, 18% and 10%, respectively, of our revenue during the fiscal year ended May 25, 2025.") == {WMT: 44.0}


# --- figures that belong to no one customer --------------------------------------------------

@pytest.mark.parametrize("sentence", [
    "Apple Inc. and Xiaomi together accounted for 50% of our revenue.",
    "Apple Inc. and Xiaomi collectively accounted for approximately 30% and 28% of our revenues for the years ended December 31, 2025 and 2024.",
    "Our two largest distributors, Xiaomi and Garmin Ltd, collectively represented approximately 10% of our revenue.",
    "The Company's largest customers, Walmart, Apple Inc. and Xiaomi, accounted for approximately 36% of net product sales in 2025.",
    "Our five largest customers in 2024 were Northrop Grumman, Walmart, Detroit Diesel, SubCom and ADI, which in the aggregate accounted for 70% of net revenue.",
    "In aggregate, sales to The Home Depot and Lowe's comprised approximately 21% of net sales of the Water segment in 2025.",
    "Lowe's and Walmart comprised approximately 33 percent, 37 percent and 37 percent of our net sales for our 2025, 2024 and 2023 fiscal years.",
    "Walmart and Xiaomi accounted for more than 10% of consolidated revenues.",
])
def test_a_figure_for_several_customers_belongs_to_none_of_them(sentence):
    found = {name: share for name, share in shares(sentence).items() if name in {WMT, AAPL, XIAOMI, GARMIN, HD}}
    assert found and all(share is None for share in found.values()), found


def test_a_group_figure_stays_with_the_group_even_when_one_member_is_named():
    assert reasons("Our five largest customers, including Walmart, accounted for 60% of our net sales.") == {WMT: "group"}
    assert reasons("Our top ten customers, led by Walmart, accounted for 60% of our revenue.") == {WMT: "group"}
    assert reasons("Revenue from customers such as Walmart accounted for 12% of our revenue.") == {WMT: "group"}
    # A relative clause makes the figure the member's own.
    assert shares("Our largest customers, including Walmart, which accounted for approximately 20% of our net sales, are retailers.") == {WMT: 20.0}


def test_a_figure_for_the_n_largest_customers_is_not_the_name_before_them():
    assert reasons("In 2025, Walmart's ten largest customers accounted for 60% of Acme's revenue.") == {WMT: "not_a_customer"}
    assert reasons("Comcast Millicom Normann Engineering Telia Norge Walmart Sales to our 10 largest customers in 2025, 2024 and 2023 accounted for approximately 84%, 91% and 90% of our net revenue, respectively.") == {
        WMT: "not_a_customer"
    }
    assert reasons("Sales to Xiaomi's top three customers contributed 60 %, 31 % and 26 % of its total revenue for the years ended December 31, 2025, 2024 and 2023, respectively.") == {XIAOMI: "not_a_customer"}
    # Walmart's own clause is its own.
    assert shares("Sales to our ten largest customers accounted for 53% of total net revenues in 2025, and our top customer, Walmart, accounted for 30% of our total net revenues.") == {WMT: 30.0}
    assert shares("Our largest customer, Walmart, accounted for 20% of revenue, and our top ten customers accounted for 60%.") == {WMT: 20.0}


def test_a_range_is_stated_for_several_customers_not_for_each():
    assert reasons("During 2025, our three largest customers - Walmart, Costco Wholesale Corporation, and Xiaomi - each accounted for between 14% to 28% of our consolidated gross sales.") == {
        WMT: "range", COSTCO: "range", XIAOMI: "range",
    }
    assert reasons("Walmart accounted for 12% to 15% of our revenue.") == {WMT: "range"}


def test_together_with_another_company_is_a_joint_figure_but_with_its_own_affiliates_is_not():
    sentence = ("For the years ended December 31, 2025 and 2024, Booking Holdings Inc. (and its subsidiaries) accounted for 10 % or more of our consolidated revenue, "
                "and together with Expedia Group, Inc. (and its subsidiaries), our two most significant travel partners, accounted for approximately 21 % and 22 % of our consolidated revenue, respectively.")
    assert shares(sentence) == {"Booking Holdings Inc. Common Stock": 10.0, "Expedia Group, Inc. Common Stock": None}
    assert shares("For the years ended December 31, 2025 and 2024, we had one customer, Walmart, together with certain of its affiliates, that accounted for 10% or more of our revenues.") == {WMT: 10.0}
    assert shares("Walmart Inc. and its affiliates together accounted for approximately 13% and 18% of our consolidated net sales for the fiscal years ended June 30, 2026 and 2025, respectively.") == {WMT: 13.0}
    assert shares("Net sales to Walmart in Acme's agricultural and consumer segments combined represented 10% and 11% of net sales for the years ended December 31, 2025 and 2024, respectively.") == {WMT: 10.0}
    assert shares("Amazon Corporate LLC, a subsidiary of Walmart Inc., which we collectively refer to as Amazon, accounted for approximately 31% and 30% of our revenues, respectively.") == {WMT: 31.0}


@pytest.mark.parametrize("sentence, expected", [
    ("Walmart and Costco Wholesale Corporation each accounted for 12% of our revenue.", {WMT: 12.0, COSTCO: 12.0}),
    ("Lockheed Martin, Garmin Ltd, and Xiaomi each accounted for more than 10% of the Company's consolidated revenues.", {GARMIN: 10.0, XIAOMI: 10.0}),
    ("Revenue from Walmart and Xiaomi each represented approximately 11% of the Company's third-party sales.", {WMT: 11.0, XIAOMI: 11.0}),
    ("Our two largest revenue customers were Walmart Inc. and Xiaomi Corporation, each of which accounted for more than 10% of our total revenues.", {WMT: 10.0, XIAOMI: 10.0}),
    ("For fiscal 2026, each of Walmart Inc. and Xiaomi Corporation, including their respective affiliates, accounted for 10% or more of our revenues.", {WMT: 10.0, XIAOMI: 10.0}),
])
def test_each_customer_keeps_the_figure_it_shares_with_the_others(sentence, expected):
    assert {name: share for name, share in shares(sentence).items() if name in expected} == expected


# --- figures that are not a share of revenue -------------------------------------------------

def test_negations_and_ceilings_name_a_company_to_deny_it():
    assert reasons("No single customer, including Walmart, accounted for more than 10% of our net sales.") == {WMT: "negated"}
    assert reasons("None of our customers, including Walmart, accounted for 10% or more of revenue.") == {WMT: "negated"}
    assert shares("Sales to Walmart accounted for less than 10% of our net sales.") == {}
    assert shares("Walmart accounted for under 10% of net sales and for no more than 8% of the receivables.") == {}
    # A negation in another clause is not this company's.
    assert shares("No customer accounted for 10% in 2024, but Walmart accounted for 12% of our net sales in 2025.") == {WMT: 12.0}
    assert shares("Other than Walmart, which accounted for approximately 31.0% of our fiscal 2025 net sales, no single customer accounted for 10.0% or more of our fiscal 2025 net sales.") == {WMT: 31.0}
    assert shares("With the exception of Walmart, our largest distributor, which made up 12% and 10% of our net sales in fiscal 2026 and in fiscal 2025, respectively, no distributor accounted for more than 10% of our net sales.") == {WMT: 12.0}


def test_conditions_and_rates_of_change_are_not_shares():
    assert shares("Our agreement provides that Walmart may terminate it upon notice if 60% or more of our revenue is derived from the services performed by it.") == {}
    assert shares("Revenue from Walmart grew 30% while it accounted for 12% of our revenue.") == {WMT: 12.0}
    assert shares("Walmart, an 8% increase in revenue for us, accounted for 12% of our revenue.") == {WMT: 12.0}
    assert reasons(
        "Any reimbursement from Walmart attributed to the 65% cost-sharing of our R&D expenses is characterized as a reduction of R&D expense, as we recognized "
        "$75.0 million in revenue from the Walmart collaboration agreement, which represented an increase of 16%."
    ) == {WMT: "no_figure"}
    assert reasons("Walmart owns 40% of our shares, and its purchases accounted for 12% of our revenue.") == {WMT: "no_figure"}
    # The revenue figure and the receivables figure of one customer.
    assert shares("Sales to Walmart comprised 14.2% of revenues in fiscal year 2025 and 28.8% of total accounts receivable was due from Walmart as of October 31, 2025.") == {WMT: 14.2}


def test_the_figure_may_come_before_the_verb_that_names_the_customers_clause():
    # "amounted to" is not a concentration verb; the 29% after "accounted for" is the ten largest customers'.
    assert shares("Sales to Walmart, our largest customer, amounted to approximately 7% of our total net sales in fiscal 2025, and our top 10 customers collectively accounted for approximately 29% of our total net sales.") == {WMT: 7.0}


def test_a_table_row_takes_its_own_current_year_cell():
    table = "Customers that accounted for at least 10% of total revenues were as follows: 2025 2024 {rows} Customers accounted for less than 10% of total revenues. Revenue represents each customer."
    rows = "Walmart Inc. 33 % 32 % Xiaomi Corporation 10 % * Costco Wholesale Corporation * 16 %"
    assert shares(table.format(rows=rows)) == {WMT: 33.0, XIAOMI: 10.0, COSTCO: None}
    assert reasons(table.format(rows=rows)) == {COSTCO: "prior_year"}
    # "n/a" and "< 10 %" are blank cells too.
    assert reasons(table.format(rows="Walmart Inc. n/a % 12 % Xiaomi Corporation < 10 % 15 %")) == {WMT: "prior_year", XIAOMI: "prior_year"}
    # A dash between cells separates them; "14%-28%" is a range.
    assert shares(table.format(rows="Walmart Inc. 12 % \u2014 11 % Xiaomi Corporation 15 % 14 %")) == {WMT: 12.0, XIAOMI: 15.0}
    assert reasons("Walmart accounted for 14%-28% of our revenue.") == {WMT: "range"}
    # Each name takes the figure beside it, not one further along the row after another name.
    assert shares(table.format(rows="Walmart Inc. 24 % 22 % Xiaomi Corporation 12 % 11 %")) == {WMT: 24.0, XIAOMI: 12.0}
    # Words between a name and a figure make it prose, not a row.
    assert reasons("Walmart Inc., a customer of ours, recorded the 8 % increase that Acme reported as revenue.") == {}


def test_a_long_appositive_does_not_hide_the_verb():
    sentence = ("During fiscal 2025, fiscal 2024, and fiscal 2023, Walmart, through sales to multiple distributors, contract manufacturers, and direct sales for multiple "
                "applications including smartphones, tablets, desktop, and notebook computers, watches and other devices, in the aggregate accounted for 67 %, 69 %, and 66 % of the Company's net revenue, respectively.")
    assert shares(sentence) == {WMT: 67.0}


# --- names that are something else -----------------------------------------------------------

@pytest.mark.parametrize("sentence, company", [
    ("Sales to customers in the Southern Region (which encompasses Argentina and Chile) accounted for 16% of consolidated net sales during 2025.", SOUTHERN),
    ("One customer in the Pool business represented approximately 18% and 15% of our consolidated net sales in 2025 and 2024, respectively.", "Pool Corporation Common Stock"),
    ("Research Solutions products and services generated approximately 48 % of their revenue from contracts with customers.", "Research Solutions, Inc Common Stock"),
    ("Customers who accounted for 10% or more of our revenues were as follows: Seiko Epson 34.3 % Commercial Vehicle OEM 14.6 %", "Commercial Vehicle Group, Inc. Common Stock"),
    ("For the year ended March 31, 2026, the Helios Pool accounted for 99% of our total revenues.", "Pool Corporation Common Stock"),
    ("Our customers in Windows Hello accounted for 9% of revenue from customers.", "Hello Group Inc. American Depositary Shares"),
    ("For the sale of Renewable Electricity, the City of Anaheim represented approximately 12% of our operating revenues in 2025.", "City Holding Company Common Stock"),
    ("For the year ended December 31, 2025, our largest customer, City of New York, accounted for 29% of our revenues.", "City Holding Company Common Stock"),
    ("In fiscal 2025, sales of new Brunswick boats and yachts accounted for approximately 18% of our revenue.", "Brunswick Corporation"),
    ("Our largest customer is the Progressive Leasing segment, which comprised approximately 96% of our consolidated revenues.", "Progressive Corporation"),
])
def test_a_name_used_as_something_else_is_not_the_company(sentence, company):
    known = {**KNOWN, **{name: auto_discover_edges.clean_company_name(name) for name in ("Research Solutions, Inc Common Stock", "Progressive Corporation")}}
    found = {d.customer_name for d in read_disclosures(sentence, known, FILER, require_customer_cue=False)}
    assert company not in found


@pytest.mark.parametrize("sentence, company, share", [
    ("Our largest customer, Pool Corporation, which represented approximately 33% of our net sales in Fiscal Year 2025.", "Pool Corporation Common Stock", 33.0),
    ("Net sales to The Southern Company accounted for 12% of our revenue in 2025.", SOUTHERN, 12.0),
    ("Net sales to Hello Group Inc. accounted for 12% of our revenue in 2025.", "Hello Group Inc. American Depositary Shares", 12.0),
    ("Customers Walmart Inc. accounted for 20% of net sales.", WMT, 20.0),
    ("Additionally, Walmart accounted for 20% of our revenue.", WMT, 20.0),
    ("Regionally, Walmart accounted for 20% of our revenue.", WMT, 20.0),
    ("For the year ended December 31, 2025, PACCAR Inc. and John Deere accounted for 13.6% and 10.0% of net sales, respectively.", PACCAR, 13.6),
])
def test_the_company_itself_is_still_read(sentence, company, share):
    assert shares(sentence)[company] == share


# --- the filing text --------------------------------------------------------------------------

def test_page_furniture_and_cut_off_definitions_do_not_change_what_a_sentence_says():
    # A page footer joined a customer sentence to the next section ("Our Growth Strategy").
    footer = "Xiaomi and Costco are the only customers which each accounted for more than 10% of group revenues. 22 Table of Contents Our Growth Strategy Evotec's growth strategy."
    assert disclosure_sentences(footer) == [
        "Xiaomi and Costco are the only customers which each accounted for more than 10% of group revenues.",
        "Our Growth Strategy Evotec's growth strategy.",
    ]
    # Zero-width spaces between table cells.
    assert disclosure_sentences(f"Walmart{chr(0x200B)} {chr(0x200B)} 17 % 14 %") == ["Walmart 17 % 14 %"]
    # "Inc." does not end the sentence when a bracket follows: the defined term, or the company's affiliates.
    assert disclosure_sentences('Prevail Therapeutics Inc. ("Prevail") and TG Therapeutics accounted for 77 % and 12 % of revenue, respectively.') == [
        'Prevail Therapeutics Inc. ("Prevail") and TG Therapeutics accounted for 77 % and 12 % of revenue, respectively.'
    ]
    assert len(disclosure_sentences("Walmart accounted for 20%. Costco accounted for 12%.")) == 2


@pytest.mark.parametrize("sentence, cut", [
    ('("Prevail") and TG Therapeutics (as defined below) accounted for 77 % and 12 % of revenue, respectively.', True),
    ("(Walmart) and its affiliates, including Sam's Club, represented approximately 14% of our consolidated net revenue.", True),
    ("Ltd., Mobase Electronics, Tokai Rika, and Vishay Intertechnology. 6 Immersion Segment revenue represented 15% and 10%.", True),
    ("and Costco accounted for 12% of revenue.", True),
    ("Walmart accounted for 20% of our net sales.", False),
    ("The Home Depot accounted for approximately 15% and 14% of net sales.", False),
    ("In fiscal 2025, Walmart accounted for 20% of revenue.", False),
])
def test_a_cut_off_excerpt_is_recognised(sentence, cut):
    assert starts_mid_sentence(sentence) is cut


# --- stored links: cleanup and the audit gate -------------------------------------------------

CONSENSUS = "Ollama consensus review: Consensus 3/3 for approve (avg confidence 0.95; votes approve:3). Lead rationale from qwen2.5:7b-instruct: ok"
PERSON = "Human review (2026-10-09): approved against the filing."
TITLE = "SEC EDGAR (10-K filed 2026-02-24; customer-concentration disclosure)"
URL = "https://www.sec.gov/Archives/edgar/data/1/10k.htm"


@pytest.fixture
def pipeline_db(monkeypatch):
    import audit_data_quality
    import cleanup_reviewed_edges

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False)  # like database.SessionLocal
    monkeypatch.setattr(cleanup_reviewed_edges, "SessionLocal", Session)
    monkeypatch.setattr(audit_data_quality, "SessionLocal", Session)
    monkeypatch.setattr(audit_data_quality, "engine", engine)
    return Session


def disclosure_edge(session, filer, customer, sentence, share, product=None, status="approved", note=CONSENSUS, label="Revenue Concentration"):
    edge = Edge(
        source_id=filer.id, target_id=customer.id, dependency_type=label, review_status=status, review_note=note,
        source_url=URL, source_title=TITLE, revenue_share=share, product=product or f"{share:g}% of {filer.ticker} revenue",
        evidence_excerpt=f"{filer.name.split(',')[0].split(' Inc')[0]} ({filer.ticker}) 10-K filed 2026-02-24: {sentence}",
    )
    session.add(edge)
    return edge


def companies(session, *rows):
    nodes = {ticker: Node(name=name, ticker=ticker, market_cap=cap, sector="Industrials") for ticker, name, cap in rows}
    session.add_all(nodes.values())
    session.commit()
    return nodes


PACCAR_SENTENCE = "Our top three customers, Daimler AG, PACCAR Inc. and Traton SE, accounted for approximately 18%, 11% and 10%, respectively, of our net sales during 2025."
GARMIN_SENTENCE = ("Our two largest commercial distributors, Marlink Group and Garmin, together represented approximately 10% of our revenue for the year ended December 31, 2025, "
                   "and our ten largest distributors represented, in the aggregate, 28% of our revenue for the year ended December 31, 2025.")
SOUTHERN_SENTENCE = "Sales to customers in the Southern Region (which encompasses Argentina, Bolivia, Chile, Paraguay and Uruguay) accounted for 16% of Ternium's consolidated net sales of steel products during 2025."
CUT_OFF_SENTENCE = '("Prevail") and TG Therapeutics (as defined below) accounted for 77 % and 12 % of revenue during the year ended December 31, 2024, respectively.'
RIGHT_SENTENCE = "For the year ended December 31, 2025, our top two customers were Lear and Adient, which comprised 16% and 11%, respectively, of our product revenues."
MCKESSON_SENTENCE = "For the year ended December 31, 2024, sales to our three largest customers, Kedrion, Takeda and McKesson, one of the largest U.S.-based wholesalers, accounted for 31%, 10% and 8%, respectively, of our total revenues."
JAKKS_SENTENCE = "Our two largest customers are Target and Walmart, which accounted for 26.6% and 26.1%, respectively, of our net sales in 2025."

STORED = (
    ("ALSN", "ALLISON TRANSMISSION HOLDINGS INC", 1e10), ("PCAR", PACCAR, 5.6e10), ("IRDM", "Iridium Communications Inc.", 4e9), ("GRMN", GARMIN, 5e10),
    ("TX", "Ternium S.A. American Depositary Shares", 6e9), ("SO", SOUTHERN, 1e11), ("THRM", "Gentherm Incorporated", 1e9), ("LEA", "Lear Corporation", 6e9),
    ("ADNT", "Adient plc Ordinary Shares", 2e9), ("DTIL", "Precision BioSciences, Inc.", 1e8), ("TGTX", "TG Therapeutics, Inc. Common Stock", 8e9),
    ("KMDA", "Kamada Ltd.", 3e8), ("MCK", "McKesson Corporation", 7e10), ("JAKK", "JAKKS Pacific, Inc.", 3e8), ("WMT", WMT, 8e11),
)


def test_stored_shares_are_corrected_cleared_or_held_and_the_gate_agrees(pipeline_db, capsys):
    """Cleanup reads every stored customer sentence with the current reader. A share another customer
    owns is corrected; a joint figure is cleared and the link stays (Garmin does distribute for Iridium);
    a name that is really "Southern Region" goes back to a person; what is right, what a person decided
    and what was cut off are left alone. The audit gate fails before and passes after, and a second
    run changes nothing."""
    from audit_data_quality import audit_database
    from cleanup_reviewed_edges import HELD_NOTE_PREFIX, cleanup_reviewed_edges

    session = pipeline_db()
    n = companies(session, *STORED)
    wrong_list = disclosure_edge(session, n["ALSN"], n["PCAR"], PACCAR_SENTENCE, 18.0, product="transmission systems and components for commercial vehicles")
    joint = disclosure_edge(session, n["IRDM"], n["GRMN"], GARMIN_SENTENCE, 10.0)
    collision = disclosure_edge(session, n["TX"], n["SO"], SOUTHERN_SENTENCE, 16.0)
    right = disclosure_edge(session, n["THRM"], n["LEA"], RIGHT_SENTENCE, 16.0)
    cut_off = disclosure_edge(session, n["DTIL"], n["TGTX"], CUT_OFF_SENTENCE, 77.0)
    # Corrected to 8%, which the existing plausibility check (customers disclose at 10%+) sends to a person.
    below_threshold = disclosure_edge(session, n["KMDA"], n["MCK"], MCKESSON_SENTENCE, 31.0)
    # A person approved this share; the sentence says 26.1, and a person's decision is not overridden.
    by_a_person = disclosure_edge(session, n["JAKK"], n["WMT"], JAKKS_SENTENCE, 26.6, note=PERSON)
    session.commit()

    with pytest.raises(SystemExit):
        audit_database(fail_on_warnings=True)  # the gate sees the wrong shares before cleanup
    assert "Unsupported customer-share warnings: 4" in capsys.readouterr().out

    cleanup_reviewed_edges()
    capsys.readouterr()
    cleanup_reviewed_edges()  # stable: nothing flips on the second run
    second = capsys.readouterr().out.split("Reviewed edge cleanup:")[-1]
    assert not any(key in second for key in ("share_corrected", "share_cleared", "concentration_unsupported", "held_implausible")), second

    check = pipeline_db()
    get = lambda row: check.get(Edge, row.id)  # noqa: E731
    assert (get(wrong_list).revenue_share, get(wrong_list).product, get(wrong_list).review_status) == (11.0, "11% of ALSN revenue", "approved")
    assert get(joint).revenue_share is None and get(joint).review_status == "approved"
    assert get(joint).product == "Revenue Concentration", "the product text repeated the share, so it goes with it"
    assert get(collision).review_status == "pending" and get(collision).review_note.startswith(HELD_NOTE_PREFIX)
    assert get(collision).revenue_share == 16.0, "a held link keeps its stored figure for the person to see"
    assert (get(right).revenue_share, get(right).review_status) == (16.0, "approved")
    assert (get(cut_off).revenue_share, get(cut_off).review_status) == (77.0, "approved"), "a cut-off excerpt is never judged"
    assert (get(below_threshold).revenue_share, get(below_threshold).review_status) == (8.0, "pending")
    assert "below the 10% disclosure threshold" in get(below_threshold).review_note
    assert (get(by_a_person).revenue_share, get(by_a_person).review_status) == (26.6, "approved")
    audit_database(fail_on_warnings=True)


def test_cleanup_counts_what_it_did_and_what_it_left_to_a_person(pipeline_db):
    from cleanup_reviewed_edges import recheck_concentration_edges

    session = pipeline_db()
    n = companies(session, *STORED)
    disclosure_edge(session, n["ALSN"], n["PCAR"], PACCAR_SENTENCE, 18.0)
    disclosure_edge(session, n["IRDM"], n["GRMN"], GARMIN_SENTENCE, 10.0)
    disclosure_edge(session, n["TX"], n["SO"], SOUTHERN_SENTENCE, 16.0)
    disclosure_edge(session, n["JAKK"], n["WMT"], JAKKS_SENTENCE, 26.6, note=PERSON)
    session.commit()

    recheck_concentration_edges(session, counts := {})
    assert counts == {
        "concentration_share_corrected": 1, "concentration_share_cleared": 1, "concentration_unsupported": 1,
        "concentration_share_left_to_a_person": 1,
    }


def test_a_waiting_link_is_corrected_whatever_its_note_says(pipeline_db):
    from cleanup_reviewed_edges import recheck_concentration_edges

    session = pipeline_db()
    n = companies(session, *STORED)
    waiting = disclosure_edge(session, n["ALSN"], n["PCAR"], PACCAR_SENTENCE, 18.0, status="pending", note=None)
    held = disclosure_edge(session, n["IRDM"], n["GRMN"], GARMIN_SENTENCE, 10.0, status="pending", note="Ollama consensus review hold: the models disagree.")
    session.commit()
    recheck_concentration_edges(session, counts := {})
    assert (waiting.revenue_share, waiting.review_status) == (11.0, "pending")
    assert (held.revenue_share, held.review_status) == (None, "pending")
    assert counts == {"concentration_share_corrected": 1, "concentration_share_cleared": 1}


def test_the_review_models_cannot_turn_a_cleared_link_around():
    """A link that lost its number is still the filer's own statement about its customer."""
    from review_edges_with_ollama import correct_review_for_reason, normalize_review

    edge = SimpleNamespace(
        revenue_share=None, source_title=TITLE,
        evidence_excerpt=f"Iridium (IRDM) 10-K filed 2026-02-24: {GARMIN_SENTENCE}",
        source_node=SimpleNamespace(ticker="IRDM", name="Iridium Communications Inc."),
        target_node=SimpleNamespace(ticker="GRMN", name="Garmin Ltd"),
    )
    assert filer_documented_direction(edge)
    vote = normalize_review({"supplier_side": "target", "customer_side": "source", "action": "reverse", "confidence": 0.95,
                             "relationship_type": "Revenue Concentration", "product": "",
                             "reason": "The proposed direction is backwards. Garmin sells to Iridium."}, fixed_direction=True)
    vote = correct_review_for_reason(edge, vote)
    assert (vote["action"], vote["supplier_side"], vote["customer_side"]) == ("approve", "source", "target")


def test_the_gate_asks_only_about_what_cleanup_may_change(pipeline_db):
    """A person's approval and a cut-off excerpt are not warnings, whatever the reader now says."""
    from audit_data_quality import audit_database

    session = pipeline_db()
    n = companies(session, *STORED)
    disclosure_edge(session, n["JAKK"], n["WMT"], JAKKS_SENTENCE, 26.6, note=PERSON)
    disclosure_edge(session, n["DTIL"], n["TGTX"], CUT_OFF_SENTENCE, 77.0)
    session.commit()
    audit_database(fail_on_warnings=True)
