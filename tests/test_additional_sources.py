import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import additional_sources


def test_configured_entries_supports_ticker_and_global_sources():
    config = {
        "sources": {
            "AAPL": [{"url": "https://example.com/aapl-ir", "source_type": "Company IR"}],
            "*": [{"url": "https://example.com/global-source"}],
        }
    }

    entries = additional_sources.configured_entries(config, "aapl")

    assert [entry["url"] for entry in entries] == [
        "https://example.com/aapl-ir",
        "https://example.com/global-source",
    ]


def test_usaspending_payload_targets_recipient_contract_awards():
    payload = additional_sources.usaspending_payload("Acme Corporation", limit=3)

    assert payload["filters"]["recipient_search_text"] == ["Acme"]
    assert payload["filters"]["award_type_codes"] == ["A", "B", "C", "D"]
    assert payload["limit"] == 3


def test_source_section_labels_evidence_and_company():
    section = additional_sources.source_section(
        "Company IR",
        "Acme Corp",
        "ACME",
        "Acme depends on supplier Beta Manufacturing for motor assemblies.",
        url="https://example.com/ir",
        title="Investor relations",
    )

    assert "SOURCE: Company IR" in section
    assert "COMPANY: Acme Corp (ACME)" in section
    assert "Beta Manufacturing" in section


def test_configured_source_text_fetches_configured_urls(monkeypatch):
    class Response:
        headers = {"content-type": "text/html"}
        text = "<p>Acme uses supplier Beta Manufacturing for assemblies.</p>"

        def raise_for_status(self):
            pass

    monkeypatch.setattr(additional_sources.requests, "get", lambda *args, **kwargs: Response())

    text = additional_sources.configured_source_text(
        "ACME",
        company_name="Acme Corp",
        config={"ACME": [{"url": "https://example.com/acme", "title": "Acme source"}]},
    )

    assert "Configured Web Source" in text
    assert "supplier Beta Manufacturing" in text
