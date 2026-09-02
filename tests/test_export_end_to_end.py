import json
import sys
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import export
from models import Base, Edge, Node
from validate_dashboard_data import validate_dashboard_data


def build_graph(session):
    def node(name, ticker, sector="Technology", cap=1e10, price=100.0):
        row = Node(name=name, ticker=ticker, sector=sector, industry="Semis", market_cap=cap, current_price=price, percent_change=1.0)
        session.add(row)
        session.flush()
        return row

    tsm = node("Taiwan Semiconductor Manufacturing", "TSM")
    amd = node("Advanced Micro Devices", "AMD")
    nvda = node("NVIDIA", "NVDA")
    micron = node("Micron", "MU", cap=None, price=None)
    ghost = node("Ghost Corp", "GHST")
    bank = node("Some Bank", "BNK", sector="Financial Services")
    session.add_all([
        Edge(source_id=tsm.id, target_id=amd.id, dependency_type="Advanced Silicon Fabrication", product="chips", confidence_score=1.0, source_url="Manual System Jumpstart", review_status="approved"),
        Edge(source_id=tsm.id, target_id=amd.id, dependency_type="Foundry Services", product="wafers", confidence_score=0.9, source_url="https://www.sec.gov/x", review_status="approved", evidence_excerpt="TSMC provides foundry services to AMD under a long-term agreement."),
        # Stored backwards; export must canonicalize TSM as the foundry supplier.
        Edge(source_id=nvda.id, target_id=tsm.id, dependency_type="Advanced Silicon Fabrication", product="semiconductor chips", confidence_score=0.9, source_url="AI Multi-Source Research", review_status="approved", evidence_excerpt="TSMC manufactures advanced semiconductor chips used by NVIDIA."),
        Edge(source_id=micron.id, target_id=nvda.id, dependency_type="High-Bandwidth Memory", product="HBM3e", confidence_score=0.95, source_url="AI Multi-Source Research", review_status="approved", evidence_excerpt="Micron supplies HBM3e memory to NVIDIA for its accelerators."),
        Edge(source_id=amd.id, target_id=nvda.id, dependency_type="Competitor", product="GPUs", confidence_score=0.9, source_url="AI Multi-Source Research", review_status="rejected", evidence_excerpt="AMD competes with NVIDIA in GPUs for data centers."),
        Edge(source_id=ghost.id, target_id=amd.id, dependency_type="Packaging", product="substrates", confidence_score=0.9, source_url="AI Multi-Source Research", review_status="approved", evidence_excerpt="Ghost Corp supplies advanced substrates to AMD for packaging."),
        Edge(source_id=bank.id, target_id=amd.id, dependency_type="Banking", product="loans", confidence_score=0.9, source_url="Manual System Jumpstart", review_status="approved"),
    ])
    session.commit()
    # SQLite foreign keys are not enforced by the app, so an orphaned edge is possible.
    session.execute(Node.__table__.delete().where(Node.__table__.c.id == ghost.id))
    session.commit()


def test_export_to_json_end_to_end(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'graph.db'}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    build_graph(Session())

    dashboard_path = tmp_path / "dashboard_data.json"
    history_path = tmp_path / "link_history.json"
    monkeypatch.setattr(export, "SessionLocal", Session)
    monkeypatch.setattr(export, "EXPORT_PATH", str(dashboard_path))
    monkeypatch.setattr(export, "HISTORY_PATH", str(history_path))

    export.export_to_json()

    assert dashboard_path.exists(), "export must honour the configured output path, not a definition-time default"
    assert history_path.exists()
    assert not [path for path in tmp_path.iterdir() if path.suffix == ".tmp"]
    dashboard = json.loads(dashboard_path.read_text(encoding="utf-8"))
    summary = validate_dashboard_data(dashboard)
    by_ticker = {company["ticker"]: company for rows in dashboard["industries"].values() for company in rows}

    assert summary["companies"] == 4
    assert "BNK" not in by_ticker, "ignored sectors are never exported"
    assert "MU" in by_ticker, "approved endpoints without market data are exported"
    assert [link["ticker"] for link in by_ticker["TSM"]["downstream"]] == ["AMD", "NVDA"]
    assert by_ticker["TSM"]["upstream"] == []
    assert by_ticker["TSM"]["downstream"][0]["type"] == "Advanced Silicon Fabrication / Foundry Services"
    assert by_ticker["TSM"]["downstream"][0]["relationship_key"] == "TSM->AMD:ADVANCED SILICON FABRICATION"
    assert {link["ticker"] for link in by_ticker["NVDA"]["upstream"]} == {"MU", "TSM"}
    assert all(link["review_status"] == "approved" for company in by_ticker.values() for side in ("upstream", "downstream") for link in company[side])
    assert dashboard["investor_metrics"]["unique_links"] == 3
    assert dashboard["investor_metrics"]["history"][-1]["unique_links"] == 3
    assert json.loads(history_path.read_text(encoding="utf-8"))[-1]["unique_links"] == 3
