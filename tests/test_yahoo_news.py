"""Recent news for the extractor: Yahoo Finance's headline feed (yahooquery's news call answers ["error"])."""
import sys
from pathlib import Path

import pytest
import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import additional_sources  # noqa: E402
import auto_discover_edges  # noqa: E402

ITEM = """<item><title>{title}</title><link>{link}</link><description>{summary}</description>
<pubDate>Thu, 12 Nov 2026 16:27:18 +0000</pubDate><guid>x</guid></item>"""


def feed(*items, title="Yahoo! Finance: RDW News"):
    return (
        '<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel>'
        f"<title>{title}</title><description>Latest Financial News</description>{''.join(items)}"
        "</channel></rss>"
    ).encode()


def item(title="Redwire wins NASA award", link="https://finance.yahoo.com/news/redwire-1.html", summary="Redwire will provide a power system to NASA."):
    return ITEM.format(title=title, link=link, summary=summary)


class FakeResponse:
    def __init__(self, body=b"", status=200):
        self.body, self.status_code, self.closed = body, status, False

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} Server Error")

    def iter_content(self, size):
        for start in range(0, len(self.body), size):
            yield self.body[start:start + size]

    def close(self):
        self.closed = True


@pytest.fixture
def answer(monkeypatch):
    """Make the feed answer with a given response; records each request's params."""
    state = {"response": FakeResponse(feed(item())), "requests": []}

    def get(url, params=None, headers=None, timeout=None, stream=False):
        state["requests"].append({"url": url, "params": params, "headers": headers, "stream": stream})
        response = state["response"]
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr(additional_sources.requests, "get", get)
    return state


def test_headlines_come_from_the_feed_with_link_summary_and_date(answer):
    headlines = additional_sources.yahoo_headlines("rdw")

    assert headlines == [{
        "title": "Redwire wins NASA award",
        "link": "https://finance.yahoo.com/news/redwire-1.html",
        "summary": "Redwire will provide a power system to NASA.",
        "published": "Thu, 12 Nov 2026 16:27:18 +0000",
    }]
    sent = answer["requests"][0]
    assert sent["url"] == "https://feeds.finance.yahoo.com/rss/2.0/headline"
    assert sent["params"]["s"] == "RDW" and sent["stream"] is True
    assert answer["response"].closed


def test_only_the_newest_few_are_kept(answer):
    answer["response"] = FakeResponse(feed(*(item(title=f"Story {n}", link=f"https://example.com/{n}") for n in range(9))))

    assert [h["title"] for h in additional_sources.yahoo_headlines("RDW", count=3)] == ["Story 0", "Story 1", "Story 2"]


def test_markup_and_entities_in_a_summary_become_plain_text(answer):
    answer["response"] = FakeResponse(feed(item(summary="&lt;p&gt;Supplies   &amp;amp; services&lt;/p&gt; to &lt;b&gt;NASA&lt;/b&gt;")))

    assert additional_sources.yahoo_headlines("RDW")[0]["summary"] == "Supplies & services to NASA"


def test_items_without_a_title_or_a_web_link_are_skipped(answer):
    answer["response"] = FakeResponse(feed(
        item(title=""),
        item(link="javascript:alert(1)"),
        item(link="https://example.com/a b"),
        item(title="Kept", link="http://example.com/kept"),
    ))

    assert [h["title"] for h in additional_sources.yahoo_headlines("RDW")] == ["Kept"]


def test_a_ticker_with_no_news_is_an_empty_list_not_an_error(answer):
    answer["response"] = FakeResponse(feed(title="Yahoo! Finance:  News"))

    assert additional_sources.yahoo_headlines("ZZZZQ") == []
    assert additional_sources.yahoo_headlines("") == []


@pytest.mark.parametrize("response, message", [
    (FakeResponse(b"", status=503), "503"),
    (FakeResponse(b"<html><body>Please consent</body></html>"), "not an RSS feed"),
    (FakeResponse(b"not xml at all"), "syntax error"),
    (FakeResponse(b'<?xml version="1.0"?><!DOCTYPE rss [<!ENTITY a "aaaa">]><rss><channel></channel></rss>'), "DOCTYPE"),
    (FakeResponse(b"<rss><channel>" + b" " * (additional_sources.MAX_FEED_BYTES + 1) + b"</channel></rss>"), "larger than"),
    (requests.ConnectionError("connection refused"), "connection refused"),
])
def test_a_failed_or_foreign_answer_raises_so_the_caller_can_count_it(answer, response, message):
    answer["response"] = response

    with pytest.raises(Exception, match=message):
        additional_sources.yahoo_headlines("RDW")
    if isinstance(response, FakeResponse):
        assert response.closed


@pytest.fixture
def gatherer(monkeypatch):
    monkeypatch.setattr(auto_discover_edges.IntelGatherer, "yahoo_news_disabled", False)
    monkeypatch.setattr(auto_discover_edges.IntelGatherer, "yahoo_news_failures", 0)
    monkeypatch.setattr(auto_discover_edges, "YAHOO_NEWS_MAX_FAILURES", 3)
    return auto_discover_edges.IntelGatherer


def test_the_collector_writes_each_headline_as_a_source_the_extractor_can_cite(gatherer, monkeypatch):
    monkeypatch.setattr(auto_discover_edges, "yahoo_headlines", lambda ticker, count=5: [
        {"title": "Redwire wins NASA award", "link": "https://example.com/a", "summary": "Redwire will power the base.", "published": "Thu, 12 Nov 2026"},
        {"title": "Headline only", "link": "https://example.com/b", "summary": "", "published": ""},
    ])

    assert gatherer.get_yahoo_news("RDW") == (
        "SOURCE: RECENT NEWS (Redwire wins NASA award; https://example.com/a)\nDATA:\nRedwire will power the base. (Thu, 12 Nov 2026)\n"
        "SOURCE: RECENT NEWS (Headline only; https://example.com/b)\nDATA:\nHeadline only\n"
    )


def test_tickers_without_news_never_switch_the_source_off(gatherer, monkeypatch):
    calls = []
    monkeypatch.setattr(auto_discover_edges, "yahoo_headlines", lambda ticker, count=5: calls.append(ticker) or [])

    for ticker in ("A", "B", "C", "D", "E"):
        assert gatherer.get_yahoo_news(ticker) == ""
    assert calls == ["A", "B", "C", "D", "E"] and not gatherer.yahoo_news_disabled


def test_only_failures_in_a_row_switch_the_source_off(gatherer, monkeypatch, capsys):
    script = {"A": "fail", "B": "fail", "C": "ok", "D": "fail", "E": "fail", "F": "fail", "G": "ok"}
    calls = []

    def headlines(ticker, count=5):
        calls.append(ticker)
        if script[ticker] == "fail":
            raise requests.Timeout("read timed out")
        return [{"title": "T", "link": "https://example.com/t", "summary": "S", "published": ""}]

    monkeypatch.setattr(auto_discover_edges, "yahoo_headlines", headlines)

    results = [gatherer.get_yahoo_news(ticker) for ticker in "ABCDEFG"]

    assert calls == ["A", "B", "C", "D", "E", "F"]  # C's success reset the count; D-F switched it off
    assert results[2] and results[6] == "" and gatherer.yahoo_news_disabled
    out = capsys.readouterr().out
    assert out.count("Yahoo news unavailable") == 5 and out.count("skipping it for the rest of this run") == 1


def test_the_optional_article_reader_uses_the_same_feed(monkeypatch):
    monkeypatch.setenv("HEPHAESTUS_FETCH_NEWS_ARTICLES", "1")
    monkeypatch.setattr(additional_sources, "yahoo_headlines", lambda ticker, count=2: [
        {"title": "Redwire wins NASA award", "link": "https://example.com/a", "summary": "", "published": ""},
    ])
    fetched = []
    monkeypatch.setattr(additional_sources, "fetch_url_text", lambda url: fetched.append(url) or "Redwire is the supplier of solar arrays to NASA.")

    text = additional_sources.yahoo_news_article_text("RDW", company_name="Redwire")

    assert fetched == ["https://example.com/a"]
    assert "Redwire is the supplier of solar arrays to NASA." in text and "https://example.com/a" in text


def test_the_article_reader_survives_a_dead_feed(monkeypatch, capsys):
    monkeypatch.setenv("HEPHAESTUS_FETCH_NEWS_ARTICLES", "1")

    def down(ticker, count=2):
        raise requests.ConnectionError("down")

    monkeypatch.setattr(additional_sources, "yahoo_headlines", down)

    assert additional_sources.yahoo_news_article_text("RDW") == ""
    assert "Yahoo article fetch unavailable for RDW" in capsys.readouterr().out


def test_yahooquery_news_is_no_longer_called():
    source = (ROOT / "backend" / "auto_discover_edges.py").read_text(encoding="utf-8") + (ROOT / "backend" / "additional_sources.py").read_text(encoding="utf-8")
    assert ".news(" not in source
