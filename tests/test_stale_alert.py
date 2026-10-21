import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("stale_alert", ROOT / "scripts" / "stale_alert.py")
stale_alert = importlib.util.module_from_spec(spec)
sys.modules["stale_alert"] = stale_alert
spec.loader.exec_module(stale_alert)

NOW = datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc)


def hours_ago(hours):
    return (NOW - timedelta(hours=hours)).isoformat()


class FakeGh:
    """Stands in for the gh CLI: records every call and answers `issue list` from `open_issues`."""

    def __init__(self, open_issues=()):
        self.open_issues = list(open_issues)
        self.calls = []

    def __call__(self, *args):
        self.calls.append(args)
        if args[:2] == ("issue", "list"):
            return json.dumps(self.open_issues)
        return ""

    def verbs(self):
        return [call[:2] for call in self.calls if call[:2] != ("issue", "list")]


def write_status(tmp_path, data_as_of):
    path = tmp_path / "status.json"
    path.write_text(json.dumps({"data_as_of": data_as_of}))
    return str(path)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (hours_ago(5), 5.0),
        ("2026-10-17T07:00:00Z", 5.0),
        ("2026-10-17T07:00:00", 5.0),  # no offset: read as UTC, not local time
        ("2026-10-17T14:00:00+02:00", 0.0),
        ("2026-10-18T12:00:00+00:00", -24.0),
    ],
)
def test_the_data_age_is_read_from_the_status_file(value, expected):
    assert stale_alert.data_age_hours({"data_as_of": value}, NOW) == pytest.approx(expected)


def test_generated_at_is_the_fallback_and_unreadable_dates_are_unknown():
    assert stale_alert.data_age_hours({"generated_at": hours_ago(3)}, NOW) == pytest.approx(3)
    for status in ({}, {"data_as_of": "yesterday"}, {"data_as_of": None}, None, [], "x"):
        assert stale_alert.data_age_hours(status, NOW) is None


@pytest.mark.parametrize(
    ("age", "issue", "expected"),
    [
        (None, None, "none"),  # a status file that cannot be read is a different failure
        (None, {"number": 3, "updatedAt": hours_ago(1)}, "none"),
        (10, None, "none"),
        (35.9, None, "none"),  # the retry run can start hours late: up to ~30 h is normal
        (36.1, None, "open"),
        (60, None, "open"),
        (60, {"number": 3, "updatedAt": hours_ago(2)}, "none"),  # already reported today
        (60, {"number": 3, "updatedAt": hours_ago(24)}, "remind"),
        (60, {"number": 3, "updatedAt": hours_ago(23.9)}, "none"),
        (60, {"number": 3, "updatedAt": "garbled"}, "remind"),
        (6, {"number": 3, "updatedAt": hours_ago(2)}, "close"),
        (35, {"number": 3, "updatedAt": hours_ago(2)}, "close"),
    ],
)
def test_decide(age, issue, expected):
    assert stale_alert.decide(age, issue, NOW) == expected


def test_a_custom_limit_is_honoured():
    assert stale_alert.decide(2, None, NOW, limit=1) == "open"
    assert stale_alert.decide(2, None, NOW, limit=72) == "none"


def test_fresh_data_with_no_issue_does_nothing(tmp_path):
    gh = FakeGh()
    assert stale_alert.main(write_status(tmp_path, hours_ago(8)), now=NOW, run=gh) == "none"
    assert gh.verbs() == []


def test_stale_data_opens_one_labelled_issue_that_names_the_runner(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORY", "kdigitalsystems/Hephaestus")
    gh = FakeGh()
    assert stale_alert.main(write_status(tmp_path, hours_ago(50)), now=NOW, run=gh) == "open"
    assert gh.verbs() == [("label", "create"), ("issue", "create")]
    create = next(call for call in gh.calls if call[:2] == ("issue", "create"))
    body = create[create.index("--body") + 1]
    assert stale_alert.LABEL in create and stale_alert.TITLE in create
    assert "50 hours old" in body and "server1" in body
    assert "https://github.com/kdigitalsystems/Hephaestus/actions/workflows/gpu_pipeline.yml" in body


def test_an_open_issue_is_not_duplicated_and_reminders_are_spaced_out(tmp_path):
    path = write_status(tmp_path, hours_ago(50))
    quiet = FakeGh([{"number": 9, "updatedAt": hours_ago(3)}])
    assert stale_alert.main(path, now=NOW, run=quiet) == "none"
    assert quiet.verbs() == []
    old = FakeGh([{"number": 9, "updatedAt": hours_ago(30)}])
    assert stale_alert.main(path, now=NOW, run=old) == "remind"
    assert old.verbs() == [("issue", "comment")] and old.calls[-1][2] == "9"


def test_the_issue_closes_itself_when_the_data_moves_again(tmp_path):
    gh = FakeGh([{"number": 9, "updatedAt": hours_ago(30)}])
    assert stale_alert.main(write_status(tmp_path, hours_ago(4)), now=NOW, run=gh) == "close"
    assert gh.verbs() == [("issue", "close")] and gh.calls[-1][2] == "9"


def test_an_unreadable_status_file_never_alerts(tmp_path):
    gh = FakeGh()
    broken = tmp_path / "status.json"
    broken.write_text("{not json")
    assert stale_alert.main(str(broken), now=NOW, run=gh) == "none"
    assert stale_alert.main(str(tmp_path / "missing.json"), now=NOW, run=gh) == "none"
    assert gh.calls == []


def test_the_limit_can_be_overridden_for_a_test_run(tmp_path, monkeypatch):
    monkeypatch.setenv("STALE_AFTER_HOURS", "1")
    gh = FakeGh()
    assert stale_alert.main(write_status(tmp_path, hours_ago(8)), now=NOW, run=gh) == "open"
    monkeypatch.setenv("STALE_AFTER_HOURS", "")  # the workflow passes an empty input by default
    gh = FakeGh()
    assert stale_alert.main(write_status(tmp_path, hours_ago(8)), now=NOW, run=gh) == "none"


def test_the_workflow_is_wired_to_run_without_the_publisher_or_the_gpu_runner():
    workflow = (ROOT / ".github" / "workflows" / "stale_alert.yml").read_text(encoding="utf-8")
    # Every job runs on a GitHub-hosted machine (the comment may mention the self-hosted one).
    runners = [line.split(":", 1)[1].strip() for line in workflow.splitlines() if line.strip().startswith("runs-on:")]
    assert runners == ["ubuntu-latest"]
    assert "issues: write" in workflow and "contents: read" in workflow
    assert "group: hephaestus-stale-alert" in workflow and "hephaestus-main-publisher" not in workflow
    assert "schedule:" in workflow and "workflow_dispatch:" in workflow
    assert "STALE_AFTER_HOURS: ${{ inputs.stale_after_hours }}" in workflow
    assert "run: python3 scripts/stale_alert.py" in workflow
    assert "scripts/stale_alert.py" in workflow and "docs/status.json" in workflow
