import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import seed_db  # noqa: E402
from models import Base, Node  # noqa: E402


def asset(symbol, name, exchange="NASDAQ", marginable=True, tradable=True, **extra):
    return {"id": f"id-{symbol}", "class": "us_equity", "symbol": symbol, "name": name, "exchange": exchange,
            "status": "active", "marginable": marginable, "tradable": tradable, "shortable": True, "easy_to_borrow": True,
            "fractionable": True, **extra}


# What Alpaca returned on 2026-10-31: ordinary assets plus ones on the new Texas Stock Exchange.
PAYLOAD = [
    asset("AAPL", "Apple Inc. Common Stock"),
    asset("TXNC", "Texan Example Corp Common Stock", exchange="TXSE"),
    asset("MSFT", "Microsoft Corporation Common Stock"),
    asset("TXDL", "Dual Listed Example Inc.", exchange="TXSE"),
]


def test_an_asset_on_an_unknown_exchange_is_skipped_not_fatal():
    nodes, skipped = seed_db.new_company_nodes(PAYLOAD, set())
    assert [node.ticker for node in nodes] == ["AAPL", "MSFT"]
    assert skipped == {"TXSE": 2}


def test_the_library_would_have_rejected_the_whole_list():
    """Why the seeder asks for raw data: alpaca-py's strict model refuses an exchange it does not know and
    takes every other asset down with it. If a library upgrade learns TXSE this test may be deleted."""
    from typing import List

    from alpaca.trading.models import Asset
    from pydantic import TypeAdapter, ValidationError

    try:
        TypeAdapter(List[Asset]).validate_python(PAYLOAD)
    except ValidationError as error:
        assert "TXSE" in str(error)
    else:
        pytest.skip("alpaca-py now accepts TXSE; the raw-data path is still correct but no longer needed")


@pytest.mark.parametrize(
    ("entry", "kept"),
    [
        (asset("BRK.B", "Berkshire Hathaway Class B"), False),
        (asset("RDS-A", "Dash Class A"), False),
        (asset("OTCX", "Over The Counter Inc.", exchange="OTC"), False),
        (asset("EMPT", "Empty Exchange Inc.", exchange=""), False),
        (asset("NONE", "No Exchange Inc.", exchange=None), False),
        (asset("NOMG", "Not Marginable Inc.", marginable=False), False),
        (asset("NOTR", "Not Tradable Inc.", tradable=False), False),
        (asset("NYSX", "New York Example Inc.", exchange="NYSE"), True),
        (asset("ARCX", "Arca Example ETF", exchange="ARCA"), True),
        (asset("BTSX", "Bats Example Inc.", exchange="BATS"), True),
    ],
)
def test_the_existing_filters_are_unchanged(entry, kept):
    nodes, _ = seed_db.new_company_nodes([entry], set())
    assert bool(nodes) is kept


def test_existing_tickers_and_repeated_names_are_not_added_twice():
    assets = [asset("AAPL", "Apple Inc."), asset("NEWX", "New Example Inc."), asset("NEWY", "New Example Inc."), asset("NEWZ", "Another Inc.")]
    nodes, _ = seed_db.new_company_nodes(assets, {"AAPL"})
    assert [node.ticker for node in nodes] == ["NEWX", "NEWZ"]


@pytest.mark.parametrize("junk", [None, "AAPL", 7, [], {}, {"symbol": "ONLYSYM"}, {"name": "Only A Name"}, {"symbol": "", "name": "x"}])
def test_malformed_entries_are_skipped(junk):
    nodes, _ = seed_db.new_company_nodes([junk, asset("GOOD", "Good Inc.")], set())
    assert [node.ticker for node in nodes] == ["GOOD"]


def test_new_nodes_are_public_companies_awaiting_their_sector():
    node = seed_db.new_company_nodes([asset("AAPL", "Apple Inc.")], set())[0][0]
    assert (node.entity_type, node.sector, node.name) == ("Company", "Pending Update", "Apple Inc.")


class FakeClient:
    """Stands in for TradingClient: refuses to parse like the real library unless asked for raw data."""

    created_with = None

    def __init__(self, api_key, secret_key, paper=True, raw_data=False, **_):
        type(self).created_with = {"paper": paper, "raw_data": raw_data}
        self.raw_data = raw_data

    def get_all_assets(self, _request):
        if not self.raw_data:
            raise ValueError("10 validation errors for list[Asset]: exchange Input should be ... input_value='TXSE'")
        return PAYLOAD


def test_the_whole_seeding_run_survives_a_new_exchange(monkeypatch, capsys):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False)
    monkeypatch.setattr(seed_db, "SessionLocal", factory)
    monkeypatch.setattr(seed_db, "TradingClient", FakeClient)
    monkeypatch.setattr(seed_db, "get_alpaca_credentials", lambda: ("key", "secret"))

    seed_db.seed_database_from_alpaca()

    assert FakeClient.created_with == {"paper": True, "raw_data": True}
    assert sorted(ticker for (ticker,) in factory().query(Node.ticker)) == ["AAPL", "MSFT"]
    assert "Skipped assets on exchanges outside NASDAQ/NYSE/ARCA/BATS: TXSE 2." in capsys.readouterr().out
    seed_db.seed_database_from_alpaca()  # a second night adds nothing and does not fail
    assert factory().query(Node).count() == 2


def test_a_real_failure_still_stops_the_run(monkeypatch):
    class Broken(FakeClient):
        def get_all_assets(self, _request):
            raise RuntimeError("Alpaca is down")

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    monkeypatch.setattr(seed_db, "SessionLocal", sessionmaker(bind=engine, autoflush=False))
    monkeypatch.setattr(seed_db, "TradingClient", Broken)
    monkeypatch.setattr(seed_db, "get_alpaca_credentials", lambda: ("key", "secret"))
    with pytest.raises(RuntimeError):
        seed_db.seed_database_from_alpaca()  # exits non-zero so a pipeline does not export an unseeded graph
