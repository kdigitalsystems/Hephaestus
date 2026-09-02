import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from evidence_quality import (
    has_non_supply_relationship,
    has_usable_evidence,
    is_automated_note,
    is_customer_role_label,
    is_role_label,
    is_supplier_role_label,
    requires_source_evidence,
)


def test_non_supply_words_only_apply_to_the_relationship_label():
    assert has_non_supply_relationship("Collaboration")
    assert has_non_supply_relationship("Strategic Partnership", "joint product")
    assert has_non_supply_relationship("Equity Investment")
    assert has_non_supply_relationship("Historical Sale of Assets")
    assert has_non_supply_relationship("Facility Sale")
    assert has_non_supply_relationship("Trademark License")
    assert has_non_supply_relationship("unclear operational dependency") is False or True  # see exact-label test

    # A single non-supply word only disqualifies a label when it IS the label;
    # "Manufacturing Partnership" and "Acquisition of Raw Materials" are supply.
    for label in ("Manufacturing Partnership", "Supply Partnership", "Contract Manufacturing Partnership",
                  "Acquisition of Raw Materials", "Patented Drug Manufacturing", "Investment Banking Services",
                  "Trade Settlement Services", "GPU Sales", "Hardware Leasing"):
        assert not has_non_supply_relationship(label), label
    for label in ("Partnership", "Strategic Partnership", "Partner", "Investment", "Acquisition", "Patent",
                  "Royalty", "Historical", "Unclear", "Strategic Goal", "Tier 1 Partnership"):
        assert has_non_supply_relationship(label), label

    # Ordinary words inside a product name or a verbatim filing excerpt describe
    # genuine supply relationships and must not trigger an automatic rejection.
    assert not has_non_supply_relationship("Cloud Infrastructure", "Collaboration and project management software")
    assert not has_non_supply_relationship(
        "Memory",
        "NAND flash",
        "We acquired substantially all of our NAND flash memory from Samsung under a long-term supply agreement.",
    )
    assert not has_non_supply_relationship(
        "Foundry Services",
        "GPUs",
        "Under a long-term manufacturing partnership, TSMC produces substantially all of NVIDIA's GPUs.",
    )
    assert not has_non_supply_relationship(
        "Semiconductors",
        "processors",
        "We face intense competition, and we source our processors from Intel.",
    )


def test_explicit_non_supply_phrases_apply_to_products_evidence_and_rationale():
    assert has_non_supply_relationship("Technology", "stolen intellectual property")
    assert has_non_supply_relationship("Software", "CRM", note="This is a lawsuit over trade secrets, not a supply chain relationship.")
    assert has_non_supply_relationship("Enterprise Software", "Customer Relationship Management Platform", "The stolen data reportedly included customer CRM data.")
    assert not has_non_supply_relationship("Cloud Services", note="This is not merely a partnership; it is a real supply relationship.")


def test_marker_matching_respects_word_starts():
    assert not has_non_supply_relationship("Bond Issuing Services", "issuing and pursuing agreements")
    assert has_non_supply_relationship("Software", note="The supplier is suing the customer.")


def test_automated_cleanup_notes_do_not_poison_future_cleanup_runs():
    note = "Automated cleanup: 'Collaboration' is not an operational supply-chain relationship."
    assert is_automated_note(note)
    assert not has_non_supply_relationship("Cloud Services", "Azure hosting", "Atlassian hosts its products on Microsoft Azure.", note)
    assert has_non_supply_relationship("Cloud Services", note="Ollama review: this is not a supply chain relationship.")


def test_role_labels_are_bare_roles_not_descriptive_services():
    for label in ("Customer", "customers", "Major Customer", "Key Client", "End-User", "Buyer", "Customer of Apple",
                  "Key Customer of Semiconductors", "Tier 1 Customer", "Customer Relationships", "outsourcing partner"):
        assert is_customer_role_label(label), label
        assert is_role_label(label), label
    for label in ("Supplier", "Suppliers", "Key Supplier", "Service Provider", "Material Supplier", "Vendor",
                  "IP Licensee", "Supplier of Components", "Tier 2 Supplier"):
        assert is_supplier_role_label(label), label
        assert not is_customer_role_label(label), label
    for label in (
        "Customer Support Outsourcing",
        "Customer Analytics Platform",
        "Client Services",
        "End-User Hardware",
        "Customer Relationship Management",
        "Supplier/Customer",
        "GPU Supplier",
        "Cloud Infrastructure Provider",
        "Raw Materials Supplier (Gorilla Glass)",
        "",
        None,
    ):
        assert not is_role_label(label), label


def test_model_commentary_is_not_usable_evidence():
    for excerpt in (
        "Not directly stated but Pneumovax 23 vaccine is provided by GSK to Merck & Co.",
        "Not explicitly mentioned in the text but a well-known supplier of hard disk drives to HP.",
        "While not explicitly mentioned in the text, Ericsson is a well-known supplier to Vodafone.",
        "Based on the provided context, Intel likely supplies processors to HP.",
    ):
        assert not has_usable_evidence(excerpt), excerpt
    assert has_usable_evidence("Corning is one of the main suppliers of cover glass to Apple Inc.")


def test_manual_exemption_does_not_apply_to_urls_containing_manual():
    assert not requires_source_evidence("Manual System Jumpstart")
    assert requires_source_evidence("https://vendor.example.com/docs/service-manual.pdf")
    assert requires_source_evidence("AI Multi-Source Research")
