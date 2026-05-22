import sys
from pathlib import Path

from sqlalchemy import create_engine


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import audit_data_quality
from models import Base


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
