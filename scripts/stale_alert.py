#!/usr/bin/env python3
"""Open a GitHub issue when the published data stops moving, close it when it moves again.

server1 was offline on 2026-10-17 and the day's pipeline run sat queued for hours; the earlier
outage (2026-10-09 to 10-10) cost two days. Nothing told the owner either time. The data date in
docs/status.json is the only signal that does not depend on any one runner, so this runs on a
GitHub-hosted machine, compares it with the clock, and uses an issue as the notice (the owner
already gets GitHub notifications for the repository).
"""
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

LABEL = "pipeline-stale"
TITLE = "The pipeline has stopped publishing"
# The retry run (14:23 UTC) can itself start hours late, so a normal gap is up to ~30 hours.
DEFAULT_LIMIT_HOURS = 36.0
# An open issue is reminded about at most this often, so a long outage is not a daily email.
REMIND_HOURS = 24.0


def parse_time(value):
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def data_age_hours(status, now):
    """Hours since the published data date, or None when the file does not say."""
    if not isinstance(status, dict):
        return None
    moment = parse_time(status.get("data_as_of") or status.get("generated_at"))
    return None if moment is None else (now - moment).total_seconds() / 3600


def decide(age_hours, issue, now, limit=DEFAULT_LIMIT_HOURS, remind=REMIND_HOURS):
    """'open', 'remind', 'close' or 'none'.

    An unreadable date is 'none': a broken status file is a different failure, and a
    guess here would raise false alarms.
    """
    if age_hours is None:
        return "none"
    if age_hours > limit:
        if issue is None:
            return "open"
        touched = parse_time(issue.get("updatedAt"))
        if touched is None or (now - touched).total_seconds() / 3600 >= remind:
            return "remind"
        return "none"
    return "close" if issue else "none"


def describe(status, age_hours, repo):
    as_of = status.get("data_as_of") or status.get("generated_at")
    return (
        f"The published data is {age_hours:.0f} hours old (last update {as_of}). The daily update normally "
        "publishes every day by midday UTC.\n\n"
        "The pipeline job is pinned to the self-hosted runner `server1`; while that runner is offline the job "
        "waits in the queue instead of moving. Things to check:\n"
        "1. Is `server1` powered on and showing as online and idle (organisation settings, Actions, Runners)?\n"
        f"2. The latest runs: https://github.com/{repo}/actions/workflows/gpu_pipeline.yml\n\n"
        "The site's status line already tells visitors the data is old. This issue closes itself when the "
        "data publishes again."
    )


def gh(*args):
    result = subprocess.run(["gh", *args], capture_output=True, text=True)
    if result.returncode:
        raise SystemExit(f"gh {' '.join(args[:2])} failed: {result.stderr.strip()[:300]}")
    return result.stdout


def find_open_issue(run=gh):
    found = json.loads(run("issue", "list", "--label", LABEL, "--state", "open", "--limit", "1",
                           "--json", "number,updatedAt") or "[]")
    return found[0] if found else None


def act(action, status, age_hours, issue, repo, run=gh):
    if action == "open":
        run("label", "create", LABEL, "--color", "B60205", "--force",
            "--description", "The daily update has stopped publishing")
        run("issue", "create", "--title", TITLE, "--label", LABEL, "--body", describe(status, age_hours, repo))
    elif action == "remind":
        run("issue", "comment", str(issue["number"]), "--body",
            f"Still stale: the published data is now {age_hours:.0f} hours old.")
    elif action == "close":
        run("issue", "close", str(issue["number"]), "--comment",
            f"The data publishes again (now {age_hours:.0f} hours old). Closing.")


def main(status_path="docs/status.json", now=None, run=gh):
    now = now or datetime.now(timezone.utc)
    limit = float(os.environ.get("STALE_AFTER_HOURS") or DEFAULT_LIMIT_HOURS)
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    try:
        with open(status_path, encoding="utf-8") as handle:
            status = json.load(handle)
    except (OSError, ValueError) as exc:
        print(f"Could not read {status_path} ({exc}); not alerting on a file that cannot be read.")
        return "none"
    age = data_age_hours(status, now)
    issue = find_open_issue(run)
    action = decide(age, issue, now, limit=limit)
    print(f"data age: {'unknown' if age is None else f'{age:.1f} h'}; limit {limit:g} h; "
          f"open issue: {issue['number'] if issue else 'none'}; action: {action}")
    act(action, status, age, issue, repo, run=run)
    return action


if __name__ == "__main__":
    main()
    sys.exit(0)
