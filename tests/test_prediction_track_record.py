"""The track record has to be able to become established, and measure the right thing.

Covers retention and compaction of the history file, a single price basis for start and
outcome (stock splits), stale-dashboard duplicates, and independent calibration.
"""

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import generate_predictions as cli
import historical_prices
import predictions
from predictions import (
    EXCLUDED_OUTCOME,
    NO_CALL_OUTCOME,
    calibration_from_history,
    compact_settled_entry,
    dashboard_staleness,
    evaluate_history,
    generate_predictions,
    independent_outcomes,
    prune_history,
    retrieve_prior_outcomes,
    stale_generation_days,
    track_record,
    write_outputs,
)


def company(ticker, cap, price=100.0, name=None, downstream=None):
    return {
        "ticker": ticker, "name": name or ticker, "sector": "Technology", "market_cap": cap, "price": price,
        "change": 2, "recommendation": "Buy", "target_price": 130, "upstream": [], "downstream": downstream or [],
    }


def link(ticker, kind="Customer"):
    return {
        "ticker": ticker, "type": kind, "confidence": 0.9, "review_status": "approved",
        "evidence_excerpt": "A long excerpt of source evidence that the settled history does not need to keep. " * 3,
        "source": "https://example.com/filing",
    }


def at(year, month, day, hour=11):
    return datetime(year, month, day, hour, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------------------
# 1. Retention: the track record can become established, and the file stays bounded
# ---------------------------------------------------------------------------------------

UNIVERSE = 50


def series_close(ticker, day):
    """A stub split-adjusted close that rises half a unit a day."""
    return 100 + int(ticker[1:]) + 0.5 * (day - datetime(2026, 1, 1).date()).days


def stub_price_lookup(ticker, target_at):
    return series_close(ticker, target_at.date()), f"stub_close:{target_at.date().isoformat()}"


def replay_dashboard(day):
    """The dashboard of a morning: the previous session's closes, as a pre-market quote is."""
    prices = [series_close(f"S{index:02d}", (day - timedelta(days=1)).date()) for index in range(UNIVERSE)]
    return {"industries": {"Technology": [
        company(f"S{index:02d}", 10_000 - index, prices[index],
                downstream=[link(f"S{(index + 1) % UNIVERSE:02d}"), link(f"S{(index + 7) % UNIVERSE:02d}", "Foundry")])
        for index in range(UNIVERSE)
    ]}}


def replay(weekdays, start=at(2026, 1, 5)):
    """The nightly job, repeated: generate, evaluate, prune, and carry the pruned file to the next night."""
    history = []
    day, done = start, 0
    timeline = []
    while done < weekdays:
        if day.weekday() < 5:
            done += 1
            payload, history = generate_predictions(replay_dashboard(day), history, UNIVERSE, now=day, price_lookup=stub_price_lookup)
            history = prune_history(history, day)
            timeline.append((day, payload["track_record"], payload["calibration"], len(history)))
        day += timedelta(days=1)
    return timeline, history


def test_track_record_becomes_established_and_the_history_stays_bounded(monkeypatch):
    # The default keeps a year; four months show the same shape in a fraction of the time.
    assert predictions.SCORED_RETENTION_DAYS >= 4 * predictions.HORIZON_DAYS
    monkeypatch.setattr(predictions, "SCORED_RETENTION_DAYS", 4 * predictions.HORIZON_DAYS)

    timeline, history = replay(190)

    established = next((index for index, (_, record, _, _) in enumerate(timeline) if record["status"] == "established"), None)
    # Three periods need a first generation, one 30 days on, one 60 days on, and the
    # last one matured: about 90 calendar days, 65 weekdays. The 2,000-entry cap never got there.
    assert established is not None and established + 1 <= 70, established
    day, record, calibration, _ = timeline[-1]
    assert record["status"] == "established" and record["independent_periods"] >= 4
    assert record["resolved"] >= 30 and calibration["resolved_predictions"] == record["resolved"]

    # Months, not entries: well past the old cap, nothing younger than the window is
    # dropped and nothing older is kept...
    oldest = min(predictions.parse_generated_at(entry) for entry in history)
    assert oldest >= day - timedelta(days=predictions.SCORED_RETENTION_DAYS)
    assert len(history) > 2000
    # ...so the file stops growing instead of growing without bound.
    sizes = [size for _, _, _, size in timeline]
    assert max(sizes[-30:]) - min(sizes[-30:]) <= 2 * UNIVERSE, sizes[-30:]  # a window holds a few more or fewer weekdays
    # Scored entries are small next to the unresolved ones they sit beside.
    settled = [entry for entry in history if entry.get("outcome")]
    open_ = [entry for entry in history if not entry.get("outcome")]
    average = lambda entries: sum(len(json.dumps(entry)) for entry in entries) / len(entries)
    assert average(settled) < average(open_) / 2


def test_compaction_keeps_everything_that_is_read_and_drops_the_bulk():
    full = {
        "prediction_id": "AAA-20260727-graph-signal-v1", "ticker": "AAA", "company_name": "Alpha", "sector": "Technology",
        "horizon_days": 30, "direction": "up", "confidence": 0.7, "score": 0.4, "direct_signal": 0.1, "network_signal": 0.3,
        "starting_price": 100.0, "key_inputs": [{"name": "recent_price_change", "value": 1.0, "weight": 0.45}],
        "scenario_summary": "prose", "bull_case": "prose", "bear_case": "prose", "scenario_model": "qwen",
        "connection_paths": [
            {"relationship_type": "GPU Supplier", "connected_ticker": "BBB", "evidence": "x" * 500, "contribution": 0.1},
            {"relationship_type": "gpu supplier", "connected_ticker": "CCC", "evidence": "y" * 500, "contribution": 0.2},
            {"relationship_type": "Foundry", "connected_ticker": "DDD", "evidence": "z" * 500, "contribution": 0.3},
        ],
        "model_name": "graph-signal-v1", "generated_at": "2026-07-27T12:00:00+00:00", "research_only": True,
        "realized_return_pct": 4.0, "evaluated_at": "2026-09-01T09:00:00+00:00", "evaluation_target_date": "2026-08-26",
        "outcome_price": 104.0, "outcome_price_source": "yahoo_historical_close:2026-08-26", "outcome": "correct",
        "evaluation_attempts": 1, "last_evaluation_status": "historical_close_unavailable", "last_evaluation_at": "2026-08-27T00:00:00+00:00",
    }
    compact = compact_settled_entry(full)

    for field in ("scenario_summary", "bull_case", "bear_case", "scenario_model", "evaluation_attempts"):
        assert field not in compact
    assert compact["connection_paths"] == [{"relationship_type": "GPU Supplier"}, {"relationship_type": "Foundry"}]
    assert len(json.dumps(compact)) < len(json.dumps(full)) / 2
    assert compact_settled_entry(compact) == compact
    # Every reader gives the same answer on the compact entry as on the full one.
    other = dict(full, ticker="BBB", company_name="Beta", prediction_id="BBB-1")
    prediction = {"ticker": "ZZZ", "sector": "Technology", "connection_paths": [{"relationship_type": "Foundry"}]}
    now = at(2026, 10, 1)
    assert calibration_from_history([full, other]) == calibration_from_history([compact, compact_settled_entry(other)])
    assert track_record([full, other], now) == track_record([compact, compact_settled_entry(other)], now)
    assert retrieve_prior_outcomes([full, other], prediction) == retrieve_prior_outcomes([compact, compact_settled_entry(other)], prediction)
    # Abstentions and excluded entries teach nothing about relationship types.
    assert compact_settled_entry(dict(full, outcome=NO_CALL_OUTCOME))["connection_paths"] == []
    assert compact_settled_entry(dict(full, outcome=EXCLUDED_OUTCOME))["connection_paths"] == []


def test_prune_compacts_settled_entries_but_leaves_unresolved_ones_whole():
    now = at(2026, 9, 1)
    settled = {"prediction_id": "done", "outcome": "correct", "generated_at": "2026-07-01T12:00:00+00:00",
               "scenario_summary": "prose", "connection_paths": [{"relationship_type": "A", "evidence": "x" * 300}]}
    unresolved = {"prediction_id": "open", "generated_at": "2026-08-25T12:00:00+00:00", "horizon_days": 30,
                  "scenario_summary": "prose", "connection_paths": [{"relationship_type": "A", "evidence": "x" * 300}]}

    retained = {entry["prediction_id"]: entry for entry in prune_history([settled, unresolved], now)}

    assert "scenario_summary" not in retained["done"] and retained["done"]["connection_paths"] == [{"relationship_type": "A"}]
    assert retained["open"] == unresolved


def test_entry_ceiling_drops_the_oldest_scored_entries_never_unresolved_ones(monkeypatch):
    monkeypatch.setattr(predictions, "HISTORY_ENTRY_CEILING", 10)
    now = at(2026, 9, 1)
    scored = [{"prediction_id": f"s{index}", "outcome": "correct", "generated_at": (at(2026, 6, 10) + timedelta(days=index)).isoformat()}
              for index in range(12)]
    # Older than every scored entry, yet still within the time an unresolved entry is kept.
    unresolved = [{"prediction_id": f"u{index}", "generated_at": "2026-06-02T12:00:00+00:00", "horizon_days": 30} for index in range(4)]

    retained = [entry["prediction_id"] for entry in prune_history(unresolved + scored, now)]

    assert len(retained) == 10
    assert all(f"u{index}" in retained for index in range(4))
    assert [name for name in retained if name.startswith("s")] == [f"s{index}" for index in range(6, 12)]


def test_history_file_has_one_line_per_entry_and_still_parses(tmp_path):
    history = [{"prediction_id": f"p{index}", "nested": {"a": [1, 2]}} for index in range(3)]
    history_path = tmp_path / "history.json"

    write_outputs({"predictions": []}, history, tmp_path / "predictions.json", history_path, now=at(2026, 9, 1))

    text = history_path.read_text(encoding="utf-8")
    assert json.loads(text) == history
    assert len(text.strip().splitlines()) == len(history) + 2
    write_outputs({"predictions": []}, [], tmp_path / "predictions.json", history_path, now=at(2026, 9, 1))
    assert json.loads(history_path.read_text(encoding="utf-8")) == []


# ---------------------------------------------------------------------------------------
# 2. One price basis for the start and the outcome
# ---------------------------------------------------------------------------------------

def matured_entry(**overrides):
    entry = {
        "prediction_id": "APH-20260825-graph-signal-v1", "ticker": "APH", "company_name": "Amphenol", "direction": "up",
        "starting_price": 155.55, "horizon_days": 30, "generated_at": "2026-08-25T10:00:00+00:00", "connection_paths": [],
    }
    entry.update(overrides)
    return entry


class Lookups:
    """Stub provider: a split-adjusted series keyed by ISO date, with a call log."""

    def __init__(self, closes):
        self.closes = closes
        self.calls = []

    def _find(self, kind, ticker, at_):
        self.calls.append((kind, ticker, at_.date().isoformat()))
        if self.closes is None:
            return None, "historical_close_unavailable"
        return self.closes[kind], f"yahoo_historical_close:{at_.date().isoformat()}"

    def end(self, ticker, at_):
        return self._find("end", ticker, at_)

    def base(self, ticker, at_):
        return self._find("base", ticker, at_)


NOW = at(2026, 10, 9)
# Amphenol split 2-for-1: the series says 77.78 before it and 83.08 a month later.
SPLIT = {"base": 77.78, "end": 83.08}


def test_a_split_between_generation_and_maturity_is_not_graded_as_a_crash():
    lookups = Lookups(SPLIT)
    entry = matured_entry()

    evaluate_history([entry], {}, NOW, lookups.end, lookups.base)

    # 155.55 -> 83.08 is -46.6%; 77.78 -> 83.08 is +6.8%, which is what happened.
    assert entry["outcome"] == "correct" and entry["realized_return_pct"] == 6.81
    assert entry["start_price_basis"] == "adjusted_series_close" and entry["graded_start_price"] == 77.78
    assert entry["starting_price"] == 155.55, "the published quote is never rewritten"


def test_an_ordinary_quote_is_kept_when_it_matches_the_series():
    lookups = Lookups({"base": 99.0, "end": 110.0})
    entry = matured_entry(starting_price=100.0)

    evaluate_history([entry], {}, NOW, lookups.end, lookups.base)

    assert entry["start_price_basis"] == "quote_matches_series" and "graded_start_price" not in entry
    assert entry["realized_return_pct"] == 10.0, "graded from the quote as published, not from the series close"


def test_a_reverse_split_is_reconciled_too():
    lookups = Lookups({"base": 100.0, "end": 104.0})
    entry = matured_entry(starting_price=10.0, direction="down")

    evaluate_history([entry], {}, NOW, lookups.end, lookups.base)

    assert entry["start_price_basis"] == "adjusted_series_close"
    assert entry["realized_return_pct"] == 4.0 and entry["outcome"] == "incorrect"


def test_an_entry_waits_when_the_start_basis_cannot_be_checked():
    def base_unavailable(_ticker, _at):
        return None, "historical_close_unavailable"

    entry = matured_entry()

    evaluate_history([entry], {}, NOW, Lookups(SPLIT).end, base_unavailable)

    assert "outcome" not in entry and "start_price_basis" not in entry
    assert entry["evaluation_attempts"] == 1 and entry["last_evaluation_status"] == "historical_close_unavailable"


def stored_split_grade():
    """What the nightly job published for Amphenol: 'incorrect', from a raw start price."""
    return matured_entry(
        outcome="incorrect", realized_return_pct=-46.59, outcome_price=83.08, outcome_price_date="2026-09-24",
        outcome_price_source="yahoo_historical_close:2026-09-24", evaluation_target_date="2026-09-24",
        evaluated_at="2026-09-25T10:00:00+00:00",
    )


def test_stored_entries_are_regraded_once_from_the_adjusted_series():
    lookups = Lookups(SPLIT)
    entry = stored_split_grade()

    evaluate_history([entry], {}, NOW, lookups.end, lookups.base)

    assert entry["outcome"] == "correct" and entry["outcome_before_regrade"] == "incorrect"
    assert entry["realized_return_pct"] == 6.81 and entry["graded_start_price"] == 77.78
    assert entry["regraded_at"] == NOW.isoformat()
    assert entry["evaluated_at"] == "2026-09-25T10:00:00+00:00", "when it was first scored is kept"
    # Idempotent: the next run neither changes it nor asks the provider again.
    before, calls = json.dumps(entry, sort_keys=True), len(lookups.calls)
    evaluate_history([entry], {}, NOW + timedelta(days=1), lookups.end, lookups.base)
    assert json.dumps(entry, sort_keys=True) == before and len(lookups.calls) == calls


def test_stored_entries_that_agree_with_the_series_are_only_marked_checked():
    lookups = Lookups({"base": 100.5, "end": 107.0})
    entry = matured_entry(starting_price=100.0, outcome="correct", realized_return_pct=7.0, outcome_price=107.0)

    evaluate_history([entry], {}, NOW, lookups.end, lookups.base)

    assert entry["start_price_basis"] == "quote_matches_series"
    assert entry["outcome"] == "correct" and entry["realized_return_pct"] == 7.0 and "regraded_at" not in entry


def test_a_split_after_scoring_regrades_both_ends_from_the_current_series():
    # Scored at 100 -> 110 before a 2-for-1 split; the series now reads 50 -> 55.
    lookups = Lookups({"base": 50.0, "end": 55.0})
    entry = matured_entry(starting_price=100.0, outcome="correct", realized_return_pct=10.0, outcome_price=110.0)

    evaluate_history([entry], {}, NOW, lookups.end, lookups.base)

    assert entry["realized_return_pct"] == 10.0 and entry["outcome_price"] == 55.0
    assert entry["start_price_basis"] == "adjusted_series_close"


def test_a_stored_outcome_price_the_series_no_longer_agrees_with_is_regraded_too():
    # The start still matches the series, but the end in the file does not: the series wins.
    lookups = Lookups({"base": 100.0, "end": 140.0})
    entry = matured_entry(starting_price=100.0, outcome="incorrect", direction="up", realized_return_pct=-8.0, outcome_price=92.0)

    evaluate_history([entry], {}, NOW, lookups.end, lookups.base)

    assert entry["outcome"] == "correct" and entry["realized_return_pct"] == 40.0 and entry["outcome_price"] == 140.0


def test_stored_entries_stay_unchecked_while_the_provider_fails_and_are_untouched_without_a_base_lookup():
    entry = stored_split_grade()
    evaluate_history([entry], {}, NOW, Lookups(None).end, Lookups(None).base)
    assert "start_price_basis" not in entry and entry["outcome"] == "incorrect"

    evaluate_history([entry], {}, NOW, Lookups(SPLIT).end)
    assert "start_price_basis" not in entry and entry["outcome"] == "incorrect"


def test_generate_predictions_hands_the_base_lookup_to_the_evaluation():
    lookups = Lookups(SPLIT)
    entry = matured_entry(generated_at=(NOW - timedelta(days=35)).isoformat())

    _, history = generate_predictions({"industries": {"Technology": [company("AAA", 5)]}}, [entry], now=NOW,
                                      price_lookup=lookups.end, base_lookup=lookups.base)

    assert history[0]["start_price_basis"] == "adjusted_series_close"


class SplitTicker:
    """Yahoo after a 2-for-1 split on 2026-09-01: every earlier close is already halved."""

    def __init__(self, ticker):
        self.ticker = ticker

    def history(self, **_kwargs):
        rows = []
        day = datetime(2026, 8, 20).date()
        while day <= datetime(2026, 9, 30).date():
            if day.weekday() < 5:
                raw = 160.0 if day < datetime(2026, 9, 1).date() else 80.0
                rows.append({"date": day, "close": raw / 2 if day < datetime(2026, 9, 1).date() else raw})
            day += timedelta(days=1)
        return type("Frame", (), {"reset_index": lambda self: self, "to_dict": lambda self, _orient: rows})()


def test_the_provider_series_serves_both_ends_from_one_split_adjusted_fetch(monkeypatch):
    fetches = []

    class Counting(SplitTicker):
        def history(self, **kwargs):
            fetches.append(kwargs)
            return super().history(**kwargs)

    monkeypatch.setattr(historical_prices, "Ticker", Counting)
    monkeypatch.setattr(historical_prices, "_SERIES_CACHE", {})
    generated = datetime(2026, 8, 25, 10, tzinfo=timezone.utc)   # a Tuesday, before the market opens

    base, base_source = historical_prices.historical_close_before("APH", generated)
    end, end_source = historical_prices.historical_close_on_or_after("APH", generated + timedelta(days=30))

    assert (base, base_source) == (80.0, "yahoo_historical_close:2026-08-24")
    assert (end, end_source) == (80.0, "yahoo_historical_close:2026-09-24")
    assert len(fetches) == 1, "dates a month apart share one request per ticker"
    # The quote recorded at the time (160) is what the old code compared with 80.
    assert 160.0 / base == 2.0


def test_the_session_before_generation_walks_back_over_a_weekend(monkeypatch):
    monkeypatch.setattr(historical_prices, "Ticker", SplitTicker)
    monkeypatch.setattr(historical_prices, "_SERIES_CACHE", {})

    monday = datetime(2026, 8, 24, 10, tzinfo=timezone.utc)
    assert historical_prices.historical_close_before("APH", monday)[1] == "yahoo_historical_close:2026-08-21"
    # A date with no session in the window is unavailable, never a guess.
    assert historical_prices.historical_close_before("APH", datetime(2025, 1, 1, tzinfo=timezone.utc))[0] is None


def test_a_failed_series_fetch_is_remembered_for_both_lookups(monkeypatch):
    attempts = []

    class Failing:
        def __init__(self, ticker):
            attempts.append(ticker)

        def history(self, **_kwargs):
            raise RuntimeError("rate limited")

    monkeypatch.setattr(historical_prices, "Ticker", Failing)
    monkeypatch.setattr(historical_prices, "_SERIES_CACHE", {})
    when = datetime(2026, 8, 25, 10, tzinfo=timezone.utc)

    assert historical_prices.historical_close_before("APH", when)[0] is None
    assert historical_prices.historical_close_on_or_after("APH", when)[0] is None
    assert attempts == ["APH"]


# ---------------------------------------------------------------------------------------
# 3. Stale dashboards
# ---------------------------------------------------------------------------------------

def day_of_entries(day, prices, outcome=None, direction="up"):
    entries = []
    for index, price in enumerate(prices):
        entry = {
            "prediction_id": f"T{index}-{day}", "ticker": f"T{index}", "company_name": f"Company {index}", "direction": direction,
            "starting_price": price, "horizon_days": 30, "generated_at": f"{day}T10:00:00+00:00", "connection_paths": [],
        }
        if outcome:
            entry.update(outcome=outcome, realized_return_pct=2.0, outcome_price=price * 1.02)
        entries.append(entry)
    return entries


def test_a_day_that_repeats_the_previous_days_prices_is_found_in_the_history_itself():
    prices = [100 + index for index in range(12)]
    history = (
        day_of_entries("2026-07-27", prices)
        + day_of_entries("2026-07-28", prices)              # the dashboard had not refreshed
        + day_of_entries("2026-07-29", [p + 1 for p in prices])
        + day_of_entries("2026-07-30", [p + 1 for p in prices[:10]] + [p + 2 for p in prices[10:]])  # 10 of 12 repeat
        + day_of_entries("2026-08-03", [p + 5 for p in prices[:6]] + [p + 1 for p in prices[6:]])    # 6 of 12 repeat: ordinary
    )

    assert stale_generation_days(history) == {"2026-07-28", "2026-07-30"}
    assert stale_generation_days(day_of_entries("2026-07-27", prices[:5]) + day_of_entries("2026-07-28", prices[:5])) == set(), (
        "too few companies in common to call it"
    )


def test_stale_start_entries_are_excluded_from_scoring_graded_or_not():
    prices = [100 + index for index in range(12)]
    history = (
        day_of_entries("2026-07-27", prices, outcome="correct")
        + day_of_entries("2026-07-28", prices, outcome="correct")
        + day_of_entries("2026-07-29", prices)               # matured but never graded: also excluded, not graded
    )
    lookups = Lookups({"base": 100.0, "end": 130.0})

    evaluate_history(history, {}, NOW, lookups.end)

    by_day = {}
    for entry in history:
        by_day.setdefault(entry["generated_at"][:10], set()).add(entry.get("outcome"))
    assert by_day == {"2026-07-27": {"correct"}, "2026-07-28": {EXCLUDED_OUTCOME}, "2026-07-29": {EXCLUDED_OUTCOME}}
    excluded = [entry for entry in history if entry["outcome"] == EXCLUDED_OUTCOME]
    assert all(entry["exclusion_reason"] == "duplicate_start_price" and "realized_return_pct" not in entry for entry in excluded)
    assert not lookups.calls, "an excluded entry costs no price lookups"
    record = track_record(history, NOW)
    assert record["excluded"] == 24 and record["resolved_with_overlap"] == 12
    assert calibration_from_history(history)["resolved_with_overlap"] == 12
    # Idempotent.
    snapshot = json.dumps(history, sort_keys=True)
    evaluate_history(history, {}, NOW, lookups.end)
    assert json.dumps(history, sort_keys=True) == snapshot


def stale_scenario():
    now = at(2026, 10, 9, 10)
    companies = [company(f"T{index}", 1000 - index, 100 + index) for index in range(12)]
    dashboard = {"industries": {"Technology": companies}, "generated_at": (now - timedelta(hours=2)).isoformat()}
    previous_run = now - timedelta(days=1)
    history = day_of_entries(previous_run.date().isoformat(), [100 + index + 7 for index in range(12)])
    return now, dashboard, history


def test_a_fresh_dashboard_with_new_prices_may_generate():
    now, dashboard, history = stale_scenario()

    assert dashboard_staleness(dashboard, history, now) is None
    assert dashboard_staleness(dashboard, [], now) is None


def test_a_dashboard_not_republished_since_the_last_run_is_stale():
    now, dashboard, history = stale_scenario()
    dashboard["generated_at"] = (now - timedelta(days=1, hours=2)).isoformat()   # before the 10:00 run yesterday

    assert "not after the previous signal run" in dashboard_staleness(dashboard, history, now)


def test_a_dashboard_older_than_the_limit_is_stale_even_if_newer_than_the_last_run():
    now, dashboard, _ = stale_scenario()
    dashboard["generated_at"] = (now - predictions.MAX_DASHBOARD_AGE - timedelta(hours=1)).isoformat()

    assert "hours ago" in dashboard_staleness(dashboard, [], now)


def test_repeated_prices_are_stale_even_when_the_timestamp_looks_new():
    # export.py stamps generated_at on every run, so a partial price refresh still looks new.
    now, dashboard, history = stale_scenario()
    for entry, built in zip(history, dashboard["industries"]["Technology"]):
        entry["starting_price"] = built["price"]

    assert "repeat the previous run's" in dashboard_staleness(dashboard, history, now)
    del dashboard["generated_at"]
    assert "repeat the previous run's" in dashboard_staleness(dashboard, history, now), "no timestamp: the prices decide"


def run_cli(monkeypatch, tmp_path, dashboard, history, *extra):
    paths = {name: tmp_path / f"{name}.json" for name in ("dashboard", "history", "output")}
    paths["dashboard"].write_text(json.dumps(dashboard), encoding="utf-8")
    paths["history"].write_text(json.dumps(history), encoding="utf-8")
    seen = {"end": [], "base": []}
    monkeypatch.setattr(cli, "historical_close_on_or_after", lambda ticker, at_: (seen["end"].append(ticker), (120.0, "yahoo_historical_close:x"))[1])
    monkeypatch.setattr(cli, "historical_close_before", lambda ticker, at_: (seen["base"].append(ticker), (100.0, "yahoo_historical_close:y"))[1])
    monkeypatch.setattr(sys, "argv", ["generate_predictions.py", "--dashboard", str(paths["dashboard"]), "--history", str(paths["history"]),
                                      "--output", str(paths["output"]), *extra])
    return paths, seen


def test_the_scheduled_run_refuses_a_stale_dashboard_and_writes_nothing(monkeypatch, tmp_path):
    now, dashboard, history = stale_scenario()
    dashboard["generated_at"] = (now - timedelta(days=2)).isoformat()
    monkeypatch.setattr(cli, "utc_now", lambda: now)
    paths, _ = run_cli(monkeypatch, tmp_path, dashboard, history)
    before = paths["history"].read_text(encoding="utf-8")

    with pytest.raises(SystemExit) as refused:
        cli.main()

    assert "Refusing to generate" in str(refused.value) and refused.value.code not in (0, None)
    assert not paths["output"].exists() and paths["history"].read_text(encoding="utf-8") == before


def test_a_fresh_run_generates_and_checks_start_prices_against_the_series(monkeypatch, tmp_path):
    now, dashboard, history = stale_scenario()
    history += [matured_entry(prediction_id="OLD-1", ticker="T0", generated_at=(now - timedelta(days=40)).isoformat(), starting_price=100.0)]
    monkeypatch.setattr(cli, "utc_now", lambda: now)
    paths, seen = run_cli(monkeypatch, tmp_path, dashboard, history)

    cli.main()

    assert json.loads(paths["output"].read_text(encoding="utf-8"))["universe_size"] == 12
    saved = {entry["prediction_id"]: entry for entry in json.loads(paths["history"].read_text(encoding="utf-8"))}
    assert saved["OLD-1"]["start_price_basis"] == "quote_matches_series" and seen["base"] == ["T0"]


def test_a_stale_dashboard_can_be_forced_for_a_manual_run(monkeypatch, tmp_path, capsys):
    now, dashboard, history = stale_scenario()
    dashboard["generated_at"] = (now - timedelta(days=2)).isoformat()
    monkeypatch.setattr(cli, "utc_now", lambda: now)
    paths, _ = run_cli(monkeypatch, tmp_path, dashboard, history, "--allow-stale-dashboard")

    cli.main()

    assert paths["output"].exists() and "stale dashboard" in capsys.readouterr().out


# ---------------------------------------------------------------------------------------
# 4. Calibration learns from independent outcomes
# ---------------------------------------------------------------------------------------

def scored_entry(ticker, day, outcome="correct", relationship="Historical", name=None, offset=0):
    generated = at(2026, 7, 1) + timedelta(days=day, minutes=offset)
    return {
        "prediction_id": f"{ticker}-{day}", "ticker": ticker, "company_name": name or ticker, "direction": "up", "outcome": outcome,
        "horizon_days": 30, "generated_at": generated.isoformat(), "realized_return_pct": 1.0 if outcome == "correct" else -1.0,
        "connection_paths": [{"relationship_type": relationship}],
    }


def test_eleven_daily_repeats_of_one_company_are_one_observation_for_the_weights():
    repeated = [scored_entry("AAA", day) for day in range(11)]
    single = [scored_entry("AAA", 0)]

    weights = calibration_from_history(repeated)["relationship_weights"]

    assert weights == calibration_from_history(single)["relationship_weights"]
    # One correct observation against a five-observation prior: 0.70 + 0.6 * 3.5 / 6.
    assert weights["historical"] == 1.05
    # The old count learned (11 + 2.5) / 16 -> 1.206 from the same single company.


def test_calibration_resolved_count_matches_the_track_record_and_keeps_the_raw_count():
    history = [scored_entry(f"T{index}", day) for index in range(5) for day in range(10)]

    calibration = calibration_from_history(history)
    record = track_record(history, at(2026, 12, 1))

    assert calibration["resolved_predictions"] == record["resolved"] == 5
    assert calibration["resolved_with_overlap"] == record["resolved_with_overlap"] == 50
    assert calibration["hit_rate"] == record["hit_rate"] == 1.0


def test_a_company_counts_again_once_its_next_window_opens():
    history = [scored_entry("AAA", 0), scored_entry("AAA", 29), scored_entry("AAA", 30, outcome="incorrect")]

    kept = independent_outcomes(history)

    assert [entry["prediction_id"] for entry in kept] == ["AAA-0", "AAA-30"]
    assert calibration_from_history(history)["resolved_predictions"] == 2


def test_calibration_ignores_abstentions_and_excluded_entries():
    history = [scored_entry("AAA", 0), dict(scored_entry("BBB", 0), outcome=EXCLUDED_OUTCOME), dict(scored_entry("CCC", 0), outcome=NO_CALL_OUTCOME)]

    assert calibration_from_history(history)["resolved_predictions"] == 1


# ---------------------------------------------------------------------------------------
# 5. One company, several listings
# ---------------------------------------------------------------------------------------

def test_share_classes_of_one_company_are_one_independent_outcome():
    goog = scored_entry("GOOG", 0, name="Alphabet Inc. Class C Capital Stock")
    googl = scored_entry("GOOGL", 0, name="Alphabet Inc. Class A Common Stock", offset=1)
    msft = scored_entry("MSFT", 0, name="Microsoft Corporation Common Stock")

    kept = independent_outcomes([googl, goog, msft])

    assert sorted(entry["ticker"] for entry in kept) == ["GOOG", "MSFT"], "the earlier of the two is kept"
    record = track_record([goog, googl, msft], at(2026, 12, 1))
    assert record["resolved"] == 2 and record["resolved_with_overlap"] == 3
    # Different companies that merely share a first word stay separate.
    toro = [scored_entry("TTC", 0, name="Toro Company"), scored_entry("TORO", 0, name="Toro Corp.")]
    assert len(independent_outcomes(toro)) == 2


def test_entries_without_a_company_name_fall_back_to_the_ticker():
    first = {k: v for k, v in scored_entry("AAA", 0).items() if k != "company_name"}
    second = {k: v for k, v in scored_entry("AAA", 3).items() if k != "company_name"}

    assert len(independent_outcomes([first, second])) == 1
