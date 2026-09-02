"""Publish machine-readable change feeds from the daily link-history snapshots.

Writes docs/changes.json (structured, newest day first) and docs/feed.xml (RSS 2.0)
so people can follow additions, removals, and updates to the published graph without
re-reading the dashboard. Output is derived only from docs/link_history.json, so
re-running it without a new snapshot produces byte-identical files.
"""

from __future__ import annotations

import argparse
import os
import tempfile
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from xml.sax.saxutils import escape

from export import history_entry_date, load_link_history, publishable_file_mode, write_json_atomic


ROOT = Path(__file__).resolve().parents[1]
DOCS_DIR = ROOT / "docs"
DEFAULT_HISTORY_PATH = DOCS_DIR / "link_history.json"
DEFAULT_CHANGES_PATH = DOCS_DIR / "changes.json"
DEFAULT_FEED_PATH = DOCS_DIR / "feed.xml"
SITE_URL = os.environ.get("HEPHAESTUS_SITE_URL", "https://kdigitalsystems.github.io/Hephaestus/")
FEED_DAYS = 14
MAX_LINKS_PER_LIST = 50


def company_url(ticker):
    return f"{SITE_URL}#company?ticker={ticker}"


def link_summary(key, entry):
    entry = entry or {}
    return {
        "relationship_key": key,
        "source_ticker": entry.get("source_ticker") or "",
        "target_ticker": entry.get("target_ticker") or "",
        "type": entry.get("type") or "Supply Link",
        "product": entry.get("product") or entry.get("type") or "Supply Link",
        "review_status": entry.get("review_status") or "pending",
        "confidence": entry.get("confidence"),
    }


def diff_snapshots(previous, current):
    previous_links = previous.get("links") or {}
    current_links = current.get("links") or {}
    new_keys = sorted(set(current_links) - set(previous_links))
    removed_keys = sorted(set(previous_links) - set(current_links))
    changed_keys = sorted(
        key for key in set(current_links) & set(previous_links)
        if current_links[key].get("review_status") != previous_links[key].get("review_status")
        or current_links[key].get("confidence") != previous_links[key].get("confidence")
    )
    return {
        "date": history_entry_date(current),
        "generated_at": current.get("generated_at"),
        "previous_date": history_entry_date(previous),
        "unique_links": current.get("unique_links", len(current_links)),
        "net_change": len(current_links) - len(previous_links),
        "new_count": len(new_keys),
        "removed_count": len(removed_keys),
        "changed_count": len(changed_keys),
        "new_links": [link_summary(key, current_links[key]) for key in new_keys[:MAX_LINKS_PER_LIST]],
        "removed_links": [link_summary(key, previous_links[key]) for key in removed_keys[:MAX_LINKS_PER_LIST]],
        "changed_links": [link_summary(key, current_links[key]) for key in changed_keys[:MAX_LINKS_PER_LIST]],
    }


def snapshot_entries(history):
    return [entry for entry in history if isinstance(entry, dict) and isinstance(entry.get("links"), dict)]


def daily_changes(history, days=FEED_DAYS):
    """Day-over-day diffs between consecutive snapshots, newest first."""
    entries = snapshot_entries(history)
    diffs = [diff_snapshots(entries[index - 1], entries[index]) for index in range(1, len(entries))]
    diffs.reverse()
    return diffs[:days]


def build_changes_payload(history, days=FEED_DAYS):
    entries = snapshot_entries(history)
    latest = entries[-1] if entries else {}
    return {
        "site": SITE_URL,
        "generated_at": latest.get("generated_at"),
        "unique_links": latest.get("unique_links"),
        "days": daily_changes(history, days),
    }


def rfc822(value, fallback_date=None):
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        try:
            moment = datetime.fromisoformat(f"{fallback_date}T00:00:00+00:00")
        except (TypeError, ValueError):
            moment = datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return format_datetime(moment)


def describe_link(link):
    text = f"{link['source_ticker']} → {link['target_ticker']}: {link['type']}"
    if link.get("product") and link["product"] != link["type"]:
        text += f" ({link['product']})"
    return text


def link_html(link):
    source = f'<a href="{escape(company_url(link["source_ticker"]))}">{escape(link["source_ticker"])}</a>'
    target = f'<a href="{escape(company_url(link["target_ticker"]))}">{escape(link["target_ticker"])}</a>'
    detail = escape(link["type"])
    if link.get("product") and link["product"] != link["type"]:
        detail += f" ({escape(link['product'])})"
    return f"<li>{source} → {target}: {detail}</li>"


def day_description_html(day):
    parts = [
        f"<p>{day['unique_links']} published supply links "
        f"({day['net_change']:+d} since {escape(str(day['previous_date']))}).</p>"
    ]
    for label, links, count in (
        ("New", day["new_links"], day["new_count"]),
        ("Removed", day["removed_links"], day["removed_count"]),
        ("Updated", day["changed_links"], day["changed_count"]),
    ):
        if not count:
            continue
        suffix = f" (showing {len(links)} of {count})" if count > len(links) else ""
        parts.append(f"<h4>{label}{suffix}</h4><ul>{''.join(link_html(link) for link in links)}</ul>")
    return "".join(parts)


def build_rss(payload):
    items = []
    for day in payload.get("days", []):
        if not (day["new_count"] or day["removed_count"] or day["changed_count"]):
            continue
        title = (
            f"{day['date']}: {day['new_count']} new, {day['removed_count']} removed, "
            f"{day['changed_count']} updated supply links ({day['unique_links']} total)"
        )
        items.append(
            "<item>"
            f"<title>{escape(title)}</title>"
            f"<link>{escape(SITE_URL)}</link>"
            f'<guid isPermaLink="false">hephaestus-changes-{escape(str(day["date"]))}</guid>'
            f"<pubDate>{rfc822(day.get('generated_at'), day.get('date'))}</pubDate>"
            f"<description>{escape(day_description_html(day))}</description>"
            "</item>"
        )
    latest_day = payload["days"][0]["date"] if payload.get("days") else None
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">\n'
        "<channel>\n"
        "<title>Hephaestus supply-chain graph changes</title>\n"
        f"<link>{escape(SITE_URL)}</link>\n"
        f'<atom:link href="{escape(SITE_URL)}feed.xml" rel="self" type="application/rss+xml"/>\n'
        "<description>Daily additions, removals, and updates to the reviewed supply-chain relationships published by Hephaestus.</description>\n"
        f"<lastBuildDate>{rfc822(payload.get('generated_at'), latest_day)}</lastBuildDate>\n"
        + "\n".join(items)
        + "\n</channel>\n</rss>\n"
    )


def write_text_atomic(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.chmod(temporary, publishable_file_mode(str(path)))
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def generate_change_feed(history_path=DEFAULT_HISTORY_PATH, changes_path=DEFAULT_CHANGES_PATH, feed_path=DEFAULT_FEED_PATH, days=FEED_DAYS):
    payload = build_changes_payload(load_link_history(str(history_path)), days)
    write_json_atomic(str(changes_path), payload)
    write_text_atomic(feed_path, build_rss(payload))
    return payload


def main():
    parser = argparse.ArgumentParser(description="Publish JSON and RSS feeds of daily supply-chain graph changes.")
    parser.add_argument("--history", type=Path, default=DEFAULT_HISTORY_PATH)
    parser.add_argument("--changes", type=Path, default=DEFAULT_CHANGES_PATH)
    parser.add_argument("--feed", type=Path, default=DEFAULT_FEED_PATH)
    parser.add_argument("--days", type=int, default=FEED_DAYS)
    args = parser.parse_args()
    payload = generate_change_feed(args.history, args.changes, args.feed, max(1, args.days))
    days_with_changes = sum(1 for day in payload["days"] if day["new_count"] or day["removed_count"] or day["changed_count"])
    print(f"Change feed: {len(payload['days'])} day(s) summarized, {days_with_changes} with changes -> {args.changes}, {args.feed}")


if __name__ == "__main__":
    main()
