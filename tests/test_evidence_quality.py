import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from evidence_quality import has_non_supply_relationship, is_automated_note, is_role_label


def test_non_supply_words_only_apply_to_the_relationship_label():
    assert has_non_supply_relationship("Collaboration")
    assert has_non_supply_relationship("Strategic Partnership", "joint product")
    assert has_non_supply_relationship("Equity Investment")

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
    for label in ("Customer", "customers", "Major Customer", "Key Client", "End-User", "Buyer", "Customer of Apple", "outsourcing partner"):
        assert is_role_label(label), label
    for label in (
        "Customer Support Outsourcing",
        "Customer Analytics Platform",
        "Client Services",
        "End-User Hardware",
        "Customer Relationship Management",
        "Supplier/Customer",
        "",
        None,
    ):
        assert not is_role_label(label), label
