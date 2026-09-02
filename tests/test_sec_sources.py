import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import sec_sources


def test_ticker_to_cik_uses_zero_padded_sec_cik():
    ticker_map = {"AAPL": "0000320193"}

    assert sec_sources.ticker_to_cik("aapl", ticker_map=ticker_map) == "0000320193"


def test_filing_url_removes_accession_dashes_and_integer_cik_path():
    assert sec_sources.filing_url(
        "0000320193",
        "0000320193-26-000010",
        "aapl-20260926.htm",
    ) == (
        "https://www.sec.gov/Archives/edgar/data/"
        "320193/000032019326000010/aapl-20260926.htm"
    )


def test_html_to_text_removes_boilerplate_tags():
    html = """
    <html>
      <head><style>.hide{}</style><script>alert(1)</script></head>
      <body><p>Major customers include Acme Corp.</p></body>
    </html>
    """

    text = sec_sources.html_to_text(html)

    assert "alert" not in text
    assert "Major customers include Acme Corp." in text


def test_relevant_windows_prioritizes_supply_chain_terms():
    text = (
        "Intro text. " * 80
        + "Our key supplier is TSMC for advanced silicon fabrication. "
        + "Other text. " * 80
    )

    relevant = sec_sources.relevant_windows(text, window=40, max_chars=160)

    assert "key supplier is TSMC" in relevant
    assert len(relevant) <= 160


def test_recent_filings_prefers_annual_filings(monkeypatch):
    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {
                "filings": {
                    "recent": {
                        "form": ["10-Q", "10-K"],
                        "accessionNumber": [
                            "0000320193-26-000013",
                            "0000320193-25-000079",
                        ],
                        "primaryDocument": [
                            "aapl-20260328.htm",
                            "aapl-20250927.htm",
                        ],
                        "filingDate": ["2026-05-01", "2025-10-31"],
                    }
                }
            }

    monkeypatch.setattr(sec_sources.requests, "get", lambda *args, **kwargs: Response())

    filings = sec_sources.recent_filings("0000320193", limit=1)

    assert filings[0]["form"] == "10-K"


def test_recent_filings_tolerates_ragged_submission_arrays(monkeypatch):
    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {
                "filings": {
                    "recent": {
                        "form": ["10-K", "10-Q"],
                        "accessionNumber": ["0000320193-25-000079", "0000320193-26-000013"],
                        "primaryDocument": ["aapl-20250927.htm", "aapl-20260328.htm"],
                        # filingDate missing entirely
                    }
                }
            }

    monkeypatch.setattr(sec_sources.requests, "get", lambda *args, **kwargs: Response())

    filings = sec_sources.recent_filings("0000320193", limit=2)

    assert [filing["form"] for filing in filings] == ["10-K", "10-Q"]
    assert filings[0]["filing_date"] is None


def test_exhibit_forms_prefer_annual_reports_over_8k_press_releases(monkeypatch):
    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {
                "filings": {
                    "recent": {
                        "form": ["8-K"] * 12 + ["10-K", "10-Q"],
                        "accessionNumber": [f"0000320193-26-{index:06d}" for index in range(14)],
                        "primaryDocument": [f"doc{index}.htm" for index in range(14)],
                        "filingDate": [f"2026-01-{index + 1:02d}" for index in range(14)],
                    }
                }
            }

    monkeypatch.setattr(sec_sources.requests, "get", lambda *args, **kwargs: Response())

    filings = sec_sources.recent_filings("0000320193", forms=sec_sources.EXHIBIT_FORMS, limit=4)

    assert [filing["form"] for filing in filings][:2] == ["10-K", "10-Q"]


def test_relevant_windows_respects_max_chars_across_separators():
    text = " ".join(f"filler{index} supplier note" if index % 7 == 0 else f"filler{index}" for index in range(120))

    assert len(sec_sources.relevant_windows(text, window=20, max_chars=60)) <= 60
    assert sec_sources.relevant_windows("nothing relevant here", max_chars=60, require_match=True) == ""


def test_html_to_text_does_not_double_unescape_or_leak_comments():
    escaped = "<p>Contract text &amp;lt;b&amp;gt; stays escaped</p><!-- <p>hidden draft</p> -->"

    text = sec_sources.html_to_text(escaped)

    assert "&lt;b&gt;" in text
    assert "hidden draft" not in text


def test_decode_body_handles_missing_charset():
    body = "Société Générale — supplier".encode("utf-8")

    assert sec_sources.decode_body(body, "text/html") == "Société Générale — supplier"
    assert sec_sources.decode_body(body, "text/html; charset=utf-8") == "Société Générale — supplier"
    assert "supplier" in sec_sources.decode_body("caf\xe9 supplier".encode("cp1252"), "text/html")


def test_filing_index_url_uses_accession_directory():
    assert sec_sources.filing_index_url(
        "0000320193",
        "0000320193-26-000010",
    ) == (
        "https://www.sec.gov/Archives/edgar/data/"
        "320193/000032019326000010/index.json"
    )


def test_exhibit_documents_keeps_supply_chain_contract_exhibits():
    filing = {"cik": "0000320193", "accession": "0000320193-26-000010"}
    index_payload = {
        "directory": {
            "item": [
                {"name": "aapl-20260926.htm", "type": "10-K", "description": "Annual report"},
                {"name": "exhibit101.htm", "type": "EX-10.1", "description": "Manufacturing supply agreement"},
                {"name": "image001.jpg", "type": "GRAPHIC", "description": "Logo"},
            ]
        }
    }

    docs = sec_sources.exhibit_documents(filing, index_payload=index_payload)

    assert len(docs) == 1
    assert docs[0]["url"].endswith("/exhibit101.htm")
