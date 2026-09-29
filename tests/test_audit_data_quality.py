import sys
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import audit_data_quality
from models import Base
from types import SimpleNamespace


def test_audit_schema_validation_reports_missing_tables(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    monkeypatch.setattr(audit_data_quality, "engine", engine)

    try:
        audit_data_quality.validate_database_schema()
    except audit_data_quality.DatabaseSchemaError as exc:
        assert "missing required table" in str(exc)
        assert "python backend/database.py" in str(exc)
    else:
        raise AssertionError("missing schema should stop the audit")


def test_audit_schema_validation_accepts_initialized_database(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    monkeypatch.setattr(audit_data_quality, "engine", engine)

    audit_data_quality.validate_database_schema()


def test_audit_schema_validation_reports_locked_database(monkeypatch):
    # A second job holding the SQLite file (or a Windows UNC path into WSL) must stop the
    # audit with an actionable message, not a raw SQLAlchemy traceback.
    class LockedInspector:
        def get_table_names(self):
            raise OperationalError("SELECT name FROM sqlite_master", {}, RuntimeError("database is locked"))

    monkeypatch.setattr(audit_data_quality, "inspect", lambda _engine: LockedInspector())

    try:
        audit_data_quality.validate_database_schema()
    except audit_data_quality.DatabaseAccessError as exc:
        assert "Unable to inspect the Hephaestus database" in str(exc)
        assert "WSL shell" in str(exc)
        assert isinstance(exc.__cause__, OperationalError)
    else:
        raise AssertionError("a locked database should stop the audit cleanly")


def test_audit_flags_ip_theft_as_non_supply_relationship():
    assert audit_data_quality.has_non_supply_label(
        "trade secrets theft",
        "Autonomous vehicle technology",
    )
    assert audit_data_quality.has_non_supply_label(
        "Technology",
        "stolen intellectual property",
    )
    assert audit_data_quality.has_non_supply_label(
        "Enterprise Software",
        "Customer Relationship Management Platform",
        "The stolen data reportedly included customer CRM data.",
    )
    assert audit_data_quality.has_non_supply_label("data breach incident")
    assert audit_data_quality.has_non_supply_label("legal dispute")
    assert audit_data_quality.has_non_supply_label("sale_of_assets")
    assert audit_data_quality.has_non_supply_label("not a supply chain relationship")
    assert audit_data_quality.has_non_supply_label("neither supply chain nor operational dependency")
    assert audit_data_quality.has_non_supply_label("zero emission vehicle credits")
    assert audit_data_quality.has_non_supply_label("generic substitutes")
    assert audit_data_quality.has_invalid_dependency_label("unknown")
    assert audit_data_quality.has_invalid_dependency_label("news")
    assert not audit_data_quality.has_invalid_dependency_label("News Content")


def test_audit_does_not_flag_descriptive_labels_or_filing_prose():
    assert not audit_data_quality.has_non_supply_label(
        "Cloud Infrastructure",
        "Collaboration and project management software",
    )
    assert not audit_data_quality.has_non_supply_label(
        "Memory",
        "NAND flash",
        "We acquired substantially all of our NAND flash memory from Samsung under a long-term supply agreement.",
    )
    assert audit_data_quality.has_non_supply_label("Collaboration")
    assert audit_data_quality.has_reversed_role_label("Customer")
    assert audit_data_quality.has_reversed_role_label("Major Customer")
    assert not audit_data_quality.has_reversed_role_label("Customer Support Outsourcing")
    assert not audit_data_quality.has_reversed_role_label("Customer Relationship Management")


def test_audit_flags_speculative_relationship_evidence():
    assert audit_data_quality.has_speculative_supply_label(
        "HP's supply chain likely involves Intel as a supplier of microprocessors.",
    )
    assert audit_data_quality.has_speculative_supply_label("No evidence in text")
    assert audit_data_quality.has_speculative_supply_label("While not explicitly stated, the vendor would use this service.")
    assert audit_data_quality.has_speculative_supply_label("Not found in source text")


def test_a_filing_statement_is_judged_speculative_on_the_rationale_alone():
    from types import SimpleNamespace

    netflix, amazon = SimpleNamespace(ticker="NFLX"), SimpleNamespace(ticker="AMZN")
    statement = SimpleNamespace(
        source_node=amazon, target_node=netflix, revenue_share=None,
        source_title="SEC EDGAR (10-K filed 2026-01-23; supplier-dependence disclosure)",
        evidence_excerpt="Netflix (NFLX) 10-K filed 2026-01-23: We rely upon Amazon Web Services to operate certain aspects "
                         "of our service and any disruption ... would impact our operations and our business would be adversely impacted.",
        review_note="Ollama consensus review: Consensus 2/3 for approve. The text explicitly states that Netflix relies on AWS.",
    )
    # Risk-factor wording hedges the consequence, not the relationship.
    assert not audit_data_quality.speculative_evidence(statement)
    # A hedging rationale still counts, and the same words from a model's excerpt do too.
    assert audit_data_quality.speculative_evidence(SimpleNamespace(**{**statement.__dict__, "review_note": "Netflix likely uses AWS."}))
    assert audit_data_quality.speculative_evidence(SimpleNamespace(**{**statement.__dict__, "source_title": "AI Multi-Source Research"}))


def test_audit_flags_wrong_direction_review_rationale():
    assert audit_data_quality.has_wrong_direction_review(
        "Consensus 2/3 for reverse (avg confidence 0.93; votes reverse:2, pending:1).",
    )
    assert audit_data_quality.has_wrong_direction_review(
        "The supplier is Magna International, not the other way around.",
    )


def test_audit_flags_reciprocal_edges_with_identical_evidence():
    left = SimpleNamespace(ticker="MRVL", name="Marvell")
    right = SimpleNamespace(ticker="AAPL", name="Apple")
    evidence = "Marvell supplied the Wi-Fi chip for the original Apple iPhone."
    forward = SimpleNamespace(source_node=left, target_node=right, evidence_excerpt=evidence)
    reverse = SimpleNamespace(source_node=right, target_node=left, evidence_excerpt=evidence)

    assert audit_data_quality.reciprocal_same_evidence_edges([forward, reverse]) == [
        forward,
        reverse,
    ]
