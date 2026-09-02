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
        url = "https://example.com/acme"
        # No charset declared: the UTF-8 name must survive decoding.
        content = "<p>Acme uses supplier Beta Manufacturing and Société Générale for assemblies.</p>".encode("utf-8")

        def raise_for_status(self):
            pass

    monkeypatch.setattr(additional_sources.requests, "get", lambda *args, **kwargs: Response())
    monkeypatch.setattr(additional_sources, "is_public_http_url", lambda url: True)

    text = additional_sources.configured_source_text(
        "ACME",
        company_name="Acme Corp",
        config={"ACME": [{"url": "https://example.com/acme", "title": "Acme source"}]},
    )

    assert "Configured Web Source" in text
    assert "supplier Beta Manufacturing" in text
    assert "Société Générale" in text


def test_fetch_url_text_refuses_non_public_urls(monkeypatch):
    monkeypatch.setattr(additional_sources.requests, "get", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not fetch")))

    for url in ("file:///etc/passwd", "http://localhost:11434/api/tags", "http://127.0.0.1/", "http://169.254.169.254/latest/meta-data/", "http://10.0.0.5/admin", "ftp://example.com/x"):
        try:
            additional_sources.fetch_url_text(url)
        except additional_sources.requests.RequestException as exc:
            assert "non-public" in str(exc)
        else:
            raise AssertionError(f"{url} must be refused")


def test_candidate_ir_urls_use_the_site_origin():
    urls = additional_sources.candidate_ir_urls("https://sub.example.com/investors/index.html")

    assert urls[0] == "https://sub.example.com/investors/index.html"
    assert "https://sub.example.com/investors" in urls
    assert not any("index.html/" in url for url in urls)


def test_source_section_ignores_pages_without_supply_chain_vocabulary():
    assert additional_sources.source_section(
        "Company IR / Website",
        "Acme",
        "ACME",
        "Cookie consent. We use cookies to improve your experience. Accept all. Privacy policy.",
        url="https://example.com/",
    ) == ""


def test_sam_opportunities_requires_api_key(monkeypatch):
    monkeypatch.delenv("HEPHAESTUS_SAM_API_KEY", raising=False)

    assert additional_sources.sam_opportunities_text(
        "ACME",
        company_name="Acme Corp",
        sector="Aerospace & Defense",
    ) == ""


def test_sam_opportunities_text_formats_api_results(monkeypatch):
    monkeypatch.setenv("HEPHAESTUS_SAM_API_KEY", "test-key")

    def fake_fetch_json(url, params=None, timeout=20):
        return {
            "opportunitiesData": [
                {
                    "postedDate": "2026-01-15",
                    "type": "Solicitation",
                    "fullParentPathName": "Department of Defense",
                    "title": "Acme avionics supplier support",
                    "description": "Seeking supplier support for aircraft components.",
                }
            ]
        }

    monkeypatch.setattr(additional_sources, "fetch_json", fake_fetch_json)

    text = additional_sources.sam_opportunities_text(
        "ACME",
        company_name="Acme Corp",
        sector="Aerospace & Defense",
    )

    assert "SAM.gov Opportunities" in text
    assert "aircraft components" in text


def test_openfda_device_510k_is_health_sector_only(monkeypatch):
    calls = []

    def fake_openfda_search(endpoint, search, limit=5):
        calls.append((endpoint, search))
        return {
            "results": [
                {
                    "k_number": "K260001",
                    "applicant": "Acme Medical",
                    "device_name": "Infusion Pump",
                    "decision_date": "2026-02-01",
                }
            ]
        }

    monkeypatch.setattr(additional_sources, "openfda_search", fake_openfda_search)

    assert additional_sources.openfda_device_510k_text("ACME", "Acme Medical", sector="Technology") == ""
    text = additional_sources.openfda_device_510k_text("ACME", "Acme Medical", sector="Health Care")

    assert calls == [("device/510k", 'applicant:"Acme Medical"')]
    assert "openFDA Device 510(k)" in text
    assert "Infusion Pump" in text


def test_fcc_equipment_authorization_text_formats_rows(monkeypatch):
    def fake_fetch_json(url, params=None, timeout=20):
        assert url == "https://opendata.fcc.gov/resource/3b3k-34jp.json"
        assert params["$q"] == "Acme"
        return [
            {
                "grantee_name": "Acme Wireless",
                "fcc_id": "XYZ123",
                "equipment_class": "Bluetooth module",
                "grant_date": "2026-03-02",
            }
        ]

    monkeypatch.setattr(additional_sources, "fetch_json", fake_fetch_json)

    text = additional_sources.fcc_equipment_authorization_text(
        "ACME",
        company_name="Acme Corporation",
        sector="Technology",
    )

    assert "FCC Equipment Authorization" in text
    assert "Bluetooth module" in text


def test_get_additional_supply_chain_text_honors_source_toggles(monkeypatch):
    calls = []

    monkeypatch.setenv("HEPHAESTUS_USE_CONFIGURED_SOURCE_URLS", "0")
    monkeypatch.setenv("HEPHAESTUS_USE_IR_SOURCES", "0")
    monkeypatch.setenv("HEPHAESTUS_USE_PROCUREMENT_SOURCE", "0")
    monkeypatch.setenv("HEPHAESTUS_USE_REGULATORY_SOURCE", "0")
    monkeypatch.setenv("HEPHAESTUS_FETCH_NEWS_ARTICLES", "0")

    monkeypatch.setattr(
        additional_sources,
        "configured_source_text",
        lambda *args, **kwargs: calls.append("configured") or "configured",
    )
    monkeypatch.setattr(
        additional_sources,
        "company_ir_text",
        lambda *args, **kwargs: calls.append("ir") or "ir",
    )
    monkeypatch.setattr(
        additional_sources,
        "usaspending_text",
        lambda *args, **kwargs: calls.append("usaspending") or "usaspending",
    )
    monkeypatch.setattr(
        additional_sources,
        "sam_opportunities_text",
        lambda *args, **kwargs: calls.append("sam") or "sam",
    )
    monkeypatch.setattr(
        additional_sources,
        "openfda_regulatory_text",
        lambda *args, **kwargs: calls.append("openfda") or "openfda",
    )
    monkeypatch.setattr(
        additional_sources,
        "fcc_equipment_authorization_text",
        lambda *args, **kwargs: calls.append("fcc") or "fcc",
    )
    monkeypatch.setattr(
        additional_sources,
        "nhtsa_manufacturer_text",
        lambda *args, **kwargs: calls.append("nhtsa") or "nhtsa",
    )
    monkeypatch.setattr(
        additional_sources,
        "yahoo_news_article_text",
        lambda *args, **kwargs: calls.append("news") or "",
    )

    text = additional_sources.get_additional_supply_chain_text("Acme Corp", "ACME")

    assert text == ""
    assert calls == ["news"]


def test_get_additional_supply_chain_text_combines_enabled_sources(monkeypatch):
    monkeypatch.setenv("HEPHAESTUS_USE_CONFIGURED_SOURCE_URLS", "1")
    monkeypatch.setenv("HEPHAESTUS_USE_IR_SOURCES", "1")
    monkeypatch.setenv("HEPHAESTUS_USE_PROCUREMENT_SOURCE", "1")
    monkeypatch.setenv("HEPHAESTUS_USE_REGULATORY_SOURCE", "1")

    monkeypatch.setattr(additional_sources, "configured_source_text", lambda *args, **kwargs: "configured source\n")
    monkeypatch.setattr(additional_sources, "company_ir_text", lambda *args, **kwargs: "ir source\n")
    monkeypatch.setattr(additional_sources, "usaspending_text", lambda *args, **kwargs: "usaspending source\n")
    monkeypatch.setattr(additional_sources, "sam_opportunities_text", lambda *args, **kwargs: "sam source\n")
    monkeypatch.setattr(additional_sources, "openfda_regulatory_text", lambda *args, **kwargs: "openfda source\n")
    monkeypatch.setattr(additional_sources, "fcc_equipment_authorization_text", lambda *args, **kwargs: "fcc source\n")
    monkeypatch.setattr(additional_sources, "nhtsa_manufacturer_text", lambda *args, **kwargs: "nhtsa source\n")
    monkeypatch.setattr(additional_sources, "yahoo_news_article_text", lambda *args, **kwargs: "")

    text = additional_sources.get_additional_supply_chain_text(
        "Acme Corp",
        "ACME",
        sector="Technology",
        industry="Medical Devices",
    )

    assert "configured source" in text
    assert "ir source" in text
    assert "usaspending source" in text
    assert "sam source" in text
    assert "openfda source" in text
    assert "fcc source" in text
    assert "nhtsa source" in text
