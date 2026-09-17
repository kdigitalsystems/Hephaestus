#!/usr/bin/env bash
# Decide whether a scheduled publishing run still has work to do today.
#
# Manual dispatches always run. A scheduled run is skipped when docs/status.json on
# main already carries today's UTC date, which lets the workflow keep a second daily
# trigger as a retry without publishing twice. When the status file cannot be read the
# run goes ahead: a duplicate run is cheap, a missed day is not.
#
# Writes publish=true|false to $GITHUB_OUTPUT (stdout when run by hand).
set -euo pipefail

event="${EVENT_NAME:-schedule}"
output="${GITHUB_OUTPUT:-/dev/stdout}"
publish=true

if [ "$event" = "schedule" ]; then
  today="$(date -u +%F)"
  raw=""
  if ! raw="$(gh api "repos/${REPO:?REPO must name the repository}/contents/docs/status.json?ref=main" \
    -H "Accept: application/vnd.github.raw+json" 2>/tmp/published-today.err)"; then
    # Fail open, but say why: a permanently broken gate would run the four-hour job twice
    # a day forever without a single signal.
    echo "Freshness check could not read docs/status.json: $(tr '\n' ' ' </tmp/published-today.err | cut -c1-200)"
  fi
  published="$(printf '%s' "$raw" | jq -r '.data_as_of // empty' 2>/dev/null | cut -c1-10 || true)"
  if [ -n "$published" ] && [ "$published" = "$today" ]; then
    publish=false
    echo "Today's data is already published (data_as_of $published); skipping this scheduled run."
  else
    echo "Last published data is from ${published:-an unreadable status file}; running the pipeline for $today."
  fi
else
  echo "Triggered by $event; manual runs always publish."
fi

echo "publish=$publish" >> "$output"
